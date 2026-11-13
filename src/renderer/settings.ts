// Settings: you as the creator of Zenodo records, the Zenodo server and token, where packages go, the code editor,
// the app's backend (status, output, its configuration: LLM and agent profiles, OpenAlex), and updates.
import { Config, host, UpdateStatus } from "./api";
import { esc, modal, showError } from "./util";

const ZENODO = { zenodo: "https://zenodo.org", sandbox: "https://sandbox.zenodo.org" } as const;
const tokenPage = (server: keyof typeof ZENODO) => `${ZENODO[server]}/account/settings/applications/tokens/new/`;
const ORCID = /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/;

export async function settingsDialog(onSaved: (changed: boolean) => void, focus?: "zenodo" | "backend") {
  let cfg: Config;
  // Reading the settings asks the system keyring and the backend; if that takes a moment, say so.
  let waiting: HTMLDialogElement | undefined;
  const slow = setTimeout(() => {
    waiting = modal("dlg-waiting", `<p class="waiting"><span class="spinner" aria-hidden="true"></span> Opening the settings…</p>
      <p class="hint">Reading your saved settings, the tokens in the system keyring, and the backend's status.</p>`);
    waiting.addEventListener("cancel", (e) => e.preventDefault());
  }, 250);
  try {
    cfg = await host.getConfig();
  } catch (e) {
    return showError("Opening the settings", e);
  } finally {
    clearTimeout(slow);
    waiting?.close();
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
      <p class="hint" id="upd-explain"></p>
      <label class="inline"><input type="checkbox" name="checkupd" ${cfg.checkUpdates ? "checked" : ""} /> Check for a new version when ReproCrowd starts</label>
      <label class="inline" id="upd-auto-row" hidden><input type="checkbox" name="autoupd" ${cfg.autoUpdate ? "checked" : ""} /> Download new versions in the background, and install them when ReproCrowd restarts or quits</label>
      <div class="actions"><button type="button" id="upd-check">Check now</button><button type="button" id="upd-download" class="primary" hidden>Download</button><button type="button" id="upd-install" class="primary" hidden>Restart and install</button><button type="button" id="upd-page" hidden>Open the release page</button></div>
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
  const upd = {
    status: q<HTMLElement>("#upd-status"),
    check: q<HTMLButtonElement>("#upd-check"),
    download: q<HTMLButtonElement>("#upd-download"),
    install: q<HTMLButtonElement>("#upd-install"),
    page: q<HTMLButtonElement>("#upd-page"),
  };
  let updUrl = "";
  let installOnQuit = false;
  let updPoll: number | undefined;
  const showUpdate = (s: UpdateStatus) => {
    const say = (cls: string, html: string) => (upd.status.innerHTML = `<span class="${cls}">${html}</span>`);
    upd.download.hidden = s.state !== "available";
    upd.download.disabled = false;
    upd.install.hidden = s.state !== "ready";
    upd.check.disabled = s.state === "checking" || s.state === "downloading";
    updUrl = "url" in s ? (s.url ?? "") : "";
    upd.page.hidden = !updUrl;
    upd.page.textContent = s.state === "manual" || s.state === "error" ? "Open the release page" : "Release notes";
    if (s.state === "idle") say("muted", `You have ReproCrowd ${esc(s.current)}.`);
    else if (s.state === "disabled") say("muted", esc(s.reason));
    else if (s.state === "checking") say("muted", "Checking…");
    else if (s.state === "not-available") say("muted", `You're up to date (${esc(s.current)}).${s.note ? ` ${esc(s.note)}` : ""}`);
    else if (s.state === "available") say("ok", `Version ${esc(s.version)} is available (you have ${esc(s.current)}).`);
    else if (s.state === "downloading") say("ok", `Version ${esc(s.version)} is available; downloading… ${s.percent} %`);
    else if (s.state === "ready") say("ok", `✓ Version ${esc(s.version)} is downloaded. Restart to install it${installOnQuit ? " (or it installs when you quit)" : ""}.`);
    else if (s.state === "manual") say("ok", `Version ${esc(s.version)} is available (you have ${esc(s.current)}). ${esc(s.howTo)}`);
    else if (s.state === "error") upd.status.innerHTML = `<span class="warn-inline">${esc(s.message)}</span>${s.detail ? `<br><span class="muted">${esc(s.detail)}</span>` : ""}`;
    // While downloading, follow the progress until the dialog closes.
    window.clearTimeout(updPoll);
    if ((s.state === "downloading" || s.state === "checking") && dlg.open) updPoll = window.setTimeout(refreshUpdate, 1000);
  };
  const refreshUpdate = async () => {
    try {
      const info = await host.updateInfo();
      q<HTMLElement>("#upd-explain").textContent = info.explain;
      upd.check.hidden = info.mode === "off";
      q<HTMLElement>("#upd-auto-row").hidden = info.mode !== "auto";
      installOnQuit = info.installOnQuit;
      showUpdate(info.status);
    } catch (err) {
      upd.status.innerHTML = `<span class="warn-inline">${esc((err as Error).message)}</span>`;
    }
  };
  void refreshUpdate();
  upd.check.addEventListener("click", async () => {
    showUpdate({ state: "checking", current: "" });
    try {
      showUpdate(await host.checkForUpdates());
    } catch (err) {
      upd.status.innerHTML = `<span class="warn-inline">${esc((err as Error).message)}</span>`;
      upd.check.disabled = false;
    }
  });
  upd.download.addEventListener("click", async () => {
    upd.download.disabled = true;
    try {
      showUpdate(await host.downloadUpdate());
    } catch (err) {
      showError("Downloading the update", err);
      void refreshUpdate();
    }
  });
  upd.install.addEventListener("click", () => host.installUpdate().catch((err) => showError("Installing the update", err)));
  upd.page.addEventListener("click", () => updUrl && host.openUrl(updUrl).catch((err) => showError("Opening the release page", err)));
  dlg.addEventListener("close", () => window.clearTimeout(updPoll));

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
        checkUpdates: q<HTMLInputElement>("[name=checkupd]").checked,
        autoUpdate: q<HTMLInputElement>("[name=autoupd]").checked,
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
