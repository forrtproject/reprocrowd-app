// The package view: one paper's numbered workflow. The paper's text is its JATS XML, which the app retrieves; steps 2
// and 3 show it with the extracted items aligned next to it.
import { brandHtml } from "./brand";
import { ApiError, host, Info, isBackendDown, pkg } from "./api";
import { backendProblem, TITLES } from "./backend";
import { Wizard } from "./repro/wizard";
import { settingsDialog } from "./settings";
import { loadUiState, UiState } from "./state";
import type { Document, PackageSummary, Resource } from "./types";
import { $, esc } from "./util";

document.querySelector("#app")!.innerHTML = `
<header>
  ${brandHtml()}
  <button id="library-btn" class="ghost" title="All reproductions">← Library</button>
  <span id="paper-title"></span>
  <nav id="route"></nav>
  <button id="settings-btn" title="Settings">⚙ Settings</button>
</header>
<div id="banner" class="banner" hidden></div>
<div id="offline" class="offline" hidden></div>
<div id="rp-body"></div>
<footer id="rp-foot"><button id="rp-back" disabled>← Back</button><span id="status"></span><button id="rp-next" class="primary" disabled>Next →</button></footer>`;

// ---------- state ----------

let info: Info;
let summary: (PackageSummary & { jats?: string; example?: boolean }) | undefined;
let meta: any = null;
let state: UiState = loadUiState(null);
let restoring = true;
const docs = new Map<string, { hash: string; doc: Document }>();

function status(msg: string, kind: "info" | "error" = "info") {
  const el = $("#status");
  el.textContent = msg;
  el.className = kind;
}

let saveTimer: number | undefined;
function save() {
  if (restoring) return;
  clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => host.saveState(state).catch(() => {}), 400);
}

async function loadDoc(name: string): Promise<Document> {
  const res = (await pkg.get<Resource[]>("/extractions")).find((x) => x.name === name);
  if (!res) throw new Error(`no extraction ${name}`);
  const hit = docs.get(name);
  if (hit?.hash === res.hash) return hit.doc;
  const doc = await pkg.get<Document>(`/extractions/${encodeURIComponent(name)}`);
  docs.set(name, { hash: res.hash, doc });
  return doc;
}

// ---------- the numbered workflow ----------

const wizard = new Wizard({
  get state() {
    return state;
  },
  save,
  info: () => info,
  summary: () => summary!,
  refreshSummary: async () => {
    summary = { ...(await pkg.get<PackageSummary>("")), jats: summary?.jats };
  },
  meta: () => meta,
  jats: () => summary?.jats ?? "",
  loadDoc,
  status,
});

$("#settings-btn").addEventListener("click", () => settingsDialog((reconnect) => (reconnect ? location.reload() : status("Settings saved"))));
let leaving = false;
$("#library-btn").addEventListener("click", async () => {
  if (!restoring) await host.saveState(state).catch(() => {});
  leaving = true;
  await host.openLibrary().catch(() => {});
  location.hash = "";
  location.reload();
});

// ---------- start ----------

/** The package couldn't be opened: the backend can't be started, or the package has a problem. */
function offline(e: unknown) {
  status("");
  const el = $("#offline");
  el.hidden = false;
  $("#rp-body").hidden = true;
  $("#rp-foot").hidden = true;
  if (e instanceof ApiError && e.code === "jats_unavailable") {
    el.innerHTML = `<div class="offline-card"><h2>This article isn't supported</h2><p>${esc(e.message)}</p>
      <p class="hint">ReproCrowd works from the article's JATS XML, which Europe PMC provides for the open-access subset of PubMed Central.</p>
      <div class="actions"><button class="primary" id="back-lib">← Library</button></div></div>`;
    $("#back-lib").addEventListener("click", () => $("#library-btn").click());
    return;
  }
  if (!isBackendDown(e)) {
    el.innerHTML = `<div class="offline-card"><h2>Couldn't open the package</h2><p>${esc((e as Error).message)}</p>
      <div class="actions"><button class="primary" id="retry">Retry</button><button id="back-lib">← Library</button></div></div>`;
    $("#retry").addEventListener("click", () => location.reload());
    $("#back-lib").addEventListener("click", () => $("#library-btn").click());
    return;
  }
  el.innerHTML = `<div class="offline-card"><h2>The ReproCrowd backend isn't running</h2><div class="backend-problem"></div></div>`;
  backendProblem($(".backend-problem", el), () => location.reload());
}

// ---------- losing the backend while working ----------

let bannerPoll: number | undefined;
window.addEventListener("reprocrowd-backend-down", (e) => {
  if (restoring || !$("#offline").hidden) return; // the start screen handles it
  const { code, message } = (e as CustomEvent).detail as { code: string; message: string };
  const el = $("#banner");
  el.hidden = false;
  el.innerHTML = `<b>${esc(TITLES[code] ?? "Lost the connection to the ReproCrowd backend")}.</b> ${esc(message)}
    <span class="spacer"></span><span class="muted small">Reconnects automatically.</span><button id="banner-retry">Retry now</button><button id="banner-settings">Settings…</button>`;
  $("#banner-retry").addEventListener("click", reconnect);
  $("#banner-settings").addEventListener("click", () => settingsDialog(() => reconnect()));
  clearInterval(bannerPoll);
  bannerPoll = window.setInterval(reconnect, 5000);
});

async function reconnect() {
  const c = await host.checkBackend().catch(() => undefined);
  if (!c?.ok) return;
  clearInterval(bannerPoll);
  $("#banner").hidden = true;
  status("Reconnected to the ReproCrowd backend");
  await wizard.render();
}

export async function start(id: string) {
  try {
    await host.openPackage(id);
    info = await host.info();
    $("#paper-title").textContent = info.title;
    document.title = `ReproCrowd — ${info.title}`;
    state = loadUiState(await host.loadState().catch(() => null));
    status("Opening the package (retrieving the paper's text if needed)…");
    summary = await host.connect();
    if (summary.metadata_error) status(`OpenAlex metadata: ${summary.metadata_error}`, "error");
    else status("");
    meta = await pkg.get<any>("/metadata").catch(() => null);
    if (meta?.title) $("#paper-title").textContent = meta.title;
    if (summary.example) {
      const b = document.createElement("span");
      b.className = "badge example-badge";
      b.textContent = "example";
      b.title = "This is a bundled example reproduction";
      $("#paper-title")?.after(b);
    }
    restoring = false;
    await wizard.start();
  } catch (e) {
    restoring = false;
    offline(e);
  }
}

window.addEventListener("beforeunload", () => {
  if (!restoring && !leaving) host.saveState(state).catch(() => {});
});
