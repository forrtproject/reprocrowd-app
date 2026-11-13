// Step 6: run the master script (a job of the backend, with its log streaming in), then compare the reproduced
// results with the targets: numbers by the reported rounding and a tolerance, tables cell by cell, figures side by
// side with crops of the originals.
import { ApiError, pkg } from "../api";
import type { Comparison, CompareRow, CropOutcome, FigureOriginal, ScriptState, Status } from "../types";
import { FIGURE_HEIGHT } from "../state";
import { blobUrl, esc, showError, trunc, when } from "../util";
import { appendEvents, JobFollower, statusBadge } from "./jobs";
import type { Step, Wizard } from "./wizard";

export const STATUS_LABEL: Record<Status, string> = { match: "match", within: "within tolerance", deviation: "deviation", missing: "missing", figure: "compare visually", unparsable: "unparsable" };

export class RunStep implements Step {
  title = "Run & compare";
  private job = new JobFollower("run");
  private script?: ScriptState;
  private cmp?: Comparison;
  private cmpError?: string;
  private originals: FigureOriginal[] = [];
  private urls: string[] = [];

  constructor(private w: Wizard) {}

  async load() {
    [this.script] = await Promise.all([pkg.get<ScriptState>("/script"), this.job.load()]);
    await this.loadComparison();
    this.originals = await pkg.get<FigureOriginal[]>("/figures").catch(() => []);
  }

  private async loadComparison() {
    const c = this.w.app.state.compare;
    try {
      this.cmp = await pkg.get<Comparison>("/comparison", { rounding: String(c.rounding), threshold: c.threshold });
      this.cmpError = undefined;
    } catch (e) {
      this.cmp = undefined;
      this.cmpError = e instanceof ApiError && e.code === "results_missing" ? "No outputs/results.json yet: run the master script." : e instanceof ApiError && e.code === "script_missing" ? "No master script yet (step 4)." : (e as Error).message;
    }
  }

  pollMs = () => 1000;

  async poll() {
    if (!this.job.running) return;
    await this.job.poll();
    if (!this.job.running) return this.w.render();
    const log = document.querySelector<HTMLElement>("#rp-log");
    if (log) appendEvents(log, this.job.events, true);
  }

  draw(body: HTMLElement) {
    const st = this.w.app.state;
    const j = this.job.job;
    const running = this.job.running;
    const res = j?.result?.type === "run" ? j.result : undefined;
    const c = st.compare;
    body.innerHTML = `
    <div class="card">
      <div class="card-head"><h3>Full run</h3><span class="spacer"></span>
        <button id="rp-run" class="primary" ${running || !this.script?.template ? "disabled" : ""}>${running ? "Running…" : "Run master script"}</button>
        <button id="rp-stop" ${running ? "" : "disabled"}>Stop</button></div>
      <p class="hint">Runs <code>${esc(this.script?.command ?? "(no master script yet)")}</code> in the package folder, as configured in the template. The previous <code>outputs/results.json</code> is removed first, so a failed run never leaves stale results.</p>
      ${j ? `<p>${statusBadge(j)} · ${esc(when(j.created_at))}${res ? ` · results file ${res.results_file ? "✓" : "✗ missing"}` : ""}</p>` : ""}
      ${res?.output_errors.length ? `<div class="warn"><b>outputs/results.json has problems:</b><ul>${res.output_errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
      ${res?.output_warnings.length ? `<details class="warn-soft"><summary>${res.output_warnings.length} warning(s)</summary><ul>${res.output_warnings.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : ""}
      ${j ? `<details ${running || (j.status !== "succeeded") ? "open" : ""}><summary>Run log</summary><div id="rp-log" class="log"></div></details>` : ""}
    </div>
    <div class="card" id="rp-compare" style="--fig-h:${st.figureHeight}px">
      <div class="card-head"><h3>Comparison</h3><span class="spacer"></span>
        ${this.cmp?.rows.some((r) => r.type === "figure") ? `<label title="Height of the figures in the table; they never get wider than their column">Figure size <input id="rp-figsize" type="range" min="${FIGURE_HEIGHT.min}" max="${FIGURE_HEIGHT.max}" step="20" value="${st.figureHeight}" /></label>` : ""}
        <label>Rounding <select id="rp-round"><option value="reported" ${c.rounding === "reported" ? "selected" : ""}>as reported</option>${[0, 1, 2, 3, 4, 5, 6].map((d) => `<option value="${d}" ${c.rounding === d ? "selected" : ""}>${d} decimals</option>`).join("")}</select></label>
        <label>Within tolerance up to <input id="rp-thr" type="number" min="0" step="0.5" value="${c.threshold}" style="width:5em" /> %</label>
        <button id="rp-crop" title="Get the original figures of figure targets: as published where possible, otherwise from the PDF">Get original figures</button></div>
      ${this.cmpError ? `<p class="hint">${esc(this.cmpError)}</p>` : this.cmp ? compareHtml(this.cmp, this.originals) : ""}
    </div>`;
    const log = body.querySelector<HTMLElement>("#rp-log");
    if (log) appendEvents(log, this.job.events, true);
    body.querySelector("#rp-run")!.addEventListener("click", async () => {
      await this.w.busy("run-start", "Starting the run", () => this.job.start({}));
    });
    body.querySelector("#rp-stop")!.addEventListener("click", () => this.w.busy("stop", "Stopping the run", () => this.job.cancel()));
    const recompare = async () => {
      this.w.app.save();
      await this.loadComparison();
      this.w.draw();
    };
    body.querySelector<HTMLSelectElement>("#rp-round")!.addEventListener("change", (e) => {
      const v = (e.target as HTMLSelectElement).value;
      c.rounding = v === "reported" ? "reported" : Number(v);
      recompare();
    });
    body.querySelector<HTMLInputElement>("#rp-thr")!.addEventListener("change", (e) => {
      c.threshold = Math.max(0, Number((e.target as HTMLInputElement).value) || 0);
      recompare();
    });
    // Rescales live, without a redraw (which would load the images again).
    const figsize = body.querySelector<HTMLInputElement>("#rp-figsize");
    figsize?.addEventListener("input", () => {
      st.figureHeight = Number(figsize.value);
      body.querySelector<HTMLElement>("#rp-compare")!.style.setProperty("--fig-h", `${st.figureHeight}px`);
    });
    figsize?.addEventListener("change", () => this.w.app.save());
    body.querySelector("#rp-crop")!.addEventListener("click", async () => {
      const out = await this.w.busy("crop", "Getting the original figures", () => pkg.put<CropOutcome[]>("/figures", {}));
      const bad = out?.data.filter((o) => !o.ok) ?? [];
      if (bad.length) showError("Getting some figures", new Error(bad.map((o) => `${o.target}: ${o.error}`).join("\n")));
      const count = (method: CropOutcome["method"]) => out?.data.filter((o) => o.ok && o.method === method).length ?? 0;
      const kinds = [[count("published"), "published"], [count("crop"), "cropped from the PDF"], [count("page"), "full page"]].filter(([n]) => n).map(([n, kind]) => `${n} ${kind}`);
      const note = out?.data.find((o) => o.note)?.note;
      if (kinds.length) this.w.setStatus(`Original figures: ${kinds.join(", ")}${note ? ` (${note})` : ""}`);
    });
    this.loadImages(body);
  }

  private async loadImages(body: HTMLElement) {
    this.urls.forEach(URL.revokeObjectURL);
    this.urls = [];
    for (const img of body.querySelectorAll<HTMLImageElement>("img[data-src]")) {
      try {
        const url = blobUrl(await pkg.bytes(img.dataset.src!), "image/png");
        this.urls.push(url);
        img.src = url;
      } catch {
        img.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: img.alt }));
      }
    }
  }
}

export function compareHtml(cmp: Comparison, originals: FigureOriginal[] = []): string {
  const summary = `<p class="summary">${Object.entries(cmp.summary).map(([k, v]) => `<span class="st st-${k}">${STATUS_LABEL[k as Status] ?? k}: ${v}</span>`).join(" ")}</p>`;
  const problems = cmp.output_errors.length ? `<div class="warn"><ul>${cmp.output_errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "";
  const rows = cmp.rows.map((r) => rowHtml(r, originals.find((o) => o.target === r.target))).join("");
  return problems + summary + `<table class="tbl-list tbl-compare"><colgroup><col class="c-id" /><col /><col class="c-reported" /><col class="c-reproduced" /><col class="c-dev" /><col class="c-status" /></colgroup><thead><tr><th>ID</th><th>Result</th><th>Reported</th><th>Reproduced</th><th>Dev.</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function rowHtml(r: CompareRow, original?: FigureOriginal): string {
  const main = `<tr><td><b>${esc(r.target)}</b></td><td>${esc(trunc(r.description, 200))}</td><td>${esc(r.expected_text)}</td>
    <td>${esc(trunc(r.reproduced_text, 60))}${r.note ? `<div class="muted small">${esc(r.note)}</div>` : ""}</td>
    <td>${r.deviation != null && Number.isFinite(r.deviation) ? `${r.deviation.toFixed(2)}%` : ""}</td><td class="st st-${r.status}">${STATUS_LABEL[r.status]}</td></tr>`;
  if (r.type === "figure") {
    const rep = r.status === "figure" ? `<img data-src="/files/${r.reproduced_text.split("/").map(encodeURIComponent).join("/")}" alt="(reproduced figure not readable)" />` : `<span class="muted">not produced</span>`;
    const kind = original?.method === "published" ? "published image" : original?.method === "page" ? `full page ${original.page}, no exact crop` : "";
    return main + `<tr><td></td><td colspan="5"><div class="figs"><figure><figcaption>Original${kind ? ` <span class="muted">(${kind})</span>` : ""}</figcaption><img data-src="/figures/${encodeURIComponent(r.target)}" alt="(not yet: Get original figures)" /></figure><figure><figcaption>Reproduced</figcaption>${rep}</figure></div></td></tr>`;
  }
  if (r.cells)
    return main + `<tr><td></td><td colspan="5"><details><summary>Cells (${r.matched ?? 0}/${r.total ?? 0})</summary><div class="tbl-wrap"><table class="cells">${r.cells.map((row) => `<tr>${row.map((c) => `<td class="st-${c.status}" title="reported ${esc(c.expected)}${c.deviation != null ? ` · dev ${c.deviation.toFixed(2)}%` : ""}">${esc(c.reproduced)}<br><small>${esc(c.expected)}</small></td>`).join("")}</tr>`).join("")}</table></div></details></td></tr>`;
  return main;
}
