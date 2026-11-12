// Step 2: claims. The paper's claims as a tree (claim → sub-claims → evidence values and the method that tests them),
// reviewed by the user before anything is run. Left: the tree with a status pill per claim. Right: the selected
// claim's evidence, quotes, sub-claims, notes, the tick that says "reviewed", the last result and the assumptions the
// coding agent flagged. Four views: Review (this), Reader (the paper with a card per claim), Table, JSON (the claim's
// trace). Edits of evidence and methods untick the claims using them in the backend, so everything reloads after one.
import { host, pkg } from "../api";
import { type Action, type Claim, type ClaimResult, type ClaimsFile, type ClaimsTrace, type ClaimState, type ClaimStatus, type ClaimTrace, type Document, type LlmProfile, type LlmRun, type Target } from "../types";
import { choose, confirmDialog, esc, modal, showError, trunc } from "../util";
import { AlignedItem, AlignedView } from "./aligned";
import { CurationStep } from "./curation";
import type { Step, Wizard } from "./wizard";

type View = "review" | "reader" | "table" | "json";

interface Row {
  claim: Claim;
  /** "manual", or the id of the LLM run. */
  origin: string;
  run?: LlmRun<Claim>;
  off: boolean;
  edited: boolean;
}

const PILL: Record<ClaimState, string> = {
  review: "To review",
  ready: "Ready to run",
  running: "Running",
  reproduced: "Reproduced",
  partly: "Partly reproduced",
  not_reproduced: "Not reproduced",
  not_testable: "Not testable",
};
const pill = (s: ClaimState | undefined, stale = false) => (s ? `<span class="pill st-${s}">${PILL[s]}${stale ? " (stale)" : ""}</span>` : "");
const modeText = (c: Claim) => (!c.testable ? "Not testable" : c.sub_claims.length ? `${c.sub_claims.length} sub-claim${c.sub_claims.length > 1 ? "s" : ""}` : "Atomic");

export class ClaimsStep implements Step {
  title = "Claims";
  /** Run the claim (set by the run step's code in Task 9); the button is enabled only for claims that are ready. */
  onRun?: (claimId: string) => Promise<void>;

  private file?: ClaimsFile;
  private status = new Map<string, ClaimStatus>();
  private targets = new Map<string, Target>();
  private actions = new Map<string, Action>();
  private trace?: ClaimsTrace;
  private doc?: Document;
  private profiles: LlmProfile[] = [];
  private selected = "";
  private view: View = "review";
  private marked = new Set<string>();
  /** The old targets table, for packages made before claims. */
  private legacy?: CurationStep;
  private showLegacy = false;

  constructor(private w: Wizard) {}

  async load() {
    const [file, status, targets, actions, trace, doc] = await Promise.all([
      pkg.get<ClaimsFile>("/claims", { view: "raw" }),
      pkg.get<ClaimStatus[]>("/claims:status"),
      pkg.get<{ item: Target }[]>("/targets"),
      pkg.get<{ item: Action }[]>("/actions"),
      pkg.get<ClaimsTrace>("/claims:trace").catch(() => undefined),
      this.w.app.loadDoc(this.w.app.jats()).catch(() => undefined),
    ]);
    this.file = file;
    this.status = new Map(status.map((s) => [s.claim, s]));
    this.targets = new Map(targets.map((t) => [t.item.id, t.item]));
    this.actions = new Map(actions.map((a) => [a.item.id, a.item]));
    this.trace = trace;
    this.doc = doc;
    if (!this.profiles.length) this.profiles = await host.service<LlmProfile[]>("/v1/llm-profiles").catch(() => []);
    if (this.showLegacy) await (this.legacy ??= new CurationStep(this.w, "targets")).load();
  }

  // ---------- data ----------

  private rows(): Row[] {
    const f = this.file!;
    const rows: Row[] = f.manual.map((c) => ({ claim: c, origin: "manual", off: false, edited: false }));
    for (const run of f.llm_runs)
      for (const e of run.entries) rows.push({ claim: { ...e.base, ...(e.edit ?? {}) } as Claim, origin: run.id, run, off: !!(e.disabled || run.disabled), edited: !!e.edit });
    return rows;
  }

  private get current(): Row | undefined {
    const rows = this.rows();
    return rows.find((r) => r.claim.id === this.selected) ?? rows.find((r) => !r.off);
  }

  private get state() {
    return this.w.app.state;
  }

  /** Claims runs that are switched off: a new extraction waiting for the user's decision. */
  private pendingRuns() {
    return this.file!.llm_runs.filter((r) => r.disabled);
  }

  private edit(label: string, fn: () => Promise<unknown>) {
    return this.w.busy("edit", label, fn);
  }

  // ---------- drawing ----------

  draw(body: HTMLElement) {
    if (this.showLegacy && this.legacy) {
      this.legacy.draw(body);
      const back = document.createElement("p");
      back.className = "pad";
      back.innerHTML = `<a href="#" id="cl-back">← Back to the claims</a>`;
      body.prepend(back);
      back.querySelector("a")!.addEventListener("click", (e) => {
        e.preventDefault();
        this.showLegacy = false;
        this.w.render();
      });
      return;
    }
    const rows = this.rows();
    const live = rows.filter((r) => !r.off);
    const cur = this.current;
    if (cur) this.selected = cur.claim.id;
    const generating = this.w.running.has("llm-claims");
    const checked = live.filter((r) => r.claim.checked).length;
    const skipped = this.skippedCount();
    const seg = (["review", "reader", "table", "json"] as View[]).map((v) => `<button data-view="${v}" class="${this.view === v ? "on" : ""}">${{ review: "Review", reader: "Reader", table: "Table", json: "JSON" }[v]}</button>`).join("");
    body.innerHTML = `<div class="card ${this.view === "reader" ? "cur-reader" : ""}">
      <div class="card-head"><h3>Claims</h3><span class="muted">${live.length} in use, ${checked} reviewed</span>
        <div class="seg" id="cl-view">${seg}</div>
        <span class="spacer"></span>
        <a href="#" id="cl-targets" title="The flat table of reproduction targets, for packages made before claims">Targets table</a>
        <button id="cl-extract" class="primary" ${generating ? "disabled" : ""}>${generating ? "Extracting…" : rows.length ? "Extract again…" : "Extract claims…"}</button></div>
      <p class="hint">The paper's claims, broken into testable sub-claims with the values that report them and the method that tests them. Review each claim and tick it when you are happy; only ticked claims are run. Edits to evidence or methods untick the claims that use them.</p>
      ${this.banners()}
      <div id="cl-main"></div></div>`;
    body.querySelector("#cl-view")!.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-view]");
      if (!b || b.dataset.view === this.view) return;
      this.view = b.dataset.view as View;
      this.w.draw();
    });
    body.querySelector("#cl-extract")!.addEventListener("click", () => this.extractDialog());
    body.querySelector("#cl-targets")!.addEventListener("click", async (e) => {
      e.preventDefault();
      this.legacy ??= new CurationStep(this.w, "targets");
      this.showLegacy = true;
      await this.w.render();
    });
    this.bindBanners(body);
    const main = body.querySelector<HTMLElement>("#cl-main")!;
    if (!rows.length) {
      main.innerHTML = `<p class="muted">No claims yet. Extract them from the paper's text with an LLM.</p>`;
      return;
    }
    if (this.view === "reader") return this.drawReader(main, live);
    if (this.view === "table") return this.drawTable(main, rows);
    if (this.view === "json") return this.drawJson(main, cur);
    main.innerHTML = `<div class="cl-split"><div class="cl-tree">${this.treeHtml(rows)}<div class="cl-skipped muted small">${skipped} sentence${skipped === 1 ? "" : "s"} passed over as background or study design</div></div>
      <div class="cl-detail">${cur ? this.detailHtml(cur) : `<p class="muted">Select a claim.</p>`}</div></div>`;
    this.bindReview(main);
  }

  private skippedCount() {
    const live = new Set(this.file!.llm_runs.filter((r) => !r.disabled).map((r) => r.id));
    return Object.entries(this.file!.skipped).reduce((n, [run, s]) => n + (live.has(run) ? s.length : 0), 0);
  }

  private banners() {
    return this.pendingRuns()
      .map((run) => {
        const n = run.entries.length;
        return `<div class="cl-banner" data-run="${esc(run.id)}"><b>New extraction: ${n} claim${n === 1 ? "" : "s"}, switched off</b>
          <span class="muted small">${esc(run.id)} · ${esc(run.call.profile)}. Its claims are not used until you switch the run on. Individual claims of a run that is off cannot be picked.</span>
          <span class="spacer"></span><button data-act="use-run" class="primary">Use this run</button>
          <details><summary class="small">Show the ${n} claim${n === 1 ? "" : "s"}</summary><ul>${run.entries.map((e) => `<li><b>${esc(e.base.id)}</b> ${esc(trunc({ ...e.base, ...(e.edit ?? {}) }.text, 160))}</li>`).join("")}</ul></details></div>`;
      })
      .join("");
  }

  private bindBanners(body: HTMLElement) {
    body.querySelectorAll<HTMLElement>(".cl-banner").forEach((el) =>
      el.addEventListener("click", async (e) => {
        if (!(e.target as HTMLElement).closest("[data-act=use-run]")) return;
        const id = el.dataset.run!;
        const older = this.file!.llm_runs.filter((r) => !r.disabled && r.id !== id);
        let off: string[] = [];
        if (older.length) {
          const a = await choose("Use the new extraction", `<p>The earlier extraction (${older.map((r) => esc(r.id)).join(", ")}) is still in use, so claims would appear twice. Switch it off?</p>`, [
            ["cancel", "Cancel"],
            ["keep", "Keep both"],
            ["off", "Switch the earlier one off", "primary"],
          ]);
          if (!a || a === "cancel") return;
          if (a === "off") off = older.map((r) => r.id);
        }
        await this.edit(`Using ${id}`, async () => {
          await pkg.patch(`/claims/runs/${id}`, { disabled: false });
          for (const o of off) await pkg.patch(`/claims/runs/${o}`, { disabled: true });
        });
      }),
    );
  }

  private treeHtml(rows: Row[]) {
    return rows
      .map((r) => {
        const c = r.claim;
        const st = this.status.get(c.id);
        return `<div class="cl-item ${c.id === this.selected ? "on" : ""} ${r.off ? "off" : ""}" data-id="${esc(c.id)}">
          <div class="cl-item-head"><b>${esc(c.id)}</b><span class="badge">${esc(c.mode)}</span>${c.checked ? `<span class="cl-tick" title="Reviewed">✓</span>` : ""}<span class="spacer"></span>${r.off ? `<span class="badge">off</span>` : pill(st?.state, st?.stale)}</div>
          <div class="cl-item-text">${esc(trunc(c.text, 140))}</div>
          <div class="small muted">${modeText(c)}${r.edited ? " · edited" : ""}</div>
          ${c.sub_claims.length ? `<ul class="cl-subs">${c.sub_claims.map((s) => `<li><b>${esc(s.id)}</b> ${esc(trunc(s.text, 90))}</li>`).join("")}</ul>` : ""}</div>`;
      })
      .join("");
  }

  private evidenceHtml(ids: string[]) {
    if (!ids.length) return `<p class="muted small">No evidence values.</p>`;
    return `<table class="tbl-list cl-ev"><thead><tr><th>Value</th><th>Reported</th><th>Cmp</th><th>Unit</th></tr></thead><tbody>${ids
      .map((id) => {
        const t = this.targets.get(id);
        if (!t) return `<tr><td><b>${esc(id)}</b></td><td colspan="3" class="warn-inline">missing target</td></tr>`;
        const editable = t.type === "number" || t.type === "categorical";
        const val = editable ? `<input data-target="${esc(id)}" data-f="expected" value="${esc(t.expected ?? "")}" />` : `<span class="muted">${t.type === "table" ? "a table" : "a figure"}</span>`;
        return `<tr><td><b>${esc(id)}</b> ${esc(trunc(t.description, 100))}</td><td>${val}</td><td>${esc(t.comparator ?? "=")}</td><td>${esc(t.unit ?? "")}</td></tr>`;
      })
      .join("")}</tbody></table>`;
  }

  private methodHtml(id: string | null | undefined) {
    if (!id) return `<div class="muted small">No method yet. It is written when the claim is run.</div>`;
    const a = this.actions.get(id);
    if (!a) return `<div class="warn-inline small">${esc(id)}: missing step</div>`;
    return `<label class="small muted">Method ${esc(a.id)}<textarea rows="2" data-action="${esc(a.id)}" data-f="summary">${esc(a.summary)}</textarea></label>`;
  }

  private detailHtml(r: Row) {
    const c = r.claim;
    const st = this.status.get(c.id);
    const tr = this.trace?.claims.find((x) => x.claim_id === c.id);
    const quote =
      c.mode === "paraphrase"
        ? `<label class="small muted">Claim (paraphrase)<textarea rows="3" data-claim="${esc(c.id)}" data-f="text">${esc(c.text)}</textarea></label>
           <label class="small muted">Original quote<textarea rows="3" data-claim="${esc(c.id)}" data-f="original_quote">${esc(c.original_quote)}</textarea></label>`
        : `<label class="small muted">Claim (quoted from the paper)<textarea rows="3" data-claim="${esc(c.id)}" data-f="text">${esc(c.text)}</textarea></label>`;
    const subs = c.sub_claims.length
      ? c.sub_claims.map((s) => `<div class="cl-sub"><div><b>${esc(s.id)}</b> ${esc(s.text)}</div>${this.methodHtml(s.method)}${this.evidenceHtml(s.evidence)}</div>`).join("")
      : c.testable
        ? `${this.methodHtml(c.method)}${this.evidenceHtml(c.evidence)}`
        : `<p class="muted small">This claim is not testable by computation, so it is not run.</p>`;
    const ready = st?.state === "ready";
    const result = st?.result;
    return `<div class="cl-detail-head"><h4>${esc(c.id)}</h4><span class="badge">${esc(c.mode)}</span>${pill(st?.state, st?.stale)}<span class="spacer"></span>
        <button data-act="switch" data-claim="${esc(c.id)}" ${r.run?.disabled ? "disabled" : ""} title="${r.off ? "Use this claim again" : "Leave this claim out"}">${r.off ? "Switch on" : "Switch off"}</button></div>
      <section>${quote}</section>
      <section><h5>${c.sub_claims.length ? "Sub-claims, methods and evidence" : "Method and evidence"}</h5>${subs}</section>
      <section><label class="small muted">Notes<textarea rows="2" data-claim="${esc(c.id)}" data-f="notes" placeholder="Your notes; they do not untick the claim">${esc(c.notes)}</textarea></label></section>
      <section class="cl-actions"><label><input type="checkbox" data-claim="${esc(c.id)}" data-f="checked" ${c.checked ? "checked" : ""} ${c.testable ? "" : "disabled"} /> Reviewed (ticked claims are run)</label>
        <span class="spacer"></span><button data-act="run" data-claim="${esc(c.id)}" class="primary" ${ready && !this.w.running.has(`claim-${c.id}`) ? "" : "disabled"} title="${ready ? "Generate and run the code for this claim" : "Tick the claim first"}">Run this claim</button></section>
      ${result ? `<section><h5>Last result ${st!.stale ? `<span class="pill st-stale">stale: the claim changed since</span>` : ""}</h5>${this.resultHtml(result)}</section>` : ""}
      ${tr?.assumptions.length ? `<section><h5>Assumptions made by the coding agent</h5><ul class="cl-assume">${tr.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></section>` : ""}`;
  }

  private resultHtml(res: ClaimResult) {
    const rowsOf = (label: string, evid: ClaimResult["evidence"]) => evid.map((e) => `<tr><td>${esc(label)}</td><td>${esc(e.target)}</td><td>${esc(e.expected_text)}</td><td>${esc(e.reproduced_text)}</td><td><span class="badge">${esc(e.status)}</span></td></tr>`).join("");
    const body = [...res.by_sub_claim.map((s) => rowsOf(s.id, s.evidence)), rowsOf("", res.evidence)].join("");
    return `<p>${pill(res.verdict)} ${res.matched} of ${res.tested} computed values match${res.by_sub_claim.length ? `; by sub-claim: ${res.by_sub_claim.map((s) => `${esc(s.id)} ${s.matched}/${s.tested}`).join(", ")}` : ""}</p>
      <table class="tbl-list"><thead><tr><th>Sub-claim</th><th>Value</th><th>Paper</th><th>Reproduced</th><th>Status</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  // ---------- review interactions ----------

  private bindReview(main: HTMLElement) {
    main.querySelector(".cl-tree")!.addEventListener("click", (e) => {
      const it = (e.target as HTMLElement).closest<HTMLElement>(".cl-item");
      if (!it) return;
      this.selected = it.dataset.id!;
      this.w.draw();
    });
    const detail = main.querySelector<HTMLElement>(".cl-detail")!;
    detail.addEventListener("change", (e) => {
      const el = e.target as HTMLInputElement;
      const f = el.dataset.f;
      if (!f) return;
      if (el.dataset.claim) {
        const value = f === "checked" ? el.checked : el.value;
        return void this.edit(`Changing ${el.dataset.claim}`, () => pkg.patch(`/claims/${encodeURIComponent(el.dataset.claim!)}`, { [f]: value }));
      }
      if (el.dataset.action) return void this.edit(`Changing ${el.dataset.action}`, () => pkg.patch(`/actions/${encodeURIComponent(el.dataset.action!)}`, { [f]: el.value }));
      if (el.dataset.target) {
        const t = this.targets.get(el.dataset.target);
        const v = el.value.trim();
        const value = t?.type === "number" ? Number(v) : v;
        if (typeof value === "number" && Number.isNaN(value)) return showError(`Changing ${el.dataset.target}`, new Error("The value of a number must be a number."));
        return void this.edit(`Changing ${el.dataset.target}`, () => pkg.patch(`/targets/${encodeURIComponent(el.dataset.target!)}`, { [f]: value }));
      }
    });
    detail.addEventListener("click", async (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-act]");
      if (!b) return;
      const id = b.dataset.claim!;
      if (b.dataset.act === "run") return void this.edit(`Running ${id}`, async () => void (await this.onRun?.(id)));
      if (b.dataset.act === "switch") {
        const r = this.rows().find((x) => x.claim.id === id);
        if (!r) return;
        if (!r.off && !(await confirmDialog("Switch off", `Leave out claim <b>${esc(id)}</b>? It is kept and can be switched on again.`, "Switch off"))) return;
        await this.edit(`${r.off ? "Using" : "Leaving out"} ${id}`, () => pkg.patch(`/claims/${encodeURIComponent(id)}`, { disabled: !r.off }));
      }
    });
  }

  // ---------- other views ----------

  private drawReader(main: HTMLElement, live: Row[]) {
    const jats = this.w.app.jats();
    const items: AlignedItem[] = live.map((r) => {
      const c = r.claim;
      const st = this.status.get(c.id);
      const refs = [...c.refs, ...c.sub_claims.flatMap((s) => s.refs)].filter((x) => x.extraction === jats).map((x) => x.id);
      return {
        id: c.id,
        anchors: [...new Set(refs)],
        cls: r.off ? "off" : "",
        html: `<div class="al-card-head"><b>${esc(c.id)}</b><span class="badge">${esc(modeText(c))}</span>${c.checked ? `<span class="cl-tick">✓</span>` : ""}<span class="spacer"></span>${pill(st?.state, st?.stale)}
          <button data-act="open" title="Review this claim">Review</button></div><div class="al-desc">${esc(trunc(c.text, 220))}</div>`,
      };
    });
    const host = document.createElement("div");
    host.className = "al-host";
    main.append(host);
    new AlignedView(host, {
      doc: this.doc,
      meta: this.w.app.meta(),
      items,
      marked: this.marked,
      unlinkedLabel: "Not linked to a passage of the text",
      onMark: () => {},
      barHtml: (n) => `<span><b>${n}</b> marked</span><span class="spacer"></span><button data-act="clear">Clear marks</button>`,
      onBar: () => {
        this.marked.clear();
        this.w.draw();
      },
      onCard: (id, act) => {
        if (act !== "open") return;
        this.selected = id;
        this.view = "review";
        this.w.draw();
      },
    });
  }

  private drawTable(main: HTMLElement, rows: Row[]) {
    main.innerHTML = `<table class="tbl-list"><thead><tr><th>ID</th><th>Claim</th><th>Mode</th><th>Breakdown</th><th>Origin</th><th>Reviewed</th><th>Status</th></tr></thead><tbody>${rows
      .map((r) => {
        const c = r.claim;
        const st = this.status.get(c.id);
        return `<tr class="${r.off ? "rp-disabled" : ""}"><td><b>${esc(c.id)}</b></td><td>${esc(c.text)}${c.mode === "paraphrase" ? `<div class="quote">“${esc(trunc(c.original_quote, 240))}”</div>` : ""}</td><td>${esc(c.mode)}</td><td>${esc(modeText(c))}</td><td>${r.origin === "manual" ? "by hand" : esc(r.origin)}${r.off ? " (off)" : ""}</td><td>${c.checked ? "✓" : ""}</td><td>${r.off ? "" : pill(st?.state, st?.stale)}</td></tr>`;
      })
      .join("")}</tbody></table>`;
  }

  private drawJson(main: HTMLElement, cur: Row | undefined) {
    const tr: ClaimTrace | undefined = this.trace?.claims.find((x) => x.claim_id === cur?.claim.id);
    const text = tr ? JSON.stringify(tr, null, 2) : "No trace for this claim (switched-off claims have none).";
    main.innerHTML = `<div class="card-head"><b>${esc(cur?.claim.id ?? "")}</b><span class="muted small">the claim as the claims trace records it</span><span class="spacer"></span><button id="cl-copy" ${tr ? "" : "disabled"}>Copy</button></div>
      <p class="hint">Pick another claim in the Review view to see its trace.</p><pre class="cl-json">${esc(text)}</pre>`;
    main.querySelector("#cl-copy")!.addEventListener("click", async (e) => {
      const b = e.currentTarget as HTMLButtonElement;
      await navigator.clipboard.writeText(text).catch(() => {});
      b.textContent = "Copied";
      setTimeout(() => (b.textContent = "Copy"), 1500);
    });
  }

  // ---------- extraction ----------

  private extractDialog() {
    const st = this.state;
    const again = this.rows().length > 0;
    const dlg = modal("dlg-wide", `<form method="dialog"><h3>Extract claims with an LLM</h3>
      <p class="hint">The LLM reads the paper's text and returns its claims, each broken into testable sub-claims with the values that report them and the method that tests them.${again ? " The new extraction is added switched off; you choose whether to use it instead of the earlier one." : ""}</p>
      <label>LLM profile <select name="profile">${this.profiles.map((p) => `<option value="${esc(p.name)}" ${(st.llmProfile ? p.name === st.llmProfile : p.default) ? "selected" : ""}>${esc(p.name)}: ${esc(p.model || "default model")}${p.effort ? `, effort ${esc(p.effort)}` : ""}</option>`).join("")}</select></label>
      <p class="hint">Command (configured in the backend): <code class="argv"></code></p>
      <label>Extra instructions <textarea name="instructions" rows="4" placeholder="optional, e.g. only the main findings"></textarea></label>
      <menu><button value="cancel">Cancel</button><button value="ok" class="primary">Extract</button></menu></form>`);
    const form = dlg.querySelector("form")!;
    const sel = form.querySelector<HTMLSelectElement>("[name=profile]")!;
    const argv = () => (form.querySelector(".argv")!.textContent = this.profiles.find((p) => p.name === sel.value)?.argv.join(" ") ?? "");
    sel.addEventListener("change", argv);
    argv();
    form.addEventListener("submit", (e) => {
      if ((e.submitter as HTMLButtonElement | null)?.value !== "ok") return;
      st.llmProfile = sel.value;
      this.w.app.save();
      const request = { extraction: this.w.app.jats(), profile: sel.value || undefined, instructions: (form.elements.namedItem("instructions") as HTMLTextAreaElement).value.trim() };
      this.w.busy("llm-claims", `Extracting claims with ${sel.value}`, () => pkg.post<LlmRun<Claim>>("/claims/runs", request)).then((run) => {
        if (run?.rejected?.length) choose("Some claims were rejected", `<p>${run.entries.length} claims were added; ${run.rejected.length} of the LLM's items didn't validate and were left out.</p>`, [["ok", "OK", "primary"]]);
      });
    });
  }
}
