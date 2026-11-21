// The LLM button in the header (start page and package view): a status light for the coding CLIs, and a dialog with
// everything about them: which CLIs are installed and signed in, their logins, and, under each CLI, the profiles that
// run it, which of them text parsing and code generation use by default, and a button to add one.
import { cliModel, host } from "./api";
import { cliStates, freeName, label, LOGIN, profileRows, status, type Level, type Profile, type ProfileRow, type State } from "./llm-status";
import { profileEditor, type EditorStart } from "./profiles";
import { confirmDialog, esc, modal, showError } from "./util";

async function load(refresh = false): Promise<State> {
  const models = await host.llmModels(refresh);
  // null when the backend can't list its profiles (e.g. still starting): the status then judges installs and logins only.
  const profiles = await Promise.all([host.service<Profile[]>("/v1/llm-profiles"), host.service<Profile[]>("/v1/agent-profiles")])
    .then(([llm, agent]) => [...llm.map((p) => ({ ...p, kind: "llm" as const })), ...agent.map((p) => ({ ...p, kind: "agent" as const }))]).catch(() => null);
  return { models, profiles };
}

const light = (level: Level) => `<span class="llm-light" data-level="${level}" aria-hidden="true"></span>`;
const TASKS = { llm: "text parsing", agent: "code generation" } as const;
const PRESETS: Record<string, EditorStart["preset"]> = { claude: "claude", codex: "codex", opencode: "opencode" };

/** A CLI's profiles: model and effort, and a checkbox per task making it that task's default. */
function profilesHtml(rows: ProfileRow[], cli: string): string {
  const model = (p?: Profile) => p ? `${p.model ? `<code>${esc(cliModel(p.model))}</code>` : `<span class="muted">CLI default</span>`}${p.effort ? ` · ${esc(p.effort)}` : ""}` : "";
  const box = (row: ProfileRow, kind: "llm" | "agent") => {
    const p = row[kind];
    return `<label class="inline llm-default" title="${p ? `Use ${esc(row.name)} for ${TASKS[kind]} unless a step picks another profile` : `${esc(row.name)} has no ${TASKS[kind]} profile`}">
      <input type="checkbox" data-default="${kind}" data-name="${esc(row.name)}" ${p?.default ? "checked" : ""} ${p ? "" : "disabled"} /> ${TASKS[kind]}</label>`;
  };
  return `<table class="tbl-list small llm-profile-table">
    <thead><tr><th>Profile</th><th>Model · effort</th><th>Default for</th><th></th></tr></thead>
    <tbody>${rows.map((r) => {
      const same = r.llm && r.agent && r.llm.model === r.agent.model && r.llm.effort === r.agent.effort;
      const models = same || !r.llm || !r.agent ? model(r.llm ?? r.agent) : `parsing: ${model(r.llm)}<br>coding: ${model(r.agent)}`;
      return `<tr><td><b>${esc(r.name)}</b>${(r.llm ?? r.agent)?.available === false ? ` <span class="warn-inline small">command not found</span>` : ""}</td>
        <td>${models}</td><td><div class="llm-defaults">${box(r, "llm")}${box(r, "agent")}</div></td>
        <td><button type="button" class="ghost" data-edit="${esc(r.name)}" data-kind="${r.llm ? "llm" : "agent"}">Edit…</button></td></tr>`;
    }).join("") || `<tr><td colspan="4" class="muted">No profile runs ${esc(label(cli))} yet.</td></tr>`}</tbody></table>
    <p><button type="button" data-add="${esc(cli)}">+ Add profile</button> <span class="hint llm-profile-msg" aria-live="polite"></span></p>`;
}

/** The header button. `changed` runs after the dialog closes if models or profiles were changed in it. `refresh`
 * checks again, e.g. after Settings saved a backend configuration that may define other profiles. */
export function llmButton(className: string, changed: () => void): { button: HTMLButtonElement; refresh: () => void } {
  const button = document.createElement("button");
  button.id = "llm-btn";
  button.type = "button";
  button.className = className;
  button.innerHTML = `${light("checking")} LLM`;
  const show = (level: Level, text: string) => {
    button.querySelector<HTMLElement>(".llm-light")!.dataset.level = level;
    button.title = text;
    button.setAttribute("aria-label", `LLM: ${text}`);
  };
  let latest = 0; // checks can overlap (e.g. one still running when the dialog closes): only the newest is shown
  const update = async (refresh = false) => {
    const mine = ++latest;
    show("checking", "Looking for coding CLIs…");
    try {
      const st = status(await load(refresh));
      if (mine === latest) show(st.level, st.text);
    } catch (e) {
      if (mine === latest) show("warn", `Could not check the coding CLIs: ${(e as Error).message}`);
    }
  };
  button.addEventListener("click", () => llmDialog((didChange) => { void update(); if (didChange) changed(); }));
  void update();
  return { button, refresh: () => void update() };
}

async function llmDialog(closed: (changed: boolean) => void) {
  let changed = false; // profiles saved, or Detect again found other models: the package view reloads its pickers
  let first: string | undefined; // the models listed when the dialog opened
  const dlg = modal("dlg-llm", `<h3>LLM: coding CLIs and profiles</h3>
    <p class="hint">ReproCrowd runs its AI steps through command-line tools you install and sign into yourself; it never asks for API keys.
      Claude Code and Codex use your subscription unless an API key is set in the environment the app starts from.</p>
    <div class="llm-body"></div>
    <form method="dialog"><menu>
      <button type="button" class="llm-refresh">Detect again</button>
      <button type="button" class="llm-profiles">All profiles…</button>
      <span class="spacer"></span><button class="primary">Close</button>
    </menu></form>`);
  let rendering: Promise<void> = Promise.resolve(); // the newest render: closing waits for it, as it may find other models
  dlg.addEventListener("close", () => void rendering.then(() => closed(changed)));
  const body = dlg.querySelector<HTMLElement>(".llm-body")!;

  const expanded = new Set<string>(); // the CLIs whose profiles are shown; kept across renders
  const saved = () => { changed = true; void rerender(); };
  let latest = 0; // renders can overlap (Detect again, a saved profile): only the newest fills the dialog
  const render = async (refresh = false) => {
    const mine = ++latest;
    body.innerHTML = `<p class="waiting muted"><span class="spinner" aria-hidden="true"></span> Looking for Claude Code, Codex, OpenCode and their models…</p>`;
    let s: State;
    try {
      s = await load(refresh);
    } catch (e) {
      if (mine === latest) body.innerHTML = `<p class="warn-inline">Could not check the coding CLIs: ${esc((e as Error).message)}</p>`;
      return;
    }
    if (mine !== latest) return;
    const listed = s.models.available.map((m) => m.id).join("\n");
    if (first === undefined) first = listed;
    else if (listed !== first) changed = true;
    paint(s);
  };

  /** Fill the dialog from a loaded state (expanding a tool repaints without detecting again). */
  const paint = (s: State) => {
    const clis = cliStates(s);
    const st = status(s);
    const profiles = s.profiles ?? [];
    body.innerHTML = `
      <p class="llm-summary">${light(st.level)} ${esc(st.level === "ok" ? "Ready: at least one CLI is installed, signed in and run by a profile."
        : st.level === "none" ? "No coding CLI is installed."
        : !s.profiles ? "Not ready: the backend couldn't list its profiles, so nothing confirms that a profile runs a CLI."
        : "Not ready: no installed CLI is both signed in and run by a profile.")}</p>
      <p class="hint">Open a tool to see the profiles that run it, choose which one text parsing and code generation use by default, or add one.</p>
      <table class="tbl-list small llm-clis"><tbody>${clis.map((c) => {
        const open = expanded.has(c.cli);
        return `<tr class="llm-cli">
        <td><button type="button" class="llm-expand" data-expand="${esc(c.cli)}" aria-expanded="${open}" ${s.profiles ? "" : "disabled"} title="${open ? "Hide" : "Show"} its profiles">${open ? "▾" : "▸"}</button>
          ${light(!c.installed ? "none" : c.problem ? "warn" : "ok")} <b>${esc(label(c.cli))}</b></td>
        <td>${esc(!c.installed ? "not installed" : c.problem ? `installed, ${c.problem}` : "installed and signed in")}</td>
        <td class="muted">${s.profiles ? `${c.profiles.length || "no"} profile${c.profiles.length === 1 ? "" : "s"}` : "profiles unknown"}${c.installed ? ` · ${c.models} model${c.models === 1 ? "" : "s"}` : ""}</td>
        <td>${c.installed && (!c.signedIn || c.loginUnknown) && LOGIN.has(c.cli) ? `<button type="button" data-login="${esc(c.cli)}">Log in…</button>` : ""}</td>
      </tr>${open ? `<tr class="llm-cli-profiles"><td colspan="4">${profilesHtml(profileRows(profiles, c.cli), c.cli)}</td></tr>` : ""}`;
      }).join("")}</tbody></table>
      <p class="hint login-msg" aria-live="polite"></p>`;

    body.querySelectorAll<HTMLButtonElement>("[data-login]").forEach((b) => b.addEventListener("click", async () => {
      try {
        body.querySelector(".login-msg")!.textContent = `${await host.cliLogin(b.dataset.login!)} Then press Detect again.`;
      } catch (e) {
        showError("Logging in", e);
      }
    }));
    body.querySelectorAll<HTMLButtonElement>("[data-expand]").forEach((b) => b.addEventListener("click", () => {
      const cli = b.dataset.expand!;
      if (!expanded.delete(cli)) expanded.add(cli);
      paint(s);
    }));
    body.querySelectorAll<HTMLButtonElement>("[data-edit]").forEach((b) => b.addEventListener("click", () =>
      profileEditor(saved, { kind: b.dataset.kind as "llm" | "agent", name: b.dataset.edit })));
    body.querySelectorAll<HTMLButtonElement>("[data-add]").forEach((b) => b.addEventListener("click", () => {
      const cli = b.dataset.add!;
      profileEditor(saved, { kind: "agent", preset: PRESETS[cli] ?? "text", newName: freeName(profiles, cli) });
    }));
    body.querySelectorAll<HTMLInputElement>("[data-default]").forEach((cb) => cb.addEventListener("change", async () => {
      const kind = cb.dataset.default as "llm" | "agent";
      const msg = cb.closest("td[colspan]")?.querySelector(".llm-profile-msg");
      if (!cb.checked) { // a task always has a default: another profile takes over by ticking it
        cb.checked = true;
        if (msg) msg.textContent = `Tick another profile to make it the default for ${TASKS[kind]}.`;
        return;
      }
      cb.checked = false; // until saved
      if ((await host.runningJobs().catch(() => [])).length && !(await confirmDialog("Restart the backend?", "<p>Changing a default restarts the backend. Running jobs will stop and be recorded as interrupted.</p>", "Stop jobs and change"))) return;
      body.querySelectorAll<HTMLInputElement>("[data-default]").forEach((x) => (x.disabled = true));
      if (msg) msg.textContent = "Saving; the backend restarts…";
      try {
        await host.setDefaultProfile(kind, cb.dataset.name!);
        changed = true;
      } catch (e) {
        showError(`Making ${cb.dataset.name} the default for ${TASKS[kind]}`, e);
      }
      void rerender();
    }));
  };

  const rerender = (refresh = false) => (rendering = render(refresh).catch(() => {}));
  dlg.querySelector(".llm-refresh")!.addEventListener("click", () => void rerender(true));
  dlg.querySelector(".llm-profiles")!.addEventListener("click", () => profileEditor(saved));
  await rerender();
}
