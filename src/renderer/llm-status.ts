// What the LLM button's light means, from the detected CLIs and the backend's profiles. Pure: no DOM, no IPC.
import { CLI_LABELS, cliOf, type LlmModel, type LlmModels } from "./api";

export type Profile = { name: string; argv: string[] };
export type State = { models: LlmModels; profiles: Profile[] | null };
export type Level = "ok" | "warn" | "none" | "checking";
export type CliState = { cli: string; installed: boolean; signedIn: boolean; profiles: string[]; models: number; problem?: string };

const SUPPORTED = ["claude", "codex", "opencode"];
export const label = (cli: string) => CLI_LABELS[cli] ?? (cli === "t3" ? "T3 Code" : cli);
export const LOGIN = new Set(["claude", "codex"]); // the CLIs whose login the app can open and check

/** Each supported CLI, plus any other detected one (T3 Code): installed, signed in, run by a profile, models listed. */
export function cliStates({ models, profiles }: State): CliState[] {
  const installed = models.installed ?? [...new Set([...models.available.map((m) => m.cli), ...models.needsLogin])];
  return [...new Set([...SUPPORTED, ...installed])].map((cli) => {
    const isIn = installed.includes(cli as LlmModel["cli"]);
    const signedIn = isIn && !models.needsLogin.includes(cli as LlmModel["cli"]);
    const runs = profiles?.filter((p) => cliOf(p.argv) === cli).map((p) => p.name) ?? [];
    const count = models.available.filter((m) => m.cli === cli).length;
    const problem = !isIn ? "not installed" : !signedIn ? "not signed in" : profiles && !runs.length ? "no profile runs it"
      : cli === "opencode" && !count ? "no models listed" : undefined;
    return { cli, installed: isIn, signedIn, profiles: runs, models: count, problem };
  });
}

/** green: an installed, signed-in CLI that a profile runs; yellow: CLIs installed but none usable; red: none installed. */
export function status(s: State): { level: Level; text: string } {
  const clis = cliStates(s).filter((c) => c.installed);
  if (!clis.length) return { level: "none", text: "No coding CLI found. Install Claude Code, Codex or OpenCode and sign in." };
  const lines = clis.map((c) => `${label(c.cli)}: ${c.problem ? `installed, ${c.problem}` : "ready"}`);
  if (!s.profiles) lines.push("(profiles not checked: the backend isn't available)");
  return { level: clis.some((c) => !c.problem) ? "ok" : "warn", text: lines.join("\n") };
}

