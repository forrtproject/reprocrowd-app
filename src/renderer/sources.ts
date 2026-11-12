// Sources dialog: the paper's full texts (added from this computer, downloaded through OpenAlex / Europe PMC, or converted
// from a PDF by GROBID / Docling) and the extractions made from them (parsed, or reconciled by an LLM).
import { ApiError, host, isBackendDown, pkg } from "./api";
import type { Candidate, Converter, CoordinateReport, LlmProfile, Origin, PromptPreview, Resource } from "./types";
import { esc, modal, showError, showPrompt, sizeText, trunc } from "./util";

export interface SourcesContext {
  changed(preferExtraction?: string): Promise<void>;
  showPdf(name: string): void;
}

const PDF_MATCH = { verified: "PDF confirmed by its hash", provenance: "converted from the PDF", mismatch: "made from a PDF not in this package", unknown: "PDF unknown" };

/** What a full text locates on the PDF. Few items in a format with coordinates only mean that it missed them. */
function coordinateCell(c?: CoordinateReport): string {
  if (!c) return "";
  if (!c.has_coordinates) return `<span class="muted" title="This format has no positions on the page">none</span>`;
  const n = (k: number, what: string) => `${k} ${what}${k === 1 ? "" : "s"}`;
  const match = c.pdf_match ? PDF_MATCH[c.pdf_match] : "";
  return `<span title="Real figures and tables whose position on the page is known${c.pdf ? `, on ${esc(c.pdf)}` : ""}">${n(c.figures, "figure")}, ${n(c.tables, "table")}</span><div class="${c.pdf ? "muted" : "warn-inline"}">${esc(match)}</div>`;
}

const flavor = (r: Resource) => (r.reprocrowd.kind === "fulltext" ? r.reprocrowd.flavor : r.format);
const TEXT_FLAVORS = ["tei", "jats", "docling"];

function originText(o: Origin | undefined): string {
  if (!o) return "";
  if (o.type === "upload") return `upload (${o.filename})`;
  if (o.type === "url") return o.label;
  if (o.type === "conversion") return `${o.converter} from ${o.source}`;
  return `local file ${o.path.split("/").pop()}`;
}

export async function sourcesDialog(ctx: SourcesContext) {
  const dlg = modal("dlg-sources", `<h3>Sources</h3><div class="src-body"><p class="muted">Loading…</p></div>
    <menu><span class="src-status muted"></span><button value="close" class="primary">Close</button></menu>`);
  dlg.querySelector("menu button")!.addEventListener("click", () => dlg.close());
  const body = dlg.querySelector<HTMLElement>(".src-body")!;
  const status = dlg.querySelector<HTMLElement>(".src-status")!;
  let candidates: { candidates: Candidate[]; notes: string[] } | undefined;
  let busy = "";
  let fulltexts: Resource[] = [];
  let coordinates: CoordinateReport[] = [];
  let extractions: Resource[] = [];
  let converters: Converter[] = [];
  let profiles: LlmProfile[] = [];
  let lastExtraction: string | undefined;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    busy = label;
    status.textContent = `⏳ ${label}…`;
    render();
    try {
      await fn();
      status.textContent = `${label}: done`;
    } catch (e) {
      status.textContent = isBackendDown(e) ? `${label} failed: the ReproCrowd backend isn't available` : `${label} failed`;
      showError(label, e);
    } finally {
      busy = "";
      await load().catch(() => {});
      await ctx.changed(lastExtraction).catch(() => {});
    }
  };

  async function load() {
    [fulltexts, extractions, coordinates] = await Promise.all([
      pkg.get<Resource[]>("/fulltexts"),
      pkg.get<Resource[]>("/extractions"),
      pkg.get<CoordinateReport[]>("/coordinates").catch(() => []),
    ]);
    if (!converters.length) converters = await host.service<Converter[]>("/v1/converters").catch(() => []);
    if (!profiles.length) profiles = await host.service<LlmProfile[]>("/v1/llm-profiles").catch(() => []);
    render();
  }

  function render() {
    const dis = busy ? "disabled" : "";
    const parsedFrom = new Set(extractions.filter((x) => x.reprocrowd.kind === "extraction" && x.reprocrowd.method === "parse").flatMap((x) => (x.reprocrowd.kind === "extraction" ? x.reprocrowd.fulltexts : [])));
    const ftRows = fulltexts
      .map((f) => {
        const fl = flavor(f);
        const meta = f.reprocrowd.kind === "fulltext" ? f.reprocrowd : undefined;
        const actions: string[] = [];
        if (fl === "pdf") {
          actions.push(`<button data-act="view" data-name="${esc(f.name)}" title="Show in the PDF pane">View</button>`);
          for (const c of converters) actions.push(`<button data-act="convert" data-conv="${esc(c.name)}" data-name="${esc(f.name)}" ${c.configured ? dis : "disabled"} title="${esc(c.configured ? `${c.detail} → ${c.output}` : `${c.name} is not configured in the backend`)}">${esc(c.name)} →</button>`);
        } else if (TEXT_FLAVORS.includes(fl)) {
          actions.push(parsedFrom.has(f.name) ? `<span class="ok small">parsed ✓</span>` : `<button data-act="parse" data-name="${esc(f.name)}" class="primary" ${dis}>Parse</button>`);
        }
        actions.push(`<button data-act="del-ft" data-name="${esc(f.name)}" title="Delete" ${dis}>✕</button>`);
        const notes = meta?.check.notes.join("; ") ?? "";
        return `<tr><td><code>${esc(f.name)}</code></td><td><span class="badge k-${esc(fl)}">${esc(fl)}</span></td><td class="small">${esc(originText(meta?.origin))}</td>
          <td class="small" title="${esc(notes)}">${meta?.check.passed ? "✓" : "✗"} ${esc(trunc(notes, 60))}</td><td class="small">${coordinateCell(coordinates.find((c) => c.fulltext === f.name))}</td><td class="nowrap">${sizeText(f.bytes)}</td><td class="nowrap">${actions.join(" ")}</td></tr>`;
      })
      .join("");
    const exRows = extractions
      .map((x) => {
        const m = x.reprocrowd.kind === "extraction" ? x.reprocrowd : undefined;
        return `<tr><td><code>${esc(x.name)}</code></td><td>${esc(m?.method ?? "")}</td><td class="small">${esc(m?.fulltexts.join(", ") ?? "")}</td><td class="nowrap">${sizeText(x.bytes)}</td>
          <td class="nowrap"><button data-act="show-ex" data-name="${esc(x.name)}">Show</button> <button data-act="del-ex" data-name="${esc(x.name)}" title="Delete" ${dis}>✕</button></td></tr>`;
      })
      .join("");
    const cand = candidates
      ? `<ul class="src-cands">${candidates.candidates.map((c) => `<li><span class="badge k-${esc(c.format)}">${esc(c.format)}</span> ${esc(c.label)} <span class="muted small" title="${esc(c.url)}">${esc(trunc(c.url, 70))}</span> <button data-act="download" data-id="${esc(c.id)}" ${dis}>Download</button></li>`).join("") || `<li class="muted">No download candidates.</li>`}</ul>
         ${candidates.notes.length ? `<ul class="hint">${candidates.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`
      : "";
    body.innerHTML = `
      <section><h4>Full texts</h4>
        <table class="tbl-list"><thead><tr><th>Name</th><th>Format</th><th>From</th><th>Check</th><th>Coordinates</th><th>Size</th><th></th></tr></thead>
        <tbody>${ftRows || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table>
        <div class="actions src-add">
          <label class="btn ${busy ? "disabled" : ""}">Add file…<input type="file" id="src-upload" accept=".pdf,.xml,.nxml,.tei,.json" multiple hidden /></label>
          <button data-act="candidates" ${dis}>Find online…</button>
        </div>
        ${cand}
      </section>
      <section><h4>Extractions <span class="hint">(the paper in the common document schema; text sources for the reproduction)</span></h4>
        <table class="tbl-list"><thead><tr><th>Name</th><th>Method</th><th>From</th><th>Size</th><th></th></tr></thead>
        <tbody>${exRows || `<tr><td colspan="5" class="muted">None yet: parse a TEI/JATS/Docling full text, or reconcile several with an LLM.</td></tr>`}</tbody></table>
        <details class="src-reconcile"><summary>Reconcile full texts with an LLM…</summary>
          <p class="hint">The LLM reads the chosen full texts (a PDF counts as ground truth for wording) and writes one clean version in the common schema. It can take several minutes.</p>
          <div class="src-checks">${fulltexts.map((f) => `<label><input type="checkbox" name="rc" value="${esc(f.name)}" /> ${esc(f.name)} <span class="muted">(${esc(flavor(f))})</span></label>`).join("") || `<span class="muted">No full texts.</span>`}</div>
          <label>Profile <select id="rc-profile">${profiles.map((p) => `<option value="${esc(p.name)}" ${p.default ? "selected" : ""}>${esc(p.name)} (${esc(p.model || "default model")}${p.effort ? `, ${esc(p.effort)}` : ""})</option>`).join("")}</select></label>
          <div class="form-row"><button data-act="reconcile-prompt">Show prompt</button></div>
          <label>Extra instructions <textarea id="rc-instr" rows="2" placeholder="optional"></textarea></label>
          <div class="form-row"><button data-act="reconcile" class="primary" ${dis}>Reconcile</button></div>
        </details>
      </section>`;
    body.querySelector<HTMLInputElement>("#src-upload")?.addEventListener("change", (e) => {
      const files = Array.from((e.target as HTMLInputElement).files ?? []);
      if (files.length) run(`Adding ${files.map((f) => f.name).join(", ")}`, async () => { for (const f of files) await pkg.upload("fulltexts", f); });
    });
  }

  body.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-act]");
    if (!b || b.disabled) return;
    const name = b.dataset.name ?? "";
    switch (b.dataset.act) {
      case "view":
        ctx.showPdf(name);
        return dlg.close();
      case "show-ex":
        ctx.changed(name);
        return dlg.close();
      case "candidates":
        return run("Looking up download candidates", async () => {
          candidates = await pkg.get("/fulltexts/candidates");
        });
      case "download":
        return run("Downloading", () => pkg.post("/fulltexts", { candidate: b.dataset.id }));
      case "convert":
        return run(`Converting ${name} with ${b.dataset.conv}`, () => pkg.post("/fulltexts", { source: name, converter: b.dataset.conv }));
      case "parse":
        return run(`Parsing ${name}`, async () => {
          const r = await pkg.post<Resource>("/extractions", { method: "parse", fulltext: name });
          lastExtraction = r.name;
        });
      case "reconcile-prompt":
      case "reconcile": {
        const sources = Array.from(body.querySelectorAll<HTMLInputElement>("input[name=rc]:checked")).map((c) => c.value);
        if (!sources.length) return showError("Reconcile", new Error("Tick at least one full text."));
        const profile = body.querySelector<HTMLSelectElement>("#rc-profile")?.value || undefined;
        const instructions = body.querySelector<HTMLTextAreaElement>("#rc-instr")?.value.trim() ?? "";
        const request = { method: "reconcile", sources, profile, instructions };
        if (b.dataset.act === "reconcile-prompt") return void showPrompt("Prompt for reconciling", () => pkg.post<PromptPreview>("/extractions:preview", request));
        return run("Reconciling with the LLM", async () => {
          const r = await pkg.post<Resource>("/extractions", request);
          lastExtraction = r.name;
        });
      }
      case "del-ft":
      case "del-ex":
        if (!confirm(`Delete ${name}?`)) return;
        return run(`Deleting ${name}`, async () => {
          try {
            await pkg.del(`/${b.dataset.act === "del-ft" ? "fulltexts" : "extractions"}/${encodeURIComponent(name)}`);
          } catch (err) {
            if (err instanceof ApiError && err.code === "in_use") throw new ApiError(`${name} is still used (by an extraction, or by targets/actions referring to it).`, err.problem);
            throw err;
          }
        });
    }
  });
  await load().catch((e) => {
    body.innerHTML = isBackendDown(e)
      ? `<p class="muted">The sources can't be shown while the ReproCrowd backend isn't available (see the message at the top of the tab).</p>`
      : `<p class="warn">${esc((e as Error).message)}</p>`;
  });
}
