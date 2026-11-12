// Settings: you as the creator of Zenodo records, the Zenodo server and token, where packages go, the code editor,
// and the app's backend (status, output, its configuration: LLM and agent profiles, OpenAlex).
import { Config, host } from "./api";
import { esc, modal, showError } from "./util";

const ZENODO = { zenodo: "https://zenodo.org", sandbox: "https://sandbox.zenodo.org" } as const;
const tokenPage = (server: keyof typeof ZENODO) => `${ZENODO[server]}/account/settings/applications/tokens/new/`;
const ORCID = /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/;

export async function settingsDialog(onSaved: (changed: boolean) => void, focus?: "zenodo" | "backend") {
  let cfg: Config;
  try {
    cfg = await host.getConfig();
  } catch (e) {
    return showError("Opening the settings", e);
  }
  const z = cfg.zenodo;
  const dlg = modal("dlg-settings", `<form method="dialog">
    <h3>Settings</h3>
    <fieldset id="set-you"><legend>You, on Zenodo records</legend>
      <p class="hint">A deposit is signed with your name: it is the creator of the Zenodo record.</p>
      <div class="grid3">
        <label>Name <input name="cr_name" value="${esc(z.creator.name)}" placeholder="Family, Given" /></label>
        <label>ORCID <input name="cr_orcid" value="${esc(z.creator.orcid)}" placeholder="0000-0000-0000-0000" spellcheck="false" /></label>
        <label>Affiliation <input name="cr_affil" value="${esc(z.creator.affiliation)}" placeholder="optional" /></label>
      </div>
    </fieldset>
    <fieldset id="set-zenodo"><legend>Zenodo</legend>
      <div class="radio-row">
        <label class="inline"><input type="radio" name="zserver" value="sandbox" ${z.sandbox ? "checked" : ""} /> <b>Sandbox</b> (sandbox.zenodo.org, for testing; DOIs aren't real)</label>
        <label class="inline"><input type="radio" name="zserver" value="zenodo" ${z.sandbox ? "" : "checked"} /> <b>Zenodo</b> (zenodo.org; published records and DOIs are permanent)</label>
      </div>
      <div class="token-row">
        <label>Personal access token for <span id="z-host"></span> <input name="ztoken" type="password" autocomplete="off" spellcheck="false" /></label>
        <button type="button" id="z-verify">Check</button><button type="button" id="z-remove">Remove</button>
      </div>
      <p class="hint"><a href="#" id="z-create">Create a token</a> on Zenodo with the scopes <code>deposit:write</code> and <code>deposit:actions</code>, and paste it here. zenodo.org and the sandbox have separate accounts, so each needs its own token. ${cfg.secretStorage === "secure" ? "Tokens are kept encrypted with your system's keyring." : cfg.secretStorage === "weak" ? `<span class="warn-inline">No system keyring was found, so tokens are only obfuscated on disk.</span>` : `<span class="warn-inline">This system offers no secure storage; tokens can't be saved.</span>`}</p>
      <p class="z-check small" aria-live="polite"></p>
      <label>Community <input name="zcommunity" value="${esc(z.community)}" placeholder="optional, e.g. reprocrowd" spellcheck="false" /></label>
      <p class="hint">Deposits are submitted to this Zenodo community, whose curators accept them.</p>
    </fieldset>
    <fieldset><legend>Packages</legend>
      <label>Folder for new reproductions <span class="row"><input name="root" value="${esc(cfg.packagesRoot)}" spellcheck="false" /><button type="button" id="root-choose">Choose…</button></span></label>
      <label>Code editor command <input name="editor" value="${esc(cfg.editorCmd)}" placeholder="auto: code, codium, cursor, zed, subl, xdg-open" spellcheck="false" /></label>
    </fieldset>
    <fieldset id="set-backend"><legend>Backend</legend>
      <p class="backend-line small"></p>
      <div class="actions"><button type="button" id="be-restart">Restart</button><button type="button" id="be-log">Show output</button></div>
      <pre class="log" id="be-log-text" hidden></pre>
      <details ${focus === "backend" ? "open" : ""}><summary>Configuration: LLM and coding-agent profiles, OpenAlex</summary>
        <p class="hint">TOML, as in the backend's <code>config.example.toml</code>, without <code>[server]</code> and <code>[database]</code> (the app sets those). LLM calls and the coding agent run the commands configured here, e.g. Claude Code (<code>claude</code>) on your PATH.</p>
        <textarea class="mono big" name="becfg" spellcheck="false">${esc(cfg.backendConfig)}</textarea>
        <div class="actions"><button type="button" id="be-save" class="primary">Save configuration and restart</button><button type="button" id="be-reset">Reset to defaults</button></div>
      </details>
      <details><summary>Advanced: start command</summary>
        <label>Start the backend with <input name="becmd" value="${esc(cfg.backendCmd)}" placeholder="(the bundled backend)" spellcheck="false" /></label>
        <p class="hint">For example <code>uv run --project /path/to/backend python -m reprocrowd serve --host {host} --port {port}</code>. Empty: the backend that comes with the app.</p>
      </details>
    </fieldset>
    <fieldset id="set-updates"><legend>Updates</legend>
      <p class="hint">ReproCrowd checks the GitHub Releases feed for a newer version. Updates are only available in an installed build, not a development checkout.</p>
      <div class="actions"><button type="button" id="upd-check">Check for updates…</button></div>
      <p class="upd-status small" id="upd-status" aria-live="polite"></p>
    </fieldset>
    <p class="save-error warn-inline" hidden></p>
    <menu><button value="cancel">Cancel</button><button value="save" class="primary">Save</button></menu></form>`);
  const q = <T extends HTMLElement = HTMLInputElement>(sel: string) => dlg.querySelector<T>(sel)!;
  const server = () => (q<HTMLInputElement>("[name=zserver]:checked").value as "zenodo" | "sandbox");
  const saveError = q<HTMLElement>(".save-error");
  const check = q<HTMLElement>(".z-check");

  const showServer = () => {
    const s = server();
    q("#z-host").textContent = s === "sandbox" ? "the sandbox" : "zenodo.org";
    const has = cfg.tokens[s];
    q<HTMLInputElement>("[name=ztoken]").placeholder = has ? "(stored; paste a new one to replace it)" : "paste your token";
    q<HTMLButtonElement>("#z-remove").disabled = !has;
    check.innerHTML = has ? "" : `<span class="muted">No token for ${s === "sandbox" ? "the sandbox" : "zenodo.org"} yet.</span>`;
  };
  dlg.querySelectorAll("[name=zserver]").forEach((r) => r.addEventListener("change", showServer));
  showServer();

  q("#z-create").addEventListener("click", (e) => {
    e.preventDefault();
    host.openUrl(tokenPage(server()));
  });
  q("#z-verify").addEventListener("click", async () => {
    check.innerHTML = `<span class="muted">Checking with ${esc(ZENODO[server()])}…</span>`;
    try {
      await host.verifyZenodoToken(server() === "sandbox", q<HTMLInputElement>("[name=ztoken]").value || undefined);
      check.innerHTML = `<span class="ok">✓ Zenodo accepts the token for deposits.</span>`;
    } catch (e) {
      check.innerHTML = `<span class="warn-inline">${esc((e as Error).message)}</span>`;
    }
  });
  q("#z-remove").addEventListener("click", async () => {
    await host.setZenodoToken(server(), null).catch((e) => showError("Removing the token", e));
    cfg.tokens[server()] = false;
    showServer();
  });
  q("#root-choose").addEventListener("click", async () => {
    const dir = await host.chooseFolder(q<HTMLInputElement>("[name=root]").value);
    if (dir) q<HTMLInputElement>("[name=root]").value = dir;
  });

  // ---------- backend ----------
  const showBackend = async () => {
    const st = await host.backendStatus().catch(() => cfg.backend);
    const where = { bundled: "bundled with the app", development: "development checkout", custom: "custom start command" } as const;
    q<HTMLElement>(".backend-line").innerHTML =
      st.state === "running"
        ? `<span class="ok">Running</span> (${esc(st.kind ? where[st.kind] : "")}) at ${esc(st.url)}${st.pid ? `, pid ${st.pid}` : ""}`
        : st.state === "starting"
          ? "Starting…"
          : `<span class="warn-inline">${st.state === "failed" ? "Couldn't start" : "Not running"}</span>${st.message ? `: ${esc(st.message.split("\n")[0])}` : ""}`;
  };
  showBackend();
  q("#be-restart").addEventListener("click", async (e) => {
    const b = e.target as HTMLButtonElement;
    b.disabled = true;
    q<HTMLElement>(".backend-line").textContent = "Restarting…";
    await host.restartBackend().catch(() => {});
    b.disabled = false;
    showBackend();
  });
  q("#be-log").addEventListener("click", async () => {
    const pre = q<HTMLElement>("#be-log-text");
    pre.hidden = !pre.hidden;
    if (!pre.hidden) pre.textContent = (await host.backendLog().catch(() => "")) || "(no output)";
  });
  q("#be-reset").addEventListener("click", () => {
    q<HTMLTextAreaElement>("[name=becfg]").value = cfg.defaultBackendConfig;
  });
  q("#be-save").addEventListener("click", async (e) => {
    const b = e.target as HTMLButtonElement;
    b.disabled = true;
    q<HTMLElement>(".backend-line").textContent = "Saving and restarting…";
    try {
      await host.saveBackendConfig(q<HTMLTextAreaElement>("[name=becfg]").value);
      cfg.backendConfig = q<HTMLTextAreaElement>("[name=becfg]").value;
    } catch (err) {
      showError("Saving the backend configuration", err);
    }
    b.disabled = false;
    showBackend();
  });
  // ---------- updates ----------
  q("#upd-check").addEventListener("click", async () => {
    const el = q<HTMLElement>("#upd-status");
    el.textContent = "Checking…";
    try {
      const s = await host.checkForUpdates();
      if (s.state === "not-available") el.innerHTML = `<span class="muted">You're up to date (${esc(s.current)}).</span>`;
      else if (s.state === "available") el.innerHTML = `<span class="ok">✓ Version ${esc(s.version)} is available — downloading…</span>`;
      else if (s.state === "disabled") el.innerHTML = `<span class="muted">${esc(s.reason)}</span>`;
      else if (s.state === "error") el.innerHTML = `<span class="warn-inline">${esc(s.message)}</span>`;
    } catch (err) {
      el.innerHTML = `<span class="warn-inline">${esc((err as Error).message)}</span>`;
    }
  });

  if (focus) dlg.querySelector(`#set-${focus === "zenodo" ? "you" : "backend"}`)?.scrollIntoView();

  q<HTMLFormElement>("form").addEventListener("submit", async (e) => {
    if ((e.submitter as HTMLButtonElement | null)?.value !== "save") return;
    e.preventDefault();
    saveError.hidden = true;
    const orcid = q<HTMLInputElement>("[name=cr_orcid]").value.trim().replace(/^https?:\/\/orcid\.org\//, "");
    if (orcid && !ORCID.test(orcid)) {
      saveError.textContent = `“${orcid}” isn't an ORCID iD (0000-0000-0000-0000).`;
      saveError.hidden = false;
      return;
    }
    try {
      const token = q<HTMLInputElement>("[name=ztoken]").value.trim();
      if (token) await host.setZenodoToken(server(), token);
      if (q<HTMLTextAreaElement>("[name=becfg]").value !== cfg.backendConfig) await host.saveBackendConfig(q<HTMLTextAreaElement>("[name=becfg]").value);
      await host.setConfig({
        packagesRoot: q<HTMLInputElement>("[name=root]").value.trim(),
        editorCmd: q<HTMLInputElement>("[name=editor]").value.trim(),
        backendCmd: q<HTMLInputElement>("[name=becmd]").value.trim(),
        zenodo: {
          sandbox: server() === "sandbox",
          community: q<HTMLInputElement>("[name=zcommunity]").value.trim(),
          creator: { name: q<HTMLInputElement>("[name=cr_name]").value.trim(), orcid, affiliation: q<HTMLInputElement>("[name=cr_affil]").value.trim() },
        },
      });
    } catch (err) {
      saveError.textContent = (err as Error).message;
      saveError.hidden = false;
      return;
    }
    dlg.close("save");
    onSaved(q<HTMLInputElement>("[name=becmd]").value.trim() !== cfg.backendCmd);
  });
}
