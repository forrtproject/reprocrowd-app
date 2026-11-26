// Step 6: run the master script (a job of the backend, with its log streaming in), then compare the reproduced
// results with the targets: numbers by the reported rounding and a tolerance, tables cell by cell, figures side by
// side with crops of the originals.
import { ApiError, pkg } from "../api";
import type { CodeCheckReport, Comparison, CompareRow, CropOutcome, DeterminismCheck, FailureHint, FigureOriginal, RunIsolation, ScriptState, Status } from "../types";
import { FIGURE_HEIGHT } from "../state";
import { blobUrl, esc, showError, trunc, when } from "../util";
import { appendEvents, JobFollower, statusBadge } from "./jobs";
import type { Step, Wizard } from "./wizard";

const IMAGE_TYPES: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", svg: "image/svg+xml", bmp: "image/bmp", avif: "image/avif" };

/**
 * The media type to show a package image as: the one the backend serves it with if that is an image type, else one
 * from the file's extension, else PNG (the original figures, served without one). An SVG needs its own type to show.
 */
export function imageType(path: string, served?: string | null): string {
  const t = served?.split(";")[0].trim().toLowerCase();
  if (t?.startsWith("image/")) return t;
  const ext = /\.([a-z0-9]+)$/i.exec(path.split("?")[0])?.[1]?.toLowerCase();
  return (ext && IMAGE_TYPES[ext]) || "image/png";
}

/** A package image's bytes with its media type. */
export async function packageImage(sub: string): Promise<{ data: ArrayBuffer; type: string }> {
  const f = await pkg.file(sub);
  return { data: f.data, type: imageType(sub, f.type) };
}

export const STATUS_LABEL: Record<Status, string> = { match: "match", within: "within tolerance", deviation: "deviation", missing: "missing", figure: "compare visually", unparsable: "unparsable" };

export class RunStep implements Step {
  title = "Run & compare";
  private job = new JobFollower("run");
  private script?: ScriptState;
  private codeChecks?: CodeCheckReport;
  private cmp?: Comparison;
  private cmpError?: string;
  private originals: FigureOriginal[] = [];
  private urls: string[] = [];
  private codeChecksSeq = 0;
  private runTwice = false;

  constructor(private w: Wizard) {}

  async load() {
    [this.script] = await Promise.all([pkg.get<ScriptState>("/script"), this.job.load()]);
    // Load advisory static checks asynchronously so large text inputs never block Step 6 or /comparison.
    const seq = ++this.codeChecksSeq;
    pkg.get<CodeCheckReport>("/code-checks").then(
      (cc) => {
        if (seq !== this.codeChecksSeq) return;
        this.codeChecks = cc;
        if (this.w.steps[this.w.step] === this) this.w.draw();
      },
      () => {
        if (seq !== this.codeChecksSeq) return;
        this.codeChecks = undefined;
        if (this.w.steps[this.w.step] === this) this.w.draw();
      },
    );
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
        <label class="inline small" title="Run the master script twice sequentially and compare outputs/results.json and produced output files for determinism"><input id="rp-twice" type="checkbox" ${this.runTwice ? "checked" : ""} ${running ? "disabled" : ""} /> Verify determinism (2 runs)</label>
        <button id="rp-run" class="primary" ${running || !this.script?.template ? "disabled" : ""}>${running ? "Running…" : this.runTwice ? "Run twice" : "Run master script"}</button>
        <button id="rp-stop" ${running ? "" : "disabled"}>Stop</button></div>
      <p class="hint">Runs <code>${esc(this.script?.command ?? "(no master script yet)")}</code> in the package folder, as configured in the template. The previous <code>outputs/results.json</code> is removed first, so a failed run never leaves stale results.</p>
      ${j ? `<p>${statusBadge(j)} · ${esc(when(j.created_at))}${res ? ` · results file ${res.results_file ? "✓" : "✗ missing"}` : ""}</p>` : ""}
      ${res?.isolation ? isolationHtml(res.isolation) : ""}
      ${res?.failure_hints?.length ? failureHintsHtml(res.failure_hints) : ""}
      ${res?.determinism ? determinismHtml(res.determinism) : ""}
      ${res?.output_errors.length ? `<div class="warn"><b>outputs/results.json has problems:</b><ul>${res.output_errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
      ${res?.output_warnings.length ? `<details class="warn-soft"><summary>${res.output_warnings.length} warning(s)</summary><ul>${res.output_warnings.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>` : ""}
      ${this.codeChecks ? codeChecksHtml(this.codeChecks) : ""}
      ${j ? `<details ${running || (j.status !== "succeeded") ? "open" : ""}><summary>Run log</summary><div id="rp-log" class="log"></div></details>` : ""}
    </div>
    <div class="card" id="rp-compare" style="--fig-h:${st.figureHeight}px">
      <div class="card-head"><h3>Comparison</h3><span class="spacer"></span>
        ${this.cmp?.rows.some((r) => r.type === "figure") ? `<label title="Height of the figures in the table; they never get wider than their column">Figure size <input id="rp-figsize" type="range" min="${FIGURE_HEIGHT.min}" max="${FIGURE_HEIGHT.max}" step="20" value="${st.figureHeight}" /></label>` : ""}
        <label>Rounding <select id="rp-round"><option value="reported" ${c.rounding === "reported" ? "selected" : ""}>as reported</option>${[0, 1, 2, 3, 4, 5, 6].map((d) => `<option value="${d}" ${c.rounding === d ? "selected" : ""}>${d} decimals</option>`).join("")}</select></label>
        <label>Exploratory tolerance up to <input id="rp-thr" type="number" min="0" step="0.5" value="${c.threshold}" style="width:5em" /> %</label>
        <button id="rp-crop" title="Get the original figures of figure targets: as published where possible, otherwise from the PDF">Get original figures</button></div>
      ${this.cmpError ? `<p class="hint">${esc(this.cmpError)}</p>` : this.cmp ? compareHtml(this.cmp, this.originals) : ""}
    </div>`;
    const log = body.querySelector<HTMLElement>("#rp-log");
    if (log) appendEvents(log, this.job.events, true);
    body.querySelector<HTMLInputElement>("#rp-twice")!.addEventListener("change", (e) => {
      this.runTwice = (e.target as HTMLInputElement).checked;
      this.w.draw();
    });
    body.querySelector("#rp-run")!.addEventListener("click", async () => {
      await this.w.busy("run-start", this.runTwice ? "Starting 2-run determinism check" : "Starting the run", () => this.job.start({ runs: this.runTwice ? 2 : 1 }));
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
        const { data, type } = await packageImage(img.dataset.src!);
        const url = blobUrl(data, type);
        this.urls.push(url);
        img.src = url;
      } catch {
        img.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: img.alt }));
      }
    }
  }
}

function isolationHtml(iso: RunIsolation): string {
  const caches = iso.runtime_caches.length ? ` · <span class="warn-inline">caches left: ${esc(iso.runtime_caches.join(", "))}</span>` : "";
  const net = iso.network_denied_by === "proxy"
    ? `network restricted best-effort via <code>proxy</code> env vars`
    : `network denied via <code>${esc(iso.network_denied_by)}</code>`;
  return `<p class="small muted">Clean-room run: ${net} · <code>TZ=${esc(iso.tz)}</code> · locale <code>${esc(iso.locale)}</code>${iso.clean_home ? " · private <code>HOME</code>/<code>TMPDIR</code>" : ""}${caches}</p>`;
}

function failureHintsHtml(hints: FailureHint[]): string {
  const items = hints.map((h) => `<li><code>${esc(h.kind)}</code> ${esc(h.detail)}<div class="muted small">${esc(h.suggestion)}</div></li>`).join("");
  return `<div class="warn"><b>Likely cause${hints.length > 1 ? "s" : ""}:</b><ul>${items}</ul></div>`;
}

function determinismHtml(det: DeterminismCheck): string {
  const cls = det.status === "identical" ? "ok-badge" : det.status === "numerically_close" ? "badge" : "warn-badge";
  const label = det.status.replace("_", " ");
  const header = `<span class="badge ${cls}">Determinism: ${esc(label)}</span> ${esc(det.summary)}`;
  if (!det.differences.length) return `<p class="small">${header}</p>`;
  const items = det.differences.map((d) => {
    const loc = d.target ?? d.path ?? d.kind;
    const clock = d.looks_like_clock ? ` <span class="badge warn-badge">clock/timestamp</span>` : "";
    return `<li><code>${esc(loc)}</code>: ${esc(d.detail)}${clock}</li>`;
  }).join("");
  return `<details class="${det.status === "numerically_close" ? "warn-soft" : "warn"}" open><summary>${header}</summary><ul>${items}</ul></details>`;
}

function codeChecksHtml(cc: CodeCheckReport): string {
  if (!cc.checked_files.length && !cc.findings.length) return "";
  if (!cc.findings.length) {
    return `<p class="small muted">Static code checks (${esc(cc.checked_files.join(", "))}): ✓ no warnings.</p>`;
  }
  const items = cc.findings.map((f) => {
    const loc = f.file ? (f.line ? `${f.file}:${f.line}` : f.file) : "";
    return `<li><code>${esc(f.rule)}</code>${loc ? ` (<code>${esc(loc)}</code>)` : ""}: ${esc(f.message)}${f.suggestion ? `<div class="muted small">${esc(f.suggestion)}</div>` : ""}</li>`;
  }).join("");
  return `<details class="warn-soft"><summary>Static code checks: ${cc.findings.length} advisory warning${cc.findings.length === 1 ? "" : "s"}</summary><ul>${items}</ul></details>`;
}

export function compareHtml(cmp: Comparison, originals: FigureOriginal[] = []): string {
  const summary = `<p class="small">Exact matches and exploratory within-tolerance results are separate; tolerance ${cmp.settings.threshold}%. Policy ${esc(cmp.policy ?? "not recorded")}, rounding ${esc(cmp.rounding_mode ?? "not recorded")}.</p><p class="summary">${Object.entries(cmp.summary).map(([k, v]) => `<span class="st st-${k}">${STATUS_LABEL[k as Status] ?? k}: ${v}</span>`).join(" ")}</p>`;
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
