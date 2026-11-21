import { CLI_LABELS, cliModel, cliOf, execRestriction, host, modelCompatible, type ConfiguredProfile, type LlmModels } from "./api";
import { confirmDialog, esc, modal, showAllSuggestions, showError } from "./util";

type Kind = "llm" | "agent";
/** Codex without its shell and sub-agents, reading through ReproCrowd's read-only file server ({python}: the backend's Python). */
export const CODEX_NO_EXEC = ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "multi_agent", "-c", "mcp_servers.rc_files.command='{python}'",
  "-c", 'mcp_servers.rc_files.args=["-I", "-m", "reprocrowd.mcp_files"]', "-c", 'mcp_servers.rc_files.default_tools_approval_mode="approve"'];
/** OpenCode without command tools: every tool denied except reading and editing (its --auto approves only what is not denied). */
export const OPENCODE_NO_EXEC = '{"*":"deny","read":"allow","edit":"allow","glob":"allow","grep":"allow","list":"allow","todowrite":"allow"}';
export async function profileEditor(saved: (text: string) => void) {
  let profiles: Record<Kind, Record<string, ConfiguredProfile>>;
  try { profiles = await host.profileConfig(); } catch (e) { return showError("Opening profiles", e); }
  const dlg = modal("dlg-profiles", `<form method="dialog">
    <h3>Coding and extraction profiles</h3>
    <p class="hint">A profile runs a CLI you installed and signed into. No model call runs while editing or saving. Commands use your account; do not put passwords or API keys in arguments.</p>
    <label>Operation <select name="kind"><option value="agent">Code generation</option><option value="llm">Extract targets / analysis steps</option></select></label>
    <label>Existing profile <select name="existing"></select></label>
    <div class="grid2"><label>Preset <select name="preset"><option value="claude">Claude Code</option><option value="codex">Codex CLI</option><option value="opencode">OpenCode</option><option value="text">Other CLI (plain text)</option></select></label><label>Name <input name="name" required pattern="[A-Za-z0-9_-]+" /></label></div>
    <label>Command <input name="command" required placeholder="claude" /></label>
    <p class="hint command-status" aria-live="polite"></p>
    <label>Arguments (one per line; spaces stay inside that argument)<textarea name="args" class="mono" rows="6"></textarea></label>
    <div class="grid3"><label>Model <input name="model" list="profile-models" placeholder="(the CLI's default)" /><datalist id="profile-models"></datalist></label><label>Effort <input name="effort" /></label><label>Timeout (seconds) <input name="timeout" type="number" min="1" required /></label></div>
    <label>Output protocol <select name="protocol"></select></label>
    <p class="hint capability"></p>
    <details><summary>Tool, no-analysis and structured-output arguments</summary>
      <p class="hint">"Don't run the analysis" needs either a tool list ({tools} in the arguments, editing and execution tools below) or no-analysis settings that remove the CLI's command tools. Neither is an OS sandbox. Without them, such a request is refused before launch.</p>
      <label>Editing tools (one per line)<textarea name="tools" rows="2"></textarea></label>
      <label>Execution tools (one per line)<textarea name="exec" rows="2"></textarea></label>
      <label>No-analysis arguments (code generation; added when the analysis must not run; one per line)<textarea name="noexec" class="mono" rows="2"></textarea></label>
      <label>No-analysis environment (NAME=value per line; never credentials)<textarea name="noenv" class="mono" rows="2"></textarea></label>
      <label>Read-files arguments (extraction)<textarea name="read" rows="2"></textarea></label>
      <label>JSON Schema arguments (extraction; use {schema})<textarea name="schema" rows="2"></textarea></label>
      <label>More environment variables for the CLI (one name per line; PREFIX_* for a group)<textarea name="envpass" class="mono" rows="2"></textarea></label>
      <p class="hint">The CLI gets only what any program needs (paths, locale, proxies) and its own and its model providers' variables. Name others it needs here, such as a gateway's settings.</p>
    </details>
    <p class="hint">Saving restarts the backend. The form rewrites only this profile's section and keeps other settings and comments outside it; comments between this profile's keys are dropped. If the profile cannot be edited in place (for example, it is written as dotted keys, an inline table or a quoted header), the whole file is rewritten without comments. The original file is backed up once as backend.toml.bak. The advanced raw editor remains available.</p>
    <p class="profile-status" aria-live="polite"></p>
    <menu><button type="button" class="cancel">Cancel</button><button value="save" class="primary">Save profile</button></menu>
  </form>`);
  const form = dlg.querySelector("form")!;
  showAllSuggestions(form.querySelector<HTMLInputElement>("[name=model]")!); // all of this CLI's models, not only the current one
  // Cancel is not a submit button, so Enter in a field saves instead of closing the dialog.
  dlg.querySelector<HTMLButtonElement>("button.cancel")!.addEventListener("click", () => dlg.close());
  const el = (name: string) => form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  const value = (name: string) => el(name).value;
  const kind = () => value("kind") as Kind;
  const lines = (name: string) => value(name).split(/\r?\n/).filter((s) => s.trim());
  // NAME=value lines; a line without "=" is ignored, so it cannot make a profile look restricted without a setting.
  const env = () => Object.fromEntries(lines("noenv").filter((l) => l.indexOf("=") > 0).map((l) => { const i = l.indexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1)]; }));
  const set = (name: string, v: string | number | string[] | undefined) => { el(name).value = Array.isArray(v) ? v.join("\n") : String(v ?? ""); };
  const protocols = () => {
    const list = kind() === "agent" ? ["claude-stream-json", "codex-json", "opencode-json", "text"] : ["claude-json", "codex-json", "opencode-json", "text"];
    el("protocol").innerHTML = list.map((s) => `<option>${s}</option>`).join("");
  };
  const load = (name: string, p: ConfiguredProfile) => {
    const argv = Array.isArray(p.argv) ? p.argv : []; // a hand-edited profile without argv still opens, and the form asks for a command
    set("name", name); set("command", argv[0]); set("args", argv.slice(1));
    set("model", p.model); set("effort", p.effort); set("timeout", p.timeout_s);
    set("protocol", kind() === "agent" ? p.events : p.output);
    set("tools", p.tools); set("exec", p.exec_tools); set("read", p.read_files_argv); set("schema", p.json_schema_argv);
    set("noexec", p.no_exec_argv); set("noenv", Object.entries(p.no_exec_env ?? {}).map(([k, v]) => `${k}=${v}`));
    set("envpass", p.env_pass);
  };
  const preset = () => {
    const type = value("preset"), agent = kind() === "agent";
    const codex = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--disable", "apps", "--disable", "plugins", "-m", "{model}", "-c", "model_reasoning_effort={effort}", "-s", agent ? "workspace-write" : "read-only"];
    load(type, {
      argv: type === "claude" ? agent
        ? ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"]
        : ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"]
        : type === "codex" ? codex
        : type === "opencode" ? ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}", ...(agent ? ["--auto"] : [])] : ["your-cli"],
      model: type === "claude" ? "claude-sonnet-5-5" : "", effort: type === "claude" ? "high" : "", timeout_s: agent ? 7200 : 1800,
      ...(agent ? { events: type === "claude" ? "claude-stream-json" : type === "codex" ? "codex-json" : type === "opencode" ? "opencode-json" : "text", tools: type === "claude" ? ["Read", "Write", "Edit", "Glob", "Grep"] : [], exec_tools: type === "claude" ? ["Bash(Rscript:*)", "Bash(python:*)", "Bash(python3:*)"] : [],
          no_exec_argv: type === "claude" ? ["--disallowedTools", "Bash"] : type === "codex" ? CODEX_NO_EXEC : [],
          no_exec_env: type === "opencode" ? { OPENCODE_PERMISSION: OPENCODE_NO_EXEC } : {} }
        : { output: type === "claude" ? "claude-json" : type === "codex" ? "codex-json" : type === "opencode" ? "opencode-json" : "text", read_files_argv: type === "claude" ? ["--allowedTools", "Read"] : [], json_schema_argv: type === "claude" ? ["--json-schema", "{schema}"] : [] })
    });
    status();
  };
  // What the current fields promise, derived like the backend does (providers.exec_restriction): no model call.
  let models: LlmModels | undefined;
  const status = async () => {
    const argv = [value("command").trim(), ...lines("args")];
    const cli = cliOf(argv), label = CLI_LABELS[cli] ?? cli;
    const mode = execRestriction({ argv, model: "", effort: "", timeout_s: 1, tools: lines("tools"), exec_tools: lines("exec"), no_exec_argv: lines("noexec"), no_exec_env: env() });
    const restriction = kind() !== "agent" ? "" : mode === "tool_allowlist" ? "No-analysis mode: partial. The tool allowlist denies other commands, but the CLI may still run commands it treats as read-only (ls, cat). Add no-analysis arguments that deny command tools (Claude Code: --disallowedTools Bash)."
      : mode === "cli_settings" ? "No-analysis mode: the no-analysis settings remove the CLI's command tools (not an OS sandbox). The presets were checked live; the smoke test checks a changed profile."
      : "No-analysis mode: not supported. Runs with this profile need “let it run the analysis”; the agent can then run commands in the package folder.";
    form.querySelector(".capability")!.textContent = restriction;
    const compatible = (models?.available ?? []).filter((m) => modelCompatible(m, argv));
    form.querySelector("#profile-models")!.innerHTML = compatible.map((m) => `<option value="${esc(cliModel(m.id))}">${esc(m.name)}</option>`).join("");
    const line = form.querySelector(".command-status")!;
    if (!argv[0]) { line.textContent = ""; return; }
    const found = await host.commandFound(argv[0]).catch(() => false);
    line.textContent = !found ? `“${argv[0]}” was not found on this computer. Install ${label} and sign in once in a terminal; nothing is run while you edit.`
      : models?.needsLogin?.includes(cli as never) ? `${label} is installed but not signed in. Sign in (e.g. “${cli} login”) before running a profile.`
      : `${label} found${compatible.length ? `; ${compatible.length} model${compatible.length === 1 ? "" : "s"} offered for it` : ""}. Being found does not certify its login, model access or output.`;
  };
  host.llmModels().then((m) => { models = m; status(); }, () => {});
  const refresh = () => {
    protocols();
    el("existing").innerHTML = `<option value="">Add a profile…</option>` + Object.keys(profiles[kind()]).map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    const first = Object.keys(profiles[kind()])[0];
    if (first) { set("existing", first); load(first, profiles[kind()][first]); status(); }
    else preset();
  };
  el("kind").addEventListener("change", refresh); el("preset").addEventListener("change", preset);
  for (const name of ["command", "args", "tools", "exec", "noexec", "noenv"]) el(name).addEventListener("change", status);
  el("existing").addEventListener("change", () => { const p = profiles[kind()][value("existing")]; if (p) { load(value("existing"), p); status(); } else preset(); });
  form.addEventListener("submit", async (e) => {
    if ((e.submitter as HTMLButtonElement)?.value !== "save") return;
    e.preventDefault();
    const button = e.submitter as HTMLButtonElement;
    button.disabled = true;
    try {
      // A backend that is down has no jobs to interrupt; the form must still be able to repair its profiles.
      if ((await host.runningJobs().catch(() => [])).length && !(await confirmDialog("Restart the backend?", "<p>Running jobs will stop and be recorded as interrupted.</p>", "Stop jobs and save"))) return;
      const p: ConfiguredProfile = { argv: [value("command").trim(), ...lines("args")], model: value("model"), effort: value("effort"), timeout_s: Number(value("timeout")),
        ...(lines("envpass").length ? { env_pass: lines("envpass").map((l) => l.trim()) } : {}),
        ...(kind() === "agent" ? { events: value("protocol"), tools: lines("tools"), exec_tools: lines("exec"), no_exec_argv: lines("noexec"), no_exec_env: env() } : { output: value("protocol"), read_files_argv: lines("read"), json_schema_argv: lines("schema") }) };
      const text = await host.saveProfile(kind(), value("name"), p);
      saved(text); dlg.close();
    } catch (err) { dlg.querySelector(".profile-status")!.textContent = (err as Error).message; }
    finally { button.disabled = false; }
  });
  refresh();
}
