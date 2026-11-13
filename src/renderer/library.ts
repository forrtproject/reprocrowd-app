// The library: your reproductions (packages the app's backend knows), a new one from a DOI, or a package folder
// opened from disk. Opening one switches the window to its numbered workflow (package.ts).
import { host, isBackendDown, JATS_ONLY, JatsAvailability, LibraryEntry, LlmModel, LlmModels, Work, ZenodoRecord } from "./api";
import { backendProblem } from "./backend";
import { brandHtml } from "./brand";
import { settingsDialog } from "./settings";
import type { ZenodoDeposit } from "./types";
import { $, confirmDialog, esc, modal, showError } from "./util";
import sticker from "../../resources/hex-sticker.png";

const STEPS: [string, string][] = [
  ["Choose an article", "A paper with open data and code. Start from its DOI (ReproCrowd fetches the metadata and its JATS XML) or from a local PDF, DOCX or Markdown file (kept local, not deposited)."],
  ["Run the reproducibility check", "Extract the results to reproduce, plan the analysis, let a coding agent write the script, run it and compare."],
  ["Check and sign it", "Look at every number and its provenance in the report. The deposit carries your name and ORCID."],
  ["Deposit on Zenodo", "For a published paper, the package becomes a citable record with a DOI that links to the paper, for anyone to find, re-run or compare."],
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
        <div class="hero-actions"><button id="new-btn" class="btn-primary">New from DOI</button><button id="file-btn" class="btn-secondary">New from file</button><button id="adopt-btn" class="btn-secondary">Open a package folder…</button></div>
        <p id="setup-note" class="notice" hidden></p>
      </div>
      <div class="hero-visual"><img src="${sticker}" alt="ReproCrowd" class="hero-sticker" /></div>
    </div></section>
    <section class="container lib-list"><div id="lib-body"><p class="muted">Loading…</p></div></section>
    <section class="container llm-models"><div id="llm-models"></div></section>
  </main>`;

  $("#settings-btn").addEventListener("click", () => settingsDialog(() => render()));
  $("#about-link").addEventListener("click", (e) => {
    e.preventDefault();
    host.openUrl("https://forrt.org/reprocrowd/");
  });
  $("#new-btn").addEventListener("click", () => newDialog());
  $("#file-btn").addEventListener("click", () => newFileDialog());
  $("#adopt-btn").addEventListener("click", async () => {
    try {
      const id = await host.adoptFolder();
      if (id) openPackage(id);
    } catch (e) {
      showError("Opening the folder", e);
    }
  });
  await render();
  await renderLlmModels();
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

/** The starting page: the LLM models the user can actually use, grouped by provider/family, so they can turn off whole
 * families or single models and not spend tokens on them by accident. */
async function renderLlmModels(refresh = false) {
  const el = $("#llm-models");
  // Detecting asks each installed agent CLI (OpenCode, T3 Code, Claude Code), which can take a few seconds.
  el.innerHTML = `<h2 class="section-title">LLM models</h2>
    <div class="card llm-models-card"><p class="waiting muted"><span class="spinner" aria-hidden="true"></span> Looking for the models your coding agents offer (OpenCode, T3 Code, Claude Code)…</p></div>`;
  let m: LlmModels;
  try {
    m = await host.llmModels(refresh);
  } catch {
    el.innerHTML = "";
    return;
  }
  const off = new Set(m.disabled);
  const byFamily = new Map<string, LlmModel[]>();
  for (const x of m.available) {
    const a = byFamily.get(x.provider) ?? [];
    a.push(x);
    byFamily.set(x.provider, a);
  }
  const isOn = (x: LlmModel) => !off.has(x.id);
  const familyHtml = [...byFamily.entries()]
    .map(([family, models]) => `<div class="llm-model-family" data-family="${esc(family)}">
      <label class="llm-family"><input type="checkbox" data-family-toggle="${esc(family)}" /> <b>${esc(family)}</b> <span class="muted small">${models.length} model${models.length === 1 ? "" : "s"}</span></label>
      <div class="llm-model-list">${models
        .map((x) => `<label class="inline llm-model"><input type="checkbox" data-model="${esc(x.id)}" ${isOn(x) ? "checked" : ""} /> <code>${esc(x.name)}</code></label>`)
        .join("")}</div>
    </div>`)
    .join("");
  el.innerHTML = `<h2 class="section-title">LLM models <button class="ghost icon-btn" id="llm-recheck" title="Refresh the model list" aria-label="Refresh the model list"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg></button></h2>
    <div class="card llm-models-card">${familyHtml || '<p class="muted">No models found. Add one to your agent\u2019s config, then refresh.</p>'}
      ${m.claudeNeedsLogin ? `<div class="claude-login"><p class="hint">Claude Code is installed but not signed in.</p><button id="claude-login" class="btn">Log in to Claude Code</button></div>` : ""}</div>`;
  el.querySelector("#llm-recheck")?.addEventListener("click", () => renderLlmModels(true));
  el.querySelector("#claude-login")?.addEventListener("click", async () => {
    try {
      const msg = await host.claudeLogin();
      const hint = el.querySelector(".claude-login .hint");
      if (hint) hint.textContent = msg;
    } catch (e) {
      showError("Logging in to Claude Code", e);
    }
  });
  const syncFamilies = () => {
    el.querySelectorAll<HTMLInputElement>("[data-family-toggle]").forEach((f) => {
      const models = m.available.filter((x) => x.provider === f.dataset.familyToggle);
      const on = models.filter(isOn).length;
      f.checked = on === models.length && models.length > 0;
      f.indeterminate = on > 0 && on < models.length;
    });
  };
  syncFamilies();
  const save = () => host.setLlmModels([...off]).catch((e) => showError("Saving the model choice", e));
  el.querySelectorAll<HTMLInputElement>("[data-model]").forEach((cb) =>
    cb.addEventListener("change", () => {
      if (cb.checked) off.delete(cb.dataset.model!);
      else off.add(cb.dataset.model!);
      syncFamilies();
      save();
    }),
  );
  el.querySelectorAll<HTMLInputElement>("[data-family-toggle]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const family = cb.dataset.familyToggle!;
      for (const x of m.available) if (x.provider === family) {
        if (cb.checked) off.delete(x.id);
        else off.add(x.id);
      }
      el.querySelectorAll<HTMLInputElement>(`[data-family="${CSS.escape(family)}"] [data-model]`).forEach((mc) => (mc.checked = cb.checked));
      syncFamilies();
      save();
    }),
  );
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
    <h3>New from DOI</h3>
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

/** A reproduction from a paper the user already has on disk: a PDF, DOCX or Markdown file. No DOI or JATS needed. */
function newFileDialog() {
  const dlg = modal("dlg-new", `<form method="dialog">
    <h3>New from file</h3>
    <p class="hint">Point ReproCrowd at a paper you already have. The text is extracted (PDF, DOCX and Markdown) and you work from it; no DOI or JATS XML is needed.</p>
    <p class="warn-soft small">A reproduction from a local file stays on your computer: it can't be deposited on Zenodo, since the file may be unpublished research (a draft or a manuscript under review). For a published paper, start from its DOI.</p>
    <label>Paper file <span class="row"><input name="file" placeholder="paper.pdf, paper.docx or paper.md" spellcheck="false" /><button type="button" id="nf-file">Choose…</button></span></label>
    <label>Folder <span class="row"><input name="folder" spellcheck="false" /><button type="button" id="nf-folder">Choose…</button></span></label>
    <p class="hint">The package is a folder of plain files (a Frictionless data package); the folder must be new or empty.</p>
    <p class="nd-error warn-inline" hidden></p>
    <menu><button value="cancel">Cancel</button><button value="create" class="primary" id="nf-create" disabled>Create</button></menu></form>`);
  const q = <T extends HTMLElement = HTMLInputElement>(s: string) => dlg.querySelector<T>(s)!;
  const fileIn = q("[name=file]");
  const folderIn = q("[name=folder]");
  const create = q<HTMLButtonElement>("#nf-create");
  let folderTouched = false;
  const err = (msg: string) => {
    q<HTMLElement>(".nd-error").textContent = msg;
    q<HTMLElement>(".nd-error").hidden = !msg;
  };
  const canCreate = () => {
    create.disabled = !(fileIn.value.trim() && folderIn.value.trim());
  };
  fileIn.addEventListener("input", canCreate);
  folderIn.addEventListener("input", () => {
    folderTouched = true;
    canCreate();
  });
  q("#nf-file").addEventListener("click", async () => {
    const f = await host.chooseFile();
    if (!f) return;
    fileIn.value = f;
    if (!folderTouched) folderIn.value = await host.suggestFolder(f.split(/[\\/]/).pop()!.replace(/\.[^.]+$/, "") || "paper").catch(() => folderIn.value);
    canCreate();
  });
  q("#nf-folder").addEventListener("click", async () => {
    const dir = await host.chooseFolder(folderIn.value);
    if (dir) {
      folderIn.value = dir;
      folderTouched = true;
      canCreate();
    }
  });
  q<HTMLFormElement>("form").addEventListener("submit", async (e) => {
    if ((e.submitter as HTMLButtonElement | null)?.value !== "create") return;
    e.preventDefault();
    if (!fileIn.value.trim() || !folderIn.value.trim()) return;
    create.disabled = true;
    create.textContent = "Creating…";
    err("");
    try {
      const r = await host.createPackageFromFile({ path: fileIn.value.trim(), folder: folderIn.value.trim() });
      dlg.close("create");
      openPackage(r.id);
    } catch (e2) {
      create.disabled = false;
      create.textContent = "Create";
      err((e2 as Error).message);
    }
  });
}
