// Pure validation/serialization for the trusted local profile editor. No IPC or process access here.
import { isDeepStrictEqual } from "node:util";
import { parse, stringify } from "smol-toml";

export type ProfileKind = "llm" | "agent";
export type Profile = {
  argv: string[]; model: string; effort: string; timeout_s: number;
  output?: string; events?: string; tools?: string[]; exec_tools?: string[];
  read_files_argv?: string[]; json_schema_argv?: string[];
  no_exec_argv?: string[]; no_exec_env?: Record<string, string>; env_pass?: string[];
};
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

export function replaceProfile(text: string, kind: ProfileKind, name: string, profile: Profile): string {
  if (!["llm", "agent"].includes(kind) || !/^[\w-]+$/.test(name)) throw new Error("Choose a profile name using letters, numbers, underscores or hyphens.");
  if (!strings(profile.argv) || !profile.argv.length || !profile.argv[0].trim()) throw new Error("A command and an argument list are required.");
  if (typeof profile.model !== "string" || typeof profile.effort !== "string" || !Number.isFinite(profile.timeout_s) || profile.timeout_s <= 0) throw new Error("Model/effort must be text and timeout must be positive.");
  const allowed = new Set(["argv", "model", "effort", "timeout_s", "env_pass", ...(kind === "llm" ? ["output", "read_files_argv", "json_schema_argv"] : ["events", "tools", "exec_tools", "no_exec_argv", "no_exec_env"])]);
  if (Object.keys(profile).some((key) => !allowed.has(key))) throw new Error("Unrecognized profile field.");
  const protocols = kind === "llm" ? ["text", "claude-json", "opencode-json", "codex-json"] : ["text", "claude-stream-json", "opencode-json", "codex-json"];
  if (!protocols.includes(kind === "llm" ? profile.output ?? "" : profile.events ?? "")) throw new Error("Choose a supported output protocol.");
  for (const key of ["tools", "exec_tools", "read_files_argv", "json_schema_argv", "no_exec_argv", "env_pass"] as const) {
    if (profile[key] !== undefined && !strings(profile[key])) throw new Error(`${key} must be a list of text arguments.`);
  }
  for (const n of profile.env_pass ?? []) if (!/^[A-Za-z_][A-Za-z0-9_]*\*?$/.test(n)) throw new Error(`“${n}” isn't an environment variable name (or a PREFIX_* group).`);
  if (profile.no_exec_env !== undefined) {
    const env = profile.no_exec_env;
    if (typeof env !== "object" || env === null || Array.isArray(env)) throw new Error("The no-analysis environment must be NAME=value lines.");
    for (const [k, v] of Object.entries(env)) {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k) || typeof v !== "string") throw new Error(`Invalid environment variable ${k}.`);
      if (/KEY|TOKEN|SECRET|PASSW|AUTH|CREDENTIAL|COOKIE|SESSION/i.test(k)) throw new Error(`${k} looks like a credential; keep credentials in the CLI's own login, not in a profile.`);
    }
  }
  const data = parse(text) as Record<string, any>;
  data[kind] ??= {};
  data[kind].profiles ??= {};
  data[kind].profiles[name] = profile;
  // Edit only this profile's [kind.profiles.name] table (and its sub-tables, e.g. no_exec_env) so
  // comments elsewhere survive. Keep the splice only if it parses to exactly the intended
  // configuration; otherwise rewrite the whole file.
  const path = `${kind}.profiles.${name}`;
  const spliced = spliceProfile(text, path, stringify({ [kind]: { profiles: { [name]: profile } } }));
  try {
    if (spliced !== null && isDeepStrictEqual(parse(spliced), parse(stringify(data)))) return spliced;
  } catch { /* fall back */ }
  return stringify(data);
}

const isComment = (line: string) => /^\s*(#.*)?$/.test(line);
/** The dotted key of a [table] header line, without whitespace (TOML allows [ a . b ]); null otherwise. */
const tableKey = (line: string) => /^\s*\[[^\[]/.test(line) ? line.replace(/#.*$/, "").replace(/\s+/g, "").slice(1, -1) : /^\s*\[/.test(line) ? "" : null;

/** Replace one table and its sub-tables with `block` (which starts with that table's header),
 * keeping the comments just after the old header and before the next unrelated table. */
function spliceProfile(text: string, path: string, block: string): string | null {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const blockLines = block.trimEnd().split("\n");
  const own = blockLines.findIndex((line) => tableKey(line) === path);
  if (own < 0) return null;
  const [header, ...content] = blockLines.slice(own);
  const start = lines.findIndex((line) => tableKey(line) === path);
  if (start < 0) {
    const kept = text.replace(/\s*$/, "");
    return `${kept}${kept ? eol + eol : ""}${[header, ...content].join(eol)}${eol}`;
  }
  const inside = (key: string | null) => key !== null && key.startsWith(path + ".");
  let end = lines.findIndex((line, i) => i > start && tableKey(line) !== null && !inside(tableKey(line)));
  if (end < 0) end = lines.length;
  let first = start + 1;
  while (first < end && isComment(lines[first]) && lines[first].trim()) first++;
  let last = end;
  while (last > first && isComment(lines[last - 1])) last--;
  return [...lines.slice(0, first), ...content, ...lines.slice(last)].join(eol);
}

/** Make `name` the default profile of its task ([llm] text parsing, [agent] code generation). Only the
 * default_profile line changes; if that can't be done in place, the whole file is rewritten. */
export function setDefaultProfile(text: string, kind: ProfileKind, name: string): string {
  if (!["llm", "agent"].includes(kind) || !/^[\w-]+$/.test(name)) throw new Error("Unknown profile.");
  const data = parse(text) as Record<string, any>;
  if (!data[kind]?.profiles?.[name]) throw new Error(`There is no ${kind === "llm" ? "text-parsing" : "code-generation"} profile named “${name}”.`);
  data[kind].default_profile = name;
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const start = lines.findIndex((line) => tableKey(line) === kind);
  const setting = `default_profile = ${JSON.stringify(name)}`; // names are [A-Za-z0-9_-]: a valid TOML string as well
  let spliced: string | null = null;
  if (start >= 0) {
    let end = lines.findIndex((line, i) => i > start && tableKey(line) !== null);
    if (end < 0) end = lines.length;
    const at = lines.findIndex((line, i) => i > start && i < end && /^\s*default_profile\s*=/.test(line));
    if (at >= 0) lines[at] = setting;
    else lines.splice(start + 1, 0, setting);
    spliced = lines.join(eol);
  }
  try {
    if (spliced !== null && isDeepStrictEqual(parse(spliced), parse(stringify(data)))) return spliced;
  } catch { /* fall back */ }
  return stringify(data);
}

/** Remove one profile's table (and its sub-tables), keeping everything else; null if it can't be done in place. */
function removeProfile(text: string, kind: ProfileKind, name: string): string | null {
  const path = `${kind}.profiles.${name}`;
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const start = lines.findIndex((line) => tableKey(line) === path);
  if (start < 0) return null;
  const inside = (key: string | null) => key !== null && key.startsWith(path + ".");
  let end = lines.findIndex((line, i) => i > start && tableKey(line) !== null && !inside(tableKey(line)));
  if (end < 0) end = lines.length;
  while (end > start && end <= lines.length && end - 1 > start && isComment(lines[end - 1]) && lines[end - 1].trim()) end--; // the next table's comments stay
  return [...lines.slice(0, start), ...lines.slice(end)].join(eol).replace(/(\r?\n){3,}/g, eol + eol);
}

/** The Claude Code profiles earlier versions of the app shipped, unedited: same command, model, effort and timeout. */
const OLD_SHIPPED: { kind: ProfileKind; name: string; models: string[]; effort: string; timeout_s: number; argv: string[] }[] = [
  { kind: "llm", name: "claude", models: ["sonnet", "claude-sonnet-5-5"], effort: "medium", timeout_s: 1800, argv: ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"] },
  { kind: "llm", name: "claude-fast", models: ["haiku", "claude-haiku-5-5"], effort: "low", timeout_s: 600, argv: ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"] },
  { kind: "agent", name: "claude", models: ["sonnet", "claude-sonnet-5-5"], effort: "high", timeout_s: 7200,
    argv: ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"] },
];

/** Replace the Claude Code profiles an earlier version shipped, if still unedited, with the current ones (from
 * `shipped`, the app's starting configuration); a default that named a replaced profile moves to the current
 * default. Edited profiles and everything else stay. Returns the new text, or null if nothing needed upgrading. */
export function upgradeShippedProfiles(text: string, shipped: string): string | null {
  const data = parse(text) as Record<string, any>, now = parse(shipped) as Record<string, any>;
  const old = OLD_SHIPPED.filter((o) => {
    const p = data[o.kind]?.profiles?.[o.name];
    return p && isDeepStrictEqual(p.argv, o.argv) && o.models.includes(p.model) && p.effort === o.effort && (p.timeout_s ?? o.timeout_s) === o.timeout_s;
  });
  if (!old.length) return null;
  let next = text;
  for (const o of old) next = removeProfile(next, o.kind, o.name) ?? (() => { const d = parse(next) as Record<string, any>; delete d[o.kind].profiles[o.name]; return stringify(d); })();
  for (const kind of new Set(old.map((o) => o.kind))) {
    for (const [name, profile] of Object.entries(now[kind].profiles as Record<string, Profile>)) {
      if (cliOf(profile.argv) === "claude" && !(parse(next) as Record<string, any>)[kind]?.profiles?.[name]) next = replaceProfile(next, kind, name, profile);
    }
    const current = (parse(next) as Record<string, any>)[kind];
    if (!current.profiles?.[current.default_profile]) next = setDefaultProfile(next, kind, now[kind].default_profile);
  }
  return next;
}
const cliOf = (argv: string[]) => (argv[0] ?? "").split(/[\\/]/).pop()!.replace(/\.(cmd|exe|ps1)$/i, "");

export function configuredProfiles(text: string): Record<ProfileKind, Record<string, Profile>> {
  const data = parse(text) as Record<string, any>;
  const defaults = (kind: ProfileKind) => ({ model: "", effort: "", timeout_s: kind === "agent" ? 7200 : 1800,
    ...(kind === "agent" ? { events: "claude-stream-json", tools: ["Read", "Write", "Edit", "Glob", "Grep"], exec_tools: ["Bash(Rscript:*)", "Bash(R:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(snakemake:*)", "Bash(pip:*)", "Bash(ls:*)"], no_exec_argv: [], no_exec_env: {} }
      : { output: "text", read_files_argv: [], json_schema_argv: [] }) });
  const group = (kind: ProfileKind): Record<string, Profile> => Object.fromEntries(
    Object.entries(data[kind]?.profiles ?? {}).map(([name, profile]) => [name, { ...defaults(kind), ...(profile as Profile) }])
  );
  return { llm: group("llm"), agent: group("agent") };
}

/** The local settings transaction; injected I/O keeps rollback testable without Electron. */
export async function applyProfileChange(previous: string, next: string, io: {
  backup(text: string): void; write(text: string): void; restart(): Promise<unknown>;
}): Promise<string> {
  io.backup(previous);
  try {
    io.write(next); // inside try: a failed or partial write is rolled back too
    await io.restart();
    return next;
  } catch (e) {
    const reason = `The profile was not saved: ${(e as Error).message}.`;
    try {
      io.write(previous);
    } catch (err) {
      throw new Error(`${reason} Restoring the previous configuration failed too (${(err as Error).message}); check backend.toml in the raw editor.`);
    }
    const again = await io.restart().then(() => undefined, (err: unknown) => err as Error);
    throw new Error(`${reason} The previous configuration was restored`
      + (again ? `, but the backend did not start with it either: ${again.message}` : " and the backend restarted with it."));
  }
}
