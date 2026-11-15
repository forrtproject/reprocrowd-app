# Transparent provider configuration and conformance

Status: agreed with Opus 5.5, 14 November 2026. The amendments P1–P6 in [opus-review.md](opus-review.md) are adopted verbatim and supersede conflicting draft wording below. Their acceptance checks and bounded scope are part of this agreement. Refs #15; harness contributes to #1 without claiming complete OS coverage.

## Scope and evidence

The T3 assessment concluded that current T3 CLI is not a suitable sole provider interface: structured extraction, execution permission, event streaming, cancellation and provenance are not exposed together by a stable CLI contract. Do not implement T3/server coupling or replace direct CLIs with another heavyweight runtime.

Today profiles configure argv plus output/event formats. Claude and OpenCode have different envelopes; extraction and coding jobs are distinct operations. Model discovery is partly implemented in Electron. OpenCode's default `--auto` command does not change when `allow_exec` is false, so the UI promises a restriction the command does not encode.

## 1. One workflow-facing interface

Keep provider-specific command/output details in a small transport layer. Research code consumes a normalized extraction result or job/event stream, not vendor event formats. Support the current Claude JSON, Claude stream JSON, OpenCode JSON and plain text protocols. Adding a compatible executable/model should require a profile, not new workflow/UI branches. A genuinely different wire protocol needs one small parser and captured, sanitized conformance fixtures; no arbitrary JavaScript/Python parser hooks in user configuration.

Define an inspectable, versioned profile descriptor for each operation: ID/label, executable/argv template, model/effort, timeout, output/event protocol, structured-output capability, and execution-permission capability. Capabilities are explicit, not inferred from a model name. Include executable availability and actionable setup errors. Runtime parsing validates model output against the requested schema regardless of whether the provider supports native constrained generation.

## 2. Permissions must be honest

Profiles say whether they can enforce editing-only/no-analysis mode. If `allow_exec=false` cannot be represented by an enforceable configured command, refuse before starting the process and show a clear capability message. `--auto` plus an unchanged prompt is not enforcement. A prompt is not a sandbox. Existing customized profiles get a clear unsupported status rather than a silently invented guarantee. For profiles supporting tool lists, require the appropriate placeholder/control for the claimed capability. Prompt instructions must agree with the permission request.

Keep local execution under the user's existing authentication (subscription/API CLI setup). Do not collect provider keys into the app or claim an integration is verified merely because its executable exists.

## 3. Setup without hand-editing TOML

Provide a lean Settings form to add/edit a profile from a supported protocol preset: command arguments as a structured list, operation(s), model, effort, timeout and truthful capabilities. Save through Electron main's trusted local configuration writer and restart cleanly. No backend endpoint accepts arbitrary commands from ordinary API clients; configuration remains a local administrator operation. Keep advanced TOML available and preserve unrelated settings/custom profiles on round trips.

Show configured providers/profiles with availability, supported operations and capability limitations. Model selection must remain tied to a compatible profile; do not silently route every non-Claude model through OpenCode. A model's display name is not a proof that it is supported or validated.

## 4. Transparent tests

Expose a side-effect-free inspection/dry-plan through API/CLI/UI: resolved command without secrets, requested operation, output protocol, declared restrictions, timeout and availability. It starts no CLI inference call.

Add a deterministic conformance runner using fake executables and sanitized event fixtures. Test prompt stdin, cwd, model substitution, valid/invalid structured results, provider-declared errors even at exit 0, malformed events, nonzero exit, timeout, cancellation and descendants, and the distinction between declared capability and tested behavior. Save a versioned JSON conformance report. A replay test does not certify the installed live CLI or prove OS security.

Never auto-run a billable “test provider” call. If an optional live smoke test is later added it must be explicit, cost-aware and isolated; it is outside this slice. Do not implement many speculative vendor integrations. The offline paper harness from the comparison/evidence spec consumes this same interface and records profile/protocol/model/prompt provenance.

## Acceptance

- A non-expert can add a compatible command profile in Settings and see unavailable-executable guidance without editing TOML or making a model call.
- Profile changes preserve unrelated backend settings; ordinary service clients cannot mutate executable configuration.
- Dry-plan/probe reports restrictions and capabilities honestly; OpenCode cannot be launched in falsely advertised no-execution mode.
- Changing profile/model never silently changes provider or loses the selected model.
- One fake provider per supported protocol exercises the shared transport contract, with regression cases for CLI errors/malformed output/cancel/timeout.
- The same conformance runner is callable from tests and CLI, emitting machine-readable results suitable for future benchmark consumers.
- Document how to add a profile versus when a small parser fixture is needed, and what is untested for real providers/platforms.

## Deferred

T3 adoption, ACP migration, new paid API adapters, live provider qualification, centralized credentials, model leaderboards/eligibility gates, marketplace/plugins and remote job servers.
