// Step 1: the inputs of the reproduction.
//   The paper's text: its JATS XML from Europe PMC, parsed (retrieved by the app; shown here, nothing to choose).
//   Input files:      the study's published data (POST …/inputs; zip archives are unpacked).
import { ApiError, host, pkg } from "../api";
import { countSentences } from "../doc";
import { ROLES, type AcquireFailure, type AcquirePreview, type AcquireResult, type Document, type Resource, type Role } from "../types";
import { confirmDialog, esc, modal, sizeText } from "../util";
import type { Step, Wizard } from "./wizard";

const REPOSITORIES = "Zenodo, OSF, GitHub, GitLab, Figshare";
/** The failure code of a file left out because it didn't fit in the download limit. */
const SKIPPED = "acquire_too_large";

/** The status-bar line after a fetch. */
function fetchStatus(r: AcquireResult): [string, "info" | "error"] {
  if (!r.links.length) return [`No data or code links found in the manuscript (looked for ${REPOSITORIES})`, "info"];
  const n = r.resources.length;
  const links = r.failures.filter((x) => !x.file).length;
  const skipped = r.failures.filter((x) => x.file && x.code === SKIPPED).length;
  const files = r.failures.filter((x) => x.file && x.code !== SKIPPED).length;
  const problems = [
    links && `${links} link(s) could not be read`,
    files && `${files} file(s) could not be downloaded`,
    skipped && `${skipped} file(s) skipped (download limit)`,
  ].filter(Boolean);
  return [`Fetched supplemental materials: ${n} file(s) added from ${r.links.length} link(s)${problems.length ? `; ${problems.join(", ")} (see the list below)` : ""}`, n || !problems.length ? "info" : "error"];
}

type InputRes = Resource & { reprocrowd: Extract<Resource["reprocrowd"], { kind: "input" }> };

/** A file's size for the fetch dialog; 0 means the repository didn't say. */
const fileSize = (n: number) => (n ? sizeText(n) : "size unknown");

/** Ask the user what to download: all files, only the checked ones, or nothing (null). Links that couldn't be
 *  read are listed with the reason; the download limit applies to each link, so a link whose chosen files add
 *  up to more than it is flagged (files beyond the limit would be skipped). */
function fetchDialog(p: AcquirePreview): Promise<{ action: "all" } | { action: "selected"; keys: string[] } | null> {
  return new Promise((resolve) => {
    const linkKey = (x: { repo: string; id: string }) => `${x.repo}:${x.id}`;
    const groups = p.links.map((link) => ({
      link,
      files: p.files.filter((f) => linkKey(f) === linkKey(link)),
      failure: p.failures.find((x) => linkKey(x) === linkKey(link)),
    }));
    const html = groups.map(({ link, files, failure }, g) => {
      const rows = files.map((f) =>
        `<label class="rp-file"><input type="checkbox" data-group="${g}" data-key="${esc(f.key)}" data-size="${f.size}" checked /><code>${esc(f.path)}</code> <span class="muted">${fileSize(f.size)}</span></label>`).join("");
      const note = failure ? `<p class="rp-fetch-warn">Could not be read: ${esc(failure.error)}</p>` : files.length ? "" : `<p class="muted">No files found.</p>`;
      const all = files.length ? `<label class="rp-file"><input type="checkbox" data-all="${g}" checked /> all ${files.length} file(s)</label>` : "";
      return `<div class="rp-group"><h4><a href="${esc(link.url)}" target="_blank" rel="noopener">${esc(link.label)}</a> <span class="muted">(${esc(link.kind)})</span></h4>
        ${note}${all}<div class="rp-group-files">${rows}</div><p class="rp-fetch-warn" data-over="${g}" hidden></p></div>`;
    }).join("");
    const unknown = p.files.filter((f) => !f.size).length;
    const dlg = modal("dlg-fetch", `
      <h3>Fetch supplemental materials</h3>
      <p>Found ${p.links.length} link(s) with ${p.files.length} file(s), <b>${sizeText(p.total_bytes)}</b> in total${unknown ? ` (plus ${unknown} file(s) of unknown size)` : ""}.
        Files beyond the ${sizeText(p.limit_bytes)} download limit per link are skipped. Choose what to download:</p>
      <div class="rp-fetch-list">${html}</div>
      <p class="muted" id="rp-fetch-sum"></p>
      <menu>
        <button value="cancel">Cancel</button>
        ${p.files.length ? `<button value="selected" class="primary">Download selected</button><button value="all" class="primary">Download all</button>` : ""}
      </menu>`);
    const boxes = (g?: number) => [...dlg.querySelectorAll<HTMLInputElement>(g === undefined ? "input[data-key]" : `input[data-group="${g}"]`)];
    const update = () => {
      const on = boxes().filter((c) => c.checked);
      const bytes = on.reduce((n, c) => n + Number(c.dataset.size), 0);
      dlg.querySelector("#rp-fetch-sum")!.textContent = `Selected: ${on.length} of ${p.files.length} file(s), ${sizeText(bytes)}`;
      const sel = dlg.querySelector<HTMLButtonElement>('button[value="selected"]');
      if (sel) sel.disabled = !on.length;
      groups.forEach((_, g) => {
        const mine = boxes(g);
        const all = dlg.querySelector<HTMLInputElement>(`input[data-all="${g}"]`);
        if (all) { all.checked = mine.every((c) => c.checked); all.indeterminate = !all.checked && mine.some((c) => c.checked); }
        const used = mine.filter((c) => c.checked).reduce((n, c) => n + Number(c.dataset.size), 0);
        const over = dlg.querySelector<HTMLElement>(`[data-over="${g}"]`)!;
        over.hidden = used <= p.limit_bytes;
        over.textContent = `The chosen files (${sizeText(used)}) exceed the ${sizeText(p.limit_bytes)} download limit; some will be skipped.`;
      });
    };
    dlg.querySelectorAll<HTMLInputElement>("input[data-all]").forEach((all) =>
      all.addEventListener("change", () => { boxes(Number(all.dataset.all)).forEach((c) => (c.checked = all.checked)); update(); }));
    boxes().forEach((c) => c.addEventListener("change", update));
    update();
    let keys: string[] = [];
    dlg.querySelectorAll<HTMLButtonElement>("menu button").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.value === "selected") keys = boxes().filter((c) => c.checked).map((c) => c.dataset.key!);
        dlg.close(b.value);
      });
    });
    dlg.addEventListener("close", () => {
      if (dlg.returnValue === "all") resolve({ action: "all" });
      else if (dlg.returnValue === "selected") resolve({ action: "selected", keys });
      else resolve(null);
    });
  });
}

export class InputsStep implements Step {
  title = "Inputs";
  private inputs: InputRes[] = [];
  private docs = new Map<string, Document | Error>();
  private fetch?: AcquireResult;
  /** Links ("repo:id") the user chose no files from in the last fetch, so the list doesn't call them empty. */
  private unchosen = new Set<string>();

  constructor(private w: Wizard) {}

  async load() {
    this.inputs = (await pkg.get<Resource[]>("/inputs")) as InputRes[];
    const name = this.w.app.jats();
    if (!(this.docs.get(name) && !(this.docs.get(name) instanceof Error))) this.docs.set(name, await this.w.app.loadDoc(name).catch((e) => e as Error));
  }

  /** The result of the last fetch: each detected link with the files it added and what failed, or why nothing was found. */
  private fetchList() {
    const f = this.fetch;
    if (!f) return "";
    if (!f.links.length)
      return `<div class="card rp-fetch">
      <div class="card-head"><h3>No data or code links found</h3></div>
      <p>The manuscript doesn't link to a record on a supported repository (${REPOSITORIES}).</p>
      <p class="hint">If the data or code is elsewhere (another repository, the journal's supplementary files, or "available on request"), download it yourself and add it with <b>Add files…</b> or <b>Add a folder…</b>.</p>
    </div>`;
    const rows = f.links.map((l) => {
      const mine = f.failures.filter((x) => x.repo === l.repo && x.id === l.id);
      const linkErr = mine.find((x) => !x.file);
      const skipped = mine.filter((x) => x.file && x.code === SKIPPED);
      const fileErrs = mine.filter((x) => x.file && x.code !== SKIPPED);
      const files = f.resources.filter((r) => {
        const m = r.reprocrowd;
        return m.kind === "input" && m.origin.type === "fetched" && m.origin.repo === l.repo && m.origin.id === l.id;
      }).length;
      const parts: string[] = [];
      if (files) parts.push(`<span class="ok small">✓ ${files} file(s) added</span>`);
      if (linkErr) parts.push(`<span class="warn-inline">${files ? "stopped early" : "could not be fetched"}: ${esc(linkErr.error)}</span>`);
      if (!files && !linkErr && !fileErrs.length && !skipped.length)
        parts.push(`<span class="muted">${this.unchosen.has(`${l.repo}:${l.id}`) ? "no files chosen" : "no downloadable files in this record"}</span>`);
      const group = (list: AcquireFailure[], summary: string) =>
        list.length
          ? `<details><summary class="warn-inline">${list.length} ${summary}</summary><ul>${list
              .map((x) => `<li><a href="${esc(x.file_url ?? "")}" target="_blank" rel="noopener">${esc(x.file ?? "")}</a>: ${esc(x.error)}</li>`)
              .join("")}</ul></details>`
          : "";
      const details = group(skipped, "file(s) skipped: over the download limit") + group(fileErrs, "file(s) could not be downloaded");
      return `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a> <span class="muted">(${esc(l.kind)})</span> — ${parts.join("; ")}${details}</li>`;
    }).join("");
    return `<div class="card rp-fetch">
      <div class="card-head"><h3>Detected data &amp; code links</h3><span class="muted">${f.links.length} link(s)</span></div>
      <ul class="rp-links">${rows}</ul>
      <p class="hint">Click a link to open it; files it added appear in the table above. Files that were skipped or could not be downloaded can be downloaded from their links and added by hand.</p>
    </div>`;
  }

  draw(body: HTMLElement) {
    const app = this.w.app;
    const d = this.docs.get(app.jats());
    const ids = app.summary().identifiers ?? {};
    const contents = d instanceof Error ? `<span class="warn-inline">${esc(d.message)}</span>` : d ? `${d.sections.length} sections · ${countSentences(d)} sentences · ${d.figures.length} figures · ${d.tables.length} tables` : "";
    const archives = this.inputs.filter((r) => this.inputs.some((m) => m.reprocrowd.archive === r.name));
    const loose = this.inputs.filter((r) => !r.reprocrowd.archive && !archives.includes(r));
    const roleSel = (r: InputRes) => `<select data-f="role" ${r.reprocrowd.used ? "" : "disabled"}>${ROLES.map((x) => `<option ${x === r.reprocrowd.role ? "selected" : ""}>${x}</option>`).join("")}</select>`;
    const row = (r: InputRes, nested: string) => `<tr data-name="${esc(r.name)}" class="${nested} ${r.reprocrowd.used ? "" : "rp-disabled"}">
        <td><input type="checkbox" data-f="used" ${r.reprocrowd.used ? "checked" : ""} title="Use this file in the reproduction" /></td>
        <td><code>${esc(nested ? r.path.split("/").slice(2).join("/") || r.path : r.path.replace(/^inputs\//, ""))}</code></td><td>${roleSel(r)}</td>
        <td><input data-f="note" value="${esc(r.reprocrowd.note)}" placeholder="note" ${r.reprocrowd.used ? "" : "disabled"} /></td>
        <td class="nowrap">${sizeText(r.bytes)}</td><td>${nested ? "" : `<button data-act="del" title="Delete from the package">✕</button>`}</td></tr>`;
    let rows = loose.map((r) => row(r, "")).join("");
    for (const a of archives) {
      const members = this.inputs.filter((m) => m.reprocrowd.archive === a.name);
      const off = members.filter((m) => !m.reprocrowd.used).length;
      rows += `<tr class="rp-archive" data-name="${esc(a.name)}"><td></td><td colspan="3"><span class="badge">zip</span> <b>${esc(a.path.replace(/^inputs\//, ""))}</b> <span class="muted">${members.length} files unpacked${off ? `, ${off} not used` : ""}</span></td><td class="nowrap">${sizeText(a.bytes)}</td><td><button data-act="del" title="Remove the archive and all its files">✕</button></td></tr>`;
      rows += members.map((m) => row(m, "rp-nested")).join("");
    }
    const counts = ROLES.map((r) => `${this.inputs.filter((i) => i.reprocrowd.used && i.reprocrowd.role === r && !archives.includes(i)).length} ${r}`).join(" · ");
    const uploading = this.w.running.has("upload");
    const fetching = this.w.running.has("fetch");
    const busy = uploading || fetching;

    body.innerHTML = `
    <div class="card">
      <div class="card-head"><h3>The paper's text</h3><span class="ok small">✓ JATS XML${ids.pmcid ? ` from Europe PMC (${esc(ids.pmcid)})` : ""}</span></div>
      <p class="small">${contents}</p>
      <p class="hint">Targets (step 2) and analysis steps (step 3) are extracted from this text and point into it.</p>
    </div>
    <div class="card rp-drop">
      <div class="card-head"><h3>Input files</h3><span class="muted">${counts}</span><span class="spacer"></span>
        <button id="rp-fetch" ${busy ? "disabled" : ""} title="Find data/code repository links in the manuscript and download them here">Fetch supplemental materials…</button>
        <label class="btn primary ${busy ? "disabled" : ""}">Add files…<input type="file" multiple hidden id="rp-add-files" /></label><button id="rp-add-dir" ${busy ? "disabled" : ""}>Add a folder…</button></div>
      <p class="hint">The study's published data. The analysis is rebuilt from the procedures described in the paper, so the authors' code isn't needed. The backend guesses the role from the file type; notes are passed on to the LLM and the report. Zip archives are unpacked; files in an archive can be left out (unticked) without changing it. You can also drop files here. <b>Fetch supplemental materials…</b> looks for data/code links (Zenodo, GitHub, GitLab, OSF, Figshare) in the manuscript and downloads them here.</p>
      <table class="tbl-list rp-inputs"><thead><tr><th title="Used">Use</th><th>File</th><th>Role</th><th>Note</th><th>Size</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">No input files yet.</td></tr>`}</tbody></table>
    </div>${this.fetchList()}`;

    const add = (files: File[]) =>
      this.w.busy("upload", `Adding ${files.length} file(s)`, async () => {
        for (const f of files) await pkg.upload("inputs", f);
      });
    body.querySelector<HTMLInputElement>("#rp-add-files")!.addEventListener("change", (e) => {
      const files = Array.from((e.target as HTMLInputElement).files ?? []);
      if (files.length) add(files);
    });
    body.querySelector("#rp-add-dir")?.addEventListener("click", () => this.w.busy("upload", "Adding a folder", () => host.addFiles("inputs", true)));
    body.querySelector("#rp-fetch")?.addEventListener("click", async () => {
      // busy() reports errors itself and returns undefined.
      const preview = await this.w.busy("fetch", "Checking supplemental materials", () => pkg.acquirePreview({}));
      if (!preview) return;
      if (!preview.links.length) {
        // Show the "no links found" card, with its advice on adding the files by hand.
        this.fetch = { links: [], resources: [], failures: [] };
        await this.w.render();
        this.w.setStatus(...fetchStatus(this.fetch));
        return;
      }
      const choice = await fetchDialog(preview);
      if (!choice) return;
      const chosen = new Set(choice.action === "selected" ? choice.keys : preview.files.map((f) => f.key));
      this.unchosen = new Set(preview.links.map((l) => `${l.repo}:${l.id}`)
        .filter((k) => preview.files.some((f) => `${f.repo}:${f.id}` === k) && !preview.files.some((f) => `${f.repo}:${f.id}` === k && chosen.has(f.key))));
      const r = await this.w.busy("fetch", "Fetching supplemental materials", async () =>
        (this.fetch = await pkg.acquire(choice.action === "selected" ? { selected: choice.keys } : {})));
      if (r) this.w.setStatus(...fetchStatus(r));
    });
    const card = body.querySelector<HTMLElement>(".rp-drop")!;
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      card.classList.add("over");
    });
    card.addEventListener("dragleave", () => card.classList.remove("over"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("over");
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) add(files);
    });

    const table = body.querySelector<HTMLElement>(".rp-inputs")!;
    table.addEventListener("change", async (e) => {
      const el = e.target as HTMLInputElement | HTMLSelectElement;
      const name = el.closest<HTMLElement>("tr[data-name]")?.dataset.name;
      const f = el.dataset.f;
      if (!name || !f) return;
      const value = f === "used" ? (el as HTMLInputElement).checked : f === "role" ? (el.value as Role) : el.value.trim();
      try {
        const r = (await pkg.patch<Resource>(`/inputs/${encodeURIComponent(name)}`, { [f]: value })) as InputRes;
        this.inputs = this.inputs.map((x) => (x.name === name ? r : x));
        if (f === "used") this.w.draw();
      } catch (err) {
        this.w.setStatus(`Changing ${name} failed: ${(err as Error).message}`, "error");
        this.w.render();
      }
    });
    table.addEventListener("click", async (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-act=del]");
      const name = b?.closest<HTMLElement>("tr[data-name]")?.dataset.name;
      if (!name) return;
      const n = this.inputs.filter((m) => m.reprocrowd.archive === name).length;
      const what = n ? `the archive <code>${esc(name)}</code> and all ${n} files unpacked from it` : `<code>${esc(name)}</code>`;
      if (!(await confirmDialog("Delete input", `Delete ${what} from the package?`, "Delete", true))) return;
      await this.w.busy("delete", `Deleting ${name}`, async () => {
        try {
          await pkg.del(`/inputs/${encodeURIComponent(name)}`);
        } catch (err) {
          if (err instanceof ApiError && err.code === "in_use") throw new ApiError(`${name} is used by analysis steps (step 4); remove it there first.`, err.problem);
          throw err;
        }
      });
    });
  }
}
