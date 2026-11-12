# ReproCrowd CLI: design

A local REST service and a thin CLI client for building computational-reproducibility packages for a
paper, without Zotero. The workflow follows the MetaReader Zotero plugin. The service owns all file and
LLM operations. The CLI only sends HTTP requests, so any other client can use the same API.

Status: phase 1 (everything up to master-script templates) is implemented and tested. Later phases are
listed under [TODO](#todo-later-phases).

> **Implemented in** notes (like this one) say which files implement each component. Paths are relative to
> `src/reprocrowd/`.

## Principles

- **The package folder is the state.** Everything about a package is in its own folder, described by a
  [Frictionless Data Package](https://datapackage.org) manifest (`datapackage.json`). The service keeps only
  a registry (package id → folder).
- **One source of truth for schemas.** Every custom JSON structure is a Pydantic model. JSON Schemas
  (`schemas/*.schema.json`) and the schema reference (`docs/schemas.md`) are generated from those models, and
  a test fails if they drift. See [Schemas and validation](#schemas-and-validation).
- **Nothing executable comes from the caller.** LLM commands are *profiles* in the service config. Callers
  choose a profile by name and can pass prompt text, but never argv. Local paths can be restricted to
  configured roots.
- **Non-destructive curation.** LLM output is stored as the LLM produced it. User changes go in an overlay,
  so they can be restored.
- **Modular formats and templates.** Each full-text format and each master-script template has one module or
  folder. Adding one means adding a file and registering it.

## Components

```mermaid
flowchart LR
  CLI["rc (Typer CLI)"] -- HTTP/JSON --> API["FastAPI app /v1"]
  other["other clients"] -- HTTP/JSON --> API
  API --> PKG["package: manifest + resources + registry"]
  API --> OA["openalex: metadata, candidates, downloads"]
  API --> FMT["formats: check + parse<br/>pdf · tei · jats · docling"]
  API --> LLM["llm: configured profiles, audit trail"]
  API --> CUR["curation: targets · actions"]
  API --> TPL["scripts: master-script templates"]
  API --> VAL["validation: frictionless + jsonschema + model + semantic"]
  FMT --> DOC[("common document schema")]
  LLM --> DOC
  CUR --> DOC
  CFG[/"config.toml<br/>LLM profiles, OpenAlex key, allowed roots, token"/] --> API
```

> **Implemented in:** CLI `cli.py` · HTTP app `api/app.py`, bodies `api/models.py`, problem catalog
> `api/problems.py` · package and registry `package.py` · config `config.py` · OpenAlex `openalex.py`,
> `metadata.py`, `identifiers.py` · full texts `fulltexts.py` · formats `formats/` · extractions
> `extractions.py` · LLM runner `llm.py`, prompts `prompts/*.md` · inputs `inputs.py` · targets/actions
> `curation.py` · scripts `scripts.py`, `templates/` · validation `validation.py` · schemas `schemas/`.

## Package layout

```
<package>/
  datapackage.json            Frictionless manifest: every file below is a resource (path, bytes, sha256,
                              jsonSchema, reprocrowd: {kind, …})
  schemas/*.schema.json       copies of the JSON Schemas the resources refer to (jsonSchema)
  metadata/openalex.json      OpenAlex work record
  fulltext/<file>             added full texts (pdf / TEI / JATS / Docling JSON), unchanged
  extractions/<name>.json     common document schema (parsed or reconciled)
  inputs/…                    input files (data / code / other); zip archives unpacked under inputs/<stem>/
  targets.json                curated targets (manual + LLM runs with overlays)
  actions.json                curated actions (the former "analysis plan" steps)
  results_schema.json         \  written by the script step: enabled targets, the resolved plan
  analysis_plan.json          /  (load steps + actions + results step), the master script, README.md
  <master script>, README.md
  llm-calls/<call-id>/        prompt.txt, stdout.txt, stderr.txt, call.json for every LLM call (audit
                              trail; not listed as resources)
  jobs/<id>.json, jobs/<id>/  job records; the agent's prompt (stdin.txt), stdout.log, stderr.log
  AGENT_PROMPT.md             instructions for a coding agent (written by the script step)
  .git/                       created by the first job: commits before and after every agent run
  targets/figures/<id>.png    originals: published images, crops, or full pages (PUT …/figures)
  report.html, comparison.json  the reproduction report (PUT …/report)
  archive/<doi>-reproduction.zip  the package as a ZIP (PUT …/archive; not itself a resource)
```

> **Implemented in:** `package.py` (`init_package` writes the minimal package; `Package.edit()` is the only way
> manifests change: lock, load, validate, atomic write).

## User flow and calls

Each box is one call: **requires → produces**. The CLI command is shown in brackets.

```mermaid
flowchart TD
  A["POST /v1/packages<br/>[rc package create PATH --id ID --fetch]<br/>needs: new or empty folder<br/>makes: datapackage.json, schemas/, empty targets/actions, registry entry"]
  B["POST /v1/packages/{p}/metadata · PUT to replace<br/>[rc metadata fetch P --overwrite]<br/>needs: identifier · POST gives 409 if present<br/>makes: metadata/openalex.json, identifiers, title"]
  C1["POST /v1/packages/{p}/fulltexts {path}<br/>[rc fulltext add P FILE]<br/>needs: pdf / xml / json passing the integrity check<br/>makes: fulltext/…, resource ft-*"]
  C2["GET …/fulltexts/candidates · POST …/fulltexts {candidate}<br/>[rc fulltext candidates / fetch]<br/>needs: metadata · download passes the check"]
  D["POST …/extractions {method: parse} · PUT …/extractions/{x} to replace<br/>[rc extract parse P FT]<br/>needs: tei / jats / docling full text<br/>makes: extractions/ex-*.json"]
  E["POST …/extractions {method: reconcile}<br/>[rc extract reconcile P FT… --prompt-file --profile]<br/>needs: ≥1 full text, LLM profile<br/>makes: validated extraction, or 422 llm_output_invalid / 502 llm_call_failed / 504 llm_timeout"]
  F["POST …/inputs · PATCH / DELETE …/inputs/{in}<br/>[rc input add / edit / rm]<br/>makes: inputs/…, role / note / used"]
  G["POST …/targets (manual) · POST …/targets/runs (LLM)<br/>PATCH …/targets/{id} · DELETE …/targets/{id}/edit (restore)<br/>[rc target …]<br/>needs: refs into an extraction, or unreferenced + note"]
  H["POST …/actions · …/actions/runs<br/>[rc action …]<br/>needs: refs, inputs, the targets they produce"]
  I["PUT …/script {template, on_modified}<br/>[rc script create P --template r]<br/>needs: ≥1 enabled target and action<br/>makes: master script, results_schema.json, analysis_plan.json, README.md"]
  V["GET …/validation<br/>[rc validate P]"]
  T["TODO: code-generation job, run, compare, report, submit"]
  A --> B --> C2
  A --> C1
  C1 --> D
  C2 --> D
  C1 --> E
  C2 --> E
  D --> G
  E --> G
  A --> F
  F --> H
  G --> H --> I --> T
  I -.-> V
```

## HTTP conventions

The API follows common REST practice, so generic tooling (OpenAPI generators, HTTP caches, problem-aware
clients) works without special cases.

| Topic | Convention |
|---|---|
| Versioning | Everything under `/v1`. |
| Documentation | OpenAPI 3.1 generated from the code: Swagger UI at `/docs`, ReDoc at `/redoc`, spec at `/openapi.json`; committed copy `docs/openapi.json` (test checks it is current). Every endpoint has a summary, tags, typed request/response models and its documented error responses. |
| Resources | Plural nouns, ids in the path: `/packages/{pkg}/targets/{id}`. Sub-resources instead of verbs: LLM runs are `…/targets/runs`, the user's overlay on an LLM entry is `…/targets/{id}/edit` (`DELETE` = restore). |
| Methods | `POST` creates (201 + `Location`) and returns 409 if the thing exists. `PUT` replaces explicitly (metadata, extractions, script). `PATCH` is a JSON merge patch (RFC 7396; `application/merge-patch+json` or JSON). `DELETE` returns 204. |
| Errors | RFC 9457 `application/problem+json`: `type` (`/v1/problems/<code>`, resolvable), `title`, `status`, `detail`, `instance`, plus `code`, `request_id` and problem-specific members (`errors`, `stage`, `problems`, …). Catalog at `GET /v1/problems`; a test checks every code raised in the code is catalogued. |
| Validation | Bodies are validated against their models before anything runs (`request_invalid`, 422). Unknown fields are rejected, so a caller can't sneak in e.g. `argv`. |
| Concurrency | Package-scoped responses carry an `ETag` (hash of the manifest). Unsafe requests may send `If-Match`; on mismatch the answer is 412 `precondition_failed`. Writes are serialized per package and files are written atomically. |
| Auth | Bearer token (OpenAPI `HTTPBearer` scheme), enforced when the config sets `token`. `/v1/health` and `/v1/problems` are open. Binds to 127.0.0.1 by default. |
| Tracing | `X-Request-ID` echoed or generated on every response, also in problem bodies. |
| Uploads | Endpoints that take a server-local `path` also accept `multipart/form-data` with a `file` part (same URL; both bodies documented). Size limit `max_upload_mb`; file names are reduced to their last component. |
| Pagination | Opt-in `?limit=&offset=` on list endpoints. Bodies stay arrays; `X-Total-Count` and RFC 8288 `Link` headers (`first`, `prev`, `next`, `last`). Without `limit` the whole list is returned. |
| Long operations | Jobs: `202 Accepted` + `Location` of a job resource; progress as Server-Sent Events; `DELETE` cancels. LLM extraction calls are synchronous (bounded by the profile's timeout). |
| Custom methods | Operations that aren't CRUD on a resource use a `:verb` suffix (`POST /packages/{pkg}:sync`). |

> **Implemented in:** `api/app.py` (routes, middleware for request id and ETag, `get_pkg` for If-Match, `auth`,
> exception handlers), `api/problems.py` (catalog, `Problem` model), `api/models.py` (bodies incl. generated
> merge-patch models), `schemas/export.py` (writes `docs/openapi.json`).

### Endpoints

| Method and path | Body / query | Result | Problem codes |
|---|---|---|---|
| `GET /v1/health` · `GET /v1/problems[/{code}]` | | status · problem types | |
| `GET /v1/llm-profiles` · `GET /v1/templates` | | profiles (argv read-only) · script templates | |
| `POST /v1/packages` | `{path, identifier?, fetch_metadata?, adopt?}` | 201 summary | `package_exists`, `folder_not_empty`, `path_not_allowed`, `invalid_identifier` |
| `GET /v1/packages` · `GET …/{pkg}` · `GET …/{pkg}/manifest` | | summaries · `datapackage.json` | `not_found`, `package_missing` |
| `DELETE /v1/packages/{pkg}` | | 204, unregisters (files stay) | |
| `GET …/{pkg}/validation` | | `{valid, errors[], warnings[]}` (each with `stage`) | |
| `POST` / `PUT …/{pkg}/metadata` | `{identifier?}` | 201 / 200 summary | `metadata_exists`, `identifier_conflict`, `no_identifier`, `openalex_not_found`, `openalex_failed` |
| `GET …/{pkg}/metadata` | | the OpenAlex record | |
| `GET …/fulltexts` · `GET …/fulltexts/{ft}[/content]` | | resources · the file | |
| `GET …/fulltexts/candidates` | | `{candidates[], notes[]}` | `metadata_missing` |
| `POST …/fulltexts` | `{path}` or `{candidate}`, `name?` | 201 resource | `integrity_check_failed` (+`problems`), `duplicate_fulltext`, `download_failed` |
| `POST …/fulltexts` | `{source, converter}` (PDF → TEI via GROBID, → Docling JSON via docling) | 201 resource, origin `conversion` | `unknown_converter`, `converter_not_configured`, `not_convertible`, `conversion_failed`, `conversion_timeout` |
| `GET /v1/converters` | | converters and whether they are configured | |
| `DELETE …/fulltexts/{ft}` | | 204 | `in_use` |
| `POST …/extractions` · `PUT …/extractions/{x}` | `{method:"parse", fulltext}` | 201 / 200 resource | `not_parseable`, `parse_failed`, `extraction_exists` |
| | `{method:"reconcile", sources[], prompt?, instructions?, profile?}` | | `llm_output_invalid` (+`stage`, `errors`, `raw_output`), `llm_call_failed`, `llm_timeout`, `unknown_profile` |
| `GET …/extractions[/{x}]` | `?view=document\|outline` | resources · document JSON or text with ids | |
| `DELETE …/extractions/{x}` | | 204 | `in_use` |
| `POST …/inputs` · `GET …/inputs[/{in}]` | `{path, role?, note?, unpack?}` (file, zip or directory) or multipart `file` | 201 resources (archive + members, or the directory's files) | `invalid_archive`, `not_a_file`, `directory_too_large`, `directory_empty`, `upload_too_large` |
| `PATCH` / `DELETE …/inputs/{in}` | `{role?, note?, used?}` | resource · 204 | `archive_member`, `in_use` |
| `GET …/targets` | `?view=effective\|raw&include_disabled` | `[{item, origin}]` or `targets.json` | |
| `POST …/targets` | target fields | 201 target | `request_invalid`, `invalid_reference` |
| `GET` / `POST …/targets/runs` · `GET` / `PATCH …/targets/runs/{run}` | `{extraction, scope?, instructions?, prompt?, profile?}` · `{disabled}` | runs · 201 run | `unknown_ids`, LLM codes |
| `GET` / `PATCH` / `DELETE …/targets/{id}` | merge patch incl. `disabled` | `{item, origin}` · 204 | `schema_invalid`, `invalid_reference`, `manual_item`, `llm_entry`, `in_use` |
| `DELETE …/targets/{id}/edit` | | restored LLM version | `manual_item` |
| `…/actions…` | same as targets | | |
| `GET` / `PUT …/script` | `{template, on_modified?: refuse\|keep\|overwrite}` | template, TODO status, files · 201 / 200 `{created, files, command}` | `unknown_template`, `nothing_to_script`, `script_modified`, `job_running` |
| `GET /v1/agent-profiles` | | agent profiles (argv, tools read-only) | |
| `POST …/extractions:preview` · `POST …/targets/runs:preview` · `POST …/jobs:preview` | the body of the call they preview | `{prompt, argv, profile, model, effort, output_schema}`; runs and stores nothing | the call's input errors |
| `POST …/jobs` | `{type:"codegen", profile?, allow_exec?, instructions?}` | 202 job + `Location` | `script_missing`, `job_running`, `unknown_profile`, `job_start_failed` |
| `GET …/jobs` · `GET …/jobs/{j}` · `GET …/jobs/{j}/stdout` | | jobs · job · raw output | |
| `GET /v1/jobs` | | `[{package, job}]`: the jobs running now in all packages (to warn before stopping the service) | |
| `GET …/jobs/{j}/events` | `?after=N`; `Accept: text/event-stream`, `Last-Event-ID` | events (JSON) or live SSE ending with `end` | |
| `DELETE …/jobs/{j}` | | 202, cancels | `job_finished` |
| `POST …/jobs` | `{type:"run"}` | 202 job; result: results file present, output errors/warnings, commit | `script_missing`, `job_running` |
| `GET …/comparison` | `?rounding=reported\|N&threshold=P` | per-target status, deviation, table cells; summary | `script_missing`, `results_missing` |
| `PUT …/figures/{target}` · `PUT …/figures` · `GET …/figures` · `GET …/figures/{target}` | `{pdf?, scale?}` | published image, crop or full page · per-target outcomes · the originals · the PNG | `not_a_figure_target`, `no_figure_reference`, `no_coordinates`, `pdf_unknown` |
| `GET …/coordinates` | | what each full text locates, on which PDF | |
| `PUT …/report` · `GET …/report` | `{rounding?, threshold?}` | 201/200 summary · the HTML | `script_missing`, `results_missing`, `job_running`, `report_missing` |
| `PUT …/archive` · `GET …/archive` | | 201/200 archive info · the ZIP | `package_invalid`, `report_missing`, `archive_missing` |
| `GET /v1/database` · `GET /v1/database/packages?doi=` | | status · packages for a DOI | `database_not_configured`, `database_unreachable` |
| `POST …/submissions` | `{note?, database_token?}` | 201 submission (`finish_url` to confirm in the browser; published at once with a token) | `no_doi`, `run_not_succeeded`, `archive_missing`, `archive_stale`, `duplicate_submission`, `submission_rejected`, `database_token_invalid`, `database_forbidden`, … |
| `GET …/submissions[/{id}]` | | submissions · one, status refreshed from the database | |
| `GET /v1/zenodo` · `POST /v1/zenodo:verify-token` · `GET /v1/zenodo/records?doi=` | `?sandbox` · `{zenodo_token, sandbox?}` | server status · token accepted · published ReproCrowd records for a DOI | `zenodo_token_invalid`, `zenodo_unreachable` |
| `POST …/deposits:preview` · `POST …/deposits` | `{note?, exclude?, creators?, community?}` + `{zenodo_token?, sandbox?, publish?}` | metadata and file groups · 201 deposit (a draft with a reserved DOI, or published) | the submission preconditions, `duplicate_submission`, `zenodo_token_missing`, `zenodo_creators_missing`, `zenodo_rejected`, … |
| `GET …/deposits[/{id}]` · `POST …/deposits/{id}:refresh` · `:publish` · `:discard` | `{zenodo_token?}` | deposits · status from Zenodo · publish a draft · delete a draft | `deposit_not_draft` |
| `GET …/files` · `GET …/files/{path}` · `POST …/{pkg}:sync` | | files on disk with their resource · one file's content · `{added, updated, removed}` | `not_found`, `job_running` |

**LLM error classes (reconcile, target/action runs):**
- `llm_call_failed` (502): the configured command could not start or exited non-zero (stderr included).
- `llm_timeout` (504): the call ran longer than the profile's `timeout_s`. The whole process group is
  killed.
- `llm_output_invalid` (422): the call succeeded but its output is unusable. `stage` says where it failed:
  `json` (no parseable JSON object) or `schema` (does not validate). The raw output is kept in
  `llm-calls/<id>/stdout.txt`.

For target/action runs, *individual* invalid items do not fail the call. They are stored in the run's
`rejected` list with their errors, and the valid items are kept.

> **Implemented in:** `llm.py` (`run`, `build_argv`, `extract_json`, `output_invalid`), used by
> `extractions.reconcile` and `curation.Curation.generate`.

## Common document schema

Every parser and the reconcile call produce the same structure (`schemas/document.schema.json`):

```
Document
  title, source {method: parse|reconcile, format, fulltexts[], llm_call?}
  sections[]  {id "sec3", title, level, paragraphs[]}           flat list in reading order; level = nesting
     paragraphs[] {id "sec3.p2", sentences[], boxes?}
        sentences[] {id "sec3.p2.s1", text, boxes?}
  figures[]   {id "fig1", label, section, caption[] (sentences "fig1.s1"), boxes?}
  tables[]    {id "tab1", label, section, caption[], rows[][], boxes?}
```

The ids are hierarchical and readable, so a reference such as `{extraction: "ex-grobid", id: "sec3.p2.s1"}`
also says where it points. Parsers and the LLM produce a **DocumentDraft** (the same content without ids;
figures and tables nested in the section where they appear). `build_document` assigns all ids, so there is
one id scheme for every source, and the LLM never has to invent ids.

**Format modules** each provide `sniff`, `check(path) → problems + notes` (integrity) and optionally
`parse(path) → DocumentDraft`. TEI and JATS are declared as an `XmlMapping`: element names for sections,
headings, paragraphs, sentences, figures and tables, regions to read, elements to skip, transparent
wrappers, and coordinates. A shared walker interprets the mapping. Docling has a label → role table. XML
is parsed without DTDs, entities or network access.

| Format | Detected by | Check | Parse |
|---|---|---|---|
| pdf | `%PDF-` header | opens with pypdf, ≥ 1 page, not encrypted (notes: page count, missing text layer) | not parseable (reconcile input only) |
| tei | XML root `TEI` / `teiCorpus` | well-formed, has body or abstract | `XmlMapping` (uses GROBID `<s>` and `coords` when present) |
| jats | XML root `article` | well-formed, has body or abstract | `XmlMapping` (floats inside paragraphs are lifted out) |
| docling | JSON `schema_name: DoclingDocument` | valid JSON with `body`, non-empty `texts` | label table |

**Converters** turn a PDF full text into TEI (GROBID service: sentence segmentation and coordinates
requested) or Docling JSON (docling command). They are configured in the service (`[converters.grobid] url`,
`[converters.docling] argv` with `{input}`/`{outdir}`); the output goes through the same integrity check and
records its origin (`{type: conversion, source: <pdf resource>, converter}`), which links coordinates in
the TEI/Docling back to the PDF they refer to.

> **Implemented in:** `converters.py` (registry, GROBID client, docling runner), `fulltexts.convert`.
> Tested with a mocked GROBID and a fake docling, and with the real local GROBID and docling on Head et
> al. (2015): 20 s each.

> **Implemented in:** `schemas/document.py` (Document, DocumentDraft), `formats/builder.py` (id scheme),
> `formats/base.py` (Format interface), `formats/__init__.py` (registry, detection), `formats/xmlmap.py`
> (mapping walker), `formats/tei.py`, `formats/jats.py`, `formats/docling.py`, `formats/pdf.py`,
> `formats/segment.py` (sentence splitter), `extractions.py` (parse, reconcile, outline listing).
> Tested on the real GROBID TEI and Europe PMC JATS of Head et al. (2015) and on fixtures.

## Curation (targets and actions)

Targets and actions share one generic structure (`schemas/targets.schema.json`, `actions.schema.json`):

```
{ schema_version, kind: "targets"|"actions",
  manual:   [T, …],                                 added by hand, edited in place, deletable
  llm_runs: [{ id, disabled, request: {extraction, scope}, call: LlmCall,
               entries: [{base: T, edit?: partial T, disabled}],
               rejected: [{item, errors}] }] }
```

- The **effective** list is the manual items plus the enabled entries of enabled runs, with `base ⊕ edit`
  applied. Each item comes with its origin (`manual`, or `llm` + run + edited + disabled).
- **Ids** (`T001…`, `A01…`) are assigned by the service in creation order and never reused.
- **References**: `refs: [{extraction, id}]` where `id` is a sentence, table or figure id of that extraction.
  They are checked against the document. With no refs, the item must set `unreferenced: true` and give a
  `note`.
- **LlmCall** stores the profile, argv, model, effort, template (`default` or `custom`), instructions, the
  full prompt, the raw output, start time, duration and exit code.
- **Target** fields: `type` (number / categorical / table / figure), `description`, `quote`, `expected`
  (checked against the type), `comparator`, `decimals`, `unit`.
- **Action** fields: `kind` (transform / analysis), `summary`, `quote`, `inputs` (input resource names),
  `depends_on` (actions, no cycles), `produces` (target ids).
- LLM prompts list the document with ids in brackets and embed the JSON Schema of the expected output
  (`target-llm-output`, `action-llm-output`), generated from the same models. Custom prompts are templates
  with `$document`, `$schema`, `$instructions` (actions also `$inputs`, `$targets`). Placeholders missing
  from a custom prompt are appended, so the model always gets its material.

> **Implemented in:** `schemas/curation.py` (models), `curation.py` (`Curation`: add, patch, drop_overlay,
> delete, generate, patch_run; `Context` semantic checks; `find_cycle`), `prompts/targets.md`,
> `prompts/actions.md`.

## Master-script templates

`templates/<id>/template.toml` (label, language, command, master, files) plus Jinja files that share
`templates/_macros.j2` (header, step headers) and `templates/_README.md.j2`. Templates are discovered
automatically. Phase 1 ships `r`, `python`, `targets` (R targets) and `snakemake`.

The script step resolves the plan: one load step per used data input, then the enabled actions in id order
(moved after their dependencies), then the results step. It writes `results_schema.json` and
`analysis_plan.json` and renders the template. Generated files are registered with their hash. A file whose
current hash differs from the recorded one was edited; `on_modified` decides: `refuse` (409, default), `keep`
(regenerate everything else) or `overwrite`. Switching templates removes the previous template's unedited
files (kept edited ones become `code`).

> **Implemented in:** `scripts.py` (`templates`, `resolve_plan`, `write`), `templates/`, `schemas/exports.py`.

## Schemas and validation

**Frictionless evaluation.** The Frictionless Data Package fits the *package manifest*: one descriptor lists
every file with `bytes` and a `sha256` `hash`, custom properties survive, and `frictionless validate` checks
integrity in one pass. It does not fit the *content* schemas: Table Schema is tabular, while documents,
targets and LLM runs are nested. frictionless-py 5.21 also ignores the Data Package v2 `jsonSchema`
property on JSON resources (we tested this). So:

1. **Pydantic models are the single source of truth** (`schemas/`). `reprocrowd-schemas export` generates
   `schemas/*.schema.json`, `docs/schemas.md` and `docs/openapi.json`, and a test checks all three are current.
2. **`datapackage.json` is a Frictionless Data Package.** It is modelled by the same Pydantic `Manifest`;
   ReproCrowd properties live under `reprocrowd` on the package and on each resource, discriminated by
   `kind`. Each JSON resource names its schema in `jsonSchema`.
3. **`GET /validation`** runs everything in one go, and each issue says which stage found it:
   - `manifest`: the `Manifest` model;
   - `frictionless`: bytes, hashes and the descriptor (catches any file changed outside the API);
   - `jsonschema`: every resource with a `jsonSchema`, against the copy in the package;
   - `model`: the same files against the Pydantic models (cross-field rules);
   - `semantic`: refs resolve, inputs and produced targets exist, no dependency cycles, untracked files.

| Schema | File |
|---|---|
| `datapackage` | `datapackage.json` |
| `document`, `document-draft` | `extractions/*.json`; parser / reconcile-LLM output |
| `targets`, `actions` | `targets.json`, `actions.json` |
| `target-llm-output`, `action-llm-output` | what the extraction prompts must return |
| `results-schema`, `analysis-plan` | `results_schema.json`, `analysis_plan.json` |
| `results-output` | `outputs/results.json` (written by reproduction code; later phases) |

> **Implemented in:** `schemas/__init__.py` (registry), `schemas/common.py`, `schemas/document.py`,
> `schemas/manifest.py`, `schemas/curation.py`, `schemas/exports.py`, `schemas/export.py` (generator),
> `validation.py`; generated `../../schemas/`, `../../docs/schemas.md`, `../../docs/openapi.json`;
> `tests/test_schemas.py`.

## Configuration

`$REPROCROWD_CONFIG` or `~/.config/reprocrowd/config.toml` (see `config.example.toml`): server host and port,
optional bearer token, `allowed_roots`, registry path, OpenAlex key and mailto, download size limit, and
LLM profiles:

```toml
[llm.profiles.claude]
argv = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"]
read_files_argv = ["--allowedTools", "Read"]   # appended when the LLM must read package files (reconcile)
json_schema_argv = ["--json-schema", "{schema}"]  # structured output: the task's output schema
output = "claude-json"                         # read stdout as Claude's JSON envelope
model = "sonnet"
effort = "medium"
timeout_s = 1800
```

> **Implemented in:** `config.py`, `../../config.example.toml`.

## Code generation (jobs)

A headless coding agent implements the master script's `TODO(LLM):` markers. A run takes minutes to hours,
so it is an **asynchronous job resource**, not one long request:

```mermaid
sequenceDiagram
  participant C as client
  participant S as service
  participant G as package git repo
  participant A as agent (profile argv, cwd = package)
  C->>S: POST /v1/packages/{p}/jobs {type: codegen, profile?, allow_exec, instructions}
  S->>G: sync manifest, commit "Before code generation (job)"
  S->>A: spawn in its own process group, prompt on stdin
  S-->>C: 202 Accepted, Location: …/jobs/{j}
  loop while running
    A-->>S: stream-json lines (jobs/{j}/stdout.log)
    C->>S: GET …/jobs/{j}/events (Accept: text/event-stream)
    S-->>C: events: start · text · tool_use · tool_result · result
  end
  opt cancel
    C->>S: DELETE …/jobs/{j} (SIGTERM, then SIGKILL, to the process group)
  end
  S->>G: sync manifest (new files → code/output resources), commit the agent's changes
  S-->>C: event: end, the job {status, exit_code, result: {todo, changed_files, commits}}
```

- **Agent profiles** (`[agent.profiles.*]`) hold the command, model, effort, timeout, the always-allowed tools
  and the `exec_tools` added when the caller sets `allow_exec`. Callers pick a profile by name only.
- **Prompt**: rendered from `templates/_AGENT_PROMPT.md.j2` with the template's own rule (`agent_rule` in
  `template.toml`) and the package's inputs. The script step also writes it to `AGENT_PROMPT.md`, so the same
  instructions can be given to an agent in an editor. The exact prompt of each run is in `jobs/{j}/stdin.txt`.
- **Events**: the agent's stdout is parsed per the profile's `events` format (`claude-stream-json` or
  `text`) into numbered events. `GET …/events` returns them as JSON (`?after=N`) or as Server-Sent Events that
  follow the job live (`Last-Event-ID` resumes) and end with an `end` event carrying the final job.
- **TODO count**: only actionable markers count: `TODO(LLM)` followed by a colon. The header line that
  explains the marker ("Every TODO(LLM) block must be implemented…") is excluded. `GET …/script` lists the
  remaining markers with line numbers; every codegen result includes the count.
- **Edits are tracked, not lost**: generated files record their `generated_hash`. After the agent edits the
  master script, `PUT …/script` refuses to regenerate it unless `on_modified` is `keep` or `overwrite`, while the manifest's `hash` is
  refreshed so integrity validation stays green. Inputs, full texts and extractions are never refreshed: if
  an agent changes them, validation reports it.
- **One job per package** (409 `job_running`); script writes and `:sync` also wait for it. Job records
  (`jobs/<id>.json`, schema `job`) survive restarts. When the service shuts down it ends its running jobs
  (SIGTERM to each process group, SIGKILL after 5 s), so no agent keeps changing a package unobserved; they
  are recorded as `interrupted`, as is a job whose service died without shutting down. `GET /v1/jobs` lists
  the jobs running now, so clients can warn first.

> **Implemented in:** `jobs.py` (job manager: start, watch, timeout, cancel, interrupted), `events.py`
> (stdout → events), `codegen.py` (agent job, git, finish hook), `gitrepo.py`, `files.py` (listing, sync),
> `scripts.py` (`todo_status`, `agent_prompt`, `TODO_RE`), `templates/_AGENT_PROMPT.md.j2`, `schemas/jobs.py`,
> `config.py` (`AgentProfile`), routes and SSE in `api/app.py`, CLI `rc codegen`, `rc job …`.
> Tested with a fake agent (`tests/fake_agent.py`) and a real Claude Code run (Haiku) on Head et al. (2015).

## Run and compare

`POST …/jobs {type: "run"}` runs the master script with the template's command (`bash -c "<command>"`, from
`template.toml`; callers can't supply one) as a job. Output and errors are merged into one log, read as `log`
events (Python is run unbuffered so lines arrive live). Jobs get the service's environment minus
ReproCrowd's own virtualenv, so `python3`/`Rscript` are the user's.

- `outputs/results.json` is removed before the run: a failed run never leaves stale results (the previous
  file stays in git). The package is committed before and after the run.
- After the run, new outputs are registered (kind `output`; `outputs/results.json` with the `results-output`
  schema) and `results.json` is checked against the targets of `results_schema.json` (format errors vs.
  coverage warnings); the job result carries both.
- `GET …/comparison?rounding=reported|N&threshold=P` compares outputs with targets (ported from the plugin):
  numbers rounded to the reported decimals (or N), comparators honoured, deviations up to P % "within
  tolerance"; tables cell by cell (a reproduced table may omit the header row or label column); categorical
  by normalized text; figures "compare visually".

> **Implemented in:** `run.py` (run job, `comparison`), `compare.py` (rules, `check_output`),
> `schemas/comparison.py` (schema `comparison`), `jobs.job_env`, CLI `rc run`, `rc compare`.
> Tested on fixtures and on the agent-written analysis of the scratch package (real run, 25 targets).

## Report, provenance and archive

`PUT …/report {rounding, threshold}` writes `report.html` and `comparison.json` (schema `comparison`). The report
(template `templates/_report.html.j2`, autoescaped) has the summary, the comparison table (tables cell by
cell, original and reproduced figures side by side), the provenance diagram, sources with their origin, the
LLM extractions with profile/model/effort, inputs with hashes, the plan, and every file with its SHA-256.
Images are linked by package-relative path.

**Provenance** is one graph (edges from what was used to what used it), used twice:

```mermaid
flowchart TD
  P["sources: paper identifier · data inputs"] --> F["full texts of the paper"] --> C["converted full texts"]
  F --> X["extractions"]
  C --> X
  X --> Q["passages: referenced sentences, figures, tables"]
  Q -.-> S["steps, one layer per dependency depth"]
  P --> S
  S --> T["targets (final nodes, coloured by status)"]
  Q -.-> T
```

- **Diagram**: top-down, fixed width (layers wrap into rows; no sideways scrolling), nodes ordered by their
  parents' positions; nodes that lead to no target are left out. Clicking a node highlights its whole lineage
  (ancestors and descendants) and dims the rest.
- **Per-target trace**: each row of the comparison table has a "Provenance" section listing the target's
  ancestors: the passages with their text, the steps in order, the data files, extractions, full texts and
  the paper's identifier, readable without JavaScript and in print.

`PUT …/archive` zips `datapackage.json` and every resource it lists into `archive/` (a Frictionless zipped data
package; extracted, it validates on its own). It needs a package that validates and a report; the archive's
size and hashes are recorded in the manifest (`reprocrowd.archive`). `GET …/archive` downloads it.

> **Implemented in:** `report.py` (`write_report`, `write_archive`), `provenance.py` (`build`, `trace`, `svg`,
> `Graph.lineage`), `templates/_report.html.j2`, `schemas/manifest.py` (`ArchiveInfo`), CLI `rc report`,
> `rc archive`. Checked visually on the scratch package (25 targets, 15 actions).

## Original figures

`PUT …/figures/{target}` (or `PUT …/figures` for all figure targets) writes the original of a figure target to
`targets/figures/<target>.png` (kind `figure`), which the report shows next to the reproduced one. `GET …/figures`
says for each whether it is the `published` image, an exact `crop` or a full `page`.

- **Which figure**: one the target references (the figure or a caption sentence); otherwise one its
  description, quote or referenced sentences mention ("Fig 3"); the result says which way it was found.
  The prompt listing for a scope also includes the figures and tables its text mentions, so the extraction
  LLM can cite them.
- **As published, first**: JATS has no coordinates, but its `<graphic>` names the figure's image file, and Europe
  PMC's `/{pmcid}/supplementaryFiles` archive usually holds it (`published.py`, formats' `graphics`). Read from the
  full-text file, like coordinates, so stored extractions don't change. A figure is connected to its `<fig>` by
  caption and label, which must not disagree. Names are compared without extension: `<alternatives>` lists one image
  in several formats, and archives add small GIFs, used only if the full text names one. A `<fig>` with several
  different images (panels) has no single file and goes on to the PDF; images of a `<fig>` nested in it (eLife's
  figure supplements) don't count as its own. The archive is untrusted: only the named image
  is read, nothing is unpacked, and the PNG's resource records its URL in `source`. Any failure (no PMCID, HTTP error,
  no archive, unreadable image) falls through to the PDF and is reported in `note`. Naming a `pdf` skips this step.
- **Where, exactly**: a full text that *locates* the figure (`coordinates.py`, formats' `locate`). Targets
  usually refer to extractions without boxes (JATS, reconciled), so the figure is matched by caption, Docling
  first. Boxes on their own prove little: GROBID tags paragraphs quoting a caption as `<figure>` with `coords`
  on running text, and Docling boxes logos. So a GROBID figure counts only with a `<graphic coords>`, a Docling
  picture only with a caption, and labels aren't used (GROBID labelled body text `"Fig 3 .`).
- **Otherwise the whole page**: found by the caption's text in the PDF, or by the figure's own boxes if it has
  no caption. Nothing located and no single page → `no_coordinates`.
- **Near-identical captions**: all of the caption is looked for first, then its first 120 and 80 letters; a
  probe that matches several places gives up rather than guesses.
- **Which PDF**: the one whose hash the full text records (GROBID `<idno type="MD5">`, Docling
  `origin.binary_hash`, the last 8 bytes of the SHA-256), else the one it was converted from, or named
  explicitly (`pdf`). A full text with neither can't be cropped from (`pdf_unknown` if the caption isn't
  found either).

`GET …/coordinates` reports this per full text: `has_coordinates`, located `figures` and `tables`, `pdf` and
`pdf_match` (`verified`, `provenance`, `mismatch`, `unknown`). This is read from the full-text files rather than
stored in extractions, so packages made earlier need no migration (extractions are validated against the
schema copy in each package). `has_coordinates: false` is conclusive; low counts in a format that has
coordinates only mean it missed items, as GROBID does with most tables.

> **Implemented in:** `coordinates.py`, `figures.py` (`crop`, `crop_all`, `listing`), `formats/tei.py` and
> `formats/docling.py` (`locate`, `made_from`), `extractions.mentions`/`float_number`, PDFium (pypdfium2) for
> rendering; CLI `rc figures`, `rc coordinates`. Rules measured on six PLOS papers (15 figures, 21 tables):
> Docling linked 15 figures and 20 tables, GROBID 14 and 6; every located figure matched an image in the PDF.

## Submission to the package database

Optional (`[database] url`), compatible with the ReproCrowd Database the plugin used. The
service holds no database credentials.

```mermaid
sequenceDiagram
  participant C as client
  participant S as service
  participant O as OpenAlex
  participant D as package database
  participant U as user (browser)
  C->>S: POST …/submissions {note}
  S->>S: checks: DOI · last run succeeded · archive up to date
  S->>O: work record by DOI (title, authors, year, venue)
  S->>D: POST /api/uploads (ZIP, X-Package-Metadata)
  D-->>S: 201 {finish_url, status_url, expires}
  S-->>C: 201 submission {status: pending, finish_url}
  U->>D: sign in, confirm at finish_url
  C->>S: GET …/submissions/{id}
  S->>D: GET status_url
  S-->>C: {status: submitted, package_url}
```

- Bibliographic metadata comes only from OpenAlex, looked up at submission (as in the plugin).
- **With the user's database token** (`database_token`, which a client such as the ReproCrowd Zotero plugin gets
  from the database's browser sign-in, `/auth/client` with PKCE), the upload carries `Authorization: Bearer`
  and the database publishes it at once (`status: submitted`, `package_url`). The service forwards the token
  and never stores it. The database answering 401 becomes 403 `database_token_invalid` (sign in again), a
  suspended account 403 `database_forbidden`.
- The archive must be up to date: its `datapackage.json` must list the same resources with the same hashes
  as the package now (`archive_stale` otherwise).
- Database answers map to problems: 409 `duplicate_submission` (identical contents submitted before, with
  the existing record), 422 `submission_rejected` (with the database's `problems`), 502
  `database_unreachable`. Submissions are recorded in the manifest (`reprocrowd.submissions`).
- For the database's format, `results_schema.json` lists the targets as `results` with `paper.doi`, and the
  archive step writes `inputs.json` (schema `inputs`).
- `PUT …/script {on_modified: keep}` regenerates the exports (e.g. after targets changed) without touching
  files the agent implemented.

> **Implemented in:** `database.py` (`status`, `find`, `submit`, `refresh`), `schemas/manifest.py`
> (`Submission`), `report.write_archive` (`inputs.json`), CLI `rc submit`, `rc submission`. Tested with a mocked
> database, and end to end against the local simulation (upload, confirmation by a signed-in user, status).

## Deposits on Zenodo

The successor of the package database for public submissions (the ReproCrowd app uses only this). Same
preconditions and the same OpenAlex-only bibliographic metadata (`database.Prepared`).

```mermaid
sequenceDiagram
  participant C as client
  participant S as service
  participant Z as Zenodo
  C->>S: POST …/deposits {zenodo_token, creators, exclude?, note}
  S->>S: checks · publication copy of the archive (without excluded groups)
  S->>Z: POST /api/deposit/depositions {metadata, prereserve_doi}
  S->>Z: PUT {bucket}/<stem>-reproduction.zip
  S-->>C: 201 deposit {status: draft, doi (reserved), url}
  C->>S: POST …/deposits/{id}:publish {zenodo_token}
  S->>Z: POST …/actions/publish
  S-->>C: {status: published, doi, url}
```

- **Drafts first.** A published record and its DOI are permanent, so a deposit is a draft unless the request
  says `publish: true`; the user can check it on Zenodo, and publish there (`:refresh` notices) or through the API.
  `:discard` deletes a draft. A failed file upload deletes the new draft again.
- **Token**: the user's personal access token (scopes `deposit:write`, `deposit:actions`), sent with each request
  and never stored; `[zenodo] token` is a fallback for local CLI use. zenodo.org and the sandbox have separate
  accounts and tokens; `sandbox` picks one per request (default `[zenodo] sandbox`).
- **What is uploaded.** A Zenodo record is public, so two groups of files can be left out (`exclude`):
  `paper` (full texts, extractions, original figures; by default left out unless OpenAlex reports a CC or
  public-domain license for the paper) and `inputs` (the authors' data and code; included by default). The
  copy's `datapackage.json` lists only what it contains, and `excluded-files.json` lists the rest with hashes.
- **Metadata**: title "Computational reproduction of: …", creators from the request (or `[zenodo] creators`),
  `publication`/`report`, the configured license (`cc-by-4.0`), keywords incl. `ReproCrowd`, the paper's DOI
  as related identifier with relation `reviews`, a description with the result counts, template and note,
  and `[zenodo] community` if set. `GET /v1/zenodo/records?doi=` finds published records by that DOI and keyword.
- Deposits are recorded in the manifest (`reprocrowd.deposits`); the same archive and exclusions on the same
  server again is `duplicate_submission`.

> **Implemented in:** `zenodo.py`, `schemas/manifest.py` (`ZenodoDeposit`), CLI `rc zenodo …`. Tested with a
> mocked Zenodo (`tests/test_zenodo.py`); not yet against the real sandbox (needs a token).

## Uploads, directories, pagination

- **Uploads**: `POST …/fulltexts` and `POST …/inputs` accept either JSON with a server-local `path` or a
  multipart upload (`file` plus the same fields as form fields), so the service can run on another machine.
  Origins record which (`{type: upload, filename}`). The CLI sends the file with `--upload`.
- **Directories**: `POST …/inputs {path: <directory>}` adds every regular file in it, keeping the folder
  structure under `inputs/<name>/`; hidden files and folders and symlinks are skipped, at most 5000 files.
- **Pagination**: see the conventions table.

> **Implemented in:** `uploads.py`, `fulltexts.add_upload`, `inputs.add_upload`/`add_directory`,
> `api/app.py` (`json_or_upload`, `json_or_multipart` + OpenAPI merge, `Page`), CLI `--upload`.

## Structured output

LLM profiles may constrain the model's output to the task's JSON Schema, for CLIs that support it:
`json_schema_argv` (e.g. `["--json-schema", "{schema}"]`) is appended with `{schema}` = the output schema
(`document-draft` for reconcile, `target-llm-output` / `action-llm-output` for extraction runs), generated
from the same Pydantic models and sent without the `$schema`/`$id` metadata (Claude Code's validator rejects
the 2020-12 meta-schema URI). `output: claude-json` reads Claude's `--output-format json` envelope: its
`structured_output` if present, else the JSON in `result`; an envelope that reports an error is
`llm_call_failed`. Everything after that (validation, `llm_output_invalid` stages, rejected items) is the
same as for text output. The call record notes `output_format` and `output_schema`. The built-in `claude`
profile uses structured output; text profiles keep working for CLIs without it.

> **Implemented in:** `config.LlmProfile` (`json_schema_argv`, `output`), `llm.run`/`cli_schema`/`output_json`.
> Tested with a fake structured CLI and with Claude Code (Haiku): a target extraction (24 accepted, 0
> rejected) and a small reconcile of GROBID TEI + PDF.

## TODO

All planned phases are implemented. Possible next steps:

- Reconcile and extraction runs as jobs (like code generation), for long documents.
- Upload progress for large submissions (the plugin showed a progress bar).
- The package database serves the report from its own URL, so the report's relative links to figure crops
  don't resolve there; embedding crops (data URLs) in the archived report would fix that.
