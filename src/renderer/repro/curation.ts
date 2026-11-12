// Steps 2 and 3: targets (results to reproduce) and actions (the analysis plan). Both are curated lists in the
// backend: items added by hand, plus LLM runs whose entries keep the LLM's version and store the user's changes on
// top (restorable). Two views: Reader, the paper with each item as a card next to the passage it comes from (marking
// sentences there leads to new items), and Table, manual items first, then one group per LLM run.
import { ApiError, host, pkg } from "../api";
import { refText } from "../doc";
import { COMPARATORS, type Action, type CurationFile, type Document, type Item, type LlmEntry, type LlmProfile, type LlmRun, type PromptPreview, type Resource, type Target } from "../types";
import { choose, confirmDialog, esc, list, modal, seconds, showError, showPrompt, trunc, when } from "../util";
import { ACTION_PRESETS, Preset, ScopeTree, TARGET_PRESETS } from "./scope";
import { AlignedItem, AlignedView } from "./aligned";
import type { Step, Wizard } from "./wizard";

type Kind = "targets" | "actions";
const NOUN = { targets: "target", actions: "step" } as const;

interface Row {
  value: Item;
  manual: boolean;
  entry?: LlmEntry<Item>;
  run?: LlmRun<Item>;
}

const valueOf = (e: LlmEntry<Item>) => ({ ...e.base, ...(e.edit ?? {}) }) as Item;

export class CurationStep implements Step {
  title: string;
  private file?: CurationFile<Item>;
  private inputs: Resource[] = [];
  private targets: Target[] = [];
  private profiles: LlmProfile[] = [];
  private doc?: Document;
  /** Passages marked in the reader view (sentence, figure and table ids), for the next new item or LLM run. */
  private marked = new Set<string>();

  constructor(private w: Wizard, private kind: Kind) {
    this.title = kind === "targets" ? "Targets" : "Analysis plan";
  }

  async load() {
    const [file, inputs, targets] = await Promise.all([
      pkg.get<CurationFile<Item>>(`/${this.kind}`, { view: "raw" }),
      this.kind === "actions" ? pkg.get<Resource[]>("/inputs") : Promise.resolve([]),
      this.kind === "actions" ? pkg.get<{ item: Target }[]>("/targets").then((v) => v.map((x) => x.item)) : Promise.resolve([]),
    ]);
    this.file = file;
    this.inputs = inputs;
    this.targets = targets;
    this.doc = await this.w.app.loadDoc(this.w.app.jats()).catch(() => undefined);
    if (!this.profiles.length) this.profiles = await host.service<LlmProfile[]>("/v1/llm-profiles").catch(() => []);
  }

  private get state() {
    return this.w.app.state;
  }

  // ---------- table ----------

  private head(): string[] {
    return this.kind === "targets" ? ["Type", "Description / quote", "Expected", "Cmp", "dp", "Unit", "Refs"] : ["Kind", "Summary / quote", "Inputs", "Depends on", "Produces", "Refs"];
  }

  private cells(v: Item, dis: boolean): string[] {
    const d = dis ? "disabled" : "";
    const refs = `<span class="badge" title="${esc(v.refs.map((r) => `${r.extraction}:${r.id}`).join("\n") || (v.unreferenced ? `unreferenced: ${v.note ?? ""}` : ""))}">${v.refs.length || (v.unreferenced ? "none" : 0)}</span>`;
    const quote = v.quote ? `<div class="quote">“${esc(trunc(v.quote, 240))}”</div>` : "";
    if (this.kind === "targets") {
      const t = v as Target;
      const exp = t.type === "table" ? `<span class="muted">${(t.expected as string[][] | null)?.length ?? 0}×${(t.expected as string[][] | null)?.[0]?.length ?? 0} table</span>` : t.type === "figure" ? `<span class="muted">figure</span>` : `<input data-f="expected" value="${esc(t.expected ?? "")}" size="8" ${d} />`;
      const num = t.type === "number";
      return [
        `<select data-f="type" ${d}>${["number", "categorical", "table", "figure"].map((x) => `<option ${x === t.type ? "selected" : ""}>${x}</option>`).join("")}</select>`,
        `<textarea data-f="description" rows="2" ${d}>${esc(t.description)}</textarea>${quote}`,
        exp,
        num ? `<select data-f="comparator" ${d}>${COMPARATORS.map((c) => `<option ${(t.comparator ?? "=") === c ? "selected" : ""}>${c}</option>`).join("")}</select>` : "",
        num ? `<input data-f="decimals" value="${t.decimals ?? ""}" size="2" title="decimals as reported" ${d} />` : "",
        num ? `<input data-f="unit" value="${esc(t.unit ?? "")}" size="4" ${d} />` : "",
        refs,
      ];
    }
    const a = v as Action;
    return [
      `<select data-f="kind" ${d}>${["analysis", "transform"].map((k) => `<option ${a.kind === k ? "selected" : ""}>${k}</option>`).join("")}</select>`,
      `<textarea data-f="summary" rows="2" ${d}>${esc(a.summary)}</textarea>${quote}`,
      `<input data-f="inputs" value="${esc(a.inputs.join(", "))}" list="rp-input-names" ${d} title="input resource names, comma-separated" />`,
      `<input data-f="depends_on" value="${esc(a.depends_on.join(", "))}" size="8" ${d} title="action ids, comma-separated" />`,
      `<input data-f="produces" value="${esc(a.produces.join(", "))}" size="10" ${d} title="target ids, comma-separated" />`,
      refs,
    ];
  }

  private parse(field: string, raw: string, v: Item): unknown {
    if (this.kind === "targets") {
      const t = v as Target;
      if (field === "expected") return raw === "" ? null : t.type === "number" && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
      if (field === "decimals") return raw === "" ? null : Math.max(0, parseInt(raw, 10) || 0);
      if (field === "comparator") return raw === "=" ? null : raw;
      if (field === "unit") return raw || null;
      return raw;
    }
    return ["inputs", "depends_on", "produces"].includes(field) ? list(raw) : raw;
  }

  private rows(): { manual: Row[]; runs: { run: LlmRun<Item>; rows: Row[] }[] } {
    const f = this.file!;
    return {
      manual: f.manual.map((value) => ({ value, manual: true })),
      runs: f.llm_runs.map((run) => ({ run, rows: run.entries.map((entry) => ({ value: valueOf(entry), manual: false, entry, run })) })),
    };
  }

  private idCell(r: Row) {
    return `<b>${esc(r.value.id)}</b>${r.entry?.edit ? `<br><span class="badge edited" title="Changed from the LLM's extraction">edited</span>` : ""}`;
  }

  private rowActions(r: Row) {
    if (r.manual) return `<button data-act="remove" title="Delete">✕</button>`;
    return r.entry?.edit ? `<button data-act="restore" title="Restore the LLM's version">↺ Restore</button>` : "";
  }

  private rowHtml(r: Row): string {
    const off = !!(r.entry?.disabled || r.run?.disabled);
    const use = r.manual ? `<span class="muted" title="Added by hand (delete it to drop it)">●</span>` : `<input type="checkbox" data-act="toggle" ${r.entry?.disabled ? "" : "checked"} ${r.run?.disabled ? "disabled" : ""} title="Use this ${NOUN[this.kind]}" />`;
    return `<tr data-id="${esc(r.value.id)}" class="${r.manual ? "" : "rp-nested"} ${off ? "rp-disabled" : ""}">
      <td>${use}</td><td class="rp-id">${this.idCell(r)}</td>${this.cells(r.value, off).map((c) => `<td>${c}</td>`).join("")}
      <td class="nowrap rp-row-actions">${this.rowActions(r)}</td></tr>`;
  }

  private runSummary(run: LlmRun<Item>) {
    const off = run.entries.filter((e) => e.disabled).length;
    const edited = run.entries.filter((e) => e.edit).length;
    const scope = run.request.scope;
    const scopeText = !scope || (!scope.sections?.length && !scope.items?.length) ? "whole document" : [scope.sections?.length && `${scope.sections.length} sections`, scope.items?.length && `${scope.items.length} items`].filter(Boolean).join(" + ");
    return `${esc(run.request.extraction ?? "")} (${scopeText}) · ${esc(run.call.profile)}${run.call.model ? ` / ${esc(run.call.model)}` : ""} · ${esc(when(run.call.started_at))} · ${seconds(run.call.duration_ms)} · ${run.entries.length} ${NOUN[this.kind]}s${off ? `, ${off} not used` : ""}${edited ? `, ${edited} edited` : ""}${run.rejected?.length ? `, <span class="warn-inline">${run.rejected.length} rejected</span>` : ""}`;
  }

  draw(body: HTMLElement) {
    if (!this.file) return;
    const { manual, runs } = this.rows();
    const ncol = this.head().length + 3;
    const generating = this.w.running.has(`llm-${this.kind}`);
    const enabled = manual.length + runs.reduce((n, g) => n + (g.run.disabled ? 0 : g.rows.filter((r) => !r.entry?.disabled).length), 0);
    let tbody = "";
    if (manual.length) tbody += `<tr class="rp-group"><td colspan="${ncol}"><b>Added by hand</b> <span class="muted">${manual.length}</span></td></tr>` + manual.map((r) => this.rowHtml(r)).join("");
    for (const g of runs) {
      const key = `${this.kind}:${g.run.id}`;
      const collapsed = this.state.collapsed.includes(key);
      tbody += `<tr class="rp-group ${g.run.disabled ? "rp-disabled" : ""}" data-run="${esc(g.run.id)}"><td colspan="${ncol}"><div class="rp-group-head">
        <button data-act="collapse" class="twisty" title="${collapsed ? "Expand" : "Collapse"}">${collapsed ? "▸" : "▾"}</button>
        <b>LLM ${esc(g.run.id)}</b><span class="muted small">${this.runSummary(g.run)}</span><span class="spacer"></span>
        <button data-act="details">Call details</button>
        <button data-act="run-toggle">${g.run.disabled ? "Use this run again" : "Don't use this run"}</button></div></td></tr>`;
      if (!collapsed) tbody += g.rows.map((r) => this.rowHtml(r)).join("");
    }
    const sources = [this.w.app.jats()].filter(Boolean);
    const isT = this.kind === "targets";
    const view = this.state.curationView;
    body.innerHTML = `
    <div class="card ${view === "reader" ? "cur-reader" : ""}">
      <div class="card-head"><h3>${isT ? "Reproduction targets" : "Analysis plan"}</h3><span class="muted">${enabled} in use</span>
        <div class="seg" id="rp-view"><button data-view="reader" class="${view === "reader" ? "on" : ""}" title="The paper, with each ${NOUN[this.kind]} next to its passage">Reader</button><button data-view="table" class="${view === "table" ? "on" : ""}" title="All fields, grouped by LLM run">Table</button></div>
        <span class="spacer"></span>
        <button id="rp-add">+ Add ${NOUN[this.kind]}…</button>
        <button id="rp-llm" class="primary" ${generating || !sources.length ? "disabled" : ""}>${generating ? "Generating…" : "Generate with LLM…"}</button></div>
      <p class="hint">${isT ? "The reported results the reproduction should recompute." : "The analysis steps described in the paper, linked to input files (step 1) and to the targets they produce (step 2). Load steps (one per data file) and the final results step are added when the master script is created."}
        ${view === "reader" ? `Each ${NOUN[this.kind]} sits next to the passage it comes from. Click sentences, figures or tables to mark them, then add a ${NOUN[this.kind]} or let the LLM read just those.` : "Rows from an LLM run are grouped under it."} Your changes to LLM entries are stored on top of the LLM's version and can be restored. Unticked ${NOUN[this.kind]}s are not used.</p>
      <datalist id="rp-input-names">${this.inputs.map((i) => `<option value="${esc(i.name)}">${esc(i.path)}</option>`).join("")}</datalist>
      ${view === "reader" ? `<div class="al-host"></div>` : `<table class="tbl-list rp-items"><thead><tr><th>Use</th><th>ID</th>${this.head().map((h) => `<th>${h}</th>`).join("")}<th></th></tr></thead>
        <tbody>${tbody || `<tr><td colspan="${ncol}" class="muted">No ${NOUN[this.kind]}s yet. Generate them with the LLM or add them by hand.</td></tr>`}</tbody></table>`}
    </div>`;
    body.querySelector("#rp-add")!.addEventListener("click", () => this.addDialog());
    body.querySelector("#rp-llm")!.addEventListener("click", () => this.generateDialog());
    body.querySelector("#rp-view")!.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-view]");
      if (!b || b.dataset.view === view) return;
      this.state.curationView = b.dataset.view as "reader" | "table";
      this.w.app.save();
      this.w.draw();
    });
    if (view === "table") this.bind(body.querySelector<HTMLElement>(".rp-items")!);
    else this.drawReader(body.querySelector<HTMLElement>(".al-host")!);
  }

  // ---------- reader view ----------

  private allRows(): Row[] {
    const { manual, runs } = this.rows();
    return [...manual, ...runs.flatMap((g) => g.rows)];
  }

  private cardHtml(r: Row): string {
    const v = r.value;
    const off = !!(r.entry?.disabled || r.run?.disabled);
    const use = r.manual
      ? `<span class="muted" title="Added by hand (delete it to drop it)">●</span>`
      : `<input type="checkbox" data-act="toggle" ${r.entry?.disabled ? "" : "checked"} ${r.run?.disabled ? "disabled" : ""} title="${r.run?.disabled ? "The whole LLM run is left out (Table view)" : `Use this ${NOUN[this.kind]}`}" />`;
    const actions = [`<button data-act="edit" title="Edit all fields">Edit</button>`, r.manual ? `<button data-act="remove" title="Delete">✕</button>` : r.entry?.edit ? `<button data-act="restore" title="Restore the LLM's version">↺</button>` : ""].join("");
    const origin = r.manual ? "added by hand" : `LLM ${esc(r.run!.id)} · ${esc(r.run!.call.profile)}${off && r.run?.disabled ? " · run not used" : ""}`;
    const other = v.refs.filter((x) => x.extraction !== this.w.app.jats()).length;
    let detail: string;
    if (this.kind === "targets") {
      const t = v as Target;
      const exp =
        t.type === "table" ? `${(t.expected as string[][] | null)?.length ?? 0}×${(t.expected as string[][] | null)?.[0]?.length ?? 0} table`
        : t.type === "figure" ? "a figure"
        : t.expected === null || t.expected === undefined || t.expected === "" ? `<span class="warn-inline">no expected value</span>`
        : `<b>${esc(t.comparator && t.comparator !== "=" ? `${t.comparator} ` : "")}${esc(t.expected)}</b>${t.unit ? ` ${esc(t.unit)}` : ""}${t.decimals !== null && t.decimals !== undefined ? ` <span class="muted">(${t.decimals} dp)</span>` : ""}`;
      detail = `<div class="al-desc">${esc(t.description)}</div><div class="small">Expected ${exp}</div>`;
    } else {
      const a = v as Action;
      const links = [a.inputs.length && `inputs ${a.inputs.join(", ")}`, a.depends_on.length && `after ${a.depends_on.join(", ")}`, a.produces.length && `produces ${a.produces.join(", ")}`].filter(Boolean).join(" · ");
      detail = `<div class="al-desc">${esc(a.summary)}</div>${links ? `<div class="small">${esc(links)}</div>` : ""}`;
    }
    const kindBadge = this.kind === "targets" ? (v as Target).type : (v as Action).kind;
    return `<div class="al-card-head">${use}<b>${esc(v.id)}</b><span class="badge">${esc(kindBadge)}</span>${r.entry?.edit ? `<span class="badge edited" title="Changed from the LLM's extraction">edited</span>` : ""}<span class="spacer"></span>${actions}</div>
      ${detail}<div class="small muted">${origin}${v.unreferenced ? ` · no passage${v.note ? `: ${esc(v.note)}` : ""}` : ""}${other ? ` · ${other} reference${other > 1 ? "s" : ""} to another text` : ""}</div>`;
  }

  private drawReader(host: HTMLElement) {
    const jats = this.w.app.jats();
    const items: AlignedItem[] = this.allRows().map((r) => ({
      id: r.value.id,
      anchors: r.value.refs.filter((x) => x.extraction === jats).map((x) => x.id),
      html: this.cardHtml(r),
      cls: [r.entry?.disabled || r.run?.disabled ? "off" : "", r.manual ? "manual" : ""].join(" "),
    }));
    const noun = NOUN[this.kind];
    new AlignedView(host, {
      doc: this.doc,
      meta: this.w.app.meta(),
      items,
      marked: this.marked,
      unlinkedLabel: `Not linked to a passage of the text`,
      onMark: () => {},
      barHtml: (n) => `<span><b>${n}</b> marked</span><span class="spacer"></span>
        <button data-act="add" class="primary">+ Add ${noun} from the marked text…</button>
        <button data-act="generate" ${this.w.running.has(`llm-${this.kind}`) ? "disabled" : ""}>Generate ${noun}s from the marked text…</button>
        <button data-act="clear">Clear marks</button>`,
      onBar: (act) => {
        if (act === "clear") {
          this.marked.clear();
          return this.w.draw();
        }
        const ids = [...this.marked];
        if (act === "add") this.addDialog(ids);
        if (act === "generate") this.generateDialog({ sections: [], items: ids });
      },
      onCard: (id, act, el) => this.cardAction(id, act, el),
    });
  }

  private async cardAction(id: string, act: string, el: HTMLElement) {
    const r = this.find(id);
    if (!r) return;
    const P = `/${this.kind}`;
    if (act === "toggle") return void this.w.busy("edit", `${(el as HTMLInputElement).checked ? "Using" : "Leaving out"} ${id}`, () => pkg.patch(`${P}/${encodeURIComponent(id)}`, { disabled: !(el as HTMLInputElement).checked }));
    if (act === "edit") return this.editDialog(r);
    if (act === "restore") return void this.w.busy("edit", `Restoring ${id}`, () => pkg.del(`${P}/${encodeURIComponent(id)}/edit`));
    if (act === "remove") return this.remove(id);
  }

  private async remove(id: string) {
    if (!(await confirmDialog(`Delete ${NOUN[this.kind]}`, `Delete <b>${esc(id)}</b>?`, "Delete", true))) return;
    await this.w.busy("edit", `Deleting ${id}`, async () => {
      try {
        await pkg.del(`/${this.kind}/${encodeURIComponent(id)}`);
      } catch (e) {
        if (e instanceof ApiError && e.code === "in_use") throw new ApiError(`${id} is still used (e.g. a step produces this target); change that first.`, e.problem);
        throw e;
      }
    });
  }

  /** All fields of one item, saved as they change (the same editors as the table). */
  private editDialog(r: Row) {
    const id = r.value.id;
    const head = this.head();
    const cells = this.cells(r.value, false);
    const dlg = modal("dlg-edit", `<h3>${this.kind === "targets" ? "Target" : "Step"} ${esc(id)}</h3>
      <div class="edit-grid">${head.map((h, i) => (cells[i] ? `<span class="edit-label">${esc(h)}</span><div>${cells[i]}</div>` : "")).join("")}</div>
      <p class="hint">Changes are saved as you make them${r.manual ? "" : "; ↺ restores the LLM's version"}.</p>
      <form method="dialog"><menu><button class="primary">Done</button></menu></form>`);
    dlg.addEventListener("change", async (ev) => {
      const el = ev.target as HTMLInputElement;
      if (!el.dataset.f) return;
      const now = this.find(id);
      if (!now) return;
      try {
        await pkg.patch(`/${this.kind}/${encodeURIComponent(id)}`, { [el.dataset.f]: this.parse(el.dataset.f, el.value.trim(), now.value) });
        this.file = await pkg.get<CurationFile<Item>>(`/${this.kind}`, { view: "raw" });
      } catch (e) {
        showError(`Changing ${id}`, e);
      }
    });
    dlg.addEventListener("close", () => this.w.render());
  }

  private find(id: string): Row | undefined {
    const { manual, runs } = this.rows();
    return [...manual, ...runs.flatMap((g) => g.rows)].find((r) => r.value.id === id);
  }

  private bind(table: HTMLElement) {
    const P = `/${this.kind}`;
    table.addEventListener("change", async (ev) => {
      const el = ev.target as HTMLInputElement;
      const tr = el.closest<HTMLElement>("tr[data-id]");
      const r = tr && this.find(tr.dataset.id!);
      if (!tr || !r) return;
      const id = r.value.id;
      const patch = el.dataset.act === "toggle" ? { disabled: !el.checked } : el.dataset.f ? { [el.dataset.f]: this.parse(el.dataset.f, el.value.trim(), r.value) } : undefined;
      if (!patch) return;
      try {
        await pkg.patch(`${P}/${encodeURIComponent(id)}`, patch);
        this.file = await pkg.get<CurationFile<Item>>(P, { view: "raw" });
        // Redraw only for changes that alter the row's layout; otherwise keep focus on the next field.
        if (el.dataset.act === "toggle" || el.dataset.f === "type") return this.w.draw();
        const now = this.find(id);
        if (now) {
          tr.querySelector(".rp-id")!.innerHTML = this.idCell(now);
          tr.querySelector(".rp-row-actions")!.innerHTML = this.rowActions(now);
        }
      } catch (e) {
        showError(`Changing ${id}`, e);
        this.w.render();
      }
    });
    table.addEventListener("click", async (ev) => {
      const b = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-act]");
      if (!b) return;
      const act = b.dataset.act;
      const runId = b.closest<HTMLElement>("tr[data-run]")?.dataset.run;
      const id = b.closest<HTMLElement>("tr[data-id]")?.dataset.id;
      if (runId) {
        const run = this.file!.llm_runs.find((x) => x.id === runId)!;
        if (act === "collapse") {
          const key = `${this.kind}:${runId}`;
          const c = this.state.collapsed;
          this.state.collapsed = c.includes(key) ? c.filter((k) => k !== key) : [...c, key];
          this.w.app.save();
          return this.w.draw();
        }
        if (act === "details") return callDetails(run, this.kind);
        if (act === "run-toggle") return void this.w.busy("edit", run.disabled ? `Using ${runId} again` : `Leaving out ${runId}`, () => pkg.patch(`${P}/runs/${runId}`, { disabled: !run.disabled }));
      }
      if (!id) return;
      if (act === "restore") return void this.w.busy("edit", `Restoring ${id}`, () => pkg.del(`${P}/${encodeURIComponent(id)}/edit`));
      if (act === "remove") return this.remove(id);
    });
  }

  // ---------- dialogs ----------

  /** The scope tree of the paper's text (its JATS extraction). */
  private async sourcePicker(el: HTMLElement, opts: { floats: boolean; presets: Preset[]; onChange?: () => void; initial?: (name: string) => { sections: string[]; items: string[] } | undefined }) {
    const sources = [this.w.app.jats()].filter(Boolean);
    el.innerHTML = `<select class="src-pick" hidden>${sources.map((s) => `<option>${esc(s)}</option>`).join("")}</select><div class="tree-box"></div>`;
    const sel = el.querySelector<HTMLSelectElement>(".src-pick")!;
    const box = el.querySelector<HTMLElement>(".tree-box")!;
    const picker = { name: "", doc: undefined as Document | undefined, tree: undefined as ScopeTree | undefined };
    const show = async () => {
      picker.name = sel.value;
      box.innerHTML = `<p class="muted">Loading…</p>`;
      try {
        picker.doc = await this.w.app.loadDoc(picker.name);
        picker.tree = new ScopeTree(box, picker.doc, { floats: opts.floats, presets: opts.presets, onChange: opts.onChange });
        const init = opts.initial?.(picker.name);
        if (init) picker.tree.scope = init;
      } catch (e) {
        box.innerHTML = `<p class="warn">${esc((e as Error).message)}</p>`;
      }
    };
    sel.addEventListener("change", show);
    if (sources.length) await show();
    else el.innerHTML = `<p class="warn-soft">The paper's text isn't available.</p>`;
    return picker;
  }

  private async addDialog(marked: string[] = []) {
    const isT = this.kind === "targets";
    const fields = isT
      ? `<div class="form-row"><label>Type <select name="type"><option>number</option><option>categorical</option><option>table</option><option>figure</option></select></label>
           <label>Expected <input name="expected" placeholder="as reported" /></label>
           <label>Comparator <select name="comparator">${COMPARATORS.map((c) => `<option>${c}</option>`).join("")}</select></label>
           <label>Decimals <input name="decimals" size="3" /></label><label>Unit <input name="unit" size="6" /></label></div>
         <label>Description <textarea name="description" rows="2" placeholder="self-contained: variable, model/test, subgroup" required></textarea></label>`
      : `<div class="form-row"><label>Kind <select name="kind"><option>analysis</option><option>transform</option></select></label>
           <label>Inputs <input name="inputs" list="rp-input-names" placeholder="input names, comma-separated" /></label>
           <label>Depends on <input name="depends_on" placeholder="A01, A02" /></label>
           <label>Produces <input name="produces" placeholder="${esc(this.targets.slice(0, 3).map((t) => t.id).join(", ") || "T001")}" /></label></div>
         <label>Summary <textarea name="summary" rows="2" placeholder="imperative, specific enough to code" required></textarea></label>`;
    const dlg = modal("dlg-wide", `<form method="dialog"><h3>Add ${NOUN[this.kind]}</h3>
      <div class="grid2"><section>${fields}
        <label>Quote <textarea name="quote" rows="4" placeholder="verbatim text from the paper (filled from the marked passages)"></textarea></label>
        <label class="inline"><input type="checkbox" name="unreferenced" /> No passage in the paper (needs a note)</label>
        <label>Note <input name="note" /></label>
        <p class="hint">Mark the passages on the right; they become the ${NOUN[this.kind]}'s references.</p></section>
      <section class="picker"></section></div>
      <menu><button value="cancel">Cancel</button><button value="ok" class="primary">Add</button></menu></form>`);
    const form = dlg.querySelector("form")!;
    const q = form.querySelector<HTMLTextAreaElement>("[name=quote]")!;
    let touched = false;
    q.addEventListener("input", () => (touched = true));
    // `picker` is filled in once the text source has loaded; the handlers below are attached before that.
    let picker: Awaited<ReturnType<CurationStep["sourcePicker"]>> | undefined;
    form.addEventListener("submit", async (e) => {
      if ((e.submitter as HTMLButtonElement | null)?.value !== "ok") return;
      e.preventDefault();
      if (!picker) return;
      const v = (n: string) => (form.elements.namedItem(n) as HTMLInputElement).value.trim();
      const refs = (picker.tree?.ids ?? []).map((id) => ({ extraction: picker!.name, id }));
      const unref = (form.elements.namedItem("unreferenced") as HTMLInputElement).checked;
      const body: Record<string, unknown> = { quote: v("quote") || refText(picker.doc, picker.tree?.ids ?? []), refs, unreferenced: unref || undefined, note: v("note") || undefined };
      if (isT) {
        const type = v("type");
        Object.assign(body, {
          type,
          description: v("description"),
          expected: type === "number" && v("expected") !== "" && !Number.isNaN(Number(v("expected"))) ? Number(v("expected")) : v("expected") || undefined,
          comparator: type === "number" && v("comparator") !== "=" ? v("comparator") : undefined,
          decimals: v("decimals") === "" ? undefined : parseInt(v("decimals"), 10),
          unit: v("unit") || undefined,
        });
      } else Object.assign(body, { kind: v("kind"), summary: v("summary"), inputs: list(v("inputs")), depends_on: list(v("depends_on")), produces: list(v("produces")) });
      try {
        await pkg.post(`/${this.kind}`, body);
        dlg.close();
        this.w.render();
      } catch (err) {
        showError(`Adding the ${NOUN[this.kind]}`, err);
      }
    });
    picker = await this.sourcePicker(dlg.querySelector(".picker")!, {
      floats: true,
      presets: [],
      initial: () => (marked.length ? { sections: [], items: marked } : undefined),
      onChange: () => {
        const t = picker?.tree;
        if (!touched && t) q.value = t.quote;
      },
    });
    if (marked.length && picker.tree && !touched) q.value = picker.tree.quote;
    // Once added, the marks have served their purpose.
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "cancel" && marked.length) this.marked.clear();
    });
  }

  private async generateDialog(marked?: { sections: string[]; items: string[] }) {
    const isT = this.kind === "targets";
    const st = this.state;
    const dlg = modal("dlg-wide", `<form method="dialog"><h3>Generate ${NOUN[this.kind]}s with an LLM</h3>
      <div class="grid2"><section class="picker"></section>
      <section>
        <p class="hint">${isT ? "The LLM reads the marked part of the text source (nothing marked: the whole document) and returns one target per reported result, with references to the sentences, tables and figures it comes from." : "The LLM turns the marked methods text (nothing marked: the whole document) into ordered analysis steps, linked to the input files of step 2 and the targets of step 3."}</p>
        <label>LLM profile <select name="profile">${this.profiles.map((p) => `<option value="${esc(p.name)}" ${(st.llmProfile ? p.name === st.llmProfile : p.default) ? "selected" : ""}>${esc(p.name)}: ${esc(p.model || "default model")}${p.effort ? `, effort ${esc(p.effort)}` : ""}</option>`).join("")}</select></label>
        <label class="model-row" hidden>Model <select name="model"><option value="">(the CLI's default)</option></select></label>
        <p class="hint">Command (configured in the backend): <code class="argv"></code></p>
        <div class="form-row"><button type="button" class="show-prompt">Show prompt</button></div>
        <label>Extra instructions <textarea name="instructions" rows="4" placeholder="optional, e.g. only the main analyses"></textarea></label>
        <p class="hint">The backend builds the prompt from its template, the marked text with ids, and the JSON Schema of the answer. The full prompt and the raw answer are kept with the run (Call details).</p>
      </section></div>
      <menu><button value="cancel">Cancel</button><button value="ok" class="primary">Generate</button></menu></form>`);
    const form = dlg.querySelector("form")!;
    const profileSel = form.querySelector<HTMLSelectElement>("[name=profile]")!;
    const modelRow = form.querySelector<HTMLElement>(".model-row")!;
    const modelSel = form.querySelector<HTMLSelectElement>("[name=model]")!;
    const showArgv = () => (form.querySelector(".argv")!.textContent = this.profiles.find((p) => p.name === profileSel.value)?.argv.join(" ") ?? "");
    profileSel.addEventListener("change", () => {
      showArgv();
      showModelRow();
    });
    /** The model row is for profiles that take a chosen model (an agent CLI with a model list, e.g. OpenCode). */
    const profileUsesModel = () => this.profiles.find((p) => p.name === profileSel.value)?.output === "opencode-json";
    const showModelRow = () => {
      modelRow.hidden = !profileUsesModel();
      if (modelRow.hidden) modelSel.value = "";
    };
    showArgv();
    // Available models, with the ones the user turned off left out.
    const enabledModels = new Set<string>();
    host
      .llmModels()
      .then((m) => {
        for (const x of m.available) if (!m.disabled.includes(x.id)) enabledModels.add(x.id);
        modelSel.innerHTML = `<option value="">(the CLI's default)</option>${[...enabledModels].map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("")}`;
        showModelRow();
      })
      .catch(() => showModelRow());
    let picker: Awaited<ReturnType<CurationStep["sourcePicker"]>> | undefined;
    /** The request, once the text source has loaded. */
    const runRequest = () => {
      const scope = picker?.tree?.scope;
      if (!picker || !scope) return undefined;
      return { extraction: picker.name, scope, instructions: (form.elements.namedItem("instructions") as HTMLTextAreaElement).value.trim(), profile: profileSel.value || undefined, model: profileUsesModel() && modelSel.value ? modelSel.value : undefined };
    };
    form.querySelector(".show-prompt")!.addEventListener("click", () => {
      const request = runRequest();
      if (request) showPrompt(`Prompt for ${NOUN[this.kind]} generation`, () => pkg.post<PromptPreview>(`/${this.kind}/runs:preview`, request));
    });
    form.addEventListener("submit", (e) => {
      if ((e.submitter as HTMLButtonElement | null)?.value !== "ok") return;
      const request = runRequest();
      if (!request) return e.preventDefault(); // still loading the text source
      st.scopes[`${this.kind}:${request.extraction}`] = request.scope;
      st.llmProfile = profileSel.value;
      this.w.app.save();
      this.w
        .busy(`llm-${this.kind}`, `Generating ${NOUN[this.kind]}s with ${profileSel.value}`, () => pkg.post<LlmRun<Item>>(`/${this.kind}/runs`, request))
        .then((run) => {
          if (run?.rejected?.length)
            choose("Some items were rejected", `<p>${run.entries.length} ${NOUN[this.kind]}s were added; ${run.rejected.length} ${run.rejected.length === 1 ? "item" : "items"} of the LLM's answer didn't validate and were left out:</p>${rejectedHtml(run)}`, [["ok", "OK", "primary"]]);
        });
    });
    picker = await this.sourcePicker(dlg.querySelector(".picker")!, { floats: isT, presets: isT ? TARGET_PRESETS : ACTION_PRESETS, initial: (name) => marked ?? st.scopes[`${this.kind}:${name}`] });
  }
}

function rejectedHtml(run: LlmRun<Item>) {
  return `<ul class="rejected">${(run.rejected ?? []).map((r) => `<li><pre>${esc(JSON.stringify(r.item, null, 1).slice(0, 600))}</pre><ul>${r.errors.map((e) => `<li class="warn-inline">${esc(e)}</li>`).join("")}</ul></li>`).join("")}</ul>`;
}

function callDetails(run: LlmRun<Item>, kind: Kind) {
  const c = run.call;
  const scope = run.request.scope;
  modal("dlg-wide", `<h3>LLM ${esc(run.id)} · ${esc(kind)}</h3>
    <div class="kv">
      <span>Text source</span><span><code>${esc(run.request.extraction ?? "")}</code> · scope: ${esc(!scope || (!scope.sections?.length && !scope.items?.length) ? "whole document" : [...(scope.sections ?? []), ...(scope.items ?? [])].join(", "))}</span>
      <span>Profile</span><span>${esc(c.profile)} · model ${esc(c.model || "default")} · effort ${esc(c.effort || "default")}</span>
      <span>Command</span><code>${esc(c.argv.join(" "))}</code>
      <span>When</span><span>${esc(when(c.started_at))} · ${seconds(c.duration_ms)} · exit ${c.exit_code ?? "?"}</span>
      <span>Prompt</span><span>${c.template} template${c.instructions ? ` + instructions: ${esc(c.instructions)}` : ""}${c.output_schema ? ` · structured output (${esc(c.output_schema)})` : ""}</span>
    </div>
    <h4>Prompt</h4><textarea class="mono big" readonly>${esc(c.prompt)}</textarea>
    <h4>Answer</h4><textarea class="mono big" readonly>${esc(c.output)}</textarea>
    ${run.rejected?.length ? `<h4>Rejected items</h4>${rejectedHtml(run)}` : ""}
    <form method="dialog"><menu><button class="primary">Close</button></menu></form>`);
}
