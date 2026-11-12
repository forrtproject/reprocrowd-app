// Which LLM models the user can use: the ones the installed agent CLIs (OpenCode, T3 Code) offer. The app runs
// `opencode models` / `t3code models` and surfaces the result, so generation works with whatever the user has. The
// models the user turns off on the starting page are kept (settings.llmDisabled) so they don't accidentally spend
// tokens on them.
import { exec } from "node:child_process";
import { searchPath, which } from "./processes";

export interface LlmModel {
  provider: string;
  id: string;
}

const TTL = 5 * 60_000; // the CLI's model list doesn't change often; re-detect at most every few minutes
let cache: { at: number; value: LlmModel[] } | undefined;

function run(cmd: string): Promise<string> {
  return new Promise((resolve) => {
    exec(cmd, { timeout: 20_000, env: { ...process.env, PATH: searchPath() } }, (_err, stdout) => resolve(stdout ?? ""));
  });
}

/** Lines like `provider/model` (OpenCode lists one id per line). */
function parseModels(out: string): string[] {
  return out
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => /^[^\s]+\/[^\s]+$/.test(l));
}

export async function detectLlmModels(): Promise<LlmModel[]> {
  if (cache && Date.now() - cache.at < TTL) return cache.value;
  const found: LlmModel[] = [];
  const providers: [string, string][] = [];
  if (which("opencode")) providers.push(["opencode", "opencode models"]);
  if (which("t3code")) providers.push(["t3code", "t3code models"]);
  else if (which("t3")) providers.push(["t3code", "t3 models"]);
  for (const [provider, cmd] of providers) {
    for (const id of parseModels(await run(cmd))) found.push({ provider, id });
  }
  cache = { at: Date.now(), value: found };
  return found;
}
