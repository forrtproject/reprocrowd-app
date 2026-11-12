// The paper with extracted items aligned next to it, like comments in the margin. Each item (a target, or an
// analysis step) is a card at the height of the first passage it refers to; cards that would overlap move down, and a
// line in the gutter joins each to its passage. Hovering a card lights up its passages, hovering a passage its cards.
// Clicking sentences (or figures and tables) marks them; the step offers to add an item or run the LLM on the marks.
// Items that refer to no passage of this text are listed above the cards.
import { paperHtml } from "../paper";
import type { Document } from "../types";
import { esc } from "../util";

export interface AlignedItem {
  id: string;
  /** Ids of the elements of the text the item refers to (sentences, paragraphs, sections, figures, tables). */
  anchors: string[];
  /** The card's inner HTML. */
  html: string;
  /** Extra classes of the card, e.g. "off" for items not used. */
  cls?: string;
}

export interface AlignedOptions {
  doc: Document | undefined;
  meta: any;
  items: AlignedItem[];
  marked: Set<string>;
  /** The marks changed (clicks in the text). */
  onMark(): void;
  /** Bar shown while something is marked: buttons with data-act are passed to onBar. */
  barHtml(n: number): string;
  onBar(act: string): void;
  /** Clicks on [data-act] elements inside a card. */
  onCard(id: string, act: string, el: HTMLElement): void;
  unlinkedLabel: string;
}

const GAP = 8;

export class AlignedView {
  private grid!: HTMLElement;
  private side!: HTMLElement;
  private svg!: SVGSVGElement;
  private resize?: ResizeObserver;
  private frame = 0;

  constructor(private root: HTMLElement, private o: AlignedOptions) {
    const linked = o.items.filter((i) => i.anchors.length);
    const unlinked = o.items.filter((i) => !i.anchors.length);
    root.innerHTML = `
      <div class="al-grid">
        <div class="al-text">${paperHtml(o.doc, o.meta)}</div>
        <div class="al-gutter"><svg class="al-lines"></svg></div>
        <div class="al-side">
          ${unlinked.length ? `<div class="al-unlinked"><h4>${esc(o.unlinkedLabel)} <span class="muted">${unlinked.length}</span></h4>${unlinked.map((i) => this.card(i)).join("")}</div>` : ""}
          <div class="al-cards">${linked.map((i) => this.card(i)).join("")}</div>
        </div>
      </div>
      <div class="al-bar" hidden></div>`;
    this.grid = root.querySelector(".al-grid")!;
    this.side = root.querySelector(".al-side")!;
    this.svg = root.querySelector(".al-lines")!;
    this.markRefs();
    this.showMarks();
    this.bind();
    this.schedule();
    this.resize = new ResizeObserver(() => this.schedule());
    this.resize.observe(root.querySelector(".al-text")!);
    document.fonts?.ready.then(() => this.schedule());
  }

  private card(i: AlignedItem) {
    return `<div class="al-card ${i.cls ?? ""}" data-id="${esc(i.id)}">${i.html}</div>`;
  }

  /** The elements an id stands for in the text. */
  private els(id: string): HTMLElement[] {
    return [...this.grid.querySelectorAll<HTMLElement>(`.al-text [data-hid="${CSS.escape(id)}"]`)];
  }

  /** Underline every passage some item refers to (dimmed if only unused items do). */
  private markRefs() {
    const used = new Set<string>();
    const any = new Set<string>();
    for (const i of this.o.items) for (const a of i.anchors) (i.cls?.includes("off") ? any : used).add(a);
    for (const id of new Set([...used, ...any])) for (const el of this.els(id)) el.classList.add(used.has(id) ? "ref" : "ref-off");
  }

  private showMarks() {
    this.grid.querySelectorAll(".marked").forEach((e) => e.classList.remove("marked"));
    for (const id of this.o.marked) this.els(id).forEach((e) => e.classList.add("marked"));
    const bar = this.root.querySelector<HTMLElement>(".al-bar")!;
    bar.hidden = !this.o.marked.size;
    if (this.o.marked.size) bar.innerHTML = this.o.barHtml(this.o.marked.size);
  }

  private bind() {
    const text = this.grid.querySelector<HTMLElement>(".al-text")!;
    // Marking: a click on a sentence, figure or table (not while selecting text, and not on a section's twisty).
    text.addEventListener("click", (e) => {
      if ((e.target as HTMLElement).closest("summary") || String(window.getSelection() ?? "").trim()) return;
      const el = (e.target as HTMLElement).closest<HTMLElement>(".sent[data-hid], figure[data-hid]");
      if (!el) return;
      const id = el.dataset.hid!;
      if (this.o.marked.has(id)) this.o.marked.delete(id);
      else this.o.marked.add(id);
      this.showMarks();
      this.o.onMark();
    });
    text.addEventListener("mouseover", (e) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>("[data-hid]");
      this.hot(el ? this.o.items.filter((i) => this.covers(i, el)).map((i) => i.id) : []);
    });
    text.addEventListener("mouseleave", () => this.hot([]));
    // A collapsed or expanded section moves everything below it.
    text.addEventListener("toggle", () => this.schedule(), true);
    this.side.addEventListener("mouseover", (e) => {
      const c = (e.target as HTMLElement).closest<HTMLElement>(".al-card");
      this.hot(c ? [c.dataset.id!] : []);
    });
    this.side.addEventListener("mouseleave", () => this.hot([]));
    this.side.addEventListener("click", (e) => {
      const c = (e.target as HTMLElement).closest<HTMLElement>(".al-card");
      if (!c) return;
      const act = (e.target as HTMLElement).closest<HTMLElement>("[data-act]");
      if (act && act.tagName !== "INPUT") return this.o.onCard(c.dataset.id!, act.dataset.act!, act);
      if (!act) this.reveal(c.dataset.id!);
    });
    this.side.addEventListener("change", (e) => {
      const el = e.target as HTMLElement;
      const c = el.closest<HTMLElement>(".al-card");
      if (c && el.dataset.act) this.o.onCard(c.dataset.id!, el.dataset.act, el);
    });
    this.root.querySelector(".al-bar")!.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-act]");
      if (b) this.o.onBar(b.dataset.act!);
    });
  }

  /** Does the item refer to this element, or to something that contains it? */
  private covers(i: AlignedItem, el: HTMLElement) {
    return i.anchors.some((a) => {
      const hid = el.dataset.hid;
      if (a === hid) return true;
      return this.els(a).some((x) => x.contains(el) || el.contains(x));
    });
  }

  private hot(ids: string[]) {
    this.grid.querySelectorAll(".hot").forEach((e) => e.classList.remove("hot"));
    for (const id of ids) {
      this.side.querySelector(`.al-card[data-id="${CSS.escape(id)}"]`)?.classList.add("hot");
      const item = this.o.items.find((i) => i.id === id);
      for (const a of item?.anchors ?? []) this.els(a).forEach((e) => e.classList.add("hot"));
    }
    this.svg.querySelectorAll("path").forEach((p) => p.classList.toggle("hot", ids.includes(p.dataset.id!)));
  }

  /** Scroll the item's first passage into view and flash it. */
  private reveal(id: string) {
    const item = this.o.items.find((i) => i.id === id);
    const el = item?.anchors.flatMap((a) => this.els(a)).map((e) => this.visible(e))[0];
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1200);
  }

  /** The element itself, or the heading of the collapsed section it is hidden in. */
  private visible(el: HTMLElement): HTMLElement {
    let shown = el;
    for (let d = el.parentElement?.closest("details"); d; d = d.parentElement?.closest("details") ?? null) {
      if (!(d as HTMLDetailsElement).open) shown = d.querySelector(":scope > summary") as HTMLElement;
    }
    return shown;
  }

  private schedule() {
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => this.layout());
  }

  /** Place the cards at their passages, pushing overlapping ones down, and draw the gutter lines. */
  layout() {
    if (!this.root.isConnected) return this.resize?.disconnect();
    const g = this.grid.getBoundingClientRect();
    const box = this.side.querySelector<HTMLElement>(".al-cards")!;
    const base = box.getBoundingClientRect().top - g.top; // cards are positioned inside the box, below the unlinked list
    const placed = [...box.querySelectorAll<HTMLElement>(":scope > .al-card")]
      .map((c) => {
        const item = this.o.items.find((i) => i.id === c.dataset.id)!;
        const tops = item.anchors.flatMap((a) => this.els(a)).map((e) => this.visible(e).getBoundingClientRect().top - g.top);
        return { c, id: item.id, anchor: tops.length ? Math.min(...tops) : base };
      })
      .sort((a, b) => a.anchor - b.anchor);
    const w = (this.svg.parentElement as HTMLElement).offsetWidth;
    const lines: string[] = [];
    let bottom = base;
    for (const p of placed) {
      const top = Math.max(p.anchor, bottom);
      p.c.style.top = `${top - base}px`;
      bottom = top + p.c.offsetHeight + GAP;
      const y0 = p.anchor + 10;
      const y1 = top + 14;
      lines.push(`<path data-id="${esc(p.id)}" class="${p.c.classList.contains("off") ? "off" : ""}" d="M0,${y0} C${w / 2},${y0} ${w / 2},${y1} ${w},${y1}" />`);
    }
    box.style.height = `${Math.max(0, bottom - base)}px`;
    this.svg.setAttribute("width", String(w));
    this.svg.setAttribute("height", String(Math.max(bottom, this.grid.offsetHeight)));
    this.svg.innerHTML = lines.join("");
  }
}
