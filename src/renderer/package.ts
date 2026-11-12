// The package view: one paper's numbered workflow (step 1: the PDF next to the paper's text; then the reproduction).
import { brandHtml } from "./brand";
import { ApiError, host, Info, isBackendDown, pkg } from "./api";
import { backendProblem, TITLES } from "./backend";
import { PdfView } from "./pdfview";
import { TextPane } from "./reader";
import { Wizard } from "./repro/wizard";
import { settingsDialog } from "./settings";
import { sourcesDialog } from "./sources";
import { loadUiState, UiState } from "./state";
import type { Document, PackageSummary, Resource } from "./types";
import { $, esc, showError } from "./util";

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
<main id="reader-view">
  <section id="pdf-pane">
    <div class="pane-head">
      <select id="pdf-select"></select>
      <span class="spacer"></span>
      <button id="zoom-out">−</button><button id="zoom-fit">Fit</button><button id="zoom-in">+</button>
    </div>
    <div id="pdf"></div>
  </section>
  <section id="text-pane"></section>
</main>
<div id="rp-body" hidden></div>
<footer id="rp-foot"><button id="rp-back" disabled>← Back</button><span id="status"></span><button id="rp-next" class="primary" disabled>Next →</button></footer>`;

// ---------- state ----------

let info: Info;
let summary: PackageSummary | undefined;
let meta: any = null;
let fulltexts: Resource[] = [];
let extractions: Resource[] = [];
let state: UiState = loadUiState(null);
let restoring = true;
const docs = new Map<string, { hash: string; doc: Document }>();

const pdf = new PdfView($("#pdf"));
pdf.clear();

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

// ---------- text pane ----------

const text = new TextPane($("#text-pane"), {
  pdf,
  meta: () => meta,
  status,
  openSources,
  changed() {
    state.textMode = text.mode;
    state.extraction = text.name;
    state.rawSource = text.rawSource;
    save();
    pickPdfFor(text.doc);
  },
});

async function loadDoc(name: string): Promise<Document> {
  const res = extractions.find((x) => x.name === name);
  if (!res) throw new Error(`no extraction ${name}`);
  const hit = docs.get(name);
  if (hit?.hash === res.hash) return hit.doc;
  const doc = await pkg.get<Document>(`/extractions/${encodeURIComponent(name)}`);
  docs.set(name, { hash: res.hash, doc });
  return doc;
}

/** Re-read full texts and extractions (after the Sources dialog or a step changed them). */
async function refreshSources(preferExtraction?: string) {
  [fulltexts, extractions] = await Promise.all([pkg.get<Resource[]>("/fulltexts"), pkg.get<Resource[]>("/extractions")]);
  renderPdfSelect();
  if (preferExtraction) text.name = preferExtraction;
  await text.update(extractions, fulltexts, preferExtraction ?? state.extraction);
  if (!pdf.loaded) await showPdf(state.pdf || defaultPdf(text.doc));
}

function openSources() {
  sourcesDialog({ changed: refreshSources, showPdf: (name) => showPdf(`ft:${name}`) });
}

// ---------- PDF pane ----------

const pdfOptions = () =>
  fulltexts.filter((f) => f.reprocrowd.kind === "fulltext" && f.reprocrowd.flavor === "pdf").map((f) => ({ value: `ft:${f.name}`, label: f.name }));

/** The PDF the extraction's coordinates refer to: one of its sources, or the PDF a source was converted from. */
function defaultPdf(doc?: Document): string {
  const opts = pdfOptions().map((o) => o.value);
  for (const name of doc?.source.fulltexts ?? []) {
    if (opts.includes(`ft:${name}`)) return `ft:${name}`;
    const ft = fulltexts.find((f) => f.name === name);
    if (ft?.reprocrowd.kind === "fulltext" && ft.reprocrowd.origin.type === "conversion" && opts.includes(`ft:${ft.reprocrowd.origin.source}`)) return `ft:${ft.reprocrowd.origin.source}`;
  }
  return opts.includes(state.pdf) ? state.pdf : opts[0] ?? "";
}

let lastDocForPdf: Document | undefined;
function pickPdfFor(doc?: Document) {
  if (!doc || doc === lastDocForPdf) return;
  lastDocForPdf = doc;
  const want = defaultPdf(doc);
  if (want && want !== loadedPdf) showPdf(want);
}

function renderPdfSelect() {
  const opts = pdfOptions();
  $("#pdf-select").innerHTML = opts.length ? opts.map((o) => `<option value="${esc(o.value)}" ${o.value === loadedPdf ? "selected" : ""}>${esc(o.label)}</option>`).join("") : `<option value="">(no PDF)</option>`;
}

let loadedPdf = "";
async function showPdf(value: string) {
  if (!value) {
    loadedPdf = "";
    pdf.clear("No PDF yet. Add the paper's PDF, or download one, in Sources.");
    return;
  }
  if (value === loadedPdf) return;
  loadedPdf = value;
  state.pdf = value;
  save();
  renderPdfSelect();
  status("Opening PDF…");
  try {
    const data = await pkg.bytes(`/fulltexts/${encodeURIComponent(value.slice(3))}/content`);
    await pdf.load(data);
    pdf.fitWidth();
    status("");
  } catch (e) {
    status(`Could not open the PDF: ${(e as Error).message}`, "error");
  }
}

$("#pdf-select").addEventListener("change", (e) => showPdf((e.target as HTMLSelectElement).value));
$("#zoom-in").addEventListener("click", () => pdf.zoom(1.15));
$("#zoom-out").addEventListener("click", () => pdf.zoom(1 / 1.15));
$("#zoom-fit").addEventListener("click", () => pdf.fitWidth());

// ---------- the numbered workflow ----------

const wizard = new Wizard($("#reader-view"), {
  get state() {
    return state;
  },
  save,
  info: () => info,
  summary: () => summary!,
  refreshSummary: async () => {
    summary = await pkg.get<PackageSummary>("");
  },
  meta: () => meta,
  fulltexts: () => fulltexts,
  extractions: () => extractions,
  refreshSources,
  loadDoc,
  pdf,
  status,
  openSources,
  showInReader: async (name: string) => {
    await wizard.go(0);
    await text.show(name);
  },
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
  $("#reader-view").hidden = true;
  $("#rp-body").hidden = true;
  $("#rp-foot").hidden = true;
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
  await refreshSources().catch(() => {});
  await wizard.render();
}

export async function start(id: string) {
  try {
    await host.openPackage(id);
    info = await host.info();
    $("#paper-title").textContent = info.title;
    document.title = `ReproCrowd — ${info.title}`;
    state = loadUiState(await host.loadState().catch(() => null));
    text.mode = state.textMode;
    text.rawSource = state.rawSource;
    text.name = state.extraction;
    status("Connecting to the ReproCrowd backend…");
    summary = await host.connect();
    if (summary.metadata_error) status(`OpenAlex metadata: ${summary.metadata_error}`, "error");
    else status("");
    meta = await pkg.get<any>("/metadata").catch(() => null);
    if (meta?.title) $("#paper-title").textContent = meta.title;
    await refreshSources();
    restoring = false;
    // Only now: a click during the restore above would be undone by it.
    await wizard.start();
    $<HTMLButtonElement>("#sources-btn").disabled = false;
  } catch (e) {
    restoring = false;
    offline(e);
  }
}

window.addEventListener("beforeunload", () => {
  if (!restoring && !leaving) host.saveState(state).catch(() => {});
});
