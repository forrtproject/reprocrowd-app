// What the LLM button's light means, from the detected CLIs and the backend's profiles. Pure: no DOM, no IPC.
import { CLI_LABELS, cliOf, type LlmModel, type LlmModels } from "./api";

/** A configured profile as the backend lists it. kind: llm = text parsing, agent = code generation; default: the
 * profile that task uses unless a step picks another; available: the backend found argv[0]. */
export type Profile = { name: string; argv: string[]; available?: boolean; kind?: "llm" | "agent"; default?: boolean; model?: string; effort?: string };
export type State = { models: LlmModels; profiles: Profile[] | null };
export type Level = "ok" | "warn" | "none" | "checking";
export type CliState = { cli: string; installed: boolean; signedIn: boolean; loginUnknown: boolean; profiles: string[]; models: number; problem?: string };

const SUPPORTED = ["claude", "codex", "opencode"];
export const label = (cli: string) => CLI_LABELS[cli] ?? (cli === "t3" ? "T3 Code" : cli);
export const LOGIN = new Set(["claude", "codex"]); // the CLIs whose login the app can open and check

/** Each supported CLI, plus any other detected one (T3 Code): installed, signed in, run by a profile, models listed. */
export function cliStates({ models, profiles }: State): CliState[] {
  const installed = models.installed ?? [...new Set([...models.available.map((m) => m.cli), ...models.needsLogin])];
  return [...new Set([...SUPPORTED, ...installed])].map((cli) => {
    const isIn = installed.includes(cli as LlmModel["cli"]);
    const signedIn = isIn && !models.needsLogin.includes(cli as LlmModel["cli"]);
    // A login probe that couldn't tell (timeout, unexpected output) doesn't count as signed in for the light.
    const unconfirmed = signedIn && (models.loginUnknown ?? []).includes(cli as LlmModel["cli"]);
    // A profile whose command the backend can't find doesn't run the CLI, even if another copy is on PATH.
    const runs = [...new Set(profiles?.filter((p) => p.available !== false && cliOf(p.argv) === cli).map((p) => p.name) ?? [])];
    const count = models.available.filter((m) => m.cli === cli).length;
    const problem = !isIn ? "not installed" : !signedIn ? "not signed in" : unconfirmed ? "sign-in not confirmed"
      : profiles && !runs.length ? "no profile runs it"
      : cli === "opencode" && !count ? "no models listed" : undefined;
    return { cli, installed: isIn, signedIn, loginUnknown: unconfirmed, profiles: runs, models: count, problem };
  });
}

/** green: an installed, signed-in CLI that a profile runs; yellow: CLIs installed but none usable, or the profiles
 * couldn't be listed (nothing confirms a profile runs them, and without the backend no AI step runs); red: none installed. */
export function status(s: State): { level: Level; text: string } {
  const clis = cliStates(s).filter((c) => c.installed);
  if (!clis.length) return { level: "none", text: "No coding CLI found. Install Claude Code, Codex or OpenCode and sign in." };
  const lines = clis.map((c) => `${label(c.cli)}: ${c.problem ? `installed, ${c.problem}` : "ready"}`);
  if (!s.profiles) lines.push("(profiles not checked: the backend isn't available)");
  return { level: s.profiles && clis.some((c) => !c.problem) ? "ok" : "warn", text: lines.join("\n") };
}


/** One row per profile name a CLI runs: its text-parsing (llm) and code-generation (agent) profiles of that name. */
export type ProfileRow = { name: string; llm?: Profile; agent?: Profile };
export function profileRows(profiles: Profile[], cli: string): ProfileRow[] {
  const rows = new Map<string, ProfileRow>();
  for (const p of profiles) {
    if (cliOf(p.argv) !== cli || !p.kind) continue;
    const row = rows.get(p.name) ?? { name: p.name };
    row[p.kind] = p;
    rows.set(p.name, row);
  }
  return [...rows.values()];
}

/** A name for a new profile of this CLI that neither task uses yet: claude, claude-2, … */
export function freeName(profiles: Profile[], cli: string): string {
  const taken = new Set(profiles.map((p) => p.name));
  for (let i = 1; ; i++) if (!taken.has(i === 1 ? cli : `${cli}-${i}`)) return i === 1 ? cli : `${cli}-${i}`;
}
