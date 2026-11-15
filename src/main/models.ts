// Which LLM models the user can use, and from which CLI. Each installed agent CLI is asked for its own list:
// `opencode models` lists only providers the user has credentials for (plus OpenCode's free models), `codex debug
// models` the models of the signed-in Codex account, and Claude Code takes its tier aliases once signed in. A model
// belongs to the CLI that listed it; the renderer never routes it to another CLI. The models the user turns off on the
// starting page are kept (settings.llmDisabled) so they don't accidentally spend tokens on them.
import { exec } from "node:child_process";
import { searchPath, which } from "./processes";

export interface LlmModel {
  cli: "claude" | "opencode" | "codex" | "t3";
  /** The model's provider/family, e.g. "unigpt", "opencode", "ollama". */
  provider: string;
  /** Full id passed to the CLI, e.g. "unigpt/DeepSeek-V4-Flash". */
  id: string;
  /** Human-readable name, e.g. "DeepSeek V4 Flash". */
  name: string;
}

const TTL = 5 * 60_000; // the CLI's model list doesn't change often; re-detect at most every few minutes
type Cli = LlmModel["cli"];
/** needsLogin: installed CLIs that report no login. claudeNeedsLogin is kept for older renderer code. */
interface ModelSet { available: LlmModel[]; claudeNeedsLogin: boolean; needsLogin: Cli[] }
let cache: { at: number; value: ModelSet } | undefined;

function run(cmd: string): Promise<string> {
  return new Promise((resolve) => {
    exec(cmd, { timeout: 20_000, maxBuffer: 16 * 1024 * 1024, env: { ...process.env, PATH: searchPath() } }, (_err, stdout) => resolve(stdout ?? ""));
  });
}

/** Whether Codex is signed in, from `codex login status` ("Logged in using …" / "Not logged in"); undefined if it can't tell. */
function codexLoggedIn(): Promise<boolean | undefined> {
  return new Promise((resolve) => {
    exec("codex login status", { timeout: 20_000, env: { ...process.env, PATH: searchPath() } }, (_err, stdout, stderr) => {
      const text = `${stdout ?? ""}\n${stderr ?? ""}`;
      resolve(/not logged in/i.test(text) ? false : /logged in/i.test(text) ? true : undefined);
    });
  });
}

/** Codex's model catalog (`codex debug models`, JSON): the models listed for the signed-in account, as `codex/<slug>`. */
export function parseCodexModels(out: string): LlmModel[] {
  try {
    const raw = JSON.parse(out);
    const list: any[] = Array.isArray(raw) ? raw : Array.isArray(raw?.models) ? raw.models : [];
    return list.filter((m) => m && typeof m.slug === "string" && (m.visibility ?? "list") === "list")
      .map((m) => ({ cli: "codex", provider: "codex", id: `codex/${m.slug}`, name: typeof m.display_name === "string" ? m.display_name : m.slug }));
  } catch {
    return [];
  }
}

/** Lines like `provider/model` (OpenCode lists one id per line). */
function parseModels(out: string): string[] {
  return out
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => /^[^\s]+\/[^\s]+$/.test(l));
}

/** Claude Code has no command that lists its models; it takes these aliases, each the newest model of its tier. */
const CLAUDE_MODELS: [string, string][] = [
  ["opus", "Opus"],
  ["sonnet", "Sonnet"],
  ["haiku", "Haiku"],
];

/** Whether Claude Code is signed in (`claude auth status` prints JSON with `loggedIn`); undefined if it can't tell. */
async function claudeLoggedIn(): Promise<boolean | undefined> {
  try {
    const st = JSON.parse(await run("claude auth status --json"));
    return typeof st?.loggedIn === "boolean" ? st.loggedIn : undefined;
  } catch {
    return undefined;
  }
}

function displayName(short: string): string {
  return short.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function modelOf(id: string, cli: LlmModel["cli"]): LlmModel {
  const i = id.indexOf("/");
  const provider = i > 0 ? id.slice(0, i) : "";
  const short = i > 0 ? id.slice(i + 1) : id;
  return { cli, provider, id, name: displayName(short) };
}

let pending: Promise<ModelSet> | undefined;

/** The models the user can use. Cached for a few minutes; callers asking while a detection runs share it. */
export function detectLlmModels(refresh = false): Promise<ModelSet> {
  if (!refresh && cache && Date.now() - cache.at < TTL) return Promise.resolve(cache.value);
  if (!pending) pending = detect().finally(() => (pending = undefined));
  return pending;
}

async function detect(): Promise<ModelSet> {
  const lists: { cli: Cli; command: string }[] = [];
  if (which("opencode")) lists.push({ cli: "opencode", command: "opencode models" });
  if (which("t3code")) lists.push({ cli: "t3", command: "t3code models" });
  else if (which("t3")) lists.push({ cli: "t3", command: "t3 models" });
  const hasCodex = !!which("codex");
  // The CLIs are asked at the same time: each can take a while (it may check its providers over the network).
  const [outs, claude, codexIn, codexModels] = await Promise.all([
    Promise.all(lists.map(async (c) => ({ cli: c.cli, out: await run(c.command) }))),
    which("claude") ? claudeLoggedIn() : Promise.resolve(undefined),
    hasCodex ? codexLoggedIn() : Promise.resolve(undefined),
    hasCodex ? run("codex debug models") : Promise.resolve(""),
  ]);
  const found: LlmModel[] = [];
  const seen = new Set<string>();
  for (const { cli, out } of outs) {
    for (const id of parseModels(out)) {
      if (seen.has(`${cli}:${id}`)) continue;
      seen.add(`${cli}:${id}`);
      found.push(modelOf(id, cli));
    }
  }
  // Claude Code: its tier aliases once signed in. The `claude/` (and Codex's `codex/`) prefix makes each CLI its own
  // family; the renderer strips it before handing the model to the CLI (cliModel).
  if (claude) for (const [alias, name] of CLAUDE_MODELS) found.push({ cli: "claude", provider: "claude", id: `claude/${alias}`, name });
  if (codexIn !== false) found.push(...parseCodexModels(codexModels)); // unknown login state: still list the catalog
  found.sort((a, b) => a.provider.localeCompare(b.provider) || a.name.localeCompare(b.name));
  const needsLogin: Cli[] = [...(claude === false ? ["claude" as const] : []), ...(codexIn === false ? ["codex" as const] : [])];
  const value: ModelSet = { available: found, claudeNeedsLogin: claude === false, needsLogin };
  cache = { at: Date.now(), value };
  return value;
}
