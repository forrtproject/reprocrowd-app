import { host, type ConfiguredProfile } from "./api";
import { confirmDialog, esc, modal, showError } from "./util";

type Kind = "llm" | "agent";
export async function profileEditor(saved: (text: string) => void) {
  let profiles: Record<Kind, Record<string, ConfiguredProfile>>;
  try { profiles = await host.profileConfig(); } catch (e) { return showError("Opening profiles", e); }
  const dlg = modal("dlg-profiles", `<form method="dialog">
    <h3>Coding and extraction profiles</h3>
    <p class="hint">A profile runs a CLI you installed and signed into. No model call runs while editing or saving. Commands use your account; do not put passwords or API keys in arguments.</p>
    <label>Operation <select name="kind"><option value="agent">Code generation</option><option value="llm">Extract targets / analysis steps</option></select></label>
    <label>Existing profile <select name="existing"></select></label>
    <div class="grid2"><label>Preset <select name="preset"><option value="claude">Claude Code</option><option value="opencode">OpenCode</option><option value="text">Other CLI (plain text)</option></select></label><label>Name <input name="name" required pattern="[A-Za-z0-9_-]+" /></label></div>
    <label>Command <input name="command" required placeholder="claude" /></label>
    <label>Arguments (one per line; spaces stay inside that argument)<textarea name="args" class="mono" rows="6"></textarea></label>
    <div class="grid3"><label>Model <input name="model" /></label><label>Effort <input name="effort" /></label><label>Timeout (seconds) <input name="timeout" type="number" min="1" required /></label></div>
    <label>Output protocol <select name="protocol"></select></label>
    <details><summary>Tool and structured-output arguments</summary>
      <p class="hint">For tool-list profiles, use {tools} in the arguments. A CLI allowlist is not an OS sandbox. An unsupported no-analysis request is refused before launch.</p>
      <label>Editing tools (one per line)<textarea name="tools" rows="2"></textarea></label>
      <label>Execution tools (one per line)<textarea name="exec" rows="2"></textarea></label>
      <label>Read-files arguments (extraction)<textarea name="read" rows="2"></textarea></label>
      <label>JSON Schema arguments (extraction; use {schema})<textarea name="schema" rows="2"></textarea></label>
    </details>
    <p class="hint">Saving restarts the backend. The form preserves other settings but drops TOML comments; the original file is backed up once as backend.toml.bak. The advanced raw editor remains available.</p>
    <p class="profile-status" aria-live="polite"></p>
    <menu><button value="cancel" formnovalidate>Cancel</button><button value="save" class="primary">Save profile</button></menu>
  </form>`);
  const form = dlg.querySelector("form")!;
  const el = (name: string) => form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  const value = (name: string) => el(name).value;
  const kind = () => value("kind") as Kind;
  const lines = (name: string) => value(name).split(/\r?\n/).filter((s) => s.trim());
  const set = (name: string, v: string | number | string[] | undefined) => { el(name).value = Array.isArray(v) ? v.join("\n") : String(v ?? ""); };
  const protocols = () => {
    const list = kind() === "agent" ? ["claude-stream-json", "opencode-json", "text"] : ["claude-json", "opencode-json", "text"];
    el("protocol").innerHTML = list.map((s) => `<option>${s}</option>`).join("");
  };
  const load = (name: string, p: ConfiguredProfile) => {
    set("name", name); set("command", p.argv[0]); set("args", p.argv.slice(1));
    set("model", p.model); set("effort", p.effort); set("timeout", p.timeout_s);
    set("protocol", kind() === "agent" ? p.events : p.output);
    set("tools", p.tools); set("exec", p.exec_tools); set("read", p.read_files_argv); set("schema", p.json_schema_argv);
  };
  const preset = () => {
    const type = value("preset"), agent = kind() === "agent";
    load(type, {
      argv: type === "claude" ? agent
        ? ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"]
        : ["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"]
        : type === "opencode" ? ["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}", ...(agent ? ["--auto"] : [])] : ["your-cli"],
      model: type === "claude" ? "sonnet" : "", effort: type === "claude" ? "high" : "", timeout_s: agent ? 7200 : 1800,
      ...(agent ? { events: type === "claude" ? "claude-stream-json" : type === "opencode" ? "opencode-json" : "text", tools: type === "claude" ? ["Read", "Write", "Edit", "Glob", "Grep"] : [], exec_tools: type === "claude" ? ["Bash(Rscript:*)", "Bash(python:*)", "Bash(python3:*)"] : [] }
        : { output: type === "claude" ? "claude-json" : type === "opencode" ? "opencode-json" : "text", read_files_argv: type === "claude" ? ["--allowedTools", "Read"] : [], json_schema_argv: type === "claude" ? ["--json-schema", "{schema}"] : [] })
    });
  };
  const refresh = () => {
    protocols();
    el("existing").innerHTML = `<option value="">Add a profile…</option>` + Object.keys(profiles[kind()]).map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    const first = Object.keys(profiles[kind()])[0];
    if (first) { set("existing", first); load(first, profiles[kind()][first]); }
    else preset();
  };
  el("kind").addEventListener("change", refresh); el("preset").addEventListener("change", preset);
  el("existing").addEventListener("change", () => { const p = profiles[kind()][value("existing")]; if (p) load(value("existing"), p); else preset(); });
  form.addEventListener("submit", async (e) => {
    if ((e.submitter as HTMLButtonElement)?.value !== "save") return;
    e.preventDefault();
    const button = e.submitter as HTMLButtonElement;
    button.disabled = true;
    try {
      if ((await host.runningJobs()).length && !(await confirmDialog("Restart the backend?", "<p>Running jobs will stop and be recorded as interrupted.</p>", "Stop jobs and save"))) return;
      const p: ConfiguredProfile = { argv: [value("command"), ...lines("args")], model: value("model"), effort: value("effort"), timeout_s: Number(value("timeout")),
        ...(kind() === "agent" ? { events: value("protocol"), tools: lines("tools"), exec_tools: lines("exec") } : { output: value("protocol"), read_files_argv: lines("read"), json_schema_argv: lines("schema") }) };
      const text = await host.saveProfile(kind(), value("name"), p);
      saved(text); dlg.close();
    } catch (err) { dlg.querySelector(".profile-status")!.textContent = (err as Error).message; }
    finally { button.disabled = false; }
  });
  refresh();
}
