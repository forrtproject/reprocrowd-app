// The text pane: one extraction of the paper shown in one of three views, next to the PDF.
//   tree    one row per element (section › paragraph › sentence, figures, tables), nested and collapsible
//   reader  the running text, with collapsible sections (nested by level) and figures/tables where they appear
//   raw     the underlying source file (TEI/JATS XML, Docling JSON) or the extraction's own JSON, highlighted
// Hovering a sentence, paragraph, heading or figure highlights it in the PDF (exact boxes when the source has
// coordinates, otherwise found by its text).
import { pkg, reconstructAbstract } from "./api";
import { countFloats, countSentences, Float, hasCoords, highlightTargets, HighlightTarget, SectionNode, sectionTree } from "./doc";
import { highlight } from "./highlight";
import type { PdfView } from "./pdfview";
import type { Box, Document, Resource, Sentence } from "./types";
import { $, esc, trunc } from "./util";

export type TextMode = "tree" | "reader" | "raw";
export const MODES: [TextMode, string, string][] = [
  ["tree", "Tree", "One row per element: sections, paragraphs, sentences, figures and tables"],
  ["reader", "Reader", "The running text with collapsible sections"],
  ["raw", "Raw", "The source file the text was extracted from"],
];

export interface TextPaneContext {
  pdf: PdfView;
  meta(): any;
  status(msg: string, kind?: "info" | "error"): void;
  changed(): void; // the selection (extraction, mode, raw source) changed: persist it
  openSources(): void;
}

const EXTRACTION_JSON = "(extraction JSON)";

export class TextPane {
  mode: TextMode = "reader";
  name = "";
  rawSource = "";
  doc?: Document;
  private extractions: Resource[] = [];
  private fulltexts: Resource[] = [];
  private docs = new Map<string, { hash: string; doc: Document }>();
  private raws = new Map<string, string>();
  private targets = new Map<string, HighlightTarget>();
  private hoverToken = 0;
  private hoverTimer?: number;

  constructor(private root: HTMLElement, private ctx: TextPaneContext) {
    root.innerHTML = `
      <div class="pane-head">
        <label>Text <select id="ex-select"></select></label>
        <div class="seg" id="text-mode">${MODES.map(([m, label, title]) => `<button data-mode="${m}" title="${esc(title)}">${label}</button>`).join("")}</div>
        <select id="raw-select" hidden title="Source shown in the raw view"></select>
        <span class="spacer"></span>
        <span id="text-info" class="hint"></span>
        <button id="expand-all" title="Expand all sections">Expand all</button>
        <button id="collapse-all" title="Collapse all sections">Collapse all</button>
        <button id="sources-btn" title="Full texts and extractions of the paper" disabled>Sources…</button>
      </div>
      <div id="text" class="text-body"></div>`;
    $("#ex-select", root).addEventListener("change", (e) => this.show((e.target as HTMLSelectElement).value));
    $("#text-mode", root).addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-mode]");
      if (b) this.setMode(b.dataset.mode as TextMode);
    });
    $("#raw-select", root).addEventListener("change", (e) => {
      this.rawSource = (e.target as HTMLSelectElement).value;
      this.ctx.changed();
      this.render();
    });
    $("#sources-btn", root).addEventListener("click", () => this.ctx.openSources());
    $("#expand-all", root).addEventListener("click", () => this.toggleAll(true));
    $("#collapse-all", root).addEventListener("click", () => this.toggleAll(false));
    const body = $("#text", root);
    body.addEventListener("mouseover", (e) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>("[data-hid]");
      if (!el || el.classList.contains("active")) return;
      clearTimeout(this.hoverTimer);
      this.hoverTimer = window.setTimeout(() => this.activate(el.dataset.hid!), 60);
    });
    body.addEventListener("click", (e) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>("[data-hid]");
      if (el && this.mode === "tree" && !(e.target as HTMLElement).closest("summary")) this.activate(el.dataset.hid!);
      const src = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-open-sources]");
      if (src) this.ctx.openSources();
    });
  }

  /** New lists of extractions and full texts (after loading the package or changing sources). */
  async update(extractions: Resource[], fulltexts: Resource[], preferred?: string) {
    this.extractions = extractions;
    this.fulltexts = fulltexts;
    const names = extractions.map((x) => x.name);
    const pick = names.includes(this.name) ? this.name : preferred && names.includes(preferred) ? preferred : names[0] ?? "";
    await this.show(pick, true);
  }

  setMode(mode: TextMode) {
    this.mode = mode;
    this.ctx.changed();
    this.render();
  }

  get extraction(): Resource | undefined {
    return this.extractions.find((x) => x.name === this.name);
  }

  async show(name: string, quiet = false) {
    this.name = name;
    const sel = $<HTMLSelectElement>("#ex-select", this.root);
    sel.innerHTML = this.extractions.length
      ? this.extractions.map((x) => `<option value="${esc(x.name)}" ${x.name === name ? "selected" : ""}>${esc(x.name)}</option>`).join("")
      : `<option value="">(none yet)</option>`;
    this.doc = undefined;
    const res = this.extraction;
    if (res) {
      const cached = this.docs.get(name);
      if (cached?.hash === res.hash) this.doc = cached.doc;
      else {
        try {
          this.doc = await pkg.get<Document>(`/extractions/${encodeURIComponent(name)}`);
          this.docs.set(name, { hash: res.hash, doc: this.doc });
        } catch (e) {
          this.ctx.status(`Could not load ${name}: ${(e as Error).message}`, "error");
        }
      }
    }
    if (!quiet) this.ctx.changed();
    this.render();
  }

  /** Full texts the extraction came from (text formats only) plus the extraction's own file. */
  private rawOptions(): { value: string; label: string; path: string; hint: string }[] {
    const res = this.extraction;
    if (!res) return [];
    const src = res.reprocrowd.kind === "extraction" ? res.reprocrowd.fulltexts : [];
    const out = src
      .map((n) => this.fulltexts.find((f) => f.name === n))
      .filter((f): f is Resource => !!f && !(f.reprocrowd.kind === "fulltext" && f.reprocrowd.flavor === "pdf"))
      .map((f) => ({ value: f.name, label: `${f.name} (${f.reprocrowd.kind === "fulltext" ? f.reprocrowd.flavor : f.format})`, path: f.path, hint: f.format === "json" ? "json" : "xml" }));
    out.push({ value: EXTRACTION_JSON, label: `${res.name} (common schema JSON)`, path: res.path, hint: "json" });
    return out;
  }

  render() {
    this.root.querySelectorAll<HTMLButtonElement>("#text-mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === this.mode));
    const rawSel = $<HTMLSelectElement>("#raw-select", this.root);
    const opts = this.rawOptions();
    rawSel.hidden = this.mode !== "raw" || !opts.length;
    if (!opts.some((o) => o.value === this.rawSource)) this.rawSource = opts[0]?.value ?? "";
    rawSel.innerHTML = opts.map((o) => `<option value="${esc(o.value)}" ${o.value === this.rawSource ? "selected" : ""}>${esc(o.label)}</option>`).join("");
    $<HTMLButtonElement>("#expand-all", this.root).hidden = this.mode === "raw";
    $<HTMLButtonElement>("#collapse-all", this.root).hidden = this.mode === "raw";
    const doc = this.doc;
    this.targets = doc ? highlightTargets(doc) : new Map();
    const m = this.metaParts();
    if (m.title) this.targets.set("m-title", { text: m.title });
    m.abstract.forEach((s) => this.targets.set(s.id, { text: s.text }));
    $("#text-info", this.root).textContent = doc
      ? `${doc.sections.length} sections · ${countSentences(doc)} sentences · ${doc.figures.length} figures · ${doc.tables.length} tables${hasCoords(doc) ? " · PDF coordinates" : ""}`
      : "";
    const body = $("#text", this.root);
    body.classList.toggle("raw", this.mode === "raw");
    body.scrollTop = 0;
    if (this.mode === "raw") return this.renderRaw(body, opts.find((o) => o.value === this.rawSource));
    body.innerHTML = this.mode === "tree" ? this.treeHtml(doc, m) : this.readerHtml(doc, m);
  }

  private toggleAll(open: boolean) {
    this.root.querySelectorAll<HTMLDetailsElement>("#text details").forEach((d) => (d.open = open));
  }

  // ---------- metadata (OpenAlex) ----------

  private metaParts() {
    const meta = this.ctx.meta();
    const text = reconstructAbstract(meta?.abstract_inverted_index);
    const abstract: Sentence[] = text ? (text.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) ?? [text]).map((t, i) => ({ id: `m-abs${i}`, text: t.trim() })) : [];
    return {
      title: (meta?.title as string | undefined) ?? this.doc?.title ?? undefined,
      authors: ((meta?.authorships ?? []) as any[]).map((a) => a.author?.display_name).filter(Boolean) as string[],
      venue: [meta?.primary_location?.source?.display_name, meta?.publication_year, meta?.doi].filter(Boolean).join(" · "),
      abstract,
    };
  }

  private emptyHint() {
    return `<div class="empty-hint"><p>No structured text yet.</p><p class="hint">Add a full text (the paper's PDF from this computer, or a download through OpenAlex / Europe PMC), then parse it or convert the PDF with GROBID or Docling.</p><button class="primary" data-open-sources>Sources…</button></div>`;
  }

  // ---------- tree view ----------

  private treeHtml(doc: Document | undefined, m: ReturnType<TextPane["metaParts"]>) {
    const meta = this.ctx.meta();
    let html = `<div class="tree"><details open class="t-meta"><summary class="t-head">Metadata</summary><ul>`;
    if (m.title) html += `<li data-hid="m-title" class="t-item">Title: ${esc(m.title)}</li>`;
    if (m.authors.length) html += `<li class="t-plain">Authors: ${esc(trunc(m.authors.join(", "), 200))}</li>`;
    if (meta?.publication_year) html += `<li class="t-plain">Year: ${meta.publication_year}</li>`;
    const venue = meta?.primary_location?.source?.display_name;
    if (venue) html += `<li class="t-plain">Venue: ${esc(venue)}</li>`;
    if (meta?.open_access) html += `<li class="t-plain">Open access: ${esc(meta.open_access.oa_status ?? "")}${meta.open_access.is_oa ? " ✓" : ""}</li>`;
    if (m.abstract.length) html += `<li><details><summary class="t-sub">Abstract (OpenAlex)</summary><ul>${m.abstract.map((s) => `<li data-hid="${s.id}" class="t-item t-sent">${esc(s.text)}</li>`).join("")}</ul></details></li>`;
    html += `</ul></details>`;
    if (!doc) return html + this.emptyHint() + `</div>`;
    const { roots, loose } = sectionTree(doc);
    html += `<div class="t-label">${esc(this.name)} · ${esc(doc.source.method === "reconcile" ? "reconciled by an LLM" : `parsed from ${doc.source.format}`)}</div>`;
    // A row with children is collapsible; a childless one is a plain row, so it shows no expansion arrow.
    const branch = (detailsCls: string, headCls: string, hid: string, head: string, kids: string) =>
      kids
        ? `<details${detailsCls ? ` class="${detailsCls}"` : ""}><summary data-hid="${hid}" class="${headCls}">${head}</summary><ul>${kids}</ul></details>`
        : `<div data-hid="${hid}" class="${headCls} t-leaf">${head}</div>`;
    const node = (n: SectionNode): string => {
      let pn = 0;
      const paras = n.section.paragraphs
        .map((p) => {
          pn++;
          const sents = p.sentences.map((s) => `<li data-hid="${s.id}" class="t-item t-sent"><span class="t-id">${s.id.split(".").pop()}</span> ${esc(s.text)}</li>`).join("");
          return `<li class="t-block">${branch("", "t-item", p.id, `<b>¶${pn}</b> ${esc(trunc(p.sentences[0]?.text ?? "", 90))} <span class="t-count">${p.sentences.length}</span>`, sents)}</li>`;
        })
        .join("");
      const floats = countFloats(n);
      const counts = [n.section.paragraphs.length && `${n.section.paragraphs.length} ¶`, floats && `${floats} fig/tab`, n.children.length && `${n.children.length} subsections`].filter(Boolean).join(" · ");
      return `<li>${branch("t-sec", "t-head", n.section.id, `§ ${esc(n.section.title || "(untitled)")} <span class="t-count">${counts}</span>`, paras + n.floats.map(floatRow).join("") + n.children.map(node).join(""))}</li>`;
    };
    const floatRow = (f: Float) => {
      const icon = f.kind === "table" ? "▦" : "🖼";
      const extra = f.kind === "table" ? `<li class="t-plain">${f.rows.length} rows × ${f.rows[0]?.length ?? 0} columns</li>` : "";
      return `<li class="t-block t-${f.kind}">${branch("", "t-item", f.id, `<b>${icon} ${esc(f.label)}</b> ${esc(trunc(f.caption[0]?.text ?? "", 80))}`, f.caption.map((s) => `<li data-hid="${s.id}" class="t-item t-sent">${esc(s.text)}</li>`).join("") + extra)}</li>`;
    };
    html += `<ul class="t-root">${roots.map(node).join("")}`;
    if (loose.length) html += `<li><details class="t-sec"><summary class="t-head">Figures and tables <span class="t-count">${loose.length}</span></summary><ul>${loose.map(floatRow).join("")}</ul></details></li>`;
    return html + `</ul></div>`;
  }

  // ---------- reader view ----------

  private readerHtml(doc: Document | undefined, m: ReturnType<TextPane["metaParts"]>) {
    let html = `<article class="reader"><div class="meta-card">`;
    if (m.title) html += `<h1 data-hid="m-title">${esc(m.title)}</h1>`;
    if (m.authors.length) html += `<p class="authors">${esc(m.authors.join(", "))}</p>`;
    if (m.venue) html += `<p class="venue">${esc(m.venue)}</p>`;
    const hasAbstract = doc?.sections.some((s) => /abstract/i.test(s.title));
    if (m.abstract.length && !hasAbstract) html += `<details open class="r-sec"><summary><h2>Abstract <span class="r-note">(OpenAlex)</span></h2></summary><p>${m.abstract.map(sent).join(" ")}</p></details>`;
    html += `</div>`;
    if (!doc) return html + this.emptyHint() + `</article>`;
    const { roots, loose } = sectionTree(doc);
    const node = (n: SectionNode): string => {
      const h = Math.min(6, n.section.level + 1);
      const title = n.section.title ? esc(n.section.title) : `<span class="muted">(untitled section)</span>`;
      return `<details open class="r-sec r-l${Math.min(n.section.level, 4)}"><summary><h${h} data-hid="${n.section.id}">${title}</h${h}></summary>
        ${n.section.paragraphs.map((p) => `<p data-block="${p.id}">${p.sentences.map(sent).join(" ")}</p>`).join("")}
        ${n.floats.map(floatHtml).join("")}${n.children.map(node).join("")}</details>`;
    };
    html += roots.map(node).join("");
    if (loose.length) html += `<details open class="r-sec"><summary><h2>Figures and tables</h2></summary>${loose.map(floatHtml).join("")}</details>`;
    return html + `</article>`;
  }

  // ---------- raw view ----------

  private async renderRaw(body: HTMLElement, opt: { value: string; path: string; hint: string } | undefined) {
    if (!opt) {
      body.innerHTML = this.emptyHint();
      return;
    }
    const key = `${opt.value}@${opt.value === EXTRACTION_JSON ? this.extraction?.hash : this.fulltexts.find((f) => f.name === opt.value)?.hash}`;
    body.innerHTML = `<p class="muted">Loading…</p>`;
    let text = this.raws.get(key);
    if (text === undefined) {
      try {
        text = opt.value === EXTRACTION_JSON ? await pkg.text(`/files/${opt.path.split("/").map(encodeURIComponent).join("/")}`) : await pkg.text(`/fulltexts/${encodeURIComponent(opt.value)}/content`);
        this.raws.set(key, text);
      } catch (e) {
        body.innerHTML = `<p class="warn">Could not load the source: ${esc((e as Error).message)}</p>`;
        return;
      }
    }
    if (this.mode !== "raw" || this.rawSource !== opt.value) return; // switched away while loading
    body.innerHTML = `<pre class="raw-src"><code>${highlight(text, opt.hint)}</code></pre>`;
  }

  // ---------- hover → PDF highlight ----------

  private async activate(hid: string) {
    this.root.querySelectorAll(".active").forEach((e) => e.classList.remove("active"));
    const token = ++this.hoverToken;
    this.root.querySelectorAll(`[data-hid="${CSS.escape(hid)}"]`).forEach((e) => e.classList.add("active"));
    const t = this.targets.get(hid);
    const pdf = this.ctx.pdf;
    if (!t || !pdf.loaded) return;
    let boxes: Box[] | null | undefined = t.boxes;
    if (!boxes?.length) {
      boxes = await pdf.findText(t.text, t.fallback?.map((b) => b.page));
      if (token !== this.hoverToken) return;
      boxes ??= t.fallback;
    }
    pdf.highlight(boxes, true, t.boxes?.length || boxes !== t.fallback ? "hl" : "hl hl-approx");
  }
}

const sent = (s: Sentence) => `<span class="sent${s.boxes?.length ? " exact" : ""}" data-hid="${s.id}">${esc(s.text)}</span>`;

function floatHtml(f: Float): string {
  const cap = `<figcaption><b data-hid="${f.id}">${esc(f.label)}</b> ${f.caption.map(sent).join(" ")}</figcaption>`;
  if (f.kind === "figure") return `<figure class="fig">${cap}</figure>`;
  const rows = f.rows.map((r, i) => `<tr>${r.map((c) => (i === 0 ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`)).join("")}</tr>`).join("");
  return `<figure class="tbl">${cap}${rows ? `<div class="tbl-wrap"><table>${rows}</table></div>` : ""}</figure>`;
}
