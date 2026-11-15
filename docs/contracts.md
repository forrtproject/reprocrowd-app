# Recreation, comparison and execution-evidence contracts

This is the implemented contract for the desktop app, its FastAPI service and `rc` client. The independent `forrtproject/reprocrowd` benchmark can consume these endpoints and files; it does not define the app's extraction scope or research scoring. Provider profiles and their checks are a separate change (#15).

## Workflow tracks

**Track B: recreation** is the implemented workflow: article + published data → reviewed targets and methods → generated analysis → run → comparison → report/archive → reviewed deposit. New packages record `track: "recreation"`. Packages created before this field stay `null` (unspecified); the app does not retroactively certify their method.

**Track A: original code** remains deferred in [#45](https://github.com/forrtproject/reprocrowd-app/issues/45). Creating a package with `track: "original_code"` returns 422 `track_unavailable` before creating a folder. No original-entry-point runner is supplied.

Author-code inputs are omitted from action-extraction listings, generated plan inputs, script headers and coding-agent prompts. They can remain readable in the working folder. Each codegen job records `code_context` and `code_files_on_disk`. This is an exclusion from supplied context, not verified blinding or an OS sandbox. Review generated code and research assumptions before interpreting results.

## Printed values and comparison

For a new numerical target, send `expected` as a string, e.g. `"0.1700"` or `"< .001"`. `rc target add phacking -f type=number -f description="Mean RT" -f expected=0.1700 --unreferenced --note …` and `rc target edit phacking T001 --set expected=0.170` send the typed token unchanged. The server derives numeric `expected`, the number-only `printed` token, separate `comparator`, and `decimals` where applicable. Contradictory supplied values or precision return 422 `target_contradiction`. Table cells retain their original strings and use the same parser. Commas are accepted only as well-formed thousands grouping: `"1,234"` means 1234, while decimal commas such as `"1,5"` or `"0,512"` are rejected.

The printed token determines the rounding quantum (the place to which a result is rounded): `.1700` → 0.0001; `1.20e-5` → 0.0000001; `1.2e3` → 100. Trailing zeroes therefore matter. Older numeric targets with `decimals` use that field; targets without either field have inferred precision and a warning. Lost trailing zeroes cannot be recovered.

Policy `reprocrowd-compare/3`, mode `either_direction_at_ties`:

- Both sides are decimals: the expected side is the printed token, and a reproduced JSON number is read as the shortest decimal that round-trips its double (`2.675`, not the binary approximation 2.67499…). Any number written with at most 15 significant digits is therefore read exactly as written: this covers R's `jsonlite` with `digits = NA` (15 significant digits; its default of 4 would round, so the templates require `digits = NA`) and Python's `json`, which writes the shortest round-trip form itself. Integers are exact. Only a non-integer written with more digits than a double holds is read at double precision. A reproduced value given as a JSON string keeps its exact digits.
- At the chosen precision a reproduced value matches if rounding it half-down or half-up gives the printed number. Exact ties therefore match either neighbour, whichever convention the authors' software used: 2.675 matches 2.67 and 2.68; 0.125 matches .12 and .13. Non-ties have one rounding (2.6749 → 2.67). The report shows a tie as `2.67 or 2.68 (tie)`.
- `=` and `~` match only at the chosen precision. `<=` and `>=` compare rounded sides, accepting either tie direction (`≤ .05` matches 0.055 but not 0.0551). Strict `<` and `>` compare unrounded sides and never return `within`.
- Lukas chose this rule on 15 November 2026; it replaces `reprocrowd-compare/2` (exact binary value, half-even only). Comparisons recorded under `/2` keep that policy name until their report is regenerated.
- `within` is a separate exploratory relative tolerance, default 5%; it is not an exact match. For `~10.0`, a value of 10.4 is `within`, not `match`. Counts from old reports may change when regenerated.
- Reports and deposit descriptions count exact matches and within-tolerance results separately. Their denominator excludes figures, which need visual comparison. `ReportResult.reproduced` remains match + within for compatibility; `matched` and `within` are also returned.
- Generated analyses must write unrounded values. R instructions require `jsonlite::write_json(..., auto_unbox = TRUE, digits = NA)`; the default four digits would destroy precision before comparison. The prompt is guidance, not proof that a live agent complied.

`GET /v1/packages/{id}/comparison` computes a live comparison. `PUT …/report` writes **comparison.json** with the settings, policy and hashes of `outputs/results.json` and `results_schema.json`. Archive, Zenodo and database submission consume this recorded comparison. Changed results/targets, a newer recorded run, or scripts/inputs whose changed/added status no longer matches the recorded `evidence/execution.json` return 409 `report_stale`; regenerate report then archive. A report made before comparisons were recorded with hashes also returns `report_stale`: regenerate it. All clients use the same service; a benchmark does not need to reproduce these rules independently.

## Execution evidence

A run's existing `jobs/<id>.json` now includes a hash snapshot of the scripts/code, used inputs, frozen plan and targets before launch, plus result/log hashes and before/after commits. No parallel job store is introduced. Jobs are local audit records, not signed attestations.

`GET …/evidence` and `rc evidence ID --json` expose the latest run, process status, codegen metadata, source references and target-to-step links. `stale` lists changed/missing snapshot files; `added_since_run` lists new files separately. `results_match_run` checks the result bytes. Older jobs without snapshots report uncertainty explicitly.

An output's `step` is a declaration. A matching plan link is labelled `declared_matches_plan`; an unknown or nonproducing step gives a warning and `declared_conflicts_plan`. Missing declarations may be `planned_only` or `none`. Every target has evidence level `process`: a successful exit does not independently establish which scientific procedure produced a value.

Report generation registers `evidence/execution.json` and `evidence/run.log` so both are included in the ZIP. Logs over 5 MiB retain their tail and record truncation; the recorded SHA-256 is for the full log. Source passage permalinks in `report.html` and machine-readable target `refs` resolve to extraction/item IDs. The report and evidence name the same latest run.

## GitHub pilots and output licences

A package with a current report and ZIP can be submitted as a **GitHub pilot**: a public, unreviewed record in `[github] pilots_repo` (default [forrtproject/reprocrowd-pilots](https://github.com/forrtproject/reprocrowd-pilots)). Each reproduction (`reproduction_id`, not the DOI) gets one issue, and each run gets one pre-release and one comment. The run key is derived from the archive hash and the left-out groups, so a retry creates nothing twice. Pilots consume the recorded comparison and refuse `report_stale` exactly as Zenodo does. `PUT …/archive` now also writes `licenses.json` (policy `reprocrowd-licensing/1`): CC0 metadata, CC BY 4.0 outputs, `[licensing] code` for new code, and third-party material not relicensed. Archives therefore gain one file and its schema copy. See [the pilot spec](specs/github-pilots.md).

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

CI runs the build, schema check and backend tests; the backend tests include `test_scenario.py`, which runs the scenario twice in a temporary folder and requires equal projections. Run `test:scenario` by hand to keep a readable package, report and diff. For a real configured package, `uv run --project backend rc evidence phacking --json` shows its execution evidence (the ID follows the backend quick-start package).

## Issue scope and remaining decisions

[#15](https://github.com/forrtproject/reprocrowd-app/issues/15), [#82](https://github.com/forrtproject/reprocrowd-app/issues/82) and [#12](https://github.com/forrtproject/reprocrowd-app/issues/12) are assigned to LukasWallrich. This change addresses #82's report/API source links. The report already had provenance; the #82 additions make frozen source references explicit in comparison/evidence responses and give passages stable report anchors. The [maintainer's question](https://github.com/forrtproject/reprocrowd-app/issues/82#issuecomment-6873849253) about which output and which paper representation should have direct links still needs a scope decision; publisher-HTML/PDF deep links are not implemented. It handles #12's comparison/evidence consistency and stale-publication checks **only**: publication licensing policy, record versioning, community/moderation and broader publication acceptance remain open. #45 remains deferred. No issue is closed by this branch.

Research/product choices still open: whether archive/deposit should refuse, rather than disclose, scripts or inputs that changed after the recorded run (to be agreed in #12; Lukas's proposal is to refuse except Markdown files); stricter isolation for future recreation; and scientific criteria beyond process/number agreement. No changes here adopt the benchmark's gold labels or adjudication policy. New versions read older packages, but older app versions with strict models can reject these new additive fields; no bidirectional compatibility is promised.

Source guide: `backend/src/reprocrowd/numbers.py`, `compare.py`, `run.py`, `evidence.py`, `report.py`, `database.py`, `zenodo.py`; `codegen.py` (code context), `scripts.py`, `api/app.py`, `cli.py`; `src/renderer/repro/{curation,run,report}.ts`; and `backend/tests/{scenario.py,test_numbers.py,test_contracts.py}`. Generated contracts are in `backend/schemas/` and `backend/docs/openapi.json`.
