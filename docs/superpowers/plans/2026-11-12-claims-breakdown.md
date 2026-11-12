# Claims Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ReproCrowd extract a paper's broad claims → sub-claims → evidence + method with an LLM, have the user
check each claim, and reproduce claims one at a time in one package, with per-claim results in the report and the
Zenodo deposit.

**Architecture:**
- A third curated collection, `claims.json`, reuses the existing `Curation` machinery: manual items, LLM runs, and an
  edit overlay. A claim holds its own text, quotes and review state, and *points to* targets (its evidence) and
  actions (its methods). The evidence values and methods themselves stay in `targets.json` and `actions.json`.
- One claims LLM call writes linked runs into all three files.
- A package is in *claims mode* when it has at least one effective claim. In claims mode:
  - the plan and the script contain only the preparation steps plus the ticked claims;
  - the script is split into a generated runner, a shared preparation file and one file per claim.
- Packages without claims behave exactly as today.

**Tech Stack:**
- backend: Python 3.13, FastAPI, Pydantic 2, Jinja2, pytest + respx, run via uv
- app: TypeScript (Electron renderer, vite), Playwright e2e

**Spec:** `docs/superpowers/specs/2026-11-12-claims-breakdown-design.md`. Read it before any task. Tags such as
**[decided]** in it are binding.

## Global Constraints

- **Additive backend.** A package without claims must behave exactly as today. The plugin uses the same backend
  (spec: "keep them additive, so a package without claims behaves exactly as today"). Every existing test in
  `backend/tests/` and `test/e2e.mjs` must still pass, except where a task changes e2e on purpose.
- **Comparison rule unchanged.** Round to the decimals printed, apply the comparator, and allow the package-wide
  deviation threshold (`CompareSettings`). There is no `tolerance` field anywhere.
- **Claim roll-up.** Pool every evidence value of the claim. Sub-claim results are always shown beside it. Example:
  C2 at 14/18 with C2.4 at 0/4 is `partly`.
- **Generated files.** After any Pydantic model or route change, run `cd backend && uv run reprocrowd-schemas export`
  and commit the regenerated `schemas/`, `docs/schemas.md` and `docs/openapi.json`. Otherwise `test_schemas.py` fails.
- **Problem codes.** Every new problem code goes in the catalog in `backend/src/reprocrowd/api/problems.py`;
  `test_api.py` checks this. New codes:
  - `claim_not_checked` (409)
  - `claim_not_testable` (409)
  - `claim_no_evidence` (409)
  - `template_not_claims_ready` (400)
  - `no_claim_run` (409)
- **API conventions.** Follow the conventions in `backend/docs/design.md`: routes under `/v1`, problem+json errors,
  ETag / If-Match, `:verb` suffixes for custom methods.
- **Commands.** Backend tests: `cd backend && uv run pytest -q`. App: `npm run typecheck`, `npm run build`,
  `node test/e2e.mjs`.
- **Claim ids** are `C1`, `C2`, …; sub-claims `C2.1`, … They are never reused within a package. Results, script files
  and deposit rows are keyed by them.

## Review Focus

1. **A claims LLM output with some unresolvable refs** (a sentence id that isn't in the extraction): the good parts
   are kept and the bad items are listed as rejected. One bad evidence value must not drop the whole claim. Test in
   Task 3.
2. **Editing a target or action that belongs to a ticked claim** (not the claim itself) must untick that claim.
   Otherwise unchecked code runs. Test in Task 2.
3. **An unticked claim that ran before** must not be sourced by the runner, and its old result must show as stale, not
   as current. Tests in Tasks 4 and 6.
4. **Re-extracting after a claim has run:** the new run arrives disabled, and the ran claim keeps its id, file and
   result. Test in Task 3.
5. **Depositing again after a published deposit** must create a new Zenodo version, not a second, unrelated record. The
   `duplicate_submission` rule still applies to an identical archive. Test in Task 7.

---

### Task 1: Claims data model and storage

**Files:**
- Modify: `backend/src/reprocrowd/schemas/curation.py`: claim models; extend `T = TypeVar("T", Target, Action, Claim)`
- Modify: `backend/src/reprocrowd/curation.py`
  - `CURATIONS["claims"] = Spec("claims", ClaimsFile, Claim, ClaimFields, ClaimLlmItem, "claims", "C", 1)`
  - claims checks in `Context.item_errors`
  - lazy creation of `claims.json`
- Modify: `backend/src/reprocrowd/package.py:191-192`: `init_package` also writes `claims.json` (`ClaimsFile()`,
  schema `claims`, kind `curation`)
- Modify: `backend/src/reprocrowd/api/app.py:560`: add `"claims"` to the curated-collection route loop
- Test: `backend/tests/test_claims.py` (new)

**Interfaces:**
- Produces, in `schemas/curation.py`:
  - `ClaimMode = Literal["quote", "paraphrase"]`
  - `SubClaim(Referenced)`
    - `id` matching `^C\d+\.\d+$`
    - `text: str` (min length 1)
    - `method: str | None` (an action id)
    - `evidence: list[str]` (target ids)
  - `ClaimFields(Referenced)`
    - `text: str`
    - `mode: ClaimMode = "quote"`
    - `original_quote: str = ""`
    - `testable: bool = True`
    - `method: str | None = None`
    - `evidence: list[str] = []`
    - `sub_claims: list[SubClaim] = []`
    - `depends_on: list[str] = []`
    - `traced_under: list[str] = []`
    - `checked: bool = False`
    - `notes: str = ""`
  - `Claim(ClaimFields)` with `id` matching `^C\d+$`
  - `SkippedSentence(Model)`
    - `ref: Ref`
    - `reason: Literal["background", "study_design", "other"]`
  - `ClaimRunRecord(Model)`
    - `job: str`
    - `at: datetime`
    - `content_hash: str`
  - `ClaimsFile(Model)`
    - `schema_version: "1.0"`
    - `kind: "claims"`
    - `manual: list[Claim]`
    - `llm_runs: list[LlmRun[Claim]]`
    - `skipped: dict[str, list[SkippedSentence]] = {}` (keyed by run id)
    - `ran: dict[str, ClaimRunRecord] = {}` (keyed by claim id)
- Validators on `ClaimFields`:
  - `mode == "paraphrase"` requires a non-empty `original_quote`.
  - If `sub_claims` is non-empty, the claim-level `method` must be None and `evidence` must be empty.
  - `testable == False` requires no `method`, no `evidence` and no `sub_claims`.
- Validator on `Claim`: every sub-claim id starts with `f"{id}."`.
- `Curation(pkg, "claims")`: `load()` returns `ClaimsFile()` when `claims.json` is missing (old packages); `_save`
  creates the resource on first save.
- `Context.item_errors` for claims:
  - every `evidence` id (claim and sub-claim) is an effective target (`include_disabled=True`);
  - every `method` is an effective action;
  - `depends_on` and `traced_under` name existing claims.
  - Error strings follow the existing style, e.g. `"evidence: no target 'T009'"`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_claims.py`. Reuse `package_with_targets` from
  `test_run.py`; it makes targets T001–T004 and action A01.
  - `test_add_atomic_claim`: `POST /v1/packages/{pkg}/claims` with `{"text": "t is 2.31", "refs": REF, "method":
    "A01", "evidence": ["T001"]}`. Expect 201 with id `C1`. `GET …/claims` returns one item with
    `origin.source == "manual"`.
  - `test_claim_rules`. Each of these is a 422 `schema_invalid`:
    - paraphrase without `original_quote`
    - sub-claims plus claim-level evidence
    - `testable: false` with evidence
    - sub-claim id `C9.1` under `C1`
  - `test_claim_refs_resolve`: evidence `["T999"]` → 422 `invalid_reference` whose `errors` contains
    `"evidence: no target 'T999'"`.
  - `test_old_package_without_claims_file`: delete `claims.json` and its manifest resource, then `GET …/claims` → 200 `[]`.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_claims.py -q`. Expected: FAIL (404 on `/claims`).
- [ ] **Step 3: Implement** the models, the spec entry, the `Context` checks, the lazy file and the route loop entry.
  Until Task 3, the loop's `POST …/claims/runs` handler may call `Curation.generate`; Task 3 replaces it.
- [ ] **Step 4: Regenerate** the schemas (`uv run reprocrowd-schemas export`). Run `uv run pytest -q`. Expected: all
  pass.
- [ ] **Step 5: Commit** with the message `claims: data model and storage (claims.json)`.

### Task 2: Ticking and the approval-reset rules

**Files:**
- Modify: `backend/src/reprocrowd/curation.py`: `Curation.patch`
- Create: `backend/src/reprocrowd/claims.py`: `content_hash`, `uncheck_users`
- Test: `backend/tests/test_claims.py`

**Interfaces:**
- Produces, in `claims.py`:
  - `RESET_FIELDS = {"text", "mode", "original_quote", "testable", "method", "evidence", "sub_claims"}`
  - `content_hash(pkg: Package, claim: Claim) -> str`: `"sha256:…"` over the claim's `RESET_FIELDS` *plus* the
    effective field values of every target and action it points to, in a canonical JSON dump. Ignores `checked`,
    `notes` and `refs`. Readable locations don't reset the tick (spec).
  - `uncheck_users(pkg: Package, kind: Literal["targets", "actions"], item_id: str) -> list[str]`: unticks every
    claim whose evidence or method (at claim or sub-claim level) is `item_id`, and returns their ids.
- Behaviour of `Curation(pkg, "claims").patch`: a patch touching any key in `RESET_FIELDS` with a changed value also
  sets `checked = False`. A patch of only `notes` or `checked` keeps the tick.
- `Curation(pkg, "targets" | "actions").patch` and `.drop_overlay` call `uncheck_users` after saving. Call it outside
  the targets lock; claims have their own file.

- [ ] **Step 1: Write the failing tests:**
  - `test_edit_resets_tick`: tick C1 (`PATCH {"checked": true}`), then `PATCH {"text": "changed"}` →
    `checked is False`.
  - `test_notes_keep_tick`: tick C1, then `PATCH {"notes": "n"}` → `checked is True`.
  - `test_target_edit_unticks_claim`: tick C1 (evidence T001), then `PATCH /targets/T001 {"expected": 2.4}` → C1
    `checked is False`. (Review Focus 2)
  - `test_content_hash_ignores_notes`: the hash is unchanged after a notes edit, and changes after an `expected` edit
    on T001.
- [ ] **Step 2: Run** `uv run pytest tests/test_claims.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `content_hash`, `uncheck_users` and the patch hooks.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `claims: tick and reset-on-edit rules`.

### Task 3: Claims extraction (one LLM call → linked runs)

**Files:**
- Create: `backend/src/reprocrowd/prompts/claims.md`. It must contain the literal word `CLAIMS`; the fake LLM keys on
  it.
- Modify: `backend/src/reprocrowd/schemas/curation.py`: LLM output models
- Modify: `backend/src/reprocrowd/claims.py`: `generate`
- Modify: `backend/src/reprocrowd/api/app.py`: `POST …/claims/runs` and `…/claims/runs:preview` dispatch to
  `claims.generate` / `claims.render_prompt`
- Modify: `backend/tests/fake_llm.py`: a `CLAIMS` branch
- Test: `backend/tests/test_claims.py`

**Interfaces:**
- Produces, in `schemas/curation.py` (all `_LlmItem`):
  - `MethodLlmItem`
    - `key` matching `^M\d+$`
    - `summary: str`
    - `quote: str = ""`
    - `refs: list[str]` (min 1)
  - `PrepLlmItem`: like `MethodLlmItem`, with `key` matching `^P\d+$`
  - `SubClaimLlmItem`
    - `text: str`
    - `refs: list[str]` (min 1)
    - `method: str | None` (a method key)
    - `evidence: list[TargetLlmItem]`
  - `ClaimLlmItem`
    - `text: str`
    - `mode: ClaimMode = "quote"`
    - `original_quote: str = ""`
    - `refs: list[str]` (min 1)
    - `testable: bool = True`
    - `method: str | None = None`
    - `evidence: list[TargetLlmItem] = []`
    - `sub_claims: list[SubClaimLlmItem] = []`
    - `depends_on: list[int] = []`, `traced_under: list[int] = []`: 1-based positions in `claims`
  - `SkippedLlmItem`
    - `ref: str`
    - `reason: Literal["background", "study_design", "other"]`
  - `ClaimLlmOutput`
    - `claims: list[ClaimLlmItem]`
    - `methods: list[MethodLlmItem] = []`
    - `preparation: list[PrepLlmItem] = []`
    - `skipped: list[SkippedLlmItem] = []`
  - JSON Schema name: `claim-llm-output`.
- Produces, in `claims.py`:
  - `render_prompt(pkg, extraction: str, scope: Scope, *, instructions: str, prompt: str | None) -> str`. Its values are
    `document` (via `extractions.listing`), `schema` and `instructions`.
  - `generate(pkg, extraction: str, scope: Scope, *, instructions: str, prompt: str | None, profile: str | None) ->
    LlmRun[Claim]`.
- Algorithm of `generate`. Everything after the LLM call happens under `pkg.locked()`, writing all three files:
  1. **Run the call** with `llm.run(pkg, "claims", …, schema="claim-llm-output")`. Parse the output, raising
     `llm.output_invalid` on bad JSON or the wrong top-level shape, as `Curation.generate` does.
  2. **Preparation steps.** Each `preparation` item becomes an action entry with `kind: "transform"`. Each `methods`
     item becomes an action entry with `kind: "analysis"`, `depends_on` = every preparation action created in this
     call, and `produces` = the target ids of all evidence that names this method key. A method shared by two
     sub-claims is created **once**.
  3. **Evidence.** Each evidence item becomes a target entry, normalised with `_normalize_target`. An item that fails
     validation goes into that run's `rejected` and is left out of its claim.
  4. **Claims.** Each claim becomes a claim entry with:
     - id from `_next_ids` and sub-claim ids `f"{cid}.{n}"`;
     - `method` / `evidence` rewritten to the new action / target ids;
     - `depends_on` / `traced_under` positions mapped to the new claim ids.
     A claim whose refs fail is rejected as a whole.
  5. **Append the runs.** Append a targets `LlmRun` and an actions `LlmRun`, each with
     `request = {"via": "claims", "claims_run": <claims run id>, "extraction": …}`, then the claims `LlmRun`. Store
     `skipped[run_id]`.
  6. **A second claims run.** If an enabled claims run already exists, the new claims run and its targets and actions
     runs are created with `disabled=True`. (spec: "A new run's claims arrive switched off")
- Fake LLM: on `CLAIMS` in the prompt, print one claims output against `tests/fixtures/paper.tei.xml` ids:
  - one atomic claim
  - one claim with two sub-claims sharing method `M1`
  - one `testable: false` claim with `depends_on: [1, 2]`
  - one evidence item citing `sec9.p1.s1` (unresolvable)
  - one preparation step

- [ ] **Step 1: Write the failing tests:**
  - `test_extract_claims`: after `POST …/claims/runs {"extraction": "ex-paper-tei"}` (201):
    - three claims C1–C3;
    - C2 has sub-claims `C2.1`, `C2.2` with the **same** `method` id;
    - C3 has `testable is False` and `depends_on == ["C1", "C2"]`;
    - `actions.json` has one `transform` action, and every analysis action `depends_on` it;
    - `GET …/claims?view=raw` has `skipped["run1"]`.
  - `test_extract_keeps_good_parts`: the targets run's `rejected` has one item, its errors mention `sec9.p1.s1`, and
    its claim still exists with its other evidence. (Review Focus 1)
  - `test_reextract_arrives_disabled`: extract twice. The second claims, targets and actions runs all have
    `disabled is True`. C1–C3 keep their ids and fields. (Review Focus 4)
  - `test_claims_prompt_preview`: `POST …/claims/runs:preview` returns a prompt containing `CLAIMS` and the
    extraction's sentence ids.
- [ ] **Step 2: Run** `uv run pytest tests/test_claims.py -q`. Expected: FAIL.
- [ ] **Step 3: Write `prompts/claims.md`.** It covers the user's rules from the spec:
  - broad claims mostly from the abstract, as quotes or paraphrases with the original quote;
  - sub-claims as verbatim quotes, unless the claim is small enough to keep whole;
  - for each claim, the methods and evidence (the same evidence guidelines as `prompts/targets.md`);
  - preparation steps from the Methods section;
  - each result passage assigned to one claim (`traced_under` for the others);
  - recommendations marked not testable;
  - skipped abstract sentences with reasons;
  - `$instructions`, `$schema`, `$document`, as in the existing prompts.
- [ ] **Step 4: Implement** the models, `render_prompt`, `generate`, the route dispatch and the fake LLM branch.
- [ ] **Step 5: Regenerate the schemas.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 6: Commit** with the message `claims: LLM extraction into claims, targets and actions`.

### Task 4: Claims-mode plan and per-claim script files

**Files:**
- Modify: `backend/src/reprocrowd/claims.py`: `ClaimScope`, `scope`
- Modify: `backend/src/reprocrowd/scripts.py`
  - `resolve_plan` filters by `claims.claim_scope`
  - `write` gets a claims branch
  - `Template` gains `claims: dict | None = None`
- Modify: `backend/src/reprocrowd/schemas/manifest.py:78`: `ScriptMeta` gains `claim: str | None = None` and
  `claim_hash: str | None = None`
- Create the templates:
  - `backend/src/reprocrowd/templates/r/claims_runner.R.j2`, `claims_prepare.R.j2`, `claims_claim.R.j2`
  - `backend/src/reprocrowd/templates/python/claims_runner.py.j2`, `claims_prepare.py.j2`, `claims_claim.py.j2`
- Modify: `templates/r/template.toml`, `templates/python/template.toml`: a `[claims]` table
- Modify: `backend/src/reprocrowd/api/problems.py`: `template_not_claims_ready`
- Test: `backend/tests/test_claims_script.py` (new)

**Interfaces:**
- Produces, in `claims.py`:
  - `@dataclass(frozen=True) ClaimScope`
    - `claims: list[Claim]`: ticked, testable, with at least 1 evidence
    - `targets: set[str]`
    - `actions: set[str]`: their methods, plus every effective `transform` action
    - `preparation: list[str]`: the transform action ids
  - `claim_scope(pkg) -> ClaimScope | None`: None when the package has no effective claim, which is the old behaviour.
  - `claim_files(pkg, claim_id) -> list[str]`
- `template.toml` `[claims]` table, e.g. R:
  ```toml
  [claims]
  runner  = { src = "claims_runner.R.j2",  dest = "analysis.R" }
  prepare = { src = "claims_prepare.R.j2", dest = "prepare.R" }
  claim   = { src = "claims_claim.R.j2",   dest = "claims/{id}.R" }
  agent_rule = "Implement prepare.R and the claims/<id>.R file named below; never edit analysis.R or other claims' files."
  ```
  Python: the same, with `.py`.
- Behaviour of `scripts.write` in claims mode:
  - **Unsupported template:** any template without `[claims]` (snakemake, targets) → 400 `template_not_claims_ready`.
  - **Runner** (the master; the agent never edits it): always regenerated. It
    - loads the data and sources the prepare file;
    - for each claim in `claim_scope(pkg).claims`, in id order, sources its file inside an error handler (R `tryCatch`, Python
      `try/except`);
    - on an error in a claim's file, emits every target of that claim with `value: null` and a
      `note: "claim C2 failed: <message>"`;
    - writes `outputs/results.json` in the existing results format.

    Claim files call a runner-provided `record(id, value, step)` (Python) / `record(id, value, step)` (R).
  - **Prepare file:** written if missing, kept if it exists. It holds the preparation steps' headers and markers.
  - **Each ticked claim's file:** written if missing, or if its `ScriptMeta.claim_hash` differs from
    `claims.content_hash`. Written means overwritten: re-running an edited claim rewrites only its file, a spec rule.
    Otherwise kept. Files of unticked claims stay on disk and are not sourced.
  - **The other outputs:** `results_schema.json` = the `claim_scope` targets; `analysis_plan.json` = `resolve_plan`
    (preparation + scope actions).
  - **`on_modified`:** ignored in claims mode.
- Test helper: the `fake_agent.py` behaviour is unchanged. Tests write claim files directly.

- [ ] **Step 1: Write the failing tests** in `test_claims_script.py`, on a package from Task 3 with C1 ticked:
  - `test_claims_mode_files`: `PUT …/script {"template": "python"}` → 201. The files are `analysis.py`, `prepare.py`,
    `claims/C1.py`, and there is no `claims/C2.py`. `analysis_plan.json` steps include the transform action and C1's
    method, and not C2's.
  - `test_snakemake_refused_in_claims_mode` → 400 `template_not_claims_ready`.
  - `test_claim_file_kept_unless_claim_changed`: edit `claims/C1.py` by hand and `PUT` again → kept. Edit C1's text,
    re-tick and `PUT` → overwritten (the TODO marker is back).
  - `test_unticked_claim_not_sourced`: tick C2 and `PUT` (claims/C2.py exists), untick C2 and `PUT` →
    `claims/C2.py` is still on disk and `analysis.py` doesn't reference `C2`. (Review Focus 3)
  - `test_claim_error_isolated`: write `claims/C1.py` raising, run the runner with `python3 analysis.py` → results.json
    has C1's targets with `value: null` and a note starting `claim C1 failed`.
  - `test_no_claims_unchanged`: on `package_with_targets` (no claims), `PUT …/script {"template": "python"}` gives the
    same file list as before this change (`analysis.py`, README.md, AGENT_PROMPT.md, the two exports).
- [ ] **Step 2: Run** `uv run pytest tests/test_claims_script.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `claim_scope`, the `resolve_plan` filter, the templates and the `write` branch.
- [ ] **Step 4: Regenerate the schemas.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `claims: plan and per-claim script files`.

### Task 5: Codegen scoped to one claim

**Files:**
- Modify: `backend/src/reprocrowd/api/models.py:279`: `CodegenJobCreate` gains `claim: str | None = None`
- Modify: `backend/src/reprocrowd/codegen.py`: `plan` / `start` take `claim: str | None`
- Modify: `backend/src/reprocrowd/scripts.py:188`: `agent_prompt(pkg, tpl, instructions="", claim: str | None = None)`
- Modify: `backend/src/reprocrowd/templates/_AGENT_PROMPT.md.j2`
- Modify: `backend/src/reprocrowd/api/problems.py`: `claim_not_checked`, `claim_not_testable`, `claim_no_evidence`
- Test: `backend/tests/test_claims_script.py`

**Interfaces:**
- `POST …/jobs {"type": "codegen", "claim": "C1"}`:
  - the job's `request` includes `"claim": "C1"`;
  - preconditions in this order: the claim exists (404), `testable` (409 `claim_not_testable`), at least 1 evidence
    (409 `claim_no_evidence`), `checked` (409 `claim_not_checked`).
- The agent prompt with `claim` adds a section with:
  - the claim's text and its sub-claims;
  - the files to implement: `prepare.*` only if it still has TODO markers, plus `claims/<id>.*`;
  - the rule "Do not modify analysis.* or any other claims/ file";
  - a reminder of rule 7 (`# ASSUMPTION:`) for anything the paper does not describe. (spec: "the agent works it out
    from the study's data and code and **flags the guess**")
- `claim` without claims mode → 409 `claim_not_checked`, since the package has no ticked claims.

- [ ] **Step 1: Write the failing tests:**
  - `test_codegen_preview_for_claim`: `POST …/jobs:preview {"type": "codegen", "claim": "C1"}` → the prompt contains
    `claims/C1.py`, `Do not modify analysis.py` and `ASSUMPTION`, and does not contain `claims/C2.py`.
  - `test_codegen_claim_preconditions`: unticked C2 → 409 `claim_not_checked`; C3 → 409 `claim_not_testable`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** them.
- [ ] **Step 4: Regenerate the schemas.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `claims: codegen scoped to one claim`.

### Task 6: Per-claim results, statuses and run records

**Files:**
- Create: `backend/src/reprocrowd/schemas/claims.py`: result and status models
- Modify: `backend/src/reprocrowd/claims.py`: `results`, `statuses`, `record_run`
- Modify: `backend/src/reprocrowd/run.py:46`: `finish` calls `claims.record_run(pkg, job.id)` when the run produced a
  results file
- Modify: `backend/src/reprocrowd/api/app.py`: `GET /packages/{pkg}/claims:status`
- Test: `backend/tests/test_claims_results.py` (new)

**Interfaces:**
- Produces, in `schemas/claims.py`:
  - `Verdict = Literal["reproduced", "partly", "not_reproduced"]`
  - `ClaimState = Literal["review", "ready", "running", "reproduced", "partly", "not_reproduced", "not_testable"]`
  - `EvidenceResult(Model)`
    - `target: str`
    - `status: Status`
    - `expected_text: str`
    - `reproduced_text: str`
  - `SubClaimResult(Model)`
    - `id: str`
    - `verdict: Verdict`
    - `matched: int`
    - `tested: int`
    - `evidence: list[EvidenceResult]`
  - `ClaimResult(Model)`
    - `claim: str`
    - `verdict: Verdict`
    - `matched: int`
    - `tested: int`
    - `by_sub_claim: list[SubClaimResult]`
    - `evidence: list[EvidenceResult]` (an atomic claim's own evidence)
  - `ClaimStatus(Model)`
    - `claim: str`
    - `state: ClaimState`
    - `stale: bool`
    - `ran_at: datetime | None`
    - `result: ClaimResult | None`
- Produces, in `claims.py`:
  - `verdict(statuses: list[Status]) -> tuple[Verdict, int, int]`:
    - **tested** = rows with status in `{"match", "within", "deviation"}`; `missing`, `unparsable` and `figure` count
      as not computed.
    - **matched** = rows with status `match` or `within`.
    - **verdict:** `tested == 0` or `matched == 0` → `not_reproduced`; `matched == tested` → `reproduced`; otherwise
      `partly`.
  - `results(pkg, cmp: Comparison) -> list[ClaimResult]`: the claim verdict pools **all** evidence rows of the claim and
    its sub-claims. [decided]
  - `record_run(pkg, job_id: str) -> None`: for every claim in `claim_scope(pkg).claims`, sets
    `ran[cid] = ClaimRunRecord(job=job_id, at=now(), content_hash=content_hash(pkg, claim))`.
  - `statuses(pkg) -> list[ClaimStatus]`. State is decided in this order:
    1. not testable → `not_testable`
    2. an active job whose `request.claim == cid` → `running`
    3. `ran[cid]` exists and its hash matches → the verdict, with `stale=False`
    4. `ran[cid]` exists and its hash differs → `ready` if ticked, else `review`, with `stale=True` and the old result
       kept in `result`
    5. ticked → `ready`
    6. otherwise → `review`

    Results come from `run.comparison(pkg, CompareSettings())` when a results file exists.

- [ ] **Step 1: Write the failing tests:**
  - `test_verdict_rule`:
    - `verdict(["match"]*14 + ["deviation"]*4) == ("partly", 14, 18)`
    - `verdict(["missing", "missing"]) == ("not_reproduced", 0, 0)`
    - `verdict(["match", "within"]) == ("reproduced", 2, 2)`
  - `test_pooled_with_failed_sub_claim`: C2 with sub-claim C2.1 fully matching and C2.2 fully deviating → claim
    `partly`, and `by_sub_claim` = `[reproduced, not_reproduced]`.
  - `test_status_lifecycle`. On C1:
    1. → `review`
    2. tick → `ready`
    3. run job (`package_with_targets`-style fake results file, `POST …/jobs {"type": "run"}`) → `reproduced` with
       `stale False`
    4. edit C1's text → `review` with `stale True` and the old `result` kept (Review Focus 3)
  - `test_not_testable_status`: C3 → `not_testable`, `result None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_claims_results.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** them.
- [ ] **Step 4: Regenerate the schemas.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `claims: per-claim results and statuses`.

### Task 7: Claims trace, report section and deposit (with Zenodo versions)

**Files:**
- Modify: `backend/src/reprocrowd/claims.py`: `trace`
- Modify: `backend/src/reprocrowd/schemas/claims.py`: `ClaimTrace`, `ClaimsTrace`
- Modify: `backend/src/reprocrowd/report.py:38`: in claims mode, write `claims_trace.json` (resource
  `claims-trace`, schema `claims-trace`, meta kind `report`) and pass `claims=` to the template
- Modify: `backend/src/reprocrowd/templates/_report.html.j2`: a "Claims" section before the target table: per claim its status, matched/tested, sub-claim results and assumptions
- Modify: `backend/src/reprocrowd/zenodo.py`: `metadata` claims table; `create` precondition and new-version flow
- Modify: `backend/src/reprocrowd/schemas/manifest.py:153`: `ZenodoDeposit` gains `version_of: str | None = None` and
  `claims: list[str] = []`
- Modify: `backend/src/reprocrowd/api/app.py`: `GET /packages/{pkg}/claims:trace`
- Modify: `backend/src/reprocrowd/api/problems.py`: `no_claim_run`
- Modify: `backend/docs/design.md`: a "Claims" section (model, rules, endpoints, problem codes), in the style of the
  existing sections
- Test: `backend/tests/test_claims_deposit.py` (new)

**Interfaces:**
- `ClaimTrace`: the user's format plus the spec's additions. Fields:
  - `id` (the paper DOI), `title`, `claim_id`
  - `main_claim`, `mode`, `original_quote`
  - `testable`, `depends_on`, `traced_under`
  - `sub_claims: [{id, claim, quantitative_evidence: [{id, label, value, comparator, unit, decimals, source}]}]`
  - `quantitative_evidence` (atomic claims)
  - `methods: [{id, summary, quote}]`
  - `assumptions: list[str]`
  - `status: ClaimState`, `result: ClaimResult | None`

  There is **no `tolerance`**. `label` = the target's description. `source` = a readable location from the target's
  first ref. `assumptions` = the `# ASSUMPTION:` comment lines found in `claims/<id>.*` and `prepare.*`, stripped. This
  is how flagged guesses reach the user and the report.
- `ClaimsTrace`
  - `paper: {doi, title}`
  - `claims: list[ClaimTrace]`
  - `skipped: list[SkippedSentence]` (the enabled claims run's)
- **Zenodo `metadata`** in claims mode: after the Result paragraph, an HTML table with one row per claim: id, the claim
  (truncated to 200 chars), and status as words (`Reproduced 14/18`, `Partly reproduced 14/18`, `Not run`,
  `Not testable`). Packages without claims keep today's description.
- **Zenodo `create`** in claims mode:
  - no claim with a `ran` record → 409 `no_claim_run`;
  - `deposit.claims` = the ids with a `ran` record;
  - if the package already has a **published** deposit on the same server, call
    `_new_version(url, tok, prev.deposition_id) -> dict`:
    1. `POST {url}/api/deposit/depositions/{id}/actions/newversion`
    2. `GET` its `links.latest_draft`
    3. `DELETE` each file the draft inherited
    4. upload the new ZIP to its bucket
    5. `PUT` the metadata

    The new deposit then records `version_of = prev.id`.
  - The existing `duplicate_submission` check, for the identical archive, runs first and is unchanged.

- [ ] **Step 1: Write the failing tests**, using respx as in `test_zenodo.py`:
  - `test_deposit_needs_a_claim_run`: claims mode with no run → 409 `no_claim_run`.
  - `test_deposit_lists_claims`: after C1 ran:
    - the description contains `C1` and `Reproduced`, and the untested C2 shows `Not run`, C3 `Not testable`;
    - `deposit.claims == ["C1"]`;
    - the archive contains `claims_trace.json`, whose C1 entry has `result.verdict`.
  - `test_redeposit_is_new_version`: publish the first deposit, run C2, `PUT …/archive`, deposit again → respx saw
    `POST …/depositions/<id>/actions/newversion`, and the new deposit has `version_of == <first id>`.
    (Review Focus 5)
  - `test_trace_has_no_tolerance_and_has_assumptions`: write `# ASSUMPTION: age split at 3 years` into
    `claims/C1.py`. `GET …/claims:trace` has `assumptions == ["age split at 3 years"]`, and no key `tolerance` anywhere
    in the JSON.
  - `test_report_claims_section`: `PUT …/report` → the HTML contains `Claims`, C1's verdict and its assumption text
    (`age split at 3 years`). The
    `test_report_and_archive` output for a package without claims is unchanged.
- [ ] **Step 2: Run** `uv run pytest tests/test_claims_deposit.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** them. Update `backend/docs/design.md`.
- [ ] **Step 4: Regenerate the schemas.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `claims: trace, report section and versioned Zenodo deposits`.

### Task 8: Claims review screen (the app's step 2)

**Files:**
- Modify: `src/renderer/types.ts`: `Claim`, `SubClaim`, `ClaimStatus`, `ClaimResult`, `ClaimsTrace`, mirroring the
  Pydantic models
- Create: `src/renderer/repro/claims.ts`: `class ClaimsStep implements Step`
- Modify: `src/renderer/repro/wizard.ts:58`: replace `new CurationStep(this, "targets")` with `new ClaimsStep(this)`
- Modify: `src/renderer/style.css`: claims list/detail styles, using the existing tokens
- Test: `npm run typecheck`; e2e in Task 9

**Interfaces:**
- `ClaimsStep`
  - `title = "Claims"`
  - `load()` fetches in parallel:
    - `pkg.get("/claims", {view: "raw"})`
    - `pkg.get("/claims:status")`
    - `pkg.get("/targets")`
    - `pkg.get("/actions")`
    - the JATS document
- Layout, from the spec's "The review screen":
  - **Left:** the claim tree, with mode, "Atomic" or "N sub-claims", and a status pill per claim. The skipped-sentences
    count goes at the foot. An **Extract claims** button (`POST /claims/runs`, with a profile choice like
    `CurationStep`) shows when there is no claim, and in the toolbar otherwise.
  - **Right:**
    1. evidence values (editable: `PATCH /targets/{id}`)
    2. quotes, with the original quote under a paraphrase (`PATCH /claims/{id}`)
    3. each sub-claim with its method summary (`PATCH /actions/{id}`) and evidence
    4. notes
    5. the tick (`PATCH {checked}`)
    6. **Run this claim** (Task 9)
    7. the last result by sub-claim and value, from `ClaimStatus.result`, labelled stale when `stale`
    8. assumptions, from `GET /claims:trace`
  - The original quote stays visible under a paraphrase.
  - A **Reader/Table/JSON** switch: Reader reuses `AlignedView` with one card per claim (anchors = the claim's and
    sub-claims' refs). JSON shows `GET /claims:trace` for the selected claim, with a Copy button.
  - A **Targets table** link opens the existing `CurationStep(this.w, "targets")` view in place, for packages made
    before claims.
- A second, disabled claims run shows as a banner, "New extraction: N claims, switched off", with per-claim switches
  (`PATCH /claims/{id} {disabled}`) and **Use this run** (`PATCH /claims/runs/{run} {disabled: false}`).

- [ ] **Step 1: Implement** the types, `ClaimsStep` and the wizard swap.
- [ ] **Step 2: Run** `npm run typecheck`. Expected: no errors.
- [ ] **Step 3: Run** `npm start` against a package of the noma paper (doi 10.1371/journal.pgph.0001995), extract
  claims with the default profile, and check against the mockup claude.ai/artifact/NQZGyfKDghZ9e7Yj42suJX:
  - list, then detail, then edit;
  - an edit unticks the claim;
  - notes don't.
- [ ] **Step 4: Commit** with the message `app: claims review screen replaces the Targets step`.

### Task 9: Run one claim, deposit gating, e2e

**Files:**
- Create: `src/renderer/repro/claimrun.ts`
- Modify: `src/renderer/repro/claims.ts`: wire **Run this claim**
- Modify: `src/renderer/repro/report.ts`: claims summary and deposit gating
- Modify: `test/e2e.mjs`: the Claims step instead of Targets
- Modify: `backend/tests/fake_agent.py` only if e2e needs claim files written. It writes into whatever files the prompt
  names.

**Interfaces:**
- `runClaim(w: Wizard, claimId: string, template: string, onStage: (s: "script" | "codegen" | "run" | "report") =>
  void): Promise<void>` runs these in sequence. It stops at the first error and shows it with `showError`:
  1. `PUT /script {template}`, using the template already chosen, or else `"r"`
  2. `POST /jobs {type: "codegen", claim}`, then wait for the job, as `CodegenStep` does
  3. `POST /jobs {type: "run"}`, then wait
  4. `PUT /report`

  It refreshes `claims:status` after each stage. Closing the window mid-way leaves the backend state consistent; the
  claim then shows its real state on reopening.
- `report.ts`:
  - The deposit button is disabled until `claims:status` has a claim with `ran_at`, with the hint "Run at least one
    claim first."
  - The deposit preview lists the claims with their results.
  - If a published deposit exists, the button reads "Deposit new version".
  - Packages without claims keep today's gating.

- [ ] **Step 1: Update `test/e2e.mjs`.** After the package is created:
  1. go to step 2, **Claims**;
  2. click **Extract claims** (the fake LLM `ok` profile);
  3. expect 3 claims;
  4. tick C1, then **Run this claim**;
  5. expect C1's pill to read "Reproduced" or "Partly reproduced";
  6. go to step 7 and deposit to the fake Zenodo;
  7. assert the deposit description contains `C1`.

  Keep the existing assertions for steps 3–7.
- [ ] **Step 2: Run** `npm run build && node test/e2e.mjs`. Expected: FAIL at the Claims step.
- [ ] **Step 3: Implement** `claimrun.ts`, the run button and the `report.ts` changes.
- [ ] **Step 4: Run** `npm run typecheck && npm run build && node test/e2e.mjs`. Expected: PASS, with screenshots in
  `test-output/`. Also run `npm run test:backend`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `app: run one claim end to end; deposit after a claim has run`.

### After the last task (not automated)

- Offer the backend changes to reprocrowd-cli:
  `git subtree push --prefix=backend ../reprocrowd-cli app-sync`. This writes to another repository, so ask the user
  first.
