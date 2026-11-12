# Schemas

Generated from the Pydantic models in `src/reprocrowd/schemas/` by `reprocrowd-schemas export`; do not edit.
The JSON Schemas are in `schemas/`; every package gets a copy in its own `schemas/` folder.

Validation beyond what JSON Schema expresses (references resolve, ids unique, no dependency cycles) is
done by `GET /packages/{p}/validation` (src/reprocrowd/validation.py).

## `datapackage`: datapackage.json

File: `schemas/datapackage.schema.json`

### Root

datapackage.json

| Field | Type | Req. | Description |
|---|---|---|---|
| `name` | string | yes | pattern `^[a-z0-9][a-z0-9._-]*$` |
| `title` | string \| null |  |  |
| `resources` | list of [Resource](#resource) |  |  |
| `reprocrowd` | [PackageMeta](#packagemeta) | yes |  |

### ArchiveInfo

The last ZIP of the package (PUT …/archive).

| Field | Type | Req. | Description |
|---|---|---|---|
| `path` | string | yes | package-relative, under archive/ (not itself a resource) |
| `bytes` | integer | yes |  |
| `sha256` | string | yes |  |
| `created_at` | string (date-time) | yes |  |
| `manifest_sha256` | string | yes | hash of the datapackage.json inside the archive |
| `files` | integer | yes |  |

### CheckResult

| Field | Type | Req. | Description |
|---|---|---|---|
| `passed` | boolean | yes |  |
| `checked_at` | string (date-time) | yes |  |
| `notes` | list of string |  | non-fatal observations (e.g. page count) |

### ConvertedOrigin

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"conversion"` |  | default `conversion` |
| `source` | string | yes | resource name of the PDF it was converted from |
| `converter` | string | yes | converter name (grobid, docling) |
| `detail` | string |  | e.g. the GROBID URL or the docling command; default `` |

### ExtractionMeta

| Field | Type | Req. | Description |
|---|---|---|---|
| `kind` | `"extraction"` |  | default `extraction` |
| `added_at` | string (date-time) | yes |  |
| `method` | `"parse"` \| `"reconcile"` | yes |  |
| `fulltexts` | list of string | yes | full-text resources it was made from |
| `llm_call` | string \| null |  |  |

### FulltextMeta

| Field | Type | Req. | Description |
|---|---|---|---|
| `kind` | `"fulltext"` |  | default `fulltext` |
| `added_at` | string (date-time) | yes |  |
| `flavor` | `"pdf"` \| `"tei"` \| `"jats"` \| `"docling"` | yes |  |
| `origin` | [LocalOrigin](#localorigin) \| [UrlOrigin](#urlorigin) \| [ConvertedOrigin](#convertedorigin) \| [UploadOrigin](#uploadorigin) | yes |  |
| `check` | [CheckResult](#checkresult) | yes |  |

### Identifier

A work identifier that OpenAlex can resolve.

| Field | Type | Req. | Description |
|---|---|---|---|
| `scheme` | `"doi"` \| `"openalex"` \| `"pmid"` \| `"pmcid"` | yes |  |
| `value` | string | yes | normalized: DOI lower-case without resolver prefix, OpenAlex id like W123, PMID digits, PMCID like PMC123 |

### InputMeta

| Field | Type | Req. | Description |
|---|---|---|---|
| `kind` | `"input"` |  | default `input` |
| `added_at` | string (date-time) | yes |  |
| `role` | `"data"` \| `"code"` \| `"other"` | yes |  |
| `note` | string |  | default `` |
| `used` | boolean |  | false: kept on disk but not part of the reproduction; default `True` |
| `archive` | string \| null |  | resource name of the zip archive this file was unpacked from |
| `origin` | [LocalOrigin](#localorigin) \| [UploadOrigin](#uploadorigin) | yes |  |

### LocalOrigin

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"local"` |  | default `local` |
| `path` | string | yes | absolute path the file was copied from |

### OtherMeta

metadata: OpenAlex record · curation: targets/actions · export: files derived for the agent · schema: JSON Schema
copies · code: files created by the agent or the user · output: files under outputs/ (written by runs) ·
report: report.html and comparison.json · figure: originals of figure targets (targets/figures/).

| Field | Type | Req. | Description |
|---|---|---|---|
| `kind` | `"metadata"` \| `"curation"` \| `"export"` \| `"schema"` \| `"code"` \| `"output"` \| `"report"` \| `"figure"` | yes |  |
| `added_at` | string (date-time) | yes |  |
| `method` | `"published"` \| `"crop"` \| `"page"` \| null |  | figure: the image as published, an exact crop, or the whole page the figure is on |
| `page` | integer \| null |  | figure: page of the PDF |
| `source` | string \| null |  | figure: where a published image was downloaded from |

### PackageMeta

| Field | Type | Req. | Description |
|---|---|---|---|
| `version` | `"1.0"` |  | default `1.0` |
| `created_at` | string (date-time) | yes |  |
| `identifier` | [Identifier](#identifier) \| null |  | identifier the package was created for |
| `identifiers` | map of string |  | all identifiers known from OpenAlex (doi, openalex, pmid, pmcid) |
| `metadata_fetched_at` | string (date-time) \| null |  |  |
| `archive` | [ArchiveInfo](#archiveinfo) \| null |  |  |
| `submissions` | list of [Submission](#submission) |  |  |
| `deposits` | list of [ZenodoDeposit](#zenododeposit) |  | Zenodo deposits |

### Resource

One file of the package (Frictionless Data Resource).

| Field | Type | Req. | Description |
|---|---|---|---|
| `name` | string | yes | unique id, e.g. ft-grobid, ex-grobid, in-data-csv; pattern `^[a-z0-9][a-z0-9._-]*$` |
| `path` | string | yes | package-relative, /-separated |
| `type` | `"file"` \| `"json"` | yes | "json" for JSON files (frictionless parses them), "file" otherwise |
| `format` | string | yes |  |
| `mediatype` | string | yes |  |
| `bytes` | integer | yes |  |
| `hash` | string | yes | pattern `^sha256:[0-9a-f]{64}$` |
| `jsonSchema` | string \| null |  | package-relative path of the JSON Schema the file must satisfy |
| `reprocrowd` | [FulltextMeta](#fulltextmeta) \| [ExtractionMeta](#extractionmeta) \| [InputMeta](#inputmeta) \| [ScriptMeta](#scriptmeta) \| [OtherMeta](#othermeta) | yes |  |

### ScriptMeta

A file written from a master-script template.

| Field | Type | Req. | Description |
|---|---|---|---|
| `kind` | `"script"` |  | default `script` |
| `added_at` | string (date-time) | yes |  |
| `template` | string | yes | template id |
| `generated_hash` | string | yes | hash as generated; a different current hash means the file was edited (e.g. by the agent); pattern `^sha256:[0-9a-f]{64}$` |

### Submission

An upload of the archive to the package database.

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^sub-[0-9a-f]+$` |
| `database` | string | yes | database URL at the time |
| `upload_id` | string | yes |  |
| `finish_url` | string | yes | where the user signs in and confirms the submission |
| `status_url` | string | yes |  |
| `expires` | string | yes | unconfirmed uploads expire |
| `status` | `"pending"` \| `"submitted"` \| `"deleted"` \| `"gone"` | yes | gone: expired or discarded without confirmation |
| `submitted_at` | string (date-time) | yes |  |
| `checked_at` | string (date-time) \| null |  |  |
| `archive_sha256` | string | yes |  |
| `note` | string |  | default `` |
| `package_url` | string \| null |  | the package's page once confirmed |

### UploadOrigin

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"upload"` |  | default `upload` |
| `filename` | string | yes | file name the client sent (multipart upload) |

### UrlOrigin

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"url"` |  | default `url` |
| `url` | string | yes | download URL (secrets such as API keys removed) |
| `candidate` | string | yes | candidate id it was chosen as |
| `label` | string | yes | where the candidate came from, e.g. "OpenAlex best OA location" |

### ZenodoCreator

| Field | Type | Req. | Description |
|---|---|---|---|
| `name` | string | yes | "Family, Given" |
| `orcid` | string \| null |  |  |
| `affiliation` | string \| null |  |  |

### ZenodoDeposit

A deposit of the package on Zenodo: a draft until it is published (then it has its DOI for good).

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^zen-[0-9a-f]+$` |
| `server` | string | yes | Zenodo base URL at the time |
| `sandbox` | boolean | yes | deposited to Zenodo's test instance |
| `deposition_id` | integer | yes |  |
| `status` | `"draft"` \| `"published"` \| `"discarded"` \| `"gone"` | yes | gone: deleted on Zenodo (only drafts can be) |
| `title` | string | yes |  |
| `doi` | string \| null |  | reserved while a draft, registered once published |
| `url` | string | yes | the draft's edit page, or the published record |
| `file` | string | yes | name of the uploaded ZIP |
| `sha256` | string | yes | hash of the uploaded ZIP |
| `archive_sha256` | string | yes | hash of the package archive it was made from |
| `exclude` | list of `"paper"` \| `"inputs"` |  | groups of files left out of the upload |
| `excluded` | list of string |  | the resources that were left out |
| `community` | string \| null |  |  |
| `creators` | list of [ZenodoCreator](#zenodocreator) | yes |  |
| `note` | string |  | default `` |
| `created_at` | string (date-time) | yes |  |
| `published_at` | string (date-time) \| null |  |  |
| `checked_at` | string (date-time) \| null |  |  |

## `document`: extractions/<id>.json

File: `schemas/document.schema.json`

### Root

A paper's text as sections > paragraphs > sentences, plus figures and tables.

| Field | Type | Req. | Description |
|---|---|---|---|
| `schema_version` | `"1.0"` |  | default `1.0` |
| `title` | string \| null |  |  |
| `source` | [DocumentSource](#documentsource) | yes |  |
| `sections` | list of [Section](#section) | yes |  |
| `figures` | list of [Figure](#figure) |  |  |
| `tables` | list of [Table](#table) |  |  |

### Box

A rectangle on a PDF page.

| Field | Type | Req. | Description |
|---|---|---|---|
| `page` | integer | yes | 1-based page number |
| `x0` | number | yes |  |
| `y0` | number | yes |  |
| `x1` | number | yes |  |
| `y1` | number | yes |  |
| `origin` | `"top"` \| `"bottom"` | yes | "top": y measured from the page top (GROBID); "bottom": PDF native |

### DocumentSource

| Field | Type | Req. | Description |
|---|---|---|---|
| `method` | `"parse"` \| `"reconcile"` | yes |  |
| `format` | string \| null |  | source format for parse (tei, jats, docling); null for reconcile |
| `fulltexts` | list of string | yes | resource names of the full texts this was made from |
| `llm_call` | string \| null |  | id of the LLM call (reconcile only) |

### Figure

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^fig\d+$` |
| `label` | string | yes |  |
| `section` | string \| null |  | id of the section the figure appears in |
| `caption` | list of [Sentence](#sentence) | yes |  |
| `boxes` | list of [Box](#box) \| null |  |  |

### Paragraph

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^sec\d+\.p\d+$` |
| `sentences` | list of [Sentence](#sentence) | yes |  |
| `boxes` | list of [Box](#box) \| null |  |  |

### Section

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^sec\d+$` |
| `title` | string | yes |  |
| `level` | integer | yes |  |
| `paragraphs` | list of [Paragraph](#paragraph) | yes |  |

### Sentence

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^(sec\d+\.p\d+\|fig\d+\|tab\d+)\.s\d+$` |
| `text` | string | yes |  |
| `boxes` | list of [Box](#box) \| null |  |  |

### Table

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^tab\d+$` |
| `label` | string | yes |  |
| `section` | string \| null |  | id of the section the table appears in |
| `caption` | list of [Sentence](#sentence) | yes |  |
| `rows` | list of list of string | yes |  |
| `boxes` | list of [Box](#box) \| null |  |  |

## `document-draft`: parser / reconcile-LLM output, before ids are assigned

File: `schemas/document-draft.schema.json`

### Root

Document content without ids, in reading order (output of parsers and of the reconcile LLM).

| Field | Type | Req. | Description |
|---|---|---|---|
| `title` | string \| null |  |  |
| `sections` | list of [DraftSection](#draftsection) | yes |  |
| `figures` | list of [DraftFigure](#draftfigure) |  | figures not tied to a section |
| `tables` | list of [DraftTable](#drafttable) |  | tables not tied to a section |

### DraftFigure

| Field | Type | Req. | Description |
|---|---|---|---|
| `label` | string | yes | e.g. "Figure 2" |
| `caption` | list of [DraftSentence](#draftsentence) |  |  |
| `boxes` | list of [Box](#box) \| null |  |  |

### DraftParagraph

| Field | Type | Req. | Description |
|---|---|---|---|
| `sentences` | list of [DraftSentence](#draftsentence) | yes | sentences in order; a plain string is shorthand for {"text": ...} |
| `boxes` | list of [Box](#box) \| null |  |  |

### DraftSection

| Field | Type | Req. | Description |
|---|---|---|---|
| `title` | string | yes | section heading; "" if the source has none |
| `level` | integer |  | 1 = top level, 2 = subsection, …; default `1` |
| `paragraphs` | list of [DraftParagraph](#draftparagraph) |  |  |
| `figures` | list of [DraftFigure](#draftfigure) |  | figures that appear in this section |
| `tables` | list of [DraftTable](#drafttable) |  | tables that appear in this section |

### DraftSentence

| Field | Type | Req. | Description |
|---|---|---|---|
| `text` | string | yes |  |
| `boxes` | list of [Box](#box) \| null |  |  |

### DraftTable

| Field | Type | Req. | Description |
|---|---|---|---|
| `label` | string | yes | e.g. "Table 1" |
| `caption` | list of [DraftSentence](#draftsentence) |  |  |
| `boxes` | list of [Box](#box) \| null |  |  |
| `rows` | list of list of string |  | cell texts, header row(s) included |

## `targets`: targets.json

File: `schemas/targets.schema.json`

### Root

targets.json

| Field | Type | Req. | Description |
|---|---|---|---|
| `schema_version` | `"1.0"` |  | default `1.0` |
| `kind` | `"targets"` |  | default `targets` |
| `manual` | list of [Target](#target) |  |  |
| `llm_runs` | list of [LlmRun_Target_](#llmrun_target_) |  |  |

### LlmCall

Everything needed to understand (and repeat) one LLM call.

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | also the folder llm-calls/<id>/ holding prompt.txt, stdout.txt, stderr.txt |
| `task` | `"reconcile"` \| `"targets"` \| `"actions"` | yes |  |
| `profile` | string | yes | name of the LLM profile in the service config |
| `argv` | list of string | yes | the exact command line that was run (prompt goes to stdin) |
| `model` | string | yes | model passed to the CLI; "" = the CLI default |
| `effort` | string | yes | effort level passed to the CLI; "" = the CLI default |
| `template` | `"default"` \| `"custom"` | yes | whether the built-in prompt template or a caller-supplied one was used |
| `instructions` | string |  | extra instructions appended to the template; default `` |
| `prompt` | string | yes | the full prompt sent |
| `output` | string | yes | raw stdout of the call |
| `output_format` | `"text"` \| `"claude-json"` |  | how stdout was read; default `text` |
| `output_schema` | string \| null |  | name of the JSON Schema the CLI was asked to constrain its output to (structured output) |
| `started_at` | string (date-time) | yes |  |
| `duration_ms` | integer | yes |  |
| `exit_code` | integer \| null |  |  |

### LlmEntry_Target_

| Field | Type | Req. | Description |
|---|---|---|---|
| `base` | [Target](#target) | yes | the item as extracted from the LLM output (never changed) |
| `edit` | object \| null |  | user changes: fields of the item that override base |
| `disabled` | boolean |  | default `False` |

### LlmRun_Target_

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^run\d+$` |
| `disabled` | boolean |  | true: none of the run's entries are effective; default `False` |
| `request` | object | yes | what was asked: extraction, scope, … |
| `call` | [LlmCall](#llmcall) | yes |  |
| `entries` | list of [LlmEntry_Target_](#llmentry_target_) | yes |  |
| `rejected` | list of [Rejected](#rejected) |  | items of the output that failed validation |

### Ref

Points at one sentence, figure or table of an extraction.

| Field | Type | Req. | Description |
|---|---|---|---|
| `extraction` | string | yes | resource name of the extraction, e.g. ex-grobid |
| `id` | string | yes | sentence (sec3.p2.s1, fig1.s1), figure (fig1) or table (tab1) id in that extraction |

### Rejected

| Field | Type | Req. | Description |
|---|---|---|---|
| `item` | any | yes | the LLM's item as returned |
| `errors` | list of string | yes |  |

### Target

| Field | Type | Req. | Description |
|---|---|---|---|
| `refs` | list of [Ref](#ref) |  |  |
| `unreferenced` | boolean |  | set to allow an empty refs list; then note is required; default `False` |
| `note` | string \| null |  |  |
| `type` | `"number"` \| `"categorical"` \| `"table"` \| `"figure"` | yes |  |
| `description` | string | yes | self-contained: variable, model/test, subgroup |
| `quote` | string |  | verbatim text from the paper that states the result; default `` |
| `expected` | number \| integer \| string \| list of list of string \| null |  | number for number, string for categorical, rows of cell strings for table, null for figure |
| `comparator` | `"="` \| `"<"` \| `"<="` \| `">"` \| `">="` \| `"~"` \| null |  | for numbers: how the paper states it ("p < .001" -> "<"); null means "=" |
| `decimals` | integer \| null |  | decimals as printed |
| `unit` | string \| null |  |  |
| `id` | string | yes | pattern `^T\d{3,}$` |

## `actions`: actions.json

File: `schemas/actions.schema.json`

### Root

actions.json

| Field | Type | Req. | Description |
|---|---|---|---|
| `schema_version` | `"1.0"` |  | default `1.0` |
| `kind` | `"actions"` |  | default `actions` |
| `manual` | list of [Action](#action) |  |  |
| `llm_runs` | list of [LlmRun_Action_](#llmrun_action_) |  |  |

### Action

| Field | Type | Req. | Description |
|---|---|---|---|
| `refs` | list of [Ref](#ref) |  |  |
| `unreferenced` | boolean |  | set to allow an empty refs list; then note is required; default `False` |
| `note` | string \| null |  |  |
| `kind` | `"transform"` \| `"analysis"` | yes |  |
| `summary` | string | yes | imperative description, specific enough to code |
| `quote` | string |  | verbatim text from the paper this action implements; default `` |
| `inputs` | list of string |  | input resource names (in-…) used directly |
| `depends_on` | list of string |  | ids of actions that must run first |
| `produces` | list of string |  | ids of targets this action computes |
| `id` | string | yes | pattern `^A\d{2,}$` |

### LlmEntry_Action_

| Field | Type | Req. | Description |
|---|---|---|---|
| `base` | [Action](#action) | yes | the item as extracted from the LLM output (never changed) |
| `edit` | object \| null |  | user changes: fields of the item that override base |
| `disabled` | boolean |  | default `False` |

### LlmRun_Action_

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^run\d+$` |
| `disabled` | boolean |  | true: none of the run's entries are effective; default `False` |
| `request` | object | yes | what was asked: extraction, scope, … |
| `call` | [LlmCall](#llmcall) | yes |  |
| `entries` | list of [LlmEntry_Action_](#llmentry_action_) | yes |  |
| `rejected` | list of [Rejected](#rejected) |  | items of the output that failed validation |

## `target-llm-output`: output of the target-extraction prompt

File: `schemas/target-llm-output.schema.json`

### Root

Output format of the target-extraction prompt.

| Field | Type | Req. | Description |
|---|---|---|---|
| `targets` | list of [TargetLlmItem](#targetllmitem) | yes |  |

### TargetLlmItem

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"number"` \| `"categorical"` \| `"table"` \| `"figure"` | yes |  |
| `description` | string | yes |  |
| `quote` | string |  | default `` |
| `expected` | number \| integer \| string \| list of list of string \| null |  |  |
| `comparator` | `"="` \| `"<"` \| `"<="` \| `">"` \| `">="` \| `"~"` \| null |  |  |
| `decimals` | integer \| null |  |  |
| `unit` | string \| null |  |  |
| `refs` | list of string | yes | ids in brackets the result comes from |

## `action-llm-output`: output of the action-extraction prompt

File: `schemas/action-llm-output.schema.json`

### Root

Output format of the action-extraction prompt.

| Field | Type | Req. | Description |
|---|---|---|---|
| `actions` | list of [ActionLlmItem](#actionllmitem) | yes |  |

### ActionLlmItem

| Field | Type | Req. | Description |
|---|---|---|---|
| `key` | string | yes | A1, A2, … in order; used by depends_on |
| `kind` | `"transform"` \| `"analysis"` | yes |  |
| `summary` | string | yes |  |
| `quote` | string |  | default `` |
| `refs` | list of string | yes |  |
| `inputs` | list of string |  | input names (in-…) used directly |
| `depends_on` | list of string |  | keys of earlier actions |
| `produces` | list of string |  | target ids computed by this action |

## `results-schema`: results_schema.json

File: `schemas/results-schema.schema.json`

### Root

results_schema.json: the enabled targets at the time the master script was created. The list is called
"results", as in the package database's format.

| Field | Type | Req. | Description |
|---|---|---|---|
| `schema_version` | `"1.0"` |  | default `1.0` |
| `paper` | [Paper](#paper) | yes |  |
| `results` | list of [Target](#target) | yes | the targets |

### Paper

| Field | Type | Req. | Description |
|---|---|---|---|
| `doi` | string \| null |  | the paper's DOI, if known (the package database requires it) |
| `title` | string \| null |  |  |
| `identifier` | [Identifier](#identifier) \| null |  |  |
| `identifiers` | map of string |  |  |

## `analysis-plan`: analysis_plan.json

File: `schemas/analysis-plan.schema.json`

### Root

analysis_plan.json: load steps (one per used data input), the enabled actions, the results step.

| Field | Type | Req. | Description |
|---|---|---|---|
| `schema_version` | `"1.0"` |  | default `1.0` |
| `steps` | list of [PlanStep](#planstep) | yes |  |

### PlanStep

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | L01… load steps, A… actions, OUT the results step |
| `kind` | `"load"` \| `"transform"` \| `"analysis"` \| `"results"` | yes |  |
| `summary` | string | yes |  |
| `quote` | string |  | default `` |
| `refs` | list of [Ref](#ref) |  |  |
| `inputs` | list of string |  | package-relative paths of input files |
| `depends_on` | list of string |  |  |
| `produces` | list of string |  | target ids |

## `inputs`: inputs.json (written with the archive)

File: `schemas/inputs.schema.json`

### Root

inputs.json: the input files (also in datapackage.json), in the package database's format.

| Field | Type | Req. | Description |
|---|---|---|---|
| `inputs` | list of [InputEntry](#inputentry) | yes |  |

### InputEntry

| Field | Type | Req. | Description |
|---|---|---|---|
| `path` | string | yes |  |
| `role` | string | yes |  |
| `note` | string |  | default `` |
| `used` | boolean |  | default `True` |
| `archive` | string \| null |  | path of the zip archive it was unpacked from |

## `results-output`: outputs/results.json

File: `schemas/results-output.schema.json`

### Root

outputs/results.json, written by the reproduction code.

| Field | Type | Req. | Description |
|---|---|---|---|
| `results` | list of [OutputResult](#outputresult) | yes |  |

### OutputResult

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | target id |
| `value` | number \| integer \| string \| null |  | number/categorical targets; unrounded |
| `table` | list of list of string \| number \| integer \| null \| null |  | table targets; same layout as expected |
| `file` | string \| null |  | figure targets: package-relative image path |
| `step` | string \| null |  | plan step that produced it |
| `note` | string \| null |  |  |

## `job`: jobs/<id>.json

File: `schemas/job.schema.json`

### Root

jobs/<id>.json

| Field | Type | Req. | Description |
|---|---|---|---|
| `id` | string | yes | pattern `^job-[0-9a-z-]+$` |
| `type` | `"codegen"` \| `"run"` | yes |  |
| `status` | `"running"` \| `"succeeded"` \| `"failed"` \| `"cancelled"` \| `"timeout"` \| `"interrupted"` | yes |  |
| `profile` | string \| null |  | agent profile (codegen) |
| `argv` | list of string | yes | the command that runs (configured, never caller-supplied) |
| `request` | object |  |  |
| `created_at` | string (date-time) | yes |  |
| `finished_at` | string (date-time) \| null |  |  |
| `duration_ms` | integer \| null |  |  |
| `exit_code` | integer \| null |  |  |
| `stdout` | string | yes | package-relative path of the raw stdout |
| `stderr` | string | yes |  |
| `events_format` | `"claude-stream-json"` \| `"text"` | yes |  |
| `result` | [CodegenResult](#codegenresult) \| [RunResult](#runresult) \| null |  |  |
| `error` | string \| null |  |  |

### CodegenResult

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"codegen"` |  | default `codegen` |
| `commit_before` | string \| null |  | package git commit before the agent ran |
| `commit_after` | string \| null |  | commit of the agent's changes (null: nothing changed) |
| `changed_files` | list of string |  |  |
| `todo` | [TodoStatus](#todostatus) | yes |  |
| `git_note` | string \| null |  | why git was skipped, if it was |

### RunResult

| Field | Type | Req. | Description |
|---|---|---|---|
| `type` | `"run"` |  | default `run` |
| `command` | string | yes |  |
| `log` | string | yes | package-relative path of the run log |
| `results_file` | boolean | yes | outputs/results.json exists after the run |
| `output_errors` | list of string |  | outputs/results.json problems (format, unknown ids) |
| `output_warnings` | list of string |  | coverage problems (targets missing from the output) |
| `commit_after` | string \| null |  |  |

### TodoFile

| Field | Type | Req. | Description |
|---|---|---|---|
| `path` | string | yes |  |
| `remaining` | integer | yes |  |
| `lines` | list of [TodoLine](#todoline) | yes |  |

### TodoLine

| Field | Type | Req. | Description |
|---|---|---|---|
| `line` | integer | yes |  |
| `text` | string | yes |  |

### TodoStatus

Actionable TODO(LLM): markers left in the template's files. The header line that explains the marker
("Every TODO(LLM) block must be implemented…") is not counted: only the marker followed by a colon is.

| Field | Type | Req. | Description |
|---|---|---|---|
| `remaining` | integer | yes |  |
| `files` | list of [TodoFile](#todofile) | yes |  |

## `comparison`: comparison.json (written with the report; also GET …/comparison)

File: `schemas/comparison.schema.json`

### Root

GET …/comparison, and comparison.json written with the report.

| Field | Type | Req. | Description |
|---|---|---|---|
| `settings` | [CompareSettings](#comparesettings) | yes |  |
| `summary` | map of integer | yes | number of targets per status |
| `rows` | list of [CompareRow](#comparerow) | yes |  |
| `output_errors` | list of string | yes | outputs/results.json does not have the required format |
| `output_warnings` | list of string | yes | targets missing from the output, unknown ids |

### Cell

| Field | Type | Req. | Description |
|---|---|---|---|
| `expected` | string | yes |  |
| `reproduced` | string | yes |  |
| `status` | `"match"` \| `"within"` \| `"deviation"` \| `"missing"` \| `"figure"` \| `"unparsable"` | yes |  |
| `deviation` | number \| null |  | % deviation of a numeric cell |

### CompareRow

| Field | Type | Req. | Description |
|---|---|---|---|
| `target` | string | yes |  |
| `type` | `"number"` \| `"categorical"` \| `"table"` \| `"figure"` | yes |  |
| `description` | string | yes |  |
| `status` | `"match"` \| `"within"` \| `"deviation"` \| `"missing"` \| `"figure"` \| `"unparsable"` | yes |  |
| `expected_text` | string | yes |  |
| `reproduced_text` | string | yes |  |
| `deviation` | number \| null |  | % deviation (numbers) |
| `cells` | list of list of [Cell](#cell) \| null |  | table targets: cell by cell |
| `matched` | integer \| null |  |  |
| `total` | integer \| null |  |  |
| `note` | string \| null |  | the reproduction's note on this result |

### CompareSettings

| Field | Type | Req. | Description |
|---|---|---|---|
| `rounding` | `"reported"` \| integer |  | "reported": round to the decimals printed in the paper; or a number of decimals (0–10); default `reported` |
| `threshold` | number |  | deviation in % up to which a numeric result counts as within tolerance; default `5` |
