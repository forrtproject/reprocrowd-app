// The app's own ReproCrowd backend (backend/, `python -m reprocrowd serve`), started on demand on a free local
// port with a random bearer token, and stopped when the app quits. Nothing else can use it without the token.
//
// Which backend runs, in order:
//   1. Settings → Advanced → start command, if set ({host}, {port} filled in)
//   2. the bundled Python runtime with reprocrowd installed (resources/backend, made by scripts/bundle-backend.mjs)
//   3. in a development checkout: `uv run --project backend python -m reprocrowd serve`
//
// Its config (REPROCROWD_CONFIG) is written at every start from the user's part (userData/backend.toml: LLM and
// agent profiles, OpenAlex, run limits; editable in Settings) and the app's part ([server], no package
// database, the Zenodo choices from Settings).
import { app } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { parse, stringify } from "smol-toml";
import { searchPath, splitCommand, which } from "./processes";
import { loadSettings, userData } from "./settings";

const READY_TIMEOUT = 180_000; // `uv run` may build the environment first
const RETRY_AFTER = 30_000;
const LOG_LINES = 400;

export type BackendState = "stopped" | "starting" | "running" | "failed";

export interface BackendStatus {
  state: BackendState;
  url: string | null;
  command: string | null;
  kind: "custom" | "bundled" | "development" | null;
  pid: number | null;
  message: string | null;
  configFile: string;
  userConfigFile: string;
}

interface Running {
  proc: ChildProcess;
  base: string;
  token: string;
  argv: string[];
  kind: BackendStatus["kind"];
  ready: Promise<void>;
  exited?: number | null;
}

let running: Running | undefined;
let lastFailure: { at: number; message: string } | undefined;
let starting: Promise<Running> | undefined;
// The process start() spawned and is waiting on, before it answers; stop() ends it too (quitting during startup).
let launching: Running | undefined;
// Bumped by stop(): a start() begun before it gives up, and doesn't spawn or keep polling.
let generation = 0;
const log: string[] = [];

export const userConfigFile = () => path.join(userData(), "backend.toml");
const configFile = () => path.join(userData(), "backend", "config.toml");
const pidFile = () => path.join(userData(), "backend", "pid");

/** A backend a previous run left behind (the app was killed): stop it, if it is still that process. Only where the
 * command line can be checked (Linux), so an unrelated process that got the same pid is never touched. */
function stopStale() {
  try {
    const pid = Number(fs.readFileSync(pidFile(), "utf8").trim());
    fs.rmSync(pidFile(), { force: true });
    if (!pid || process.platform !== "linux") return;
    const cmd = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0").join(" ");
    if (cmd.includes("reprocrowd") && cmd.includes(" serve ")) {
      process.kill(pid, "SIGTERM");
      remember(`(stopped a backend left over from an earlier run, pid ${pid})`);
    }
  } catch {
    /* none, or already gone */
  }
}

export const DEFAULT_USER_CONFIG = `# ReproCrowd backend configuration (the part you can change). The app adds [server], and the Zenodo
# choices from Settings, when it starts the backend. Saving here restarts the backend.

[openalex]
api_key = ""               # needed for OpenAlex cached content (PDF, GROBID TEI)
mailto = ""                # your e-mail for OpenAlex's polite pool (optional)

[downloads]
max_download_mb = 200
max_upload_mb = 500

[zenodo]
license = "cc-by-4.0"

# Runs of the master script (the template's command, in your environment: R, Python, …).
[run]
timeout_s = 7200

[llm]
default_profile = "claude"

# {model} and {effort} are substituted; the prompt is sent on stdin, in the package folder.
[llm.profiles.claude]
argv = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"]
read_files_argv = ["--allowedTools", "Read"]
json_schema_argv = ["--json-schema", "{schema}"]
output = "claude-json"
model = "sonnet"
effort = "medium"
timeout_s = 1800

[llm.profiles.claude-fast]
argv = ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"]
read_files_argv = ["--allowedTools", "Read"]
json_schema_argv = ["--json-schema", "{schema}"]
output = "claude-json"
model = "haiku"
effort = "low"
timeout_s = 600

# OpenCode (https://opencode.ai), with the models you have there. The prompt goes to stdin; output =
# "opencode-json" reads its "run --format json" event stream. model = "" uses OpenCode's default; pick one of your
# models on the starting page or in the "Generate with LLM" dialog.
[llm.profiles.opencode]
argv = ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}"]
output = "opencode-json"
model = ""
effort = ""
timeout_s = 1800

# Coding agents for code generation. {tools}: the allowed tools, plus exec_tools when running the analysis is allowed.
[agent]
default_profile = "claude"

[agent.profiles.claude]
argv = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
        "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"]
tools = ["Read", "Write", "Edit", "Glob", "Grep"]
exec_tools = ["Bash(Rscript:*)", "Bash(R:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(snakemake:*)", "Bash(pip:*)", "Bash(ls:*)"]
model = "sonnet"
effort = "high"
timeout_s = 7200
events = "claude-stream-json"

# OpenCode as a headless coding agent: --auto approves the tool calls it makes (Read/Write/Edit/Glob/Grep/Bash).
# model = "" uses the CLI's default; pick one of your models on the starting page.
[agent.profiles.opencode]
argv = ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}", "--auto"]
model = ""
effort = ""
timeout_s = 7200
events = "opencode-json"
`;

export function readUserConfig(): string {
  const f = userConfigFile();
  if (!fs.existsSync(f)) {
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, DEFAULT_USER_CONFIG);
  }
  return fs.readFileSync(f, "utf8");
}

/** Check and save the user's part of the config; the caller restarts the backend. */
export function writeUserConfig(text: string) {
  const user = parse(text) as Record<string, any>;
  for (const k of ["server", "database"]) if (k in user) throw new Error(`[${k}] is set by the app; remove it from the backend configuration.`);
  fs.writeFileSync(userConfigFile(), text);
}

function remember(line: string) {
  log.push(line);
  if (log.length > LOG_LINES) log.splice(0, log.length - LOG_LINES);
}

export const logTail = (n = 40) => log.slice(-n).join("\n");

const bundleDir = () => (app.isPackaged ? path.join(process.resourcesPath, "backend") : path.join(app.getAppPath(), "build", "backend"));
const bundledPython = () => path.join(bundleDir(), "python", process.platform === "win32" ? "python.exe" : path.join("bin", "python3"));
const devProject = () => path.join(app.getAppPath(), "backend");

function launch(host: string, port: number): { argv: string[]; kind: BackendStatus["kind"]; env: Record<string, string> } {
  const custom = loadSettings().backendCmd.trim();
  if (custom) {
    const argv = splitCommand(custom).map((w) => w.replaceAll("{host}", host).replaceAll("{port}", String(port)));
    return { argv, kind: "custom", env: {} };
  }
  const serve = ["-m", "reprocrowd", "serve", "--host", host, "--port", String(port)];
  if (fs.existsSync(bundledPython())) return { argv: [bundledPython(), ...serve], kind: "bundled", env: { PYTHONNOUSERSITE: "1" } };
  if (!app.isPackaged && fs.existsSync(path.join(devProject(), "pyproject.toml"))) {
    const uv = which("uv");
    if (uv) return { argv: [uv, "run", "--project", devProject(), "python", ...serve], kind: "development", env: {} };
  }
  throw new Error("No ReproCrowd backend found: this build has no bundled backend, and there is no backend/ source with uv to run it. Set a start command in Settings → Advanced.");
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = (srv.address() as net.AddressInfo).port;
      srv.close(() => resolve(port));
    });
  });
}

function writeConfig(port: number, token: string) {
  let user: Record<string, any>;
  try {
    user = parse(readUserConfig()) as Record<string, any>;
  } catch (e) {
    throw new Error(`The backend configuration (Settings → Backend) isn't valid TOML: ${(e as Error).message}`);
  }
  const s = loadSettings();
  const llm = (user.llm ?? {}) as Record<string, any>;
  const profiles = { ...((llm.profiles ?? {}) as Record<string, any>) };
  // The OpenCode profile is the model-capable profile the "LLM models" feature relies on; keep it present even if the
  // user's config was written by an older version or edited to drop it.
  if (!profiles.opencode) profiles.opencode = { argv: ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}"], output: "opencode-json", model: "", effort: "", timeout_s: 1800 };
  const agent = (user.agent ?? {}) as Record<string, any>;
  const agentProfiles = { ...((agent.profiles ?? {}) as Record<string, any>) };
  // Same for the headless coding agent: keep an OpenCode profile so the agent can use the user's OpenCode models.
  if (!agentProfiles.opencode) agentProfiles.opencode = { argv: ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}", "--auto"], model: "", effort: "", timeout_s: 7200, events: "opencode-json" };
  const cfg = {
    ...user,
    llm: { ...llm, profiles },
    agent: { ...agent, profiles: agentProfiles },
    server: { host: "127.0.0.1", port, token, registry: path.join(userData(), "registry.json"), allowed_roots: [] },
    database: { url: "" },
    zenodo: { ...(user.zenodo ?? {}), sandbox: s.zenodo.sandbox, community: s.zenodo.community, token: "" },
  };
  fs.mkdirSync(path.dirname(configFile()), { recursive: true });
  fs.writeFileSync(configFile(), stringify(cfg), { mode: 0o600 });
}

async function healthy(base: string, token: string): Promise<boolean> {
  try {
    const r = await fetch(`${base}/v1/health`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(2000) });
    return r.ok;
  } catch {
    return false;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function drain(stream: NodeJS.ReadableStream | null) {
  if (!stream) return;
  let rest = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk: string) => {
    const lines = (rest + chunk).split(/\r?\n/);
    rest = lines.pop()!;
    lines.filter((l) => l.trim()).forEach(remember);
  });
  stream.on("end", () => rest.trim() && remember(rest));
}

class Cancelled extends Error {}

async function start(): Promise<Running> {
  const gen = generation;
  const cancelled = () => gen !== generation;
  const host = "127.0.0.1";
  const port = await freePort();
  if (cancelled()) throw new Cancelled("The backend was stopped while it was starting.");
  const token = crypto.randomBytes(24).toString("base64url");
  stopStale();
  writeConfig(port, token);
  const { argv, kind, env } = launch(host, port);
  const exe = which(argv[0]);
  if (!exe) throw new Error(`Can't start the backend: “${argv[0]}” isn't installed, or not on the PATH.`);
  log.length = 0;
  remember(`$ ${argv.join(" ")}`);
  const proc = spawn(exe, argv.slice(1), {
    cwd: userData(),
    env: { ...process.env, ...env, PATH: searchPath(), REPROCROWD_CONFIG: configFile(), PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  if (proc.pid) fs.writeFileSync(pidFile(), String(proc.pid));
  const base = `http://${host}:${port}`;
  const me: Running = { proc, base, token, argv, kind, ready: Promise.resolve() };
  launching = me;
  drain(proc.stdout);
  drain(proc.stderr);
  const spawnError = new Promise<Error>((resolve) => proc.on("error", resolve));
  proc.on("exit", (code, signal) => {
    me.exited = code;
    if (fs.existsSync(pidFile()) && fs.readFileSync(pidFile(), "utf8").trim() === String(proc.pid)) fs.rmSync(pidFile(), { force: true });
    remember(`(exited with ${code !== null ? `code ${code}` : `signal ${signal}`})`);
    if (running === me) running = undefined;
    if (launching === me) launching = undefined;
  });
  let failed: Error | undefined;
  spawnError.then((e) => (failed = e));
  const deadline = Date.now() + READY_TIMEOUT;
  while (Date.now() < deadline) {
    if (cancelled()) throw new Cancelled("The backend was stopped while it was starting.");
    if (failed) {
      if (launching === me) launching = undefined;
      throw new Error(`Couldn't run “${argv[0]}”: ${failed.message}`);
    }
    if (me.exited !== undefined) throw new Error(`The backend stopped right away (exit code ${me.exited}).\n\n${logTail(20)}`);
    if (await healthy(base, token)) {
      if (cancelled()) throw new Cancelled("The backend was stopped while it was starting.");
      launching = undefined;
      running = me;
      return me;
    }
    await sleep(400);
  }
  if (launching === me) launching = undefined;
  proc.kill("SIGTERM");
  throw new Error(`The backend didn't answer within ${READY_TIMEOUT / 1000} s, so it was stopped.\n\n${logTail(20)}`);
}

/** The running backend, started if needed. Concurrent callers share one start; a failed start is not retried for a while. */
export async function ensure(): Promise<{ base: string; token: string }> {
  if (running && running.exited === undefined) return running;
  if (starting) return starting;
  if (lastFailure && Date.now() - lastFailure.at < RETRY_AFTER) throw new StartError(lastFailure.message);
  const p = start().then(
    (r) => {
      lastFailure = undefined;
      return r;
    },
    (e: Error) => {
      if (!(e instanceof Cancelled)) lastFailure = { at: Date.now(), message: e.message };
      throw new StartError(e.message);
    },
  );
  starting = p;
  try {
    return await p;
  } finally {
    if (starting === p) starting = undefined; // stop() may have dropped it, and a new start begun since
  }
}

export class StartError extends Error {}

export function status(): BackendStatus {
  const r = running && running.exited === undefined ? running : undefined;
  return {
    state: r ? "running" : starting ? "starting" : lastFailure ? "failed" : "stopped",
    url: r?.base ?? null,
    command: r ? r.argv.join(" ") : null,
    kind: r?.kind ?? null,
    pid: r?.proc.pid ?? null,
    message: !r && lastFailure ? lastFailure.message : null,
    configFile: configFile(),
    userConfigFile: userConfigFile(),
  };
}

/** Stop the backend: SIGTERM lets it end running jobs (they're recorded as interrupted); SIGKILL after 10 s.
 * A backend that is still starting is stopped too, and its start() gives up. */
export async function stop(): Promise<void> {
  generation++;
  starting = undefined;
  const r = running ?? launching;
  running = undefined;
  launching = undefined;
  if (!r || r.exited !== undefined || r.proc.pid === undefined) return;
  const done = new Promise<void>((resolve) => r.proc.once("exit", () => resolve()));
  r.proc.kill("SIGTERM");
  const timer = setTimeout(() => r.proc.kill("SIGKILL"), 10_000);
  await done;
  clearTimeout(timer);
}

export async function restart() {
  await stop();
  lastFailure = undefined;
  return ensure();
}
