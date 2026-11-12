# Claims breakdown — review log

Rulings, fix rounds and deferred minor findings recorded while carrying out docs/superpowers/plans/2026-11-12-claims-breakdown.md (subagent-driven, per-task reviews plus a final whole-branch review). Lines marked `minor (deferred)` are known follow-ups.


Spec: docs/superpowers/specs/2026-11-12-claims-breakdown-design.md (reachable)
Branch: claude/claims-breakdown, start b5cba77

## Pre-flight scan

| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1↔T3 | T1's `Spec("claims", …, ClaimLlmItem, …)` needs `ClaimLlmItem`; T3 "produces" it | CONFLICT: T1 can't reference a T3 model. Ruling 1 |
| T1↔T2 | `ClaimFields` field names vs `RESET_FIELDS` | consistent (text, mode, original_quote, testable, method, evidence, sub_claims all defined in T1) |
| T1↔T6 | `ClaimsFile.ran` / `ClaimRunRecord` used by `record_run` | consistent |
| T2↔T4, T2↔T6 | `content_hash(pkg, claim)` used for `ScriptMeta.claim_hash` and for stale | consistent |
| T3↔T4 | preparation = transform actions; `claim_scope.actions` = methods + every effective transform | consistent |
| T4↔T5 | `prepare.*`, `claims/<id>.*`, `[claims].agent_rule` vs agent prompt file names | consistent |
| T4↔T6 | runner emits `value: null` + note for a failed claim → compare `missing` → not computed | consistent |
| T5↔T6 | `running` state keyed on active job `request.claim`; but the run job (T9 posts `{type:"run"}`) carries no claim | GAP: claims show `ready` while their run job runs. Ruling 2 |
| T6↔T7 | `ClaimResult`, `ClaimState` in the trace; "Not run" for claims without `ran` | consistent |
| T7↔T9 | `no_claim_run`, `version_of`, "Deposit new version" | consistent |
| T8↔T6/T7 | `claims:status`, `claims:trace`, `PATCH /claims/{id} {disabled}`, `PATCH /claims/runs/{run}` (Curation supports both) | consistent |
| T9↔T4 | `runClaim` default template `"r"`; e2e and test machines have Python but not necessarily R (`test/e2e.mjs:169` uses `python`) | CONFLICT: e2e would need Rscript. Ruling 3 |
| T1 self | lazy `claims.json` + `init_package` writes it; test deletes resource | agrees |
| T2 self | tests match `RESET_FIELDS` / notes rule | agrees |
| T3 self | fake output positions `[1,2]` → test expects `["C1","C2"]`; unresolvable evidence → targets run `rejected` | agrees |
| T4 self | `test_claim_error_isolated` runs the runner with only marker comments in prepare.py | agrees |
| T5 self | preconditions order vs tests | agrees |
| T6 self | lifecycle test needs a claim file that calls `record` in claims mode | agrees (implementer writes claims/C1.py) |
| T7 self | redeposit test needs a changed archive (run C2) | agrees |
| T8 self | typecheck + manual check only; e2e lands in T9 | agrees |
| T9 self | e2e asserts deposit description contains C1 | agrees |

Rulings:
- Ruling 1: Task 1 defines `ClaimLlmItem` (fields as listed in Task 3) plus `SubClaimLlmItem`, because its `Spec` needs it; Task 3 adds the other LLM models (`MethodLlmItem`, `PrepLlmItem`, `SkippedLlmItem`, `ClaimLlmOutput`) and the generation — the Spec tuple requires an llm_item at import time — if wrong: a model moves between files, no behaviour change.
- Ruling 2: in Task 6, state `running` also applies to every claim in `claim_scope(pkg).claims` while a `run` job is active (the run recomputes all ticked claims), in addition to a codegen job with `request.claim == cid` — the spec's status list has "Running" and a run job really does recompute those claims — if wrong: claims show Running during other claims' runs; a display change.
- Ruling 3: in Task 9, `runClaim` uses the package's current template, else `"python"` (not `"r"`) — Python is always present where the backend runs and e2e uses python; R may be absent — if wrong: users who want R pick it in step 4 first; one default string.
Task 1: implementer DONE_WITH_CONCERNS (54a61ac): no RED captured; POST claim rules return request_invalid not schema_invalid; deleting a target/action used by a claim not blocked.
Task 1: review — spec ✅ with deviation; quality Needs fixes (2 Important)
Task 1: Ruling: POST /claims returns 422 `request_invalid` for the ClaimFields rules (body-model validation, as for targets); `schema_invalid` stays for PATCH and the Claim-level sub-id rule — the brief said schema_invalid, but the existing convention (design.md: bodies validated against their models first, `request_invalid`) governs — if wrong: tests and one problem code per case change
Task 1: minor (deferred): depends_on/traced_under allow self-reference and cycles (curation.py item_errors)
Task 1: minor (deferred): _next_ids reuses the highest id after deleting a manual item (pre-existing, shared with targets/actions; spec says claim ids never reused)
Task 1: minor (deferred): import json inside test body (test_claims.py:84)
Task 1: minor (deferred): no tests for ran/skipped fields, or for sub-claim evidence/method resolution
Task 1: fix round 1/5 (2 addressed, 0 open; commits 54a61ac..ea92a52)
Task 1: minor (deferred): curation.py:249 _users comprehension hard to read; `refs` misnames claim users (re-review)
Task 1: complete (commits b5cba77..ea92a52, review clean)
Task 2: implementer DONE (8f29b41)
Task 2: review — spec ❌ (sub-claim refs edit resets tick); quality Needs fixes (2 Important)
Task 2: minor (deferred): target/action patch that changes only notes/refs or nothing still unticks claims (over-eager, safe)
Task 2: minor (deferred): {"text":…, "checked": true} in one patch silently ends unticked
Task 2: minor (deferred): uncheck_users not atomic with the target save
Task 2: minor (deferred): patch_run enable/disable and re-runs change effective targets/actions without uncheck_users — whole-branch review to check
Task 2: minor (deferred): uncheck_users return value not asserted
Task 2: fix round 1/5 (2 addressed, 1 open — new: malformed sub_claims PATCH → unhandled exception instead of 422; commits 8f29b41..8065598)
Task 2: Ruling: malformed sub_claims via HTTP → 422 request_invalid (route schema) for list/string, schema_invalid for null; direct Curation.patch → schema_invalid for all — same request_invalid convention as Task 1's ruling — if wrong: problem codes in two tests
Task 2: fix round 2/5 (1 addressed, 0 open; commits 8065598..ac93f16)
Task 2: minor (deferred): test runs show 1 warning — StarletteDeprecationWarning (httpx with starlette TestClient), from the dependency, not this work
Task 2: complete (commits ea92a52..ac93f16, review clean)
Task 3: implementer DONE (74c7323): concerns — dangling depends_on/traced_under positions dropped silently; unused methods still become actions; LlmCall.task literal gained 'claims'
Task 3: review — spec ✅; quality Needs fixes (1 Important: non-atomic three-file write); ⚠️ enabling a claims run does not enable its linked targets/actions runs — confirmed real gap (Task 8's "Use this run" relies on it)
Task 3: Ruling: `PATCH …/claims/runs/{run} {disabled}` cascades to the targets and actions runs whose request.claims_run == run; per-claim `disabled` switches act only on claim entries (cherry-picking single claims out of a still-disabled run is not supported — Task 8's banner offers "Use this run" for the whole run plus per-claim switch-off) — spec says the user "switches on the ones to keep"; whole-run enable plus switch-off of unwanted claims achieves that without orphaned evidence — if wrong: Task 8 UI and one backend cascade change
Task 3: minor (deferred): dangling depends_on/traced_under positions, invalid skipped refs and unused methods dropped/created without a trace; traced_under unasserted
Task 3: minor (deferred): generate ~140 lines, duplicates Curation.generate validation; evidence validated twice
Task 3: minor (deferred): api/app.py:661-669 pointless `gen` alias
Task 3: minor (deferred): request["scope"] only on the claims run
Task 3: minor (deferred): claim with sub-claims plus own method/evidence rejected whole (could drop the stray fields)
Task 3: minor (deferred): re-extraction duplicates targets/actions; enabling two runs duplicates targets
Task 3: fix round 1/5 (2 addressed, 0 open; commits 74c7323..3c08a51)
Task 3: minor (deferred): rollback rewrites an empty targets/actions file instead of deleting a file that didn't exist; patch_run cascade not rolled back if the final claims save fails
Task 3: minor (deferred): test_extract_is_all_or_nothing doesn't assert a claim was accepted
Task 3: complete (commits ac93f16..3c08a51, review clean)
Task 4: implementer DONE (087f8ab): concerns — tick changes apply at next PUT script; switching to claims mode overwrites an implemented analysis.py (on_modified ignored); prepare.py kept even if unmodified so new transform headers never land; claims share prepare objects; R runner unexecuted (no Rscript)
Task 4: review — spec ❌ (silent overwrite of agent-implemented monolithic analysis.py on entering claims mode; prepare never refreshed); quality Needs fixes (2 Important); ⚠️ runner reflects ticks only after PUT script — confirmed real gap
Task 4: Ruling: entering claims mode with an edited non-claims master honours on_modified — refuse → 409 script_modified (listing it); keep → the old file is moved to `<stem>.pre-claims.<ext>`, registered as a `code` resource and listed in `kept`; overwrite → replaced — the brief's "on_modified ignored in claims mode" was about claim files; the spec's anti-goal (never throw away agent code) wins — if wrong: one extra 409 on first claims-mode PUT
Task 4: Ruling: prepare file is regenerated when its current hash equals its generated_hash (unmodified); kept once edited — brief's "kept if it exists" was plan-mandated and starves new preparation steps — if wrong: prepare headers re-rendered for unedited files only, harmless
Task 4: Ruling: in claims mode `run.start` re-renders the runner (only the runner; it is never agent-edited) from the current claim_scope before running — "only ticked claims run" must hold even without a fresh PUT …/script — if wrong: one extra file write per run
Task 4: minor (deferred): claim_targets doesn't dedupe targets listed by a claim and its sub-claim (duplicate record lines)
Task 4: minor (deferred): a target shared by two ticked claims behaves by run order (later failure overwrites earlier success; no "not recorded" note)
Task 4: minor (deferred): sub-claim without a method gets step "C1", not a plan step id
Task 4: minor (deferred): except Exception / R error= miss SystemExit / quit(); a claim calling exit stops the run
Task 4: minor (deferred): todo_status/agent_prompt recompute claim_scope at GET time
Task 4: minor (deferred): _put_script duplicates the describe call of the monolithic branch
Task 4: minor (deferred): runner header says "Monolithic … claims runner"
Task 4: fix round 1 implementer DONE (6f105bd): concern — refresh_runner leaves results_schema.json, so after an untick without PUT the comparison still lists the unticked claim's targets as missing → carry to Task 6
Task 4: fix round 1/5 (3 addressed, 0 open; commits 087f8ab..6f105bd)
Task 4: minor (deferred): all claims unticked in claims mode → runner with empty CLAIMS, run yields no rows (no early nothing_to_script)
Task 4: minor (deferred): refresh_runner leaves results_schema.json/analysis_plan.json stale after an untick without PUT (carried to Task 6)
Task 4: complete (commits 3c08a51..6f105bd, review clean)
Task 5: implementer DONE (949f7b4): ticked claim without a file yet → 409 script_missing; claim on a non-claims template → claim_not_checked
Task 5: review — spec ✅; quality Needs fixes (1 Important: scoped claim file with no TODOs rendered "keep it as it is")
Task 5: minor (deferred): no tests for claim_no_evidence, job request.claim, prepare omitted once implemented, non-claims prompt byte-identity
Task 5: minor (deferred): scripts.py:402 scope re-check partly duplicates found.checked (comment it)
Task 5: minor (deferred): prompt wording "Shared code goes in prepare only if it is listed in rule 1" unclear
Task 5: minor (deferred): ^C\d+$ pattern repeated in CodegenJobCreate.claim
Task 5: minor (deferred): claim on a template without [claims] returns claim_not_checked; template_not_claims_ready would be more precise
Task 5: fix round 1/5 (1 addressed, 0 open; commits 949f7b4..f1dab84)
Task 5: minor (deferred): test_codegen_claim_job_request_and_no_evidence doesn't assert the tick PATCH succeeded
Task 5: complete (commits 6f105bd..f1dab84, review clean)
Task 6: Ruling: `ClaimRunRecord` gains `result: ClaimResult | None`, a snapshot computed by `record_run` from that run's comparison; `statuses` uses the live comparison for current claims and the snapshot for stale/unticked ones (falls back to the snapshot when the live comparison lacks the claim's rows) — the spec keeps a stale claim's last result, and Task 4's refresh_runner leaves results_schema.json listing unticked claims' targets as missing — if wrong: one stored field per claim
Task 6: implementer DONE_WITH_CONCERNS (11d8908): no RED captured; unticked-unchanged ran claim shows its verdict (not stale); record_run snapshot None on comparison error
Task 6: review — spec ❌ (unticked previously-run claim reported current); quality Needs fixes (1 Important)
Task 6: Ruling: `stale` = content hash differs OR the claim is not ticked; a stale claim's state is `review` (unticked) or `ready` (ticked, edited) with the snapshot result kept; re-ticking an unchanged claim restores its verdict (content and code unchanged since that run) — spec: unticked claims "keep their last result marked stale"; a re-ticked unchanged claim's last run still describes it — if wrong: a re-ticked claim would need a re-run to show a verdict
Task 6: minor (deferred): record_run calls Curation._save (private) from claims.py
Task 6: minor (deferred): record_run runs for failed/timed-out runs whenever results.json exists
Task 6: minor (deferred): non-ApiError exceptions in record_run would escape run finish
Task 6: minor (deferred): clumsy status-code expression in test_status_lifecycle (:1163)
Task 6: Ruling: run.finish's guard around record_run catches every Exception (incl. ApiError) and logs it — a bookkeeping failure must never fail the run that produced results — if wrong: an ApiError from record_run is logged instead of surfacing
Task 6: fix round 1/5 (2 addressed, 0 open; commits 11d8908..7ea1355)
Task 6: complete (commits f1dab84..7ea1355, review clean)
Task 7: implementer DONE (45ba515): Ref/SkippedSentence moved to schemas/refs.py; trace sub-claims gain method; write_archive rewrites claims_trace.json; report Claims section uses default compare settings; ran claim without result shows Not run; Electron allowlist for claims:trace not checked (→ Task 8)
Task 7: review — spec ❌ (1 Important: report Claims section uses default CompareSettings, can contradict the report's own table); quality Needs fixes
Task 7: minor (deferred): a failure between newversion and the file deletes (GET latest_draft, missing link/id) leaves an orphaned Zenodo draft
Task 7: minor (deferred): a ran claim with no result shows "Not run" in the Zenodo description while deposit.claims lists it
Task 7: minor (deferred): write_archive refreshes claims_trace.json but report.html/comparison.json stay from the last PUT report (can lag the trace in the ZIP)
Task 7: minor (deferred): newversion called again while an earlier new-version draft is unpublished — Zenodo behaviour unverified
Task 7: minor (deferred): TraceSubClaim.method beyond the brief's interface (harmless)
Task 7: fix round 1/5 (1 addressed, 1 open — Zenodo description claims table still uses default settings; commits 45ba515..6fef6df)
Task 7: fix round 2/5 (2 addressed, 0 open; commits 6fef6df..aab98c8)
Task 7: minor (deferred): test_claims_deposit.py:177 compares raw claim text with HTML-escaped description (breaks on quotes/ampersands)
Task 7: complete (commits 7ea1355..aab98c8, review clean)
Task 8: Ruling: the brief's Step 3 (npm start against the noma paper with a real LLM) is replaced by `npm run typecheck` + `npm run build` here; the UI is exercised by Task 9's e2e and a controller visual check at the end — a subagent cannot drive a real LLM profile or look at the window — if wrong: UI defects surface one task later
Task 8: implementer DONE (070a02f): reloads after each edit; extract dialog has no scope/preview; sub-claim text read-only; onRun hook
Task 8: review — spec ❌; quality Needs fixes (3 Important: detail order quote-before-numbers; sub-claim text read-only; empty number field → 0)
Task 8: Ruling: finding "claim-level evidence hidden when sub-claims exist" is not a defect — ClaimFields forbids claim-level method/evidence alongside sub_claims (Task 1 validator) — if wrong: nothing to show anyway
Task 8: Ruling: detail pane order = 1 "Numbers" (every evidence value of the claim, grouped by sub-claim for decomposed claims, editable) → 2 the claim's quote (+ original quote under a paraphrase) → 3 sub-claims (editable quote text + method summary; their numbers live in section 1, not repeated) → 4 notes → 5 tick + Run → 6 result → 7 assumptions — the user's "numbers and quoted text, in that order" — if wrong: a section reorder
Task 8: minor (deferred): trace/doc loads swallow errors (.catch(() => undefined))
Task 8: minor (deferred): Run button active with no-op onRun until Task 9 (busy reload)
Task 8: minor (deferred): claims of a disabled run look selectable and editable despite the banner
Task 8: minor (deferred): Copy button shows "Copied" even on clipboard error
Task 8: minor (deferred): drawReader shadows imported `host`
Task 8: fix round 1/5 (3 addressed, 0 open; commits 070a02f..32cb2aa)
Task 8: minor (deferred): Numbers block gated on c.testable; sub-claim edit handler returns silently when parent claim missing; categorical values now trimmed
Task 8: complete (commits aab98c8..32cb2aa, review clean)
Task 9: Ruling: the fake LLM's CLAIMS branch takes sentence ids from the prompt when the TEI fixture ids are absent (e2e uses the live JATS of pbio.1002106) — otherwise every e2e claim is rejected — if wrong: test-only file
Task 9: implementer DONE_WITH_CONCERNS (6aad413 agent-prompt newline fix, d4d72a3 app): stale build/backend bundle (rebuilt); curation.ts marks-clear fix; fake agent records expected values; claims:status default settings; one e2e network timeout then pass
Task 9: review — spec ✅; quality Approved
Task 9: minor (deferred): final "ran / did not run" message reads this.status, stale if the user left the Claims step mid-run
Task 9: minor (deferred): no e2e assertion of the disabled deposit button / hint before a claim has run
Task 9: minor (deferred): deposit claims gate silently dropped if claims:status fails for a claims package
Task 9: minor (deferred): e2e failure branch keys on dialog h3 text "Running claim"
Task 9: minor (deferred): mid-run failures leave no marker on the claim besides the dialog
Task 9: complete (commits 32cb2aa..d4d72a3, review clean)
Final review: With fixes — 1 Critical (ticked-but-unimplemented claims recorded/published as "Not reproduced 0/0"), 4 Important (claim id reuse after delete; target edit wipes agent code in claims/<id>.*; app verdicts use default compare settings; "switch the earlier run off" silently drops ran claims), minors 6–10
Final: Ruling: a claim counts as run only if its claim file exists and has no TODO(LLM) markers at run time; such claims are left out of the runner's CLAIMS and of record_run, so they stay "Not run" — spec anti-goal "unrun claims must say not run" — if wrong: a claim with a stray marker reads Not run until the marker is gone
Final: Ruling: claim ids never reused — _all_ids for claims includes ClaimsFile.ran keys and the claim ids recorded in ScriptMeta.claim; deleting a manual claim also drops its ran record — plan Global Constraint — if wrong: id gaps
Final: Ruling (overrides plan Task 4 "written means overwritten"): when a claim's content changed but its claims/<id>.* was edited since generated, the file is kept (listed in `kept`) and the scoped codegen prompt says "revise it to match the claim as it stands now"; only unedited files are regenerated — spec "the agent rewrites that claim's section" and the anti-goal against losing agent code — if wrong: the agent revises instead of starting from a fresh template
Final: Ruling: GET claims:status accepts rounding/threshold like GET comparison and defaults to report_settings(pkg) when a report exists; record_run snapshots use report_settings(pkg) when present; the renderer passes the step-6 compare settings — one rule across app, report, trace, Zenodo — if wrong: a query parameter
Final: Ruling: the "New extraction" dialog names the earlier run's claims that have run; its primary choice switches off only the earlier run's claims that have not run (per-claim disabled); switching the whole earlier run off stays a secondary, explicitly worded choice — spec: a ran claim is never replaced silently — if wrong: one dialog
Final fix wave: fixer DONE_WITH_CONCERNS (2341f8a backend, 3ba8154 renderer): runner PENDING block runs unimplemented claims once marker-free (agent's own analysis.py runs); codegen success sets claim_hash on revised files; kept out-of-date marker-free file still runs and records as current; finding 5 untested beyond typecheck/build; 3 tests changed to implement files first
Final fix wave: re-review — all 10 addressed, no new Critical/Important (commits d4d72a3..3ba8154)
Final: parked — (b) a succeeded codegen job marks every out-of-date file current even if the agent left it untouched — Ruling: real, deferred; "Run this claim" always runs codegen then shows the result, and a user edit re-flags the file — if wrong: an unrevised file reads as current after a no-op agent run (fix: hash files at job start, mark only changed ones)
Final: parked — (c) a kept, out-of-date, marker-free claim file runs and is recorded as current after re-tick — Ruling: matches the stale rule (run after the edit and the user's re-tick); the UI path always revises first; exposure is a plain step-6 run after a method change — if wrong: a verdict from code written for the earlier method shows as current (fix: leave outdated files out of runnable(), or record claim_hash as the run's content_hash)
Final: minor (deferred): patch_run unticks users even when `disabled` didn't change
Final: minor (deferred): dialog primary button reads "Switch off the 0 claims that have not run" when all ran
Final: minor (deferred): status_words "(unticked since)" also for a result-bearing claim in `running` state
Final: minor (deferred): template switch can leave an OtherMeta claims/C<n>.py kept for a later claim with that id
Final: minor (deferred): deposit card's claims list omits not-testable claims (C3) though the Zenodo description includes them
