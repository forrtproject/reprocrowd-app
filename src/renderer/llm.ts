// The LLM button in the header (start page and package view): a status light for the coding CLIs, and a dialog with
// everything about them: which CLIs are installed and signed in, their logins, the models they offer, and the coding and
// extraction profiles.
import { cliModel, host, modelCompatible, type LlmModel } from "./api";
import { cliStates, label, LOGIN, status, type Level, type Profile, type State } from "./llm-status";
import { profileEditor } from "./profiles";
import { esc, modal, showError } from "./util";

async function load(refresh = false): Promise<State> {
  const models = await host.llmModels(refresh);
  // null when the backend can't list its profiles (e.g. still starting): the status then judges installs and logins only.
  const profiles = await Promise.all([host.service<Profile[]>("/v1/llm-profiles"), host.service<Profile[]>("/v1/agent-profiles")])
    .then((groups) => groups.flat()).catch(() => null);
  return { models, profiles };
}

const light = (level: Level) => `<span class="llm-light" data-level="${level}" aria-hidden="true"></span>`;

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
  const dlg = modal("dlg-llm", `<h3>LLM: coding CLIs and models</h3>
    <p class="hint">ReproCrowd runs its AI steps through command-line tools you install and sign into yourself; it never asks for API keys.
      Claude Code and Codex use your subscription unless an API key is set in the environment the app starts from.</p>
    <div class="llm-body"></div>
    <form method="dialog"><menu>
      <button type="button" class="llm-refresh">Detect again</button>
      <button type="button" class="llm-profiles">Coding and extraction profiles…</button>
      <span class="spacer"></span><button class="primary">Close</button>
    </menu></form>`);
  let rendering: Promise<void> = Promise.resolve(); // the newest render: closing waits for it, as it may find other models
  dlg.addEventListener("close", () => void rendering.then(() => closed(changed)));
  const body = dlg.querySelector<HTMLElement>(".llm-body")!;

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
    const clis = cliStates(s);
    const st = status(s);
    const byFamily = new Map<string, LlmModel[]>();
    for (const m of s.models.available) {
      if (!byFamily.has(m.provider)) byFamily.set(m.provider, []);
      byFamily.get(m.provider)!.push(m);
    }
    body.innerHTML = `
      <p class="llm-summary">${light(st.level)} ${esc(st.level === "ok" ? "Ready: at least one CLI is installed, signed in and run by a profile."
        : st.level === "none" ? "No coding CLI is installed."
        : !s.profiles ? "Not ready: the backend couldn't list its profiles, so nothing confirms that a profile runs a CLI."
        : "Not ready: no installed CLI is both signed in and run by a profile.")}</p>
      <h4>Command-line tools</h4>
      <table class="tbl-list small llm-clis"><tbody>${clis.map((c) => `<tr>
        <td>${light(!c.installed ? "none" : c.problem ? "warn" : "ok")} <b>${esc(label(c.cli))}</b></td>
        <td>${esc(!c.installed ? "not installed" : c.problem ? `installed, ${c.problem}` : "installed and signed in")}</td>
        <td class="muted">${c.installed ? `${c.models} model${c.models === 1 ? "" : "s"} · ${s.profiles ? (c.profiles.length ? `profiles: ${esc(c.profiles.join(", "))}` : "no profile") : "profiles unknown"}` : ""}</td>
        <td>${c.installed && (!c.signedIn || c.loginUnknown) && LOGIN.has(c.cli) ? `<button type="button" data-login="${esc(c.cli)}">Log in…</button>` : ""}</td>
      </tr>`).join("")}</tbody></table>
      <p class="hint login-msg" aria-live="polite"></p>
      <h4>Models</h4>
      <p class="hint">Every model listed here is offered in the model pickers of the profiles that run its CLI. Claude Code's are its
        versioned models; OpenCode lists its free models and the providers you signed in to.</p>
      <div class="llm-families">${[...byFamily.entries()].map(([family, models]) => `<div class="llm-model-family">
        <div class="llm-family"><b>${esc(family)}</b> <span class="muted small">${models.length} model${models.length === 1 ? "" : "s"}</span></div>
        <ul class="llm-model-list">${models.map((m) => `<li class="llm-model"><span>${esc(m.name)}</span> <code class="muted small">${esc(cliModel(m.id))}</code>${!s.profiles || s.profiles.some((p) => modelCompatible(m, p.argv)) ? "" : ` <span class="muted small">· no compatible profile</span>`}</li>`).join("")}</ul>
      </div>`).join("") || `<p class="muted">No models found. Install and sign into a CLI, then Detect again.</p>`}</div>`;

    body.querySelectorAll<HTMLButtonElement>("[data-login]").forEach((b) => b.addEventListener("click", async () => {
      try {
        body.querySelector(".login-msg")!.textContent = `${await host.cliLogin(b.dataset.login!)} Then press Detect again.`;
      } catch (e) {
        showError("Logging in", e);
      }
    }));
  };

  const rerender = (refresh = false) => (rendering = render(refresh).catch(() => {}));
  dlg.querySelector(".llm-refresh")!.addEventListener("click", () => void rerender(true));
  dlg.querySelector(".llm-profiles")!.addEventListener("click", () => profileEditor(() => { changed = true; void rerender(); }));
  await rerender();
}
