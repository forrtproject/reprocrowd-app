# Opus review of the comparison/evidence and provider specs

> Superseded in part (15 November 2026): Decisions to review 1, the `Decimal(float)` guidance under Likely integration pitfalls and the 2.675 acceptance test describe policy `reprocrowd-compare/2`. Lukas chose `reprocrowd-compare/3` instead: reproduced numbers are read as written and an exact tie matches either neighbour. See `docs/contracts.md`. This file is the record of the agreed review and is otherwise unchanged.

Reviewed 14 November 2026 against branch `issue15-82-contracts` at commit `7aa9555`. The review covers `docs/specs/comparison-evidence.md` and `docs/specs/provider-interface.md`, checked against the backend, CLI, renderer and Electron main source. Unless a claim says otherwise, it was confirmed by reading the code or by running it in `/tmp/scratch-reprocrowd-review-20261113-venv`.

## Verdict

| Spec | Status | Condition |
|---|---|---|
| comparison-evidence.md | **Blocking objections (B1–B7).** It agrees once they are applied. | The numerics, the deposit-consistency check and the evidence contract are underspecified, and three statements conflict with the source. All fixes are bounded and listed below. |
| provider-interface.md | **Blocking objections (P1–P6).** It agrees once they are applied. | An explicit capability flag would break or overclaim existing profiles. Model routing, errors at exit 0 and the prompt/permission mismatch need exact rules. |

Neither spec is overbroad in intent. They become overbroad where they ask for new infrastructure that the code already partly provides: per-attempt job records, preview routes, and the absence of any config-write route. The corrections below reuse those pieces.

## Needs your decision

There is none that blocks implementation. The two methodological points below ship with behaviour-preserving defaults and are listed under Decisions to review.

## Decisions to review

1. **Rounding at exact ties.** The reproduced double is rounded with `Decimal(float)`, which is its exact binary value, using ROUND_HALF_EVEN. This keeps today's Python `round` results; only the quantum is now correct. For example, 2.675 is stored as 2.67499…, so it rounds to 2.67 and stays "within" against a printed 2.68. The alternative is to treat the shortest decimal repr as exact and round half-up, the APA/SPSS habit, which would make that case a match. That alternative is a policy change, so it is not adopted. The mode is recorded in the policy so the choice stays visible.
2. **The `~` comparator.** Today `~` returns `match` when the deviation is within the tolerance threshold (verified: expected `~10.0`, reproduced 10.4 gives match). This mixes tolerance into exact counts, which breaks the rule that exact and exploratory results stay apart. New rule: `~` gives `match` only when the values agree at reported precision, otherwise `within` when inside the threshold. The counts of existing packages change only after their report is regenerated.
3. **Author code under recreation.** The app excludes it explicitly instead of refusing to run. Acquire assigns `role=code` with `used=true` (`inputs.guess_role`), so a refusal rule would block every acquired package. New code-generation prompts never list or recommend author code. Each job records which code files were still readable on disk.
4. **The Settings form drops TOML comments.** `smol-toml` parse/stringify does not keep comments in `backend.toml`. The form backs up the file once, the raw editor stays, and the UI says comments are not preserved.

## Comparison and evidence spec: blocking objections

### B1. Printed numbers: exact representation rules (replaces §2, first two paragraphs)

Verified failures in `compare.py`:

| Input | Current result | Correct result |
|---|---|---|
| Expected `1.2e-5`, reproduced `9e-5` (number target) | `match` | deviation |
| The same as a table cell | `match` | deviation |
| `decimals_of(1e-05)` | `0` | quantum `1e-5` |
| `decimals_of(1.2e-5)` | `1` | quantum `1e-6` |
| Table cell `≤ .05`, reproduced 0.03 | `deviation` | match: only `<`/`>` prefixes are read, so `≤`/`≥` cells are treated as `=` |
| Expected 0.17 (printed `.170`, no `decimals`), reproduced 0.1749 | `match` | depends on the lost trailing zero, which cannot be recovered |

The cause of the `.170` case: an LLM that returns `expected` as a JSON number has already lost the trailing zero before `_normalize_target` sees it.

**The rules:**

- Add `printed: str | None` to `TargetFields`. It holds the number token only: optional sign, digits, decimal point and exponent. It may contain no comparator and no unit.
- On input from the LLM, the UI, the CLI or the API, `normalize_number(s, comparator)` does the following:
  - It strips thousands separators, `%`, footnote marks and unicode minus.
  - It splits a leading `< <= > >= ≤ ≥ ~ ≈ =` into `comparator`. When the input also carries a different explicit comparator, it fails with 422 `target_contradiction`.
  - It derives `expected = float(Decimal(token))`.
- **The quantum** is the exponent of `Decimal(token)`. That gives `.170` → -3, `1.20e-5` → -7 and `1.2e3` → +2.
- **`decimals`** is set by the server to `-exp` when exp ≤ 0, and to `None` when exp > 0, because `decimals` is ge=0 and cannot hold a positive exponent. Legacy readers keep working this way.
- **Rejected combinations** (422 `target_contradiction`):
  - an explicit `expected` with `float(Decimal(printed)) != expected`;
  - an explicit `decimals` different from the derived value;
  - `printed` on a target that is not a number.
- **The server is the only authority.** The renderer and the CLI send the user's string unchanged and never pass it through `Number()`. This applies to `curation.ts:94` and `:433`, which currently call `Number(raw)`.
- **Extraction.** The prompt `prompts/targets.md` asks for `expected` as a string exactly as printed. `TargetLlmItem.expected` already accepts strings, and `_normalize_target` becomes a call to `normalize_number`. No new field is added to the LLM output.
- **Legacy targets:**
  - Targets with `decimals` keep that quantum.
  - Targets with neither field are compared as today and flagged `precision: "inferred"` in the row.
  - For float input, the quantum is inferred from `repr(expected)`, which is a weak inference, and this is said in the row.
  - No legacy file is rewritten.
- **Where normalization runs.** It runs in a `mode="before"` validator on `TargetFields`, so the current `_expected_matches_type` check never sees a raw string for a number target. That validator also applies to `LlmEntry.edit` merges in `Curation.effective()`: an edited `expected` string replaces `printed` and re-derives `decimals`. In the table view, the `decimals` input (`curation.ts:75`) becomes derived and read-only whenever `printed` is set. Otherwise every edit to it would hit `target_contradiction`.
- **Commas.** Only the existing thousands rule `(\d),(\d{3})\b` is accepted. Any other comma is rejected as unparsable. The current `_normalize_target` uses a blanket `.replace(",", "")`, which turns "1,5" into 15.
- **One module.** `reprocrowd/numbers.py` holds `parse_printed`, `quantum`, `round_to` and `compare_num`. It is pure and has no I/O. `compare.py`, curation and the CLI import it, so there is a single parser for target fields and table cells.

### B2. The comparison algorithm (replaces §2, third paragraph)

- The reproduced value is a Decimal: `Decimal(value)` when it is a float (its exact binary value, see Decisions to review 1), and `Decimal(token)` when it is a JSON string. **The expected value is never `Decimal(float)`.** It is `Decimal(printed)`, or `Decimal(repr(expected))` for legacy targets. (`Decimal(0.17)` is 0.17000000000000001…, which never equals a quantized 0.17.) Table cells use `Decimal(cell token)`. Rounding uses `quantize(Decimal(1).scaleb(q), ROUND_HALF_EVEN)`.
- The comparison table:

  | Comparator | Match when | Otherwise |
  |---|---|---|
  | `=` | rounded value == printed value | `within` if deviation ≤ threshold, else `deviation` |
  | `<` / `>` | the strict comparison holds on the unrounded value, as today | `deviation` (a strict bound is never `within`) |
  | `<=` / `>=` | the comparison holds on the rounded value, as today, without an epsilon | `within` / `deviation` as for `=` |
  | `~` | the rounded value is equal | `within` if deviation ≤ threshold, else `deviation` (see Decisions to review 2) |

- **Fixed-decimals mode** (`rounding = n`) still overrides the quantum, which is existing behaviour. Its row text says "rounded to n decimals (setting)".
- **Table cells** use the same function with the cell's own comparator, which fixes the `≤`/`≥` cells. `Cell` gains `printed` (the cell string, as today), `comparator` and `quantum`.
- **Statuses** are unchanged: `match/within/deviation/missing/figure/unparsable`. `deviation` remains a float percentage, for display only.
- **The policy record.** `Comparison` gains `policy: Literal["reprocrowd-compare/2"]`, together with the existing `settings`. The policy names the rounding mode, `half_even_exact_binary`.

### B3. One recorded comparison for report, archive and deposit (replaces §2 "UI, API, CLI…" and §3 last paragraph)

Today the report counts match plus within as "reproduced" (`_report.html.j2:25`). The Zenodo description counts only `match` and uses `CompareSettings()` defaults (`database.py:111`, `zenodo.py:240-248`). The two disagree whenever the user changed the tolerance.

The contract:

- **`Comparison` gains a `basis` block:** `results_sha256`, `results_schema_sha256`, `created_at` and `policy`. `GET …/comparison` stays a live, exploratory view controlled by query parameters.
- **`comparison.json`, written by `PUT …/report`,** is the recorded comparison. The report, the archive, the Zenodo preview and deposit, and `database.submit` all read this file.
- **The staleness check** is one function, `recorded_comparison(pkg)`. It loads `comparison.json` and fails with 409 `report_stale` if either basis hash differs from the current file. A legacy `comparison.json` has no basis; the function then recomputes with the stored `settings` and requires equal `rows`/`summary`, otherwise `report_stale`.
- **Where the check runs:**
  - `write_archive` calls it before zipping. This closes the gap where a rerun after the report produces an archive that pairs new results with an old comparison.
  - `Prepared` calls it instead of `comparison(pkg, CompareSettings())`.
- **Wording.** Every surface reports `match` and `within` separately, with the threshold and the policy. Example: "12 of 20 match at reported precision; 3 more within 5 % (exploratory)". This applies to the report headline, the Zenodo description, `rc compare` and the renderer's run/report steps.
- **Archived records are never rewritten.**

### B4. Execution evidence: reuse the job records and do not add a parallel store (replaces §3, first two paragraphs)

What the code already does:

- `jobs/<id>.json` already preserves every attempt, with its argv, timestamps, exit code and status; a timeout is already the `timeout` status.
- A model cannot change the status. Only the process exit code and the watcher set it.
- However, `jobs/` is in `files.SKIP_TOP`. No job record or log reaches the archive today, so any `log path` inside an archived evidence file would not resolve.

The contract:

- **`RunResult` gains, additively:**
  - `commit_before`. The value already exists in `run.start` and is not stored today.
  - `snapshot`: a map from path to `sha256:` for `results_schema.json`, `analysis_plan.json`, every `script`/`code` resource, and every used input. It is taken after `files.sync` at start.
  - `results_sha256` and `log_sha256`, taken at finish.
- **`GET /v1/packages/{pkg}/evidence`** and **`rc evidence PKG [--json]`** compute `ExecutionEvidence` live:
  - `schema_version`: `"1"`.
  - `track`: see B6.
  - `run`: the latest `run` job, with its id, status, exit code, times, command and template.
  - `stale`: the snapshot entries that were changed or removed since the start of the run. Files added since then are listed separately under `added_since_run` and do not make the run stale, because a run may create files of its own, such as R's `Rplots.pdf`, which `files.sync` registers as `code`. `stale` is `null` with `stale_reason: "no snapshot (older run)"` for legacy runs.
  - `results_match_run`: a boolean.
  - `codegen`: the latest codegen job's profile, model, `allow_exec`, `exec_restriction` and `code_context`.
  - `targets[]`: see B5.
- **`PUT …/report`** additionally writes `evidence/execution.json`, which is registered as kind `report` with a JSON Schema, and `evidence/run.log`, a copy of the latest run's stdout, which is registered so that it resolves inside the archive. The log copy is capped at 5 MB and keeps the tail when it is longer, with `log_truncated: true` and the sha256 of the full log.
- **Labels.** Evidence states the attempt's status, `stale` and whether `results.json` is the file the run produced. It never states "verified", "correct" or "reproduced". A succeeded run means only that the process exited with code 0.

### B5. Step claims are unchecked (replaces §3 "For each target…")

`provenance.build` (`provenance.py:150`) uses an output's `step` whenever that step exists in the plan, even if the step does not list the target in `produces`. It falls back to the plan silently.

The contract:

- `check_output` adds a **warning**, not an error, so that old packages remain valid: `results[i] (T003): step A02 does not produce T003 in the plan`. Unknown steps get the same warning.
- Per target, the evidence's `step_evidence` is one of `declared_matches_plan`, `declared_conflicts_plan`, `planned_only` or `none`.
- The level of evidence is stated as `process` for every target: one log per run, and no per-value trace.
- `CompareRow` gains `refs: list[Ref]` from the frozen target. The report adds `id="target-T001"` anchors and links each passage to its extraction id; the text is already resolved by `provenance.trace`.

### B6. The track field and author code (replaces §1)

The README says author code is not used in this version (`README.md` "the authors' code is not used"). `_AGENT_PROMPT.md.j2` contradicts it: it lists `role=code` inputs as "The original authors' code (prefer reusing their logic…)". The agent also has Read/Glob/Grep in the package folder.

The contract:

- **`PackageMeta.track: Literal["recreation"] | None = None`.** The default is `None`. A `"recreation"` default would silently relabel every legacy manifest on its next save. Readers display `None` as "unspecified (older package)".
- **`PackageCreate.track: Literal["recreation", "original_code"] = "recreation"`.** `original_code` returns 422 `track_unavailable` ("deferred, see #45") before anything is written. No Track A job type exists, so nothing else needs a guard. `adopt=true` leaves an existing manifest's track untouched.
- **The prompts:**
  - `_AGENT_PROMPT.md.j2` drops the "original authors' code" block for all packages. It adds: "Inputs with role `code` are the original authors' code. Do not open them; rebuild the analysis from the paper's text and the data."
  - The action-extraction input listing (`curation._inputs_listing`) also leaves out `role=code` inputs.
  - `scripts.resolve_plan` removes `role=code` paths from each step's `inputs`. Otherwise they reach `analysis_plan.json` and the script headers, both of which the agent is told to read.
- **The codegen job request** records `code_context: "excluded_from_prompt"` and `code_files_on_disk: [paths]`. Older jobs lack the field, so evidence reports `unknown`.
- **The wording:** "recreation: the authors' code is not given to the agent; it was not isolated from the package folder." Never "blinded".

### B7. The offline harness: make it concrete (replaces §4)

- **Location: dev checkout only.** The tests and fakes live in `backend/tests/` and are not shipped. The entry is `backend/tests/scenario.py`. It inserts its own directory into `sys.path`, because the tests use bare imports such as `from test_api import …`. It is runnable from the project root as `uv run --project backend python backend/tests/scenario.py --out <dir>`. The pytest wrapper is `uv run --project backend pytest backend/tests/test_scenario.py`. It is not an `rc` subcommand in the bundled app. Pick this or ship the fakes as package data. Dev-only is the leaner choice.
- **The driver** uses FastAPI `TestClient` against the real app, with the `tmp_path`-style isolated config and registry from `conftest.py`. It reuses `test_api.mock_openalex`/`respx` from `test_zenodo.py`. Fixtures: `paper.jats.xml` and `data.csv`, the existing `fake_llm.py` in `ok` mode, and a new fake agent mode `python-ok` that fills in the `python` template with a stdlib-only script. `job_env` strips the service venv, so the job must not need pandas.
- **Stages:** create → fulltext/extraction → targets and actions → script (`python`) → codegen → run → comparison → evidence → report → archive → `recorded_comparison` and a mocked deposit **preview**. A local-file fulltext blocks a real deposit (`_check_published_paper`), and the preview needs mocked OpenAlex.
- **A stable projection is defined in code.** It drops job ids, LLM call ids, `added_at`/`created_at`, commits, durations and absolute paths (`AGENT_PROMPT.md` embeds the folder). It keeps the comparison rows and summary, the evidence `stale` and `step_evidence`, resource paths with hashes (excluding the files that change by time or path), and the stage outcomes.
- **The output** is `scenario-<UTC timestamp>/result.json` (version, stages, durations, projection hash and assertions) plus `diff.txt` against the previous run in the same output root. Nothing is overwritten.
- **The pytest test** runs the scenario twice and asserts that the two projections are equal.

## Provider spec: blocking objections

### P1. Execution capability: derive it, do not declare it (replaces §2, first paragraph)

A new explicit `exec_restriction` field is a trap:

- With a default of false, every user's existing `[agent.profiles.claude]` in `backend.toml` would lose `allow_exec=false`.
- With a default of true, the field invents a guarantee for OpenCode.

The mechanical rule is that **`allow_exec=false` is enforceable iff `"{tools}"` appears in `argv` and `exec_tools` is non-empty.** It is true for the shipped claude profile and false for opencode, including the copy that Electron's `writeConfig` injects.

- `codegen.plan()`, and so both `start` and `jobs:preview`, returns 409 `exec_restriction_unsupported` for `allow_exec=false` on a profile without enforcement. The message names the profile and the reason.
- `AgentProfileView` gains `exec_restriction: "tool_allowlist" | "unsupported"` and `available: bool`. The UI label for `tool_allowlist` is "restricted by the CLI's tool allowlist (not an OS sandbox)". The renderer disables the "don't run the analysis" choice for an unsupported profile instead of offering it.
- The schema is unchanged and existing custom profiles keep working; they are classified by the rule.

### P2. The prompt must agree with the permission (replaces §2 last sentence)

- Rule 8 of `_AGENT_PROMPT.md.j2` ("run the pipeline once if you can") is rendered only when `allow_exec` is true. Otherwise the prompt says "Do not run the analysis; the user runs it in step 6."
- `AGENT_PROMPT.md` is written at script time without `allow_exec`, so the copy on disk can differ from the prompt actually sent. The file should state that it is the default prompt with execution allowed. The job's `stdin.txt` is the record of what was actually sent, and the spec should say so.

### P3. Model to profile: scope it, do not route it (replaces §3, second paragraph)

Both `profileForModel` functions (`curation.ts:490`, `codegen.ts:95`) send every model without the `claude/` prefix to the first OpenCode-protocol profile. `models.ts` also lists models from `t3code models`, which would then be passed to `opencode -m`. Changing the profile clears the model, which contradicts the spec's own acceptance line.

The rules:

- `LlmModel` gains `cli: "claude" | "opencode" | "t3"`, the CLI that listed the model.
- A profile accepts a model iff `basename(argv[0]) === model.cli`. The `t3` models match no shipped profile, so they are shown under that heading as "no compatible profile".
- The model list is filtered to the selected profile. Selecting a model never switches the profile; selecting a profile keeps the model if it is compatible and otherwise reports why it was cleared.
- One shared function in `renderer/api.ts` replaces the two copies.

### P4. Errors at exit 0 (adds to §1)

- **LLM calls:**
  - An `opencode-json` stdout containing an `error` or `session_error` event raises 502 `llm_call_failed`, mirroring the Claude envelope check in `llm.run`.
  - Text with no answer stays 422 `llm_output_invalid`.
- **Codegen jobs:**
  - After exit 0, `finish` scans the events.
  - A Claude `result` event with `is_error`, or an OpenCode `error`/`session_error` event, sets the job to `failed` with `error="the agent reported an error: …"`. `parse_opencode` turns both of those OpenCode events into `log` events with `is_error`.
  - Failed `tool_result` events never count. The agent recovers from them routinely, and `fake_agent.py ok` emits one, so counting them would make `test_codegen_job` fail.
  - `exit_code` stays the real code.

### P5. Dry-plan: extend the existing previews (replaces §4, first paragraph)

`jobs:preview`, `extractions:preview` and `…/runs:preview` already return the argv and prompt without running anything. `PromptPreview` gains, additively:

- `available` (`shutil.which(argv[0])`);
- `output_protocol`;
- `exec_restriction`;
- `timeout_s`;
- `structured_output` (whether `json_schema_argv` is set).

No new endpoint is added. `rc` gets `rc agent preview` and `rc llm preview`, which print the same fields. The argv already contains no secrets, since tokens are not argv placeholders; this should be stated and not engineered.

### P6. The Settings form and the conformance runner: bound them (replaces §3 first paragraph and §4 second paragraph)

**The Settings form:**

- It lives in Electron main. A new IPC method `saveProfile(kind, name, profile)`:
  - parses `backend.toml` with smol-toml;
  - replaces only `llm.profiles.<name>` or `agent.profiles.<name>`;
  - validates the result against a TS mirror of the profile fields: the argv list is non-empty, the protocol is one of the four, the timeout is > 0;
  - writes `backend.toml.bak` once;
  - saves through `writeUserConfig`, then restarts the backend.
- If the backend fails to start, the previous text is restored and the error shown.
- The form warns before a restart when `GET /v1/jobs` is non-empty, because running jobs end as `interrupted`.
- The form offers presets: "Claude Code", "OpenCode", "other CLI (plain text)". It does not offer "arbitrary parser".
- The backend gains no write route. The acceptance test asserts that there is no non-GET route under `/v1/llm-profiles`, `/v1/agent-profiles` or a config path.

**The conformance runner:**

- `backend/tests/conformance/` holds one fake executable per protocol (`claude-json`, `claude-stream-json`, `opencode-json`, `text`) with sanitized fixtures.
- `run_conformance(profile_overrides) -> dict` is called by pytest and by a dev script that writes `conformance-<ts>.json` with `{schema_version, protocol, case, expected, observed, passed}`.
- The cases are those listed in the spec. The cancellation case also checks that descendant processes are gone; this is POSIX only, and the report says so.
- It is not an `rc` subcommand in the bundled app, because the fakes are not shipped. It never runs a real CLI.

## Non-blocking corrections to wording

- **Problem codes.** `api.problems.CATALOG` must register the new codes `report_stale`, `target_contradiction`, `track_unavailable` and `exec_restriction_unsupported`; `errors()` raises `KeyError` on an unknown code. They must also appear in the regenerated docs.
- **`ReportResult.reproduced`** keeps its meaning (match + within). Add `matched` and `within` beside it rather than changing it, in line with the additive-only rule.

- **Comparison §3:** "Preserve prior attempt records under `jobs/`" already happens. Say "keep".
- **Comparison acceptance:** "Regenerate schemas/OpenAPI" is enforced by `tests/test_schemas.py::test_generated_files_up_to_date` (`uv run reprocrowd-schemas export`). Name that check.
- **Provider acceptance:** "ordinary service clients cannot mutate executable configuration" is already true; state it as a regression test.
- **Both specs:** the `Model` base uses `extra="forbid"`. Additive fields (`printed`, `track`, `basis`, `snapshot`) make new packages unreadable by older app versions, though older packages still load in new versions. Accept this explicitly; the app auto-updates. Do not bump `schema_version` for additive fields.

## Essential versus optional

**Essential, the lean first slice that ships in full:**

1. `numbers.py` and the comparison rules in B1/B2, with the renderer and extraction-prompt string handling.
2. `recorded_comparison` and `report_stale` in the report, archive, Zenodo and database code, plus the separate exact/within wording (B3).
3. The execution capability rule and the 409 in P1; the prompt agreement in P2; the model scoping in P3; errors at exit 0 in P4.
4. The track field, the code exclusion and the prompt fix in B6.
5. The `RunResult` snapshot, `GET …/evidence`, `rc evidence`, and `evidence/execution.json` with `evidence/run.log` (B4); the step-claim warning and labels (B5).
6. The preview fields in P5.
7. The offline harness in B7.
8. The conformance fakes and runner in P6.
9. The Settings profile form in P6.

This order is the dependency order. Every item reuses existing routes, job records and fixtures.

**Optional later slices, not part of this agreement:**

- A warning when a reproduced number appears to be pre-rounded to the reported quantum or coarser.
- A per-target "show passage" UI in step 6.
- An Electron smoke route that consumes the harness.
- Signing the evidence.
- Count and interval target types.
- A live provider smoke test with a cost prompt.
- Removing the `t3` model listing entirely.

**Also essential: one bug outside both specs.** The R templates should tell the agent to write `outputs/results.json` with `jsonlite::write_json(x, path, auto_unbox = TRUE, digits = NA)`. `jsonlite::toJSON`'s default is `digits = 4`, confirmed locally with `formals(jsonlite::toJSON)$digits`, so reproduced values are silently rounded to 4 decimals before comparison. Example: a p-value of 0.000123 would become `1e-04`. This is one line in `templates/r/template.toml` and `templates/targets/template.toml` `agent_rule`, plus rule 5 of the prompt for Python: "write full precision; never round".

## Acceptance tests (exact)

**Numerics** (`tests/test_numbers.py`, plus extensions to `test_run.py::test_compare_rules`):

- `parse_printed`:
  - `.17` gives quantum -2;
  - `0.1700` gives -4;
  - `1.20e-5` gives -7;
  - `1.2e3` gives +2;
  - `−.30` gives value -0.30 and quantum -2;
  - `1,234` gives quantum 0;
  - `< .001` is split into the comparator `<` and the token `.001`;
  - `< .001` with comparator `>` returns 422.
- `expected=1.2e-5, printed="1.2e-5"` vs reproduced `9e-5` gives `deviation`; vs `1.24e-5` gives `match`. The same holds for a table cell.
- Cell `≤ .05` vs 0.03 gives `match`; vs 0.054 gives `match`; vs 0.056 gives `within` or `deviation`, never `match`.
- `printed=".170"` vs 0.1749 gives `within`; vs 0.1704 gives `match`.
- Expected `~10.0` vs 10.4 gives `within`, not `match`.
- `<` / `>` never return `within`.
- A tie: reproduced 0.125 at quantum -2 gives 0.12 (half-even, exactly representable). Reproduced 2.675 gives 2.67 (binary). The policy string is present.
- The API: posting a manual target `{"expected": "0.1700"}` stores `printed="0.1700"`, `decimals=4` and `expected=0.17`. `{"expected": 0.17, "printed": "0.18"}` returns 422.
- A legacy `targets.json` without `printed` loads, compares as before and has `precision: "inferred"`.
- A legacy target with `expected=0.17, decimals=2` vs 0.1704 gives `match`. This guards against a `Decimal(float)` expected side.
- `"1,5"` is rejected as unparsable; `"1,234"` gives 1234.
- Editing an LLM target's `expected` to `"0.170"` updates `printed` and `decimals=3`; `base` is unchanged.

**Consistency:**

- The report with threshold 10 makes the deposit preview's description state the same match/within counts as `comparison.json`.
- Rerunning after the report makes `PUT …/archive` and `deposits:preview` return 409 `report_stale`.
- Regenerating the report clears the staleness.

**Evidence:**

- After a run, `GET …/evidence` has `stale == []`, even for an R run that leaves `Rplots.pdf` (listed under `added_since_run`).
- After editing `analysis.py`, `stale` contains `analysis.py`.
- After replacing `outputs/results.json`, `results_match_run` is false.
- A timed-out run gives `run.status == "timeout"`, and no field says anything about the study.
- An archive built after the report contains `evidence/execution.json` and `evidence/run.log`, and all of its paths resolve inside the ZIP.
- An output whose `step` does not produce its target gives a warning and `declared_conflicts_plan`.

**Track:**

- `POST /packages {"track": "original_code"}` returns 422 `track_unavailable`, and no folder is created.
- A new package has `track == "recreation"`; a fixture manifest without the field reads back as `None` after a save.
- The codegen preview prompt with a `role=code` input does not contain that path or "reuse their logic". The job request has `code_files_on_disk` populated.

**Provider:**

- An OpenCode-style agent profile with `allow_exec=false` returns 409 from both preview and start, and no process is spawned.
- With `allow_exec=false` on the Claude profile, `{tools}` lacks Bash and the prompt lacks rule 8.
- A fake OpenCode run that prints an `error` event and exits 0 gives `llm_call_failed`. For codegen, the job status is `failed`.
- The preview reports `available: false` for a missing executable.
- Renderer unit check: a `t3`-listed model is not offered for the opencode profile, and a profile change keeps a compatible model.
- The route scan: no non-GET route writes configuration.
- The conformance runner: one fake per protocol passes. Malformed lines, nonzero exit, timeout and cancel (descendants gone on POSIX) behave as specified.

**The harness:** two consecutive runs give equal projections, and both run directories exist afterwards.

**Gates:** `uv run pytest`, `uv run reprocrowd-schemas export` with a clean diff, `npm run typecheck`, `npm run build`, and a visual check of the changed Settings form and report.

## Likely integration pitfalls

- **Pydantic coercion.** `OutputResult.value: float | int | str` turns the JSON number into a float before compare. Use `Decimal(float)` deliberately; do not reparse `results.json` with `parse_float=Decimal`, which would change the model types.
- **`expand_argv` drops a flag when its value is empty.** An empty `{tools}` with Claude's `--allowedTools` drops the restriction completely. The P1 rule must also require that the expanded tools list is non-empty.
- **`writeConfig` in `backend.ts`** injects OpenCode profiles that are missing. The P1 classification must treat the injected copies like any other profile.
- **`files.sync` registers new top-level files as kind `code`.** `evidence/` files must be written through `pkg.write_json`/`pkg.put` with kind `report` before any sync, or they become `code` resources and enter the stale snapshot.
- **Snapshot exclusions.** The snapshot must leave out `outputs/` and the evidence files themselves, or every run looks stale.
- **The report's `run=_last_run(pkg)`** and the evidence must name the same job. Use one helper.
- **Schema generation.** New schemas (`execution-evidence`) must be added to `schemas/__init__.py` `SCHEMAS`, or `test_generated_files_up_to_date` and `pkg.write_json(schema=…)` fail.
- **Legacy `comparison.json` equality.** Compare the model dumps, not file bytes. Float formatting differs.
- **Renderer state.** `st.agentModel` and `st.llmModel` persist model ids in app state. After P3, saved ids without a compatible profile must be cleared visibly, not silently.

## Agreement status

- **comparison-evidence.md: NOT YET AGREED.** It agrees once B1–B7 are adopted as written above. There are no further objections.
- **provider-interface.md: NOT YET AGREED.** It agrees once P1–P6 are adopted as written above. There are no further objections.

Revising both specs with these sections, or stating that they are adopted verbatim, is enough for agreement. A further review round is not needed unless the implementation departs from them.
