# Recreation, comparison and execution-evidence contracts

This is the implemented contract for the desktop app, its FastAPI service and `rc` client. The independent `forrtproject/reprocrowd` benchmark can consume these endpoints and files; it does not define the app's extraction scope or research scoring. The [comparison/evidence specification](specs/comparison-evidence.md) was agreed with Opus 5.5 through the bounded amendments in [the review](specs/opus-review.md). Provider profiles and their checks are a separate change (#15).

## Workflow tracks

**Track B: recreation** is the implemented workflow: article + published data → reviewed targets and methods → generated analysis → run → comparison → report/archive → reviewed deposit. New packages record `track: "recreation"`. Packages created before this field stay `null` (unspecified); the app does not retroactively certify their method.

**Track A: original code** remains deferred in [#45](https://github.com/forrtproject/reprocrowd-app/issues/45). Creating a package with `track: "original_code"` returns 422 `track_unavailable` before creating a folder. No original-entry-point runner is supplied.

Author-code inputs are omitted from action-extraction listings, generated plan inputs, script headers and coding-agent prompts. They can remain readable in the working folder. Each codegen job records `code_context` and `code_files_on_disk`. This is an exclusion from supplied context, not verified blinding or an OS sandbox. Review generated code and research assumptions before interpreting results.

## Printed values and comparison

For a new numerical target, send `expected` as a string, e.g. `"0.1700"` or `"< .001"`. `rc target add -f expected=0.1700` and `rc target edit … --set expected=0.170` send the typed token unchanged. The server derives numeric `expected`, the number-only `printed` token, separate `comparator`, and `decimals` where applicable. Contradictory supplied values or precision return 422 `target_contradiction`. Table cells retain their original strings and use the same parser. Decimal commas such as `"1,5"` are rejected; `"1,234"` means 1234.

The printed token determines the rounding quantum (the place to which a result is rounded): `.1700` → 0.0001; `1.20e-5` → 0.0000001; `1.2e3` → 100. Trailing zeroes therefore matter. Older numeric targets with `decimals` use that field; targets without either field have inferred precision and a warning. Lost trailing zeroes cannot be recovered.

Policy `reprocrowd-compare/2`, mode `half_even_exact_binary`:

- The expected side is an exact decimal token. Reproduced JSON floats retain their exact binary value before half-even rounding, preserving Python's existing tie behavior: 0.125 rounds to 0.12; 2.675 to 2.67. Changing to half-up is a research/product decision.
- `=` and `~` match only at the chosen precision. `<=` and `>=` compare rounded sides. Strict `<` and `>` compare unrounded sides and never return `within`.
- `within` is a separate exploratory relative tolerance, default 5%; it is not an exact match. For `~10.0`, a value of 10.4 is `within`, not `match`. Counts from old reports may change when regenerated.
- Reports and deposit descriptions count exact matches and within-tolerance results separately. Their denominator excludes figures, which need visual comparison. `ReportResult.reproduced` remains match + within for compatibility; `matched` and `within` are also returned.
- Generated analyses must write unrounded values. R instructions require `jsonlite::write_json(..., auto_unbox = TRUE, digits = NA)`; the default four digits would destroy precision before comparison. The prompt is guidance, not proof that a live agent complied.

`GET /v1/packages/{id}/comparison` computes a live comparison. `PUT …/report` writes **comparison.json** with the settings, policy and hashes of `outputs/results.json` and `results_schema.json`. Archive, Zenodo and database submission consume this recorded comparison. Changed results/targets, a newer recorded run, or scripts/inputs whose changed/added status no longer matches the recorded `evidence/execution.json` return 409 `report_stale`; regenerate report then archive. A legacy comparison without hashes is checked by recomputing its recorded settings; a disagreement in statuses, counts or row text requires regeneration, while display-only deviation percentages may differ in the last floating-point digits. All clients use the same service; a benchmark does not need to reproduce these rules independently.

## Execution evidence

A run's existing `jobs/<id>.json` now includes a hash snapshot of the scripts/code, used inputs, frozen plan and targets before launch, plus result/log hashes and before/after commits. No parallel job store is introduced. Jobs are local audit records, not signed attestations.

`GET …/evidence` and `rc evidence ID --json` expose the latest run, process status, codegen metadata, source references and target-to-step links. `stale` lists changed/missing snapshot files; `added_since_run` lists new files separately. `results_match_run` checks the result bytes. Older jobs without snapshots report uncertainty explicitly.

An output's `step` is a declaration. A matching plan link is labelled `declared_matches_plan`; an unknown or nonproducing step gives a warning and `declared_conflicts_plan`. Missing declarations may be `planned_only` or `none`. Every target has evidence level `process`: a successful exit does not independently establish which scientific procedure produced a value.

Report generation registers `evidence/execution.json` and `evidence/run.log` so both are included in the ZIP. Logs over 5 MiB retain their tail and record truncation; the recorded SHA-256 is for the full log. Source passage permalinks in `report.html` and machine-readable target `refs` resolve to extraction/item IDs. The report and evidence name the same latest run.

## Repeatable checks from the project root

Install checkout dependencies once with `npm ci` and `uv sync --project backend --locked`. These checks use a fake extraction command, a fake coding agent and mocked scholarly services, with no model costs or external publication:

```sh
npm run test:backend
npm run build
npm run test:scenario
npm run test:scenario
uv run --project backend reprocrowd-schemas check backend
```

`test:scenario` orchestrates the real API from a synthetic paper and CSV through parsing, fake extraction, a reviewed edit, fake-agent implementation, a stdlib-only calculation, comparison/evidence/report/archive and a deposit preview. All scholarly HTTP is intercepted; no deposit or upload runs. Each `test-output/paper-scenario/scenario-<timestamp>/` retains its package/report/archive, `result.json`, stable `projection.json` and `diff.txt`. The projection compares stage outcomes, stable file hashes, settings/policy, comparison rows/counts, target evidence, validity and preview metadata. It excludes times, absolute paths, job/call IDs, commits and rendered report bytes. Two successive runs must agree; intentional contract changes yield a readable diff rather than overwriting a baseline.

CI runs these same checks and retains the offline artifacts. For a real configured package, `uv run --project backend rc evidence phacking --json` shows its execution evidence (the ID follows the backend quick-start package).

## Issue scope and remaining decisions

[#15](https://github.com/forrtproject/reprocrowd-app/issues/15), [#82](https://github.com/forrtproject/reprocrowd-app/issues/82) and [#12](https://github.com/forrtproject/reprocrowd-app/issues/12) are assigned to LukasWallrich. This change addresses #82's report/API source links. The report already had provenance; the #82 additions make frozen source references explicit in comparison/evidence responses and give passages stable report anchors. The [maintainer's question](https://github.com/forrtproject/reprocrowd-app/issues/82#issuecomment-6873849253) about which output and which paper representation should have direct links still needs a scope decision; publisher-HTML/PDF deep links are not implemented. It handles #12's comparison/evidence consistency and stale-publication checks **only**: publication licensing policy, record versioning, community/moderation and broader publication acceptance remain open. #45 remains deferred. No issue is closed by this branch.

Research/product choices still available to Lukas: half-up versus the retained tie policy; whether archive/deposit should refuse, rather than disclose, scripts or inputs that changed after the recorded run; stricter isolation for future recreation; and scientific criteria beyond process/number agreement. No changes here adopt the benchmark's gold labels or adjudication policy. New versions read older packages, but older app versions with strict models can reject these new additive fields; no bidirectional compatibility is promised.

Source guide: `backend/src/reprocrowd/numbers.py`, `compare.py`, `run.py`, `evidence.py`, `report.py`, `database.py`, `zenodo.py`; `codegen.py` (code context), `scripts.py`, `api/app.py`, `cli.py`; `src/renderer/repro/{curation,run,report}.ts`; and `backend/tests/{scenario.py,test_numbers.py,test_contracts.py}`. Generated contracts are in `backend/schemas/` and `backend/docs/openapi.json`.
