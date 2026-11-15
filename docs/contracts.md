# Recreation, comparison and provider contracts

This is the implemented contract for the desktop app, its FastAPI service and `rc` client. The independent `forrtproject/reprocrowd` benchmark can consume these endpoints and files; it does not define the app's extraction scope or research scoring. Both specifications were agreed with Opus 5.5 through the bounded amendments in [the review](specs/opus-review.md): [comparison/evidence](specs/comparison-evidence.md), [providers](specs/provider-interface.md).

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

`GET /v1/packages/{id}/comparison` computes a live comparison. `PUT …/report` writes **comparison.json** with the settings, policy and hashes of `outputs/results.json` and `results_schema.json`. Archive, Zenodo and database submission consume this recorded comparison. Changed results/targets, a newer recorded run, or scripts/inputs whose changed/added status no longer matches the recorded `evidence/execution.json` return 409 `report_stale`; regenerate report then archive. A legacy comparison without hashes or policy is checked by recomputing its recorded settings and, if it agrees, is reported under the current policy; a disagreement in statuses, counts or row text requires regeneration, while display-only deviation percentages may differ in the last floating-point digits. All clients use the same service; a benchmark does not need to reproduce these rules independently.

## Execution evidence

A run's existing `jobs/<id>.json` now includes a hash snapshot of the scripts/code, used inputs, frozen plan and targets before launch, plus result/log hashes and before/after commits. No parallel job store is introduced. Jobs are local audit records, not signed attestations.

`GET …/evidence` and `rc evidence ID --json` expose the latest run, process status, codegen metadata, source references and target-to-step links. `stale` lists changed/missing snapshot files; `added_since_run` lists new files separately. `results_match_run` checks the result bytes. Older jobs without snapshots report uncertainty explicitly.

An output's `step` is a declaration. A matching plan link is labelled `declared_matches_plan`; an unknown or nonproducing step gives a warning and `declared_conflicts_plan`. Missing declarations may be `planned_only` or `none`. Every target has evidence level `process`: a successful exit does not independently establish which scientific procedure produced a value.

Report generation registers `evidence/execution.json` and `evidence/run.log` so both are included in the ZIP. Logs over 5 MiB retain their tail and record truncation; the recorded SHA-256 is for the full log. Source passage permalinks in `report.html` and machine-readable target `refs` resolve to extraction/item IDs. The report and evidence name the same latest run.

## Providers: small profiles, one process contract

A profile is a named command plus arguments, model, effort, timeout and supported output protocol. The app supplies prompts on stdin and uses the package as the working folder. Executable configuration is local administration through Electron main; the HTTP API has no configuration-write route. A preview starts no model call and stores nothing.

| Operation | Supported protocol | Expected output |
|---|---|---|
| Extraction | `text` | Answer containing a JSON object validated against the task schema |
| Extraction | `claude-json` | Claude result envelope (`structured_output` or JSON in `result`) |
| Extraction or codegen | `opencode-json` | OpenCode line-delimited events; extraction reads text events |
| Codegen | `claude-stream-json` | Claude line-delimited progress/result events |
| Extraction or codegen | `codex-json` | Codex `exec --json` events; extraction reads the last `agent_message` |
| Codegen | `text` | Opaque process logs; exit status only, no semantic completion guarantee |

Nonzero exit, timeout, launch failure and terminal vendor error events are explicit failures. A terminal error at exit zero fails the LLM call/job; a recoverable failed tool result does not. For Codex, `turn.failed` is terminal; `error` events followed by a completed turn ("Reconnecting… 2/5") and `error` items (warnings) are not. Each protocol is one entry in `providers.answer` and `providers.terminal_error`, plus an event parser in `events.py`; workflow code never branches on the vendor. Commands and prompts are recorded in existing call/job files. Credentials must remain in the CLI's own authenticated configuration/environment, not argv.

**Execution restrictions are derived or declared, never implied:** a no-analysis request (`allow_exec=false`) runs only when the profile has one of two mechanisms; otherwise both preview and start return 409 `exec_restriction_unsupported`. Neither is an OS sandbox.

- `tool_allowlist` (derived, P1, partial): argv includes `{tools}`, `exec_tools` is nonempty and the editing tool list is nonempty. Claude Code then gets `--allowedTools Read,Write,Edit,Glob,Grep`. This only pre-approves tools: in the real app Claude Code still ran `ls`/`cat`, which it treats as read-only, while Python was denied. Profiles saved before the no-analysis settings existed get this label ("partial").
- `cli_settings` (declared, an extension of P1, preferred when present): the agent profile lists `no_exec_argv` and/or `no_exec_env`, added only for no-analysis runs, which remove the CLI's command tools. The shipped Claude Code profile adds `--disallowedTools Bash` (deny rules win over its allowlist and its read-only auto-approval); the shipped OpenCode profile sets `OPENCODE_PERMISSION` to deny every tool except read/edit/glob/grep/list/todowrite, and `--auto` approves only what is not denied. Because these are declarations, the live smoke check tests them: the exact command and environment run once editing-only and once with execution allowed, with a nonce only in the CLI's environment. Editing-only must read an existing file and write its content into another, must not run `ls`, and must not read the nonce; the control must read it. This held for Claude Code 2.1.322, Codex 0.167.0 and OpenCode 1.19.5 (DeepSeek V4 Pro) on macOS; other versions and platforms are untested. OpenCode's rule for MCP tools could not be tested here (no MCP servers configured). Profiles saved before these fields existed fall back to `tool_allowlist` (Claude) or `unsupported` (OpenCode) until their preset is re-applied in Settings.
- **Codex** reads files only through its shell, so removing the shell alone (`--disable shell_tool --disable unified_exec`) leaves an agent that can write but not read. Its shipped no-analysis settings therefore also add ReproCrowd's read-only file server, `python -m reprocrowd.mcp_files` (MCP over stdio, standard library only: `list_files`, `read_file`, `search`; nothing writes or runs commands; paths are resolved and confined to the package), with its tools approved for that server only, and `--disable multi_agent`. `{python}` in `no_exec_argv` is the backend's own interpreter. Codex edits with `apply_patch`. Reads outside the package are refused; in a live check Codex tried to read the user's global instruction files and was refused.

The previews (`jobs:preview`, Show prompt, `rc agent preview`) show the argv and the added environment, and each job records them in `request`. The prompt agrees with the permission. The disk `AGENT_PROMPT.md` is the execution-allowed default; `jobs/<id>/stdin.txt` records what was actually sent. With execution allowed, the shipped Codex agent profile uses Codex's `workspace-write` sandbox (writes confined to the package folder by Codex; commands can run). Extraction uses `read-only`. Both Codex profiles pass `--ignore-user-config`, which skips `~/.codex/config.toml` (MCP servers, default model) but keeps the login, and `--disable apps --disable plugins`, which keeps ChatGPT connectors (e.g. GitHub and site-deploy actions) away from the agent. Each CLI still applies its user's global instructions: Codex the global `AGENTS.md`, Claude Code `~/.claude/CLAUDE.md`, and OpenCode `~/.claude/CLAUDE.md` when there is no `~/.config/opencode/AGENTS.md`, unless `OPENCODE_DISABLE_CLAUDE_CODE=1` is set ([OpenCode rules](https://opencode.ai/docs/rules/)); the live harness sets it. Such instructions can make an agent read files outside the package. The run records that travel with a package (`targets.json`, `actions.json`, so archives and deposits) keep only the answer and turn-status events of event-stream CLIs, never tool outputs; the full stdout stays in the local `llm-calls/<id>/stdout.txt`. Coding-agent logs (`jobs/`) are local and not archived.

Detected models carry the CLI that listed them: `opencode models` (which lists only providers with credentials, plus OpenCode's free models), `codex debug models` (models listed for the signed-in account, shown as `codex/<slug>`; a debug command whose format may change, in which case no Codex models are listed and the CLI default remains usable), and Claude Code's tier aliases once signed in. Settings and the model pickers show which installed CLIs report no login and open that CLI's own login in a terminal. The picker offers only models for the selected executable; it never switches profiles silently. Compatible profile changes keep the model. Unknown wrappers have no inferred compatibility. Models listed by T3 Code's CLI (`t3code`; assessed in [the provider spec](specs/provider-interface.md)) cannot flow into OpenCode. T3 is not the sole transport: the assessed CLI did not provide the complete extraction, permission and process contract needed here.

**Settings → Backend → Coding and extraction profiles…** edits one profile without writing TOML by hand. Presets cover Claude Code, Codex CLI, OpenCode and another plain-text CLI. While editing, the form says whether the command is installed or needs a sign-in, offers only models listed by that command's CLI, and states whether no-analysis mode is enforceable (derived like the backend), without running a model. Saving validates fields, backs up `backend.toml` once to `.bak`, restarts, and restores the previous text if restart fails. The form warns before stopping active jobs. Only that profile's `[kind.profiles.name]` section is rewritten, so other configuration and comments outside it are kept, as are comments directly under its header; comments between that section's keys are dropped, and the profile's own sub-tables (such as `no_exec_env`) are rewritten with it. A profile that cannot be edited in place (dotted keys, an inline table, a quoted header) makes the form rewrite the whole file without comments. The raw editor remains available. Availability means the executable was found, not that its login/model/protocol has been certified. A new CLI that speaks a supported protocol needs only a profile; a new wire format needs one small parser (the two functions above and an event parser), a sanitized fake in `backend/tests/conformance/` and a smoke run. Aider and other CLIs have no parser here.

## Repeatable checks from the project root

Install checkout dependencies once with `npm ci` and `uv sync --project backend --locked`. These checks use fake commands and mocked scholarly services, with no model costs or external publication:

```sh
npm run test:contracts
npm run test:backend
npm run build
npm run test:scenario
npm run test:scenario
npm run test:providers
uv run --project backend reprocrowd-schemas check backend
```

`test:scenario` orchestrates the real API from a synthetic paper and CSV through parsing, fake extraction, a reviewed edit, fake-agent implementation, a stdlib-only calculation, comparison/evidence/report/archive and a deposit preview. All scholarly HTTP is intercepted; no deposit or upload runs. Each `test-output/paper-scenario/scenario-<timestamp>/` retains its package/report/archive, `result.json`, stable `projection.json` and `diff.txt`. The projection compares stage outcomes, stable file hashes, settings/policy, comparison rows/counts, target evidence, validity and preview metadata. It excludes times, absolute paths, job/call IDs, commits and rendered report bytes. Two successive runs must agree; intentional contract changes yield a readable diff rather than overwriting a baseline.

`test:providers` runs one fake per output protocol and writes `test-output/providers/conformance-<timestamp>.json`. The 57 cases cover both operations where supported, malformed/noisy output, terminal errors at exit zero, nonzero exit, timeout and cancellation. Descendant-process cancellation is checked on POSIX and explicitly skipped on other systems. This tests the app's parsers/process handling, not a live provider's compatibility or output quality. The fakes are dev-only and are not bundled with the app. CI runs these same checks and retains the offline artifacts.

## Live provider checks (explicit, never in CI)

These use the CLI's own sign-in. Claude Code and Codex run on the signed-in subscription; the scripts refuse to start if `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` or `OPENAI_API_KEY`/`CODEX_API_KEY` would switch the CLI to API billing. OpenCode with an API-key provider is metered: the scripts need `--spend-ledger` (a private JSON file outside the repository with a `hard_cap_usd`) and `--max-usd`, refuse when the ledger total plus the run cap exceeds the hard cap, cancel a code-generation job at the run cap, and append the provider-reported cost, doubled when a run touches DeepSeek's peak window.

```sh
cd backend
uv run python tests/provider_smoke.py --profile codex          # offline layers only
uv run python tests/provider_smoke.py --live --profile claude
uv run python tests/provider_smoke.py --live --profile codex --model gpt-6.1-terra --effort low
uv run python tests/provider_smoke.py --live --profile opencode --model deepseek/deepseek-v4-pro --spend-ledger PATH --max-usd 0.10
uv run python tests/scenario.py --live --llm codex --agent codex --model gpt-6.1-terra --effort low
uv run python tests/scenario.py --live --llm opencode --agent opencode --model deepseek/deepseek-v4-pro --spend-ledger PATH --max-usd 0.30
```

`provider_smoke.py` reports each layer separately in `test-output/providers/smoke-<profile>-<ts>.json`: executable detected (with version), offline parser cases for the profile's protocols, no-analysis restriction (409 for an unsupported profile; otherwise the nonce probe with its control, above), one live extraction from the synthetic paper (sign-in, model access, parseable answer), a 5-second timeout and a cancellation of a live code-generation job with no process left in the package folder. `scenario.py --live` runs the whole synthetic paper through the API with the chosen profiles: live extraction of targets and steps, the profile's permission path, live code generation, the run, comparison/evidence/report/archive and a deposit preview. Its result records the CLI version, protocol, model, tokens and reported cost, and two checks: the printed token `512.40` survived extraction, and a **mutation check** reran the generated script on a changed CSV and obtained the stdlib means (515.0/498.0/17.0), so the values were computed from the data rather than copied from the paper. `--profiles PATH` takes the profiles from a backend.toml saved through Settings instead of the shipped defaults. A passing live run says this CLI, model and machine worked once on a two-row fixture; it does not certify output quality, other models or other platforms.

For a real configured package, the CLI previews are:

```sh
uv run --project backend rc agent preview phacking --profile claude --no-exec
uv run --project backend rc llm preview phacking ex-europepmc-jats --kind targets --profile claude
uv run --project backend rc evidence phacking --json
```

These example IDs follow the backend quick-start package. Each preview exposes argv, prompt, executable availability, protocol, restriction, timeout and structured-output support. Starting an actual extraction/codegen can use model credits; it is separate from preview and the offline suites.

## Issue scope and remaining decisions

[#15](https://github.com/forrtproject/reprocrowd-app/issues/15), [#82](https://github.com/forrtproject/reprocrowd-app/issues/82) and [#12](https://github.com/forrtproject/reprocrowd-app/issues/12) are assigned to LukasWallrich. This change addresses #15's profile UI/capabilities/testing and #82's report/API source links. The report already had provenance; the #82 additions make frozen source references explicit in comparison/evidence responses and give passages stable report anchors. The [maintainer's question](https://github.com/forrtproject/reprocrowd-app/issues/82#issuecomment-6873849253) about which output and which paper representation should have direct links still needs a scope decision; publisher-HTML/PDF deep links are not implemented. It handles #12's comparison/evidence consistency and stale-publication checks **only**: publication licensing policy, record versioning, community/moderation and broader publication acceptance remain open. #45 remains deferred. No issue is closed by this branch.

Research/product choices still open: whether archive/deposit should refuse, rather than disclose, scripts or inputs that changed after the recorded run (to be agreed in #12; Lukas's proposal is to refuse except Markdown files); stricter isolation for future recreation; scientific criteria beyond process/number agreement; and live-provider qualification after deciding providers and acceptable costs. No changes here adopt the benchmark's gold labels or adjudication policy. New versions read older packages, but older app versions with strict models can reject these new additive fields; no bidirectional compatibility is promised.

Source guide: `backend/src/reprocrowd/numbers.py`, `compare.py`, `run.py`, `evidence.py`, `report.py`; `providers.py`, `llm.py`, `codegen.py`, `api/app.py`, `cli.py`; `src/main/profiles.ts`, `backend.ts`, `models.ts`; `src/renderer/profiles.ts`, `repro/curation.ts`, `repro/codegen.ts`; and `backend/tests/{scenario.py,conformance/}`. Generated contracts are in `backend/schemas/` and `backend/docs/openapi.json`.
