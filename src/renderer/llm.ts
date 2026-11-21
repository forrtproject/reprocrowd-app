// The LLM button in the header (start page and package view): a status light for the coding CLIs, and a dialog with
// everything about them: which CLIs are installed and signed in, their logins, the models they offer (with the switches
// that keep models out of the pickers), and the coding and extraction profiles.
import { host, modelCompatible, type LlmModel } from "./api";
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
  let changed = false;
  let saving = Promise.resolve(); // model choices are written in order; closing waits for the last one
  const dlg = modal("dlg-llm", `<h3>LLM: coding CLIs and models</h3>
    <p class="hint">ReproCrowd runs its AI steps through command-line tools you install and sign into yourself; it never asks for API keys.
      Claude Code and Codex use your subscription unless an API key is set in the environment the app starts from.</p>
    <div class="llm-body"></div>
    <form method="dialog"><menu>
      <button type="button" class="llm-refresh">Detect again</button>
      <button type="button" class="llm-profiles">Coding and extraction profiles…</button>
      <span class="spacer"></span><button class="primary">Close</button>
    </menu></form>`);
  dlg.addEventListener("close", () => void saving.then(() => closed(changed)));
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
    const clis = cliStates(s);
    const st = status(s);
    const off = new Set(s.models.disabled);
    const byFamily = new Map<string, LlmModel[]>();
    for (const m of s.models.available) byFamily.set(m.provider, [...(byFamily.get(m.provider) ?? []), m]);
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
      <p class="hint">Models turned off here aren't offered in the model pickers. OpenCode lists its free models and the providers you signed in to.</p>
      <div class="llm-families">${[...byFamily.entries()].map(([family, models]) => `<div class="llm-model-family" data-family="${esc(family)}">
        <label class="llm-family"><input type="checkbox" data-family-toggle="${esc(family)}" /> <b>${esc(family)}</b> <span class="muted small">${models.length} model${models.length === 1 ? "" : "s"}</span></label>
        <div class="llm-model-list">${models.map((m) => `<label class="inline llm-model"><input type="checkbox" data-model="${esc(m.id)}" ${off.has(m.id) ? "" : "checked"} /> <code>${esc(m.name)}</code> <span class="muted small">${esc(m.cli)}${!s.profiles || s.profiles.some((p) => modelCompatible(m, p.argv)) ? "" : " · no compatible profile"}</span></label>`).join("")}</div>
      </div>`).join("") || `<p class="muted">No models found. Install and sign into a CLI, then Detect again.</p>`}</div>`;

    body.querySelectorAll<HTMLButtonElement>("[data-login]").forEach((b) => b.addEventListener("click", async () => {
      try {
        body.querySelector(".login-msg")!.textContent = `${await host.cliLogin(b.dataset.login!)} Then press Detect again.`;
      } catch (e) {
        showError("Logging in", e);
      }
    }));
    const syncFamilies = () => body.querySelectorAll<HTMLInputElement>("[data-family-toggle]").forEach((f) => {
      const models = s.models.available.filter((m) => m.provider === f.dataset.familyToggle);
      const on = models.filter((m) => !off.has(m.id)).length;
      f.checked = on === models.length && models.length > 0;
      f.indeterminate = on > 0 && on < models.length;
    });
    syncFamilies();
    const save = () => {
      changed = true;
      const disabled = [...off];
      saving = saving.then(() => host.setLlmModels(disabled)).catch((e) => showError("Saving the model choice", e));
    };
    body.querySelectorAll<HTMLInputElement>("[data-model]").forEach((cb) => cb.addEventListener("change", () => {
      if (cb.checked) off.delete(cb.dataset.model!);
      else off.add(cb.dataset.model!);
      // A choice is per model id: the same id listed by two CLIs (OpenCode and T3 Code) shares one switch state.
      body.querySelectorAll<HTMLInputElement>(`[data-model="${CSS.escape(cb.dataset.model!)}"]`).forEach((mc) => (mc.checked = cb.checked));
      syncFamilies();
      save();
    }));
    body.querySelectorAll<HTMLInputElement>("[data-family-toggle]").forEach((cb) => cb.addEventListener("change", () => {
      const family = cb.dataset.familyToggle!;
      for (const m of s.models.available) if (m.provider === family) {
        if (cb.checked) off.delete(m.id);
        else off.add(m.id);
      }
      body.querySelectorAll<HTMLInputElement>("[data-model]").forEach((mc) => (mc.checked = !off.has(mc.dataset.model!)));
      syncFamilies();
      save();
    }));
  };

  dlg.querySelector(".llm-refresh")!.addEventListener("click", () => void render(true));
  dlg.querySelector(".llm-profiles")!.addEventListener("click", () => profileEditor(() => { changed = true; void render(); }));
  await render();
}
