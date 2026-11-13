// The Claims step's Reader view: the paper with a card per claim and a chip per analysis step, and the link between
// them shown by highlighting. One claim is focused (the same selection the Review view uses); its methods open into
// full cards next to the passages they come from, every other step stays a faded chip. Clicking a chip opens that one
// card on its own — preparation steps are edited there, since Review is organised by claim and has no place for them.
// Every open card carries the cleanup controls: Switch off/on for LLM entries, Delete for manual ones.
import { ApiError, pkg } from "../api";
import { refText } from "../doc";
import type { Action, Claim, ClaimStatus, Document } from "../types";
import { confirmDialog, esc, list, trunc } from "../util";
import { AlignedItem, AlignedView } from "./aligned";
import { modeText, pill } from "./claims";
import { claimMethods, leaves, type Leaf, type MethodInfo, type MethodRole } from "./methods";

export interface ReaderCtx {
  doc: Document | undefined;
  meta: any;
  jats: string;
  rows: { claim: Claim; off: boolean }[];
  index: Map<string, MethodInfo>;
  status: Map<string, ClaimStatus>;
  marked: Set<string>;
  /** Focused claim id, "" when none. */
  focus(): string;
  openChip(): string;
  /** "" deselects. */
  select(claimId: string): void;
  setOpenChip(actionId: string): void;
  /** Switch to the Review view on that claim. */
  review(claimId: string): void;
  /** ClaimsStep.edit (Wizard.busy): redraws the whole step at the start and the end, and reloads after. */
  edit(label: string, fn: () => Promise<unknown>): Promise<unknown>;
  /** The footer's status line (Wizard.setStatus). */
  setStatus(msg: string): void;
}

const ROLE_WORD: Record<MethodRole, string> = {
  method: "method",
  preparation: "preparation",
  unused: "not used by any claim",
  "off-only": "used only by switched-off claims",
};

/** What the chip says after the id. */
const chipWord = (i: MethodInfo) => (i.origin.disabled ? "switched off" : ROLE_WORD[i.role]);

/** The line under an open card's summary: who uses this step. */
function usedLine(i: MethodInfo) {
  const who =
    i.role === "preparation" ? "preparation · used by all claims"
    : i.role === "method" ? `used by ${i.usedBy.map((l) => l.id).join(", ")}`
    : ROLE_WORD[i.role];
  return i.origin.disabled ? `${who} · switched off` : who;
}

/**
 * The scroll offset to put back after a full redraw. `ClaimsStep.edit` goes through `Wizard.busy`, which redraws the
 * step (and so rebuilds this reader) both when the request starts and when it ends; the module keeps the offset for
 * the length of that edit, so neither redraw moves the page under the user.
 */
let keptScroll: number | null = null;

/** The element the reader scrolls in (the wizard's body), or null if nothing scrolls. */
function scroller(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

export class ClaimsReader {
  private view: AlignedView;

  constructor(private root: HTMLElement, private ctx: ReaderCtx) {
    this.view = new AlignedView(root, {
      doc: ctx.doc,
      meta: ctx.meta,
      items: this.items(),
      marked: ctx.marked,
      unlinkedLabel: "Not linked to a passage of the text",
      onMark: () => {},
      barHtml: (n) => this.barHtml(n),
      onBar: (act, el) => {
        if (act === "attach") return this.attach(el.dataset.leaf ?? "");
        if (act !== "clear") return;
        this.ctx.marked.clear();
        this.view.refreshBar();
      },
      onCard: (id, act, el) => this.cardAction(id, act, el),
      onSelect: (id) => this.select(id),
    });
    const s = scroller(root);
    if (s && keptScroll !== null) {
      const top = keptScroll;
      s.scrollTop = top;
      requestAnimationFrame(() => {
        if (keptScroll !== null && s.isConnected) s.scrollTop = top;
      });
    }
  }

  /** Rebuild the cards after a change of focus or of the open chip; no full redraw, so the page stays where it is. */
  refocus() {
    this.view.setItems(this.items());
    this.view.refreshBar();
    // Place the new cards before the browser paints: setItems alone leaves them stacked at the top until the next
    // frame, which reads as the margin jumping when a claim is selected.
    this.view.layout();
  }

  /** The focused claim, if one is focused and still live. */
  private focusedClaim(): Claim | undefined {
    const focus = this.ctx.focus();
    return this.ctx.rows.find((r) => !r.off && r.claim.id === focus)?.claim;
  }

  // ---------- the marks bar ----------

  /**
   * What marking passages offers: attaching them to the focused claim as its method. A method belongs to one testable
   * leaf, so the bar names the claim it would change and offers one button per leaf; with nothing focused (or an
   * untestable claim focused) there is nothing to attach to, and it says so instead.
   */
  private barHtml(n: number) {
    const clear = `<span class="spacer"></span><button data-act="clear">Clear marks</button>`;
    const claim = this.focusedClaim();
    if (!claim) return `<span><b>${n}</b> marked</span><span class="muted">Select a claim to attach a method</span>${clear}`;
    const ls = leaves(claim);
    if (!ls.length) return `<span class="muted">${esc(claim.id)} is not testable, so it has no method</span>${clear}`;
    const buttons = ls
      .map((l) => `<button data-act="attach" data-leaf="${esc(l.id)}" title="Make the marked passages the method of ${esc(l.id)}">${esc(l.id)}: ${esc(trunc(l.text, 40))}</button>`)
      .join("");
    return `<span><b>${n}</b> marked</span><span>Attach to <b>${esc(claim.id)}</b> · ${esc(trunc(claim.text, 60))}:</span>${buttons}${clear}`;
  }

  private attach(leafId: string) {
    const claim = this.focusedClaim();
    const leaf = claim && leaves(claim).find((l) => l.id === leafId);
    if (leaf) void this.runAttach(leaf);
  }

  private async runAttach(leaf: Leaf) {
    let id = "";
    await this.edit(`Attaching a method to ${leaf.id}`, async () => {
      id = await attachMethod(this.ctx, leaf);
      this.ctx.marked.clear(); // before the closing redraw, so the bar goes away with it
    });
    if (!id) return; // the write failed; Wizard.busy has shown the error
    // After ctx.edit, so the step has reloaded and `<label>: done` has already been written.
    this.ctx.setOpenChip(id);
    this.ctx.setStatus(`Attached ${id} to ${leaf.id}; ${leaf.claim} is unticked. Rewrite the method's summary in Review.`);
  }

  // ---------- cards ----------

  private items(): AlignedItem[] {
    const focus = this.ctx.focus();
    const focused = this.focusedClaim();
    const open = this.ctx.openChip();
    const methods = focused ? claimMethods(focused) : [];
    const items = this.ctx.rows.filter((r) => !r.off).map((r) => this.claimItem(r.claim, r.claim.id === focus));
    for (const info of this.ctx.index.values()) items.push(this.methodItem(info, methods, open, !!focus || !!open));
    return items;
  }

  private anchors(refs: { extraction: string; id: string }[]) {
    return [...new Set(refs.filter((x) => x.extraction === this.ctx.jats).map((x) => x.id))];
  }

  private claimItem(c: Claim, focused: boolean): AlignedItem {
    const st = this.ctx.status.get(c.id);
    return {
      id: c.id,
      anchors: this.anchors([...c.refs, ...c.sub_claims.flatMap((s) => s.refs)]),
      cls: focused ? "on" : "",
      links: claimMethods(c),
      html: `<div class="al-card-head"><b>${esc(c.id)}</b><span class="badge">${esc(modeText(c))}</span>${c.checked ? `<span class="cl-tick">✓</span>` : ""}<span class="spacer"></span>${pill(st?.state, st?.stale)}
        <button data-act="review" title="Review this claim">Review</button></div><div class="al-desc">${esc(trunc(c.text, 220))}</div>`,
    };
  }

  private methodItem(i: MethodInfo, methods: string[], open: string, anyFocus: boolean): AlignedItem {
    const a = i.action;
    const full = methods.includes(a.id) || open === a.id;
    return {
      id: a.id,
      anchors: this.anchors(a.refs),
      chip: !full,
      cls: [full ? "open" : anyFocus ? "dim" : "", i.origin.disabled ? "off" : "", i.origin.source === "manual" ? "manual" : ""].filter(Boolean).join(" "),
      links: [...new Set(i.usedBy.map((l) => l.claim))],
      html: full ? this.cardHtml(i) : `<b>${esc(a.id)}</b> · ${esc(chipWord(i))}`,
    };
  }

  private cardHtml(i: MethodInfo) {
    const a = i.action;
    const claim = this.reviewClaim(i);
    const controls = [
      claim ? `<button data-act="review" title="Review ${esc(claim)}">Review</button>` : "",
      i.origin.source === "llm" && i.origin.disabled ? `<button data-act="switch-on" title="Use this step again">Switch on</button>` : "",
      i.origin.source === "llm" && !i.origin.disabled ? `<button data-act="switch-off" title="Leave this step out; it is kept and can be switched on again">Switch off</button>` : "",
      i.origin.source === "manual" && i.role !== "off-only" ? `<button data-act="delete" title="Delete this step">Delete</button>` : "",
    ].join("");
    return `<div class="al-card-head"><b>${esc(a.id)}</b><span class="badge">${esc(a.kind)}</span><span class="spacer"></span>${controls}</div>
      ${i.role === "preparation" ? this.fieldsHtml(i) : `<div class="al-desc">${esc(a.summary)}</div>`}
      <div class="small muted">${esc(usedLine(i))}</div>`;
  }

  /** Preparation steps are edited here: Review is organised by claim, and they belong to all of them. */
  private fieldsHtml(i: MethodInfo) {
    const a = i.action;
    // data-act="fields": a click on a label or on the blank space between the editors must reach onCard (where it is
    // ignored) rather than fall through to onSelect, which would close the card the user is typing in.
    return `<div class="al-fields" data-act="fields">
      <label class="small muted">Summary<textarea rows="2" data-act="field" data-f="summary">${esc(a.summary)}</textarea></label>
      <label class="small muted">Quote<textarea rows="2" data-act="field" data-f="quote">${esc(a.quote)}</textarea></label>
      <label class="small muted">Inputs<input data-act="field" data-f="inputs" list="rp-input-names" value="${esc(a.inputs.join(", "))}" title="input resource names, comma-separated" /></label>
      <label class="small muted">Depends on<input data-act="field" data-f="depends_on" value="${esc(a.depends_on.join(", "))}" title="step ids, comma-separated" /></label></div>`;
  }

  /** The claim a method card's Review button opens: the focused one if it uses the step, else its first live user. */
  private reviewClaim(i: MethodInfo) {
    const focus = this.ctx.focus();
    return (i.usedBy.some((l) => l.claim === focus) ? focus : i.usedBy[0]?.claim) ?? "";
  }

  // ---------- events ----------

  /** A click on a card body: claims toggle the focus, steps toggle their own open card. */
  private select(id: string) {
    const info = this.ctx.index.get(id);
    if (!info) return this.ctx.select(id === this.ctx.focus() ? "" : id);
    if (this.ctx.openChip() === id) return this.ctx.setOpenChip("");
    this.ctx.setOpenChip(id);
    // Opening an analysis method that claims use also focuses the first of them, so the link is visible at once.
    if (info.role === "method") this.ctx.select(info.usedBy[0].claim);
  }

  private cardAction(id: string, act: string, el: HTMLElement) {
    const info = this.ctx.index.get(id);
    if (act === "review") return this.ctx.review(info ? this.reviewClaim(info) : id);
    if (!info) return;
    if (act === "fields") return; // a click beside the editors: not a toggle, and nothing to save
    const path = `/actions/${encodeURIComponent(id)}`;
    if (act === "switch-off") return void this.edit(`Leaving out ${id}`, () => pkg.patch(path, { disabled: true }));
    if (act === "switch-on") return void this.edit(`Using ${id}`, () => pkg.patch(path, { disabled: false }));
    if (act === "delete") return void this.remove(id, path);
    if (act === "field") return this.field(info, el as HTMLInputElement);
  }

  /** Deleting is destructive and cannot be undone, so it is confirmed first, like every other delete in the app. */
  private async remove(id: string, path: string) {
    if (!(await confirmDialog("Delete step", `Delete <b>${esc(id)}</b>?`, "Delete", true))) return;
    await this.edit(`Deleting ${id}`, () => deleteAction(id, path));
  }

  private field(i: MethodInfo, el: HTMLInputElement) {
    const f = el.dataset.f!;
    const stored = (i.action as unknown as Record<string, unknown>)[f];
    // The value is trimmed, so trim the stored text too: a stored value with stray whitespace must not look changed.
    const was = typeof stored === "string" ? stored.trim() : stored;
    const value = f === "inputs" || f === "depends_on" ? list(el.value) : el.value.trim();
    // A click on a textarea arrives here too (AlignedView passes any [data-act]); only a real change is saved, and an
    // edit of an action unticks the claims using it, so a no-op must never be sent.
    if (Array.isArray(was) ? was.join("\u0000") === (value as string[]).join("\u0000") : was === value) return;
    void this.edit(`Changing ${i.action.id}`, () => pkg.patch(`/actions/${encodeURIComponent(i.action.id)}`, { [f]: value }));
  }

  /** ctx.edit redraws the step twice; keep the scroll offset for both redraws. */
  private async edit(label: string, fn: () => Promise<unknown>) {
    const s = scroller(this.root);
    keptScroll = s ? s.scrollTop : null;
    try {
      await this.ctx.edit(label, fn);
    } finally {
      keptScroll = null;
    }
  }
}

/**
 * Make the marked passages the method of one testable leaf: a new analysis step from their text (no LLM call — the
 * user rewrites the summary in Review), then the claim pointed at it. The step the leaf used before is left alone;
 * if nothing else uses it, the reader's index shows it as not used by any claim. Returns the new step's id.
 */
export async function attachMethod(ctx: ReaderCtx, leaf: Leaf): Promise<string> {
  const ids = [...ctx.marked];
  const text = refText(ctx.doc, ids).trim();
  const old = leaf.method ? ctx.index.get(leaf.method) : undefined;
  const created = await pkg.post<Action>("/actions", {
    kind: "analysis",
    refs: ids.map((id) => ({ extraction: ctx.jats, id })),
    quote: text,
    // A step needs a summary: marks that resolve to nothing in the document (or to an empty sentence) have no text.
    summary: text || `Implement the marked passage for ${leaf.id}`,
    inputs: old?.action.inputs ?? [],
    depends_on: [...ctx.index.values()].filter((i) => i.role === "preparation" && !i.origin.disabled).map((i) => i.action.id),
    produces: leaf.evidence,
  });
  try {
    await pkg.patch(`/claims/${encodeURIComponent(leaf.claim)}`, claimPatch(ctx, leaf, created.id));
  } catch (e) {
    // The step is only ever a method of this leaf: a half-attach would leave it behind, used by nothing.
    await pkg.del(`/actions/${encodeURIComponent(created.id)}`).catch(() => {});
    throw e;
  }
  return created.id;
}

/** JSON merge patch replaces arrays, so changing one sub-claim's method means sending the whole list back. */
function claimPatch(ctx: ReaderCtx, leaf: Leaf, method: string) {
  const claim = ctx.rows.find((r) => r.claim.id === leaf.claim)!.claim;
  if (leaf.id === claim.id) return { method };
  return { sub_claims: claim.sub_claims.map((s) => (s.id === leaf.id ? { ...s, method } : s)) };
}

/** Only manual steps can be deleted, and the backend refuses while anything still uses one. */
async function deleteAction(id: string, path: string) {
  try {
    await pkg.del(path);
  } catch (e) {
    if (e instanceof ApiError && e.code === "in_use") throw new ApiError(`${id} is still used (a claim's method, or another step depends on it); change that first.`, e.problem);
    throw e;
  }
}
