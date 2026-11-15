// Pure validation/serialization for the trusted local profile editor. No IPC or process access here.
import { parse, stringify } from "smol-toml";

export type ProfileKind = "llm" | "agent";
export type Profile = {
  argv: string[]; model: string; effort: string; timeout_s: number;
  output?: string; events?: string; tools?: string[]; exec_tools?: string[];
  read_files_argv?: string[]; json_schema_argv?: string[];
  no_exec_argv?: string[]; no_exec_env?: Record<string, string>;
};
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

export function replaceProfile(text: string, kind: ProfileKind, name: string, profile: Profile): string {
  if (!["llm", "agent"].includes(kind) || !/^[\w-]+$/.test(name)) throw new Error("Choose a profile name using letters, numbers, underscores or hyphens.");
  if (!strings(profile.argv) || !profile.argv.length || !profile.argv[0].trim()) throw new Error("A command and an argument list are required.");
  if (typeof profile.model !== "string" || typeof profile.effort !== "string" || !Number.isFinite(profile.timeout_s) || profile.timeout_s <= 0) throw new Error("Model/effort must be text and timeout must be positive.");
  const allowed = new Set(["argv", "model", "effort", "timeout_s", ...(kind === "llm" ? ["output", "read_files_argv", "json_schema_argv"] : ["events", "tools", "exec_tools", "no_exec_argv", "no_exec_env"])]);
  if (Object.keys(profile).some((key) => !allowed.has(key))) throw new Error("Unrecognized profile field.");
  const protocols = kind === "llm" ? ["text", "claude-json", "opencode-json", "codex-json"] : ["text", "claude-stream-json", "opencode-json", "codex-json"];
  if (!protocols.includes(kind === "llm" ? profile.output ?? "" : profile.events ?? "")) throw new Error("Choose a supported output protocol.");
  for (const key of ["tools", "exec_tools", "read_files_argv", "json_schema_argv", "no_exec_argv"] as const) {
    if (profile[key] !== undefined && !strings(profile[key])) throw new Error(`${key} must be a list of text arguments.`);
  }
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
  return stringify(data);
}

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
  io.write(next);
  try {
    await io.restart();
    return next;
  } catch (e) {
    io.write(previous);
    const again = await io.restart().then(() => undefined, (err: unknown) => err as Error);
    throw new Error(`The profile was not saved: ${(e as Error).message}. The previous configuration was restored`
      + (again ? `, but the backend did not start with it either: ${again.message}` : " and the backend restarted with it."));
  }
}
