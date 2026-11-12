// Checkbox tree over a document (sections › paragraphs › sentences, plus figures and tables), used to choose what an
// LLM call reads (its scope) and which passages a target or action refers to.
import { countFloats, Float, floatsOf, refText, SectionNode, sectionTree } from "../doc";
import type { Document } from "../types";
import { esc, trunc } from "../util";

type Pred = (id: string, text: string, kind: "sentence" | "table" | "figure") => boolean;
export interface Preset {
  label: string;
  pick(doc: Document): Pred;
}

const sectionPreset = (label: string, re: RegExp): Preset => ({
  label,
  pick: (doc) => {
    const ids = new Set<string>();
    // A matching section brings its subsections: under "Results" they are usually called something else.
    const visit = (n: SectionNode, inside: boolean) => {
      const hit = inside || re.test(n.section.title);
      if (hit) for (const p of n.section.paragraphs) for (const x of p.sentences) ids.add(x.id);
      for (const c of n.children) visit(c, hit);
    };
    for (const n of sectionTree(doc).roots) visit(n, false);
    return (id) => ids.has(id);
  },
});

export const TARGET_PRESETS: Preset[] = [
  // digits, ignoring citation brackets and years
  { label: "+ Sentences with numbers", pick: () => (_, text, k) => k === "sentence" && /\d/.test(text.replace(/\[\d+(?:[,–-]\s*\d+)*\]|\b(19|20)\d\d\b/g, "")) },
  sectionPreset('+ "Results" sections', /result|finding/i),
  { label: "+ All tables & figures", pick: () => (_, __, k) => k !== "sentence" },
];
export const ACTION_PRESETS: Preset[] = [sectionPreset("+ Methods-like sections", /method|material|procedure|analys|statistic|data|model|design|measure/i)];

export class ScopeTree {
  private selected = new Set<string>();
  /** node id (section, paragraph) → leaf ids beneath it */
  private leaves = new Map<string, string[]>();
  private order: string[] = [];

  constructor(private root: HTMLElement, private doc: Document, private opts: { floats: boolean; presets: Preset[]; onChange?: () => void }) {
    this.render();
    root.addEventListener("change", (e) => {
      const cb = e.target as HTMLInputElement;
      const node = cb.dataset.node;
      if (!node) return;
      for (const id of this.leaves.get(node) ?? [node]) cb.checked ? this.selected.add(id) : this.selected.delete(id);
      this.sync();
    });
    root.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-preset], button[data-clear]");
      if (!b) return;
      if (b.dataset.clear !== undefined) this.selected.clear();
      else this.select(this.opts.presets[Number(b.dataset.preset)].pick(this.doc));
      this.sync();
    });
  }

  /** Selected leaves (sentence, figure and table ids) in document order. */
  get ids(): string[] {
    return this.order.filter((id) => this.selected.has(id));
  }

  set ids(ids: string[]) {
    this.selected = new Set(ids);
    this.sync();
  }

  /** As the backend's scope: fully selected sections by id, everything else as single items. */
  get scope(): { sections: string[]; items: string[] } {
    const sections: string[] = [];
    const covered = new Set<string>();
    for (const s of this.doc.sections) {
      const leaves = this.leaves.get(s.id) ?? [];
      if (leaves.length && leaves.every((id) => this.selected.has(id))) {
        sections.push(s.id);
        leaves.forEach((id) => covered.add(id));
      }
    }
    return { sections, items: this.ids.filter((id) => !covered.has(id)) };
  }

  set scope(v: { sections: string[]; items: string[] }) {
    this.selected = new Set([...v.items, ...v.sections.flatMap((s) => this.leaves.get(s) ?? [])]);
    this.sync();
  }

  get quote() {
    return refText(this.doc, this.ids);
  }

  private select(pred: Pred) {
    for (const sec of this.doc.sections) for (const p of sec.paragraphs) for (const s of p.sentences) if (pred(s.id, s.text, "sentence")) this.selected.add(s.id);
    if (this.opts.floats) for (const f of floatsOf(this.doc)) if (pred(f.id, f.label, f.kind)) this.selected.add(f.id);
  }

  private render() {
    const cb = (node: string, disabled = false) => `<input type="checkbox" data-node="${node}" ${disabled ? "disabled" : ""} />`;
    const floatRow = (f: Float) => {
      this.order.push(f.id);
      return `<li><label>${cb(f.id)} <b>${f.kind === "table" ? "▦" : "🖼"} ${esc(f.label)}</b> <span class="muted">${esc(trunc(f.caption.map((s) => s.text).join(" "), 80))}</span></label></li>`;
    };
    // Same sections as the text pane (doc.ts sectionTree). A section's leaves include those of its subsections, so a
    // heading without text of its own ("Results") still selects everything under it.
    const section = (node: SectionNode): string => {
      const { section: sec, floats, children } = node;
      const leaves: string[] = [];
      let body = "";
      sec.paragraphs.forEach((p, i) => {
        const ids = p.sentences.map((s) => s.id);
        if (!ids.length) return;
        this.leaves.set(p.id, ids);
        leaves.push(...ids);
        this.order.push(...ids);
        body += `<li><details><summary>${cb(p.id)} <b>¶${i + 1}</b> <span class="muted">${esc(trunc(p.sentences[0].text, 80))}</span></summary><ul>${p.sentences.map((s) => `<li><label>${cb(s.id)} ${esc(s.text)}</label></li>`).join("")}</ul></details></li>`;
      });
      if (this.opts.floats)
        for (const f of floats) {
          leaves.push(f.id);
          body += floatRow(f);
        }
      for (const child of children) {
        body += section(child);
        leaves.push(...this.leaves.get(child.section.id)!);
      }
      this.leaves.set(sec.id, leaves);
      const floatCount = this.opts.floats ? countFloats(node) : 0;
      const counts = [sec.paragraphs.length && `${sec.paragraphs.length} ¶`, floatCount && `${floatCount} fig/tab`, children.length && `${children.length} subsections`].filter(Boolean).join(" · ");
      const title = `<b>§ ${esc(sec.title || "(untitled)")}</b>${counts ? ` <span class="muted">(${counts})</span>` : ""}`;
      if (!body) return `<li class="muted"><label>${cb(sec.id, true)} ${title}</label></li>`;
      return `<li><details${children.length ? " open" : ""}><summary>${cb(sec.id)} ${title}</summary><ul>${body}</ul></details></li>`;
    };
    const { roots, loose } = sectionTree(this.doc);
    const tree = roots.map(section).join("");
    const looseRows = this.opts.floats ? loose.map(floatRow).join("") : "";
    this.root.innerHTML = `<div class="actions">${this.opts.presets.map((p, i) => `<button type="button" data-preset="${i}">${esc(p.label)}</button>`).join("")}<button type="button" data-clear>Clear</button><span class="muted scope-count"></span></div>
      <div class="scope-tree"><ul class="sel-tree">${tree}${looseRows ? `<li><b>Figures and tables</b><ul>${looseRows}</ul></li>` : ""}</ul></div>`;
    this.sync();
  }

  private sync() {
    this.root.querySelectorAll<HTMLInputElement>("input[data-node]").forEach((cb) => {
      const leaves = this.leaves.get(cb.dataset.node!) ?? [cb.dataset.node!];
      const n = leaves.filter((id) => this.selected.has(id)).length;
      cb.checked = n > 0 && n === leaves.length;
      cb.indeterminate = n > 0 && n < leaves.length;
    });
    const count = this.root.querySelector(".scope-count");
    if (count) count.textContent = `${this.ids.length} selected`;
    this.opts.onChange?.();
  }
}
