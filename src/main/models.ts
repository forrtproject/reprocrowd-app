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

/** Claude Code's model list has one model id per line (no `provider/` prefix), e.g. `claude-sonnet-4-5`. It needs
 * a login; "Not logged in" yields nothing. We keep the CLI's own id and label it under the `claude` provider. */
function parseClaudeModels(out: string): string[] {
  const ids = new Set<string>();
  for (const l of out.split(/\r?\n/)) {
    const m = l.trim().match(/^(claude-[a-z0-9][a-z0-9.-]*)/i);
    if (m) ids.add(m[1].toLowerCase());
  }
  return [...ids];
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

export async function detectLlmModels(refresh = false): Promise<ModelSet> {
  if (!refresh && cache && Date.now() - cache.at < TTL) return cache.value;
  const available = configuredProviders();
  available.add("opencode"); // OpenCode's free built-in models need no key
  const found: LlmModel[] = [];
  const seen = new Set<string>();
  let claudeNeedsLogin = false;
  const clis: string[] = [];
  if (which("opencode")) clis.push("opencode models");
  if (which("t3code")) clis.push("t3code models");
  else if (which("t3")) clis.push("t3 models");
  for (const cmd of clis) {
    for (const id of parseModels(await run(cmd))) {
      const m = modelOf(id);
      if (!available.has(m.provider)) continue;
      if (seen.has(m.id)) continue;
      seen.add(m.id);
      found.push(m);
    }
  }
  // Claude Code (separate CLI from OpenCode). Listed only when installed and logged in; modelOf adds the `claude/`
  // prefix so it appears as its own family and its own profile can use it. Not logged in → offer a login instead.
  if (which("claude")) {
    const out = await run("claude model list");
    claudeNeedsLogin = /not logged in|login|authenticate/i.test(out) && !parseClaudeModels(out).length;
    for (const id of parseClaudeModels(out)) {
      const m = modelOf(`claude/${id}`);
      if (seen.has(m.id)) continue;
      seen.add(m.id);
      found.push(m);
    }
  }
  found.sort((a, b) => a.provider.localeCompare(b.provider) || a.name.localeCompare(b.name));
  const value: ModelSet = { available: found, claudeNeedsLogin };
  cache = { at: Date.now(), value };
  return value;
}
