// The library: your reproductions (packages the app's backend knows), a new one from a DOI, or a package folder
// opened from disk. Opening one switches the window to its numbered workflow (package.ts).
import { host, isBackendDown, JATS_ONLY, JatsAvailability, LibraryEntry, Work, ZenodoRecord } from "./api";
import { backendProblem } from "./backend";
import { brandHtml } from "./brand";
import { settingsDialog } from "./settings";
import type { ZenodoDeposit } from "./types";
import { $, confirmDialog, esc, modal, showError } from "./util";
import sticker from "../../resources/hex-sticker.png";

const STEPS: [string, string][] = [
  ["Choose an article", "A paper with open data and code, in the open-access subset of PubMed Central. Start from its DOI; ReproCrowd fetches its metadata and its JATS XML."],
  ["Run the reproducibility check", "Extract the results to reproduce, plan the analysis, let a coding agent write the script, run it and compare."],
  ["Check and sign it", "Look at every number and its provenance in the report. The deposit carries your name and ORCID."],
  ["Deposit on Zenodo", "The package becomes a citable record with a DOI that links to the paper, for anyone to find, re-run or compare."],
];

export const openPackage = (id: string) => {
  location.hash = `pkg=${encodeURIComponent(id)}`;
  location.reload();
};

export async function start() {
  document.title = "ReproCrowd";
  document.querySelector("#app")!.innerHTML = `
  <header class="lib-header">${brandHtml()}<span class="spacer"></span>
    <a href="#" id="about-link" class="navlink">About ReproCrowd</a>
    <button id="settings-btn" class="ghost" title="Settings">⚙ Settings</button></header>
  <div id="ribbon" class="ribbon" hidden>Zenodo sandbox</div>
  <main class="library">
    <section class="hero"><div class="hero-grid container">
      <div class="hero-copy">
        <h1 class="hero-title">Crowdsourcing AI reproductions</h1>
        <p class="hero-sub">Check whether a paper's numbers reproduce from its own data and code, with an AI-assisted workflow you stay in control of. Sign the result with your name and deposit it on Zenodo, so the community can find it.</p>
        <div class="hero-actions"><button id="new-btn" class="btn-primary">New reproduction</button><button id="adopt-btn" class="btn-secondary">Open a package folder…</button></div>
        <p id="setup-note" class="notice" hidden></p>
      </div>
      <div class="hero-visual"><img src="${sticker}" alt="ReproCrowd" class="hero-sticker" /></div>
    </div></section>
    <section class="container lib-list"><div id="lib-body"><p class="muted">Loading…</p></div></section>
  </main>`;

  $("#settings-btn").addEventListener("click", () => settingsDialog(() => render()));
  $("#about-link").addEventListener("click", (e) => {
    e.preventDefault();
    host.openUrl("https://forrt.org/reprocrowd/");
  });
  $("#new-btn").addEventListener("click", () => newDialog());
  $("#adopt-btn").addEventListener("click", async () => {
    try {
      const id = await host.adoptFolder();
      if (id) openPackage(id);
    } catch (e) {
      showError("Opening the folder", e);
    }
  });
  await render();
}

async function setupNote() {
  const cfg = await host.getConfig().catch(() => undefined);
  if (!cfg) return;
  $("#ribbon").hidden = !cfg.zenodo.sandbox;
  const missing = [!cfg.zenodo.creator.name && "your name", !cfg.tokens[cfg.zenodo.sandbox ? "sandbox" : "zenodo"] && `a Zenodo token for ${cfg.zenodo.sandbox ? "the sandbox" : "zenodo.org"}`].filter(Boolean);
  const el = $("#setup-note");
  el.hidden = !missing.length;
  if (missing.length) {
    el.innerHTML = `To deposit reproductions, add ${esc(missing.join(" and "))} in <a href="#" id="setup-link">Settings</a>.`;
    $("#setup-link").addEventListener("click", (e) => {
      e.preventDefault();
      settingsDialog(() => render(), "zenodo");
    });
  }
}

function card(p: LibraryEntry) {
  const doi = p.identifiers?.doi ?? (p.identifier?.scheme === "doi" ? p.identifier.value : "");
  const r = p.resources ?? {};
  const counts = [["fulltext", "full text"], ["input", "input"], ["output", "output"]]
    .filter(([k]) => r[k])
    .map(([k, label]) => `${r[k]} ${label}${r[k] === 1 ? "" : "s"}`)
    .join(" · ");
  return `<article class="card pkg-card ${p.missing ? "missing" : ""}" data-id="${esc(p.id)}">
    <h3 class="pkg-title">${esc(p.title ?? p.path.split(/[\\/]/).pop())}</h3>
    ${doi ? `<p class="small"><a href="#" data-doi="${esc(doi)}">doi:${esc(doi)}</a></p>` : `<p class="small muted">No DOI</p>`}
    <p class="small muted mono path" title="${esc(p.path)}">${esc(p.path)}</p>
    <p class="small">${p.missing ? `<span class="warn-inline">The folder is missing.</span>` : esc(counts || "Empty so far")}</p>
    <p class="small deposit-line" data-dep-for="${esc(p.id)}"></p>
    <div class="actions">${p.missing ? "" : `<button class="btn-primary small-btn" data-open="${esc(p.id)}">Open</button><button data-reveal="${esc(p.id)}">Show folder</button>`}<button data-remove="${esc(p.id)}" title="Remove from the list (the files stay on disk)">Remove</button></div>
  </article>`;
}

function depositLine(ds: ZenodoDeposit[]) {
  const live = ds.filter((d) => d.status === "published" || d.status === "draft");
  if (!live.length) return "";
  const d = live[live.length - 1];
  return `<span class="badge st-${d.status}">${d.status === "published" ? "On Zenodo" : "Zenodo draft"}</span>${d.sandbox ? ` <span class="badge sandbox">Sandbox</span>` : ""}${d.doi ? ` <code>${esc(d.doi)}</code>` : ""}`;
}

async function render() {
  setupNote();
  const body = $("#lib-body");
  let list: LibraryEntry[];
  try {
    list = await host.listPackages();
  } catch (e) {
    if (isBackendDown(e)) {
      body.innerHTML = `<div class="offline-card"><h2>The ReproCrowd backend isn't running</h2><div class="backend-problem"></div></div>`;
      backendProblem($(".backend-problem", body), () => render());
    } else body.innerHTML = `<p class="warn">${esc((e as Error).message)}</p>`;
    return;
  }
  list = [...list].reverse();
  body.innerHTML = list.length
    ? `<h2 class="section-title">Your reproductions</h2><div class="cards">${list.map(card).join("")}</div>`
    : `<h2 class="section-title">How it works</h2>
       <ol class="process-cards">${STEPS.map(([t, d], i) => `<li class="card"><span class="card-step">${i + 1}</span><h3>${esc(t)}</h3><p>${esc(d)}</p></li>`).join("")}</ol>
       <p class="note">Your reproductions will be listed here. Start with <b>New reproduction</b>.</p>`;
  body.querySelectorAll<HTMLElement>("[data-open]").forEach((b) => b.addEventListener("click", () => openPackage(b.dataset.open!)));
  body.querySelectorAll<HTMLElement>(".pkg-title").forEach((t) =>
    t.addEventListener("click", () => {
      const c = t.closest<HTMLElement>(".pkg-card")!;
      if (!c.classList.contains("missing")) openPackage(c.dataset.id!);
    }),
  );
  body.querySelectorAll<HTMLElement>("[data-reveal]").forEach((b) => b.addEventListener("click", () => host.revealPackage(b.dataset.reveal!).catch((e) => showError("Showing the folder", e))));
  body.querySelectorAll<HTMLElement>("[data-doi]").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      host.openUrl(`https://doi.org/${a.dataset.doi}`);
    }),
  );
  body.querySelectorAll<HTMLElement>("[data-remove]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!(await confirmDialog("Remove from the list", "<p>The package is removed from ReproCrowd's list. Its folder and files stay on disk; open it again with <b>Open a package folder…</b>.</p>", "Remove"))) return;
      await host.removePackage(b.dataset.remove!).catch((e) => showError("Removing", e));
      render();
    }),
  );
  for (const p of list.filter((x) => !x.missing))
    host
      .packageDeposits(p.id)
      .then((ds) => {
        const el = body.querySelector<HTMLElement>(`[data-dep-for="${CSS.escape(p.id)}"]`);
        if (el) el.innerHTML = depositLine(ds);
      })
      .catch(() => {});
}

// ---------- a new reproduction ----------

function newDialog() {
  const dlg = modal("dlg-new", `<form method="dialog">
    <h3>New reproduction</h3>
    <label>DOI of the paper <span class="row"><input name="doi" placeholder="10.1371/journal.pbio.1002106 or https://doi.org/…" spellcheck="false" autofocus /><button type="button" id="nd-lookup">Look up</button></span></label>
    <div id="nd-work" class="work-box" hidden></div>
    <div id="nd-jats" hidden></div>
    <div id="nd-records" hidden></div>
    <label>Folder <span class="row"><input name="folder" spellcheck="false" /><button type="button" id="nd-folder">Choose…</button></span></label>
    <p class="hint">The package is a folder of plain files (a Frictionless data package); the folder must be new or empty.</p>
    <p class="nd-error warn-inline" hidden></p>
    <menu><button value="cancel">Cancel</button><button value="create" class="primary" id="nd-create" disabled>Create</button></menu></form>`);
  const q = <T extends HTMLElement = HTMLInputElement>(s: string) => dlg.querySelector<T>(s)!;
  const doiIn = q("[name=doi]");
  const folderIn = q("[name=folder]");
  const create = q<HTMLButtonElement>("#nd-create");
  let work: Work | undefined;
  let jats: JatsAvailability | undefined;
  let folderTouched = false;
  let lookedUp = "";
  let seq = 0;
  const err = (msg: string) => {
    q<HTMLElement>(".nd-error").textContent = msg;
    q<HTMLElement>(".nd-error").hidden = !msg;
  };
  const suggest = async () => {
    if (!folderTouched) folderIn.value = await host.suggestFolder(work?.doi || doiIn.value.trim() || "paper").catch(() => folderIn.value);
  };
  suggest();
  folderIn.addEventListener("input", () => (folderTouched = true));

  async function lookup() {
    const raw = doiIn.value.trim();
    if (!raw || raw === lookedUp) return;
    lookedUp = raw;
    const n = ++seq;
    const box = q<HTMLElement>("#nd-work");
    const jbox = q<HTMLElement>("#nd-jats");
    const rec = q<HTMLElement>("#nd-records");
    box.hidden = false;
    jbox.hidden = rec.hidden = true;
    create.disabled = true;
    box.innerHTML = `<span class="muted">Looking up ${esc(raw)} in OpenAlex…</span>`;
    work = jats = undefined;
    try {
      work = await host.lookupDoi(raw);
    } catch (e) {
      box.innerHTML = `<span class="warn-inline">${esc((e as Error).message)}</span>`;
      return;
    }
    if (n !== seq) return;
    box.innerHTML = `<b class="serif">${esc(work.title ?? "(no title)")}</b><br><span class="small">${esc(work.authors.slice(0, 5).join(", "))}${work.authors.length > 5 ? " et al." : ""}${work.year ? ` (${work.year})` : ""}${work.venue ? ` · <i>${esc(work.venue)}</i>` : ""}</span>
      <br><span class="small muted">${work.open_access ? "Open access" : "Not open access"}${work.license ? ` · license ${esc(work.license)}` : ""}</span>`;
    err("");
    suggest();
    jbox.hidden = false;
    jbox.innerHTML = `<p class="muted small">Checking Europe PMC for the article's JATS XML…</p>`;
    try {
      jats = await host.jatsAvailability(work.doi);
    } catch (e) {
      if (n !== seq) return;
      jbox.innerHTML = `<p class="warn-soft small">Couldn't check for JATS XML: ${esc((e as Error).message)} <a href="#" id="nd-retry">Try again</a></p>`;
      jbox.querySelector("#nd-retry")!.addEventListener("click", (ev) => {
        ev.preventDefault();
        lookedUp = "";
        lookup();
      });
      return;
    }
    if (n !== seq) return;
    if (!jats.available) {
      jbox.innerHTML = `<div class="refusal"><b>This article isn't supported.</b> ${esc(JATS_ONLY)}<br><span class="small">${esc(jats.reason ?? "")}</span></div>`;
      return;
    }
    jbox.innerHTML = `<p class="ok small">✓ JATS XML available from Europe PMC (${esc(jats.pmcid ?? "")}).</p>`;
    create.disabled = false;
    const records = await host.zenodoRecords(work.doi).catch(() => [] as ZenodoRecord[]);
    if (n !== seq || !records.length) return;
    rec.hidden = false;
    rec.innerHTML = `<p class="notice small">Already on Zenodo: ${records.length} ReproCrowd record${records.length > 1 ? "s" : ""} for this paper — ${records.map((r) => `<a href="#" data-url="${esc(r.url)}">${esc(r.creators.join("; ") || "record")}, ${esc(r.published ?? "")}</a>`).join(" · ")}. You can still make your own.</p>`;
    rec.querySelectorAll<HTMLElement>("[data-url]").forEach((a) =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        host.openUrl(a.dataset.url!);
      }),
    );
  }
  q("#nd-lookup").addEventListener("click", () => {
    lookedUp = "";
    lookup();
  });
  doiIn.addEventListener("change", lookup);
  doiIn.addEventListener("input", () => {
    if (doiIn.value.trim() !== lookedUp) create.disabled = true;
  });
  doiIn.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      lookup();
    }
  });
  q("#nd-folder").addEventListener("click", async () => {
    const dir = await host.chooseFolder(folderIn.value);
    if (dir) {
      folderIn.value = dir;
      folderTouched = true;
    }
  });
  q<HTMLFormElement>("form").addEventListener("submit", async (e) => {
    if ((e.submitter as HTMLButtonElement | null)?.value !== "create") return;
    e.preventDefault();
    if (!work || !jats?.available) return;
    if (!folderIn.value.trim()) return err("Choose a folder for the package.");
    create.disabled = true;
    create.textContent = "Creating…";
    err("");
    try {
      const r = await host.createPackage({ doi: work.doi, folder: folderIn.value.trim() });
      dlg.close("create");
      openPackage(r.id);
    } catch (e2) {
      create.disabled = false;
      create.textContent = "Create";
      err((e2 as Error).message);
    }
  });
}
