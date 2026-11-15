# Comparison and execution evidence contract

Status: agreed with Opus 5.5, 14 November 2026. Amended 15 November 2026 by Lukas: a reproduced value at an exact tie matches the printed value rounded in either direction, with reproduced JSON numbers read as decimals (policy `reprocrowd-compare/3`). This supersedes the half-even exact-binary rule below and in the review's Decisions to review 1. The amendments B1–B7 in [opus-review.md](opus-review.md) are adopted verbatim and supersede conflicting draft wording below. Their acceptance checks and bounded scope are part of this agreement. Refs #82 and the comparison/output subset of #12.

## Scope and current behavior

The independent ReproCrowd benchmark is a potential consumer, not the definition of this application's contract. Do not import its gold labels, scoring scheme, exhaustive claim scope, or `close`/variation adjudication rules into this implementation.

Today `TargetFields` stores numeric `expected`, optional `decimals`, comparator and unit. Tables retain cell strings. `compare.py` converts to floats, rounds with Python `round`, and optionally calls a difference within a relative threshold (default 5%) `within`. Scientific notation and lost trailing zeroes are weaknesses. `results_schema.json` freezes targets for a generated script. `outputs/results.json` contains values and an optional plan-step ID. Jobs record command, timestamps, logs and exit code; the report builds a target-to-source graph, but no versioned backend-owned execution evidence record ties together all these bytes. Zenodo recomputes comparison with defaults, which can contradict the report.

## 1. Active workflow and track boundary

Implement text-and-data recreation (Track B). Track A, executing an author's entry point with justified fixes, is represented as unavailable and rejected before execution with a clear error; #45 remains deferred.

New packages explicitly record `track: recreation`. Existing packages without a declared track remain `legacy_unspecified` rather than being silently relabelled as blinded. Templates and prompts describe recreation, omit authors' code from supplied context, and explain that this desktop implementation does not certify isolation/blinding. Selecting a code input for Track B must trigger a clear refusal or explicit exclusion, not silent reuse. Do not claim that an agent's working directory is an OS sandbox.

## 2. Printed numerical representation and comparison

Add an optional exact printed token to numeric targets, preserving trailing zeroes, scientific notation, signs and inequality operators. A shared pure Python numerical module parses a finite decimal value, rounding quantum and bound operator. Store the token as the authority for new extraction/manual edits; retain numeric `expected` for existing consumers, and reject contradictory token/value/operator/precision combinations. Older targets with `decimals` retain that precision. Targets with neither token nor decimals remain readable with an explicit inferred-precision warning; do not invent lost trailing zeroes.

Use decimal arithmetic for reported-precision matches and unrounded bound checks. Define one explicit rounding mode in the versioned comparison policy, initially half-even (matching existing intent), with exact decimal ties. Do not use a relative epsilon to declare a printed-precision match. Scientific notation must derive the correct quantum, including integer-place quanta. Counts and new interval semantics are not added in this slice; intervals may remain the existing pair of numeric targets.

Keep the existing `match/within/deviation/missing/figure/unparsable` statuses. Relative tolerance stays an explicitly recorded exploratory setting, separate from exact reported-precision agreement; no `within` is silently renamed `close`. Do not import the benchmark's threshold decisions. Report exact matches and within-tolerance counts separately. UI, API, CLI, report and deposit all state the policy/version and use the same recorded settings and counts. Changing settings requires regenerating comparison/report/archive before deposit; archived publications are not rewritten.

Manual UI entries and the extraction prompt retain the printed token. Numeric editing must update token/value together and must not lose precision through JavaScript `Number` before submission. Table cells use the same decimal parser, with original cell strings and inequality semantics preserved.

## 3. Execution evidence

Write backend-owned `execution-evidence.json` after each analysis attempt, with schema/version, track, job ID/status/command/times/exit code, frozen-target/plan/input/script/output hashes and log paths/hashes. Preserve prior attempt records under `jobs/`; the current evidence resource identifies the latest attempt. No model-supplied declaration can change the recorded exit status or manufacture a run ID.

For each target expose source extraction/item IDs and the available plan-step links. If an output names a step, validate that it exists and produces that target. A process-level log does not prove that each value was computed by that step: label the level of evidence honestly. Missing/stale evidence is visible, with unknown values represented as unknown; old packages remain inspectable. Evidence never equates a successful process or numerical match with scientific validity.

The comparison includes stable links back to source items and execution evidence. The HTML report resolves source text from extraction IDs and provides usable anchors. Machine-readable evidence is available through the existing backend API and CLI, and included as a registered archive resource. Deposit uses the report's recorded comparison, not a default recomputation; stale result hashes prevent a contradictory preview/deposit.

## 4. Repeatable paper harness

Add a deterministic offline scenario that drives the real API from a small bundled paper fixture through parsing, reviewed targets/actions, template generation, fake-agent code generation, execution of harmless fixture analysis, comparison, evidence, report and archive. Mock scholarly endpoints; never upload or call a paid model. A CLI-facing command emits versioned JSON containing stages, durations, input/contract hashes, outcomes and assertions, plus a readable local artifact/diff suitable for reruns. Each run uses an isolated temporary registry/config/output folder and preserves prior run records rather than overwriting them.

The same endpoint/file contracts serve desktop, CLI, tests and future independent benchmark clients. The first harness is a contract-level API integration test; a thin Electron smoke route can consume it without another workflow implementation. Do not describe this as a live-model quality benchmark or complete UI/OS validation.

## Acceptance

- Exact `.17`, `0.1700`, `1.20e-5`, negative values, decimal ties, bounds including `<=`, and table cells preserve meaning and precision; contradictory values/operators are rejected.
- Exact match and exploratory tolerance remain distinct in every surface, including Zenodo preview.
- Backend evidence hashes detect changed scripts/inputs/results; logs and sources resolve; a timeout is a failed attempt, not an assertion about the study.
- Track B never deliberately supplies authors' code; Track A fails explicitly before job creation. Legacy track/evidence uncertainty remains visible.
- Run the offline paper scenario twice and compare stable substantive results without running real agents, scholarly reproductions or paid services.
- Regenerate schemas/OpenAPI, run appropriate backend tests and TypeScript/build checks. Preserve existing packages and external API compatibility through additive fields and clear errors rather than broad migrations.

## Deferred

Benchmark-specific certificate conversion/ranking, verified blinding, claims hierarchy, arbitrary execution commands, original-code entry-point workflow, moderation/signing, Zenodo record versioning, licensing decisions, and exhaustive numerical extraction.
