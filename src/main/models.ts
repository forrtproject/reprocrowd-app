// Which LLM models the user can use. We list the models the installed agent CLIs (OpenCode, T3 Code) offer, but only
// from providers the user actually has access to: the ones configured in the agent's config file (where the user puts
// their own API keys / local servers). Free built-in models (the OpenCode "opencode" provider) are always offered.
// The models the user turns off on the starting page are kept (settings.llmDisabled) so they don't accidentally spend
// tokens on them.
import { exec } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { searchPath, which } from "./processes";

export interface LlmModel {
  /** The model's provider/family, e.g. "unigpt", "opencode", "ollama". */
  provider: string;
  /** Full id passed to the CLI, e.g. "unigpt/DeepSeek-V4-Flash". */
  id: string;
  /** Human-readable name, e.g. "DeepSeek V4 Flash". */
  name: string;
}

const TTL = 5 * 60_000; // the CLI's model list doesn't change often; re-detect at most every few minutes
interface ModelSet { available: LlmModel[]; claudeNeedsLogin: boolean }
let cache: { at: number; value: ModelSet } | undefined;

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

/** Providers the user has configured in their agent's config (where they put their own API keys / local servers). */
function configuredProviders(): Set<string> {
  const providers = new Set<string>();
  const read = (file: string) => {
    try {
      const cfg = JSON.parse(fs.readFileSync(file, "utf8"));
      if (cfg && typeof cfg.provider === "object" && cfg.provider) for (const name of Object.keys(cfg.provider)) providers.add(name);
    } catch {
      /* unreadable or missing */
    }
  };
  const env = process.env.OPENCODE_CONFIG;
  if (env && env.trim() && !env.trim().startsWith("{")) read(env.trim());
  read(path.join(os.homedir(), ".config", "opencode", "opencode.json"));
  read(path.join(os.homedir(), ".opencode.json"));
  return providers;
}

function displayName(short: string): string {
  return short.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function modelOf(id: string): LlmModel {
  const i = id.indexOf("/");
  const provider = i > 0 ? id.slice(0, i) : "";
  const short = i > 0 ? id.slice(i + 1) : id;
  return { provider, id, name: displayName(short) };
}

let pending: Promise<ModelSet> | undefined;

/** The models the user can use. Cached for a few minutes; callers asking while a detection runs share it. */
export function detectLlmModels(refresh = false): Promise<ModelSet> {
  if (!refresh && cache && Date.now() - cache.at < TTL) return Promise.resolve(cache.value);
  if (!pending) pending = detect().finally(() => (pending = undefined));
  return pending;
}

async function detect(): Promise<ModelSet> {
  const available = configuredProviders();
  available.add("opencode"); // OpenCode's free built-in models need no key
  const clis: string[] = [];
  if (which("opencode")) clis.push("opencode models");
  if (which("t3code")) clis.push("t3code models");
  else if (which("t3")) clis.push("t3 models");
  // The CLIs are asked at the same time: each can take a while (it may check its providers over the network).
  const [lists, claude] = await Promise.all([Promise.all(clis.map(run)), which("claude") ? claudeLoggedIn() : Promise.resolve(undefined)]);
  const found: LlmModel[] = [];
  const seen = new Set<string>();
  for (const out of lists) {
    for (const id of parseModels(out)) {
      const m = modelOf(id);
      if (!available.has(m.provider) || seen.has(m.id)) continue;
      seen.add(m.id);
      found.push(m);
    }
  }
  // Claude Code (a separate CLI from OpenCode): its models once it's signed in, else a login is offered. The `claude/`
  // prefix makes it its own family; the renderer strips it before handing the model to the CLI (cliModel).
  if (claude) for (const [alias, name] of CLAUDE_MODELS) found.push({ provider: "claude", id: `claude/${alias}`, name });
  found.sort((a, b) => a.provider.localeCompare(b.provider) || a.name.localeCompare(b.name));
  const value: ModelSet = { available: found, claudeNeedsLogin: claude === false };
  cache = { at: Date.now(), value };
  return value;
}
