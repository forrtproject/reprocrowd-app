// The numbered workflow: the paper's text, then seven reproduction steps, each backed by the ReproCrowd backend. A step
// loads what it shows from the backend (load), then draws it (draw); the wizard runs them in that order, drops stale
// loads, and polls steps that watch long-running jobs. The route in the header shows where you are; Back and Next in
// the footer move between the steps.
import { isBackendDown, type Info } from "../api";
import type { PdfView } from "../pdfview";
import type { UiState } from "../state";
import type { Document, PackageSummary, Resource } from "../types";
import { $, esc, showError } from "../util";
import { CodegenStep } from "./codegen";
import { CurationStep } from "./curation";
import { InputsStep } from "./inputs";
import { ReportStep } from "./report";
import { RunStep } from "./run";
import { ScriptStep } from "./script";

export interface AppContext {
  readonly state: UiState;
  save(): void;
  info(): Info;
  summary(): PackageSummary;
  refreshSummary(): Promise<void>;
  meta(): any;
  fulltexts(): Resource[];
  extractions(): Resource[];
  refreshSources(prefer?: string): Promise<void>;
  loadDoc(name: string): Promise<Document>;
  pdf: PdfView;
  status(msg: string, kind?: "info" | "error"): void;
  openSources(): void;
  showInReader(name: string): Promise<void>;
}

export interface Step {
  title: string;
  /** Shown instead of the wizard body (the text step keeps its own, long-lived pane). */
  pane?: HTMLElement;
  /** Fetch what the step shows. */
  load(): Promise<void>;
  /** Render into the body (synchronous; uses what load fetched). */
  draw(body: HTMLElement): void;
  /** Called every pollMs while the step is shown, if defined. */
  poll?(): Promise<void>;
  pollMs?(): number;
}

/** Step 1: the PDF and the paper's text. main.ts keeps the pane up to date; the wizard only shows it. */
class TextStep implements Step {
  title = "Text";
  constructor(readonly pane: HTMLElement) {}
  async load() {}
  draw() {}
}

export class Wizard {
  readonly steps: Step[];
  readonly running = new Set<string>();
  private body: HTMLElement;
  private pollTimer?: number;
  private seq = 0;
  private drawnStep = -1;
  private started = false;

  /** Uses the page's #route (header), #rp-body, #rp-back and #rp-next (footer). */
  constructor(textPane: HTMLElement, readonly app: AppContext) {
    this.body = $("#rp-body");
    this.steps = [
      new TextStep(textPane),
      new InputsStep(this),
      new CurationStep(this, "targets"),
      new CurationStep(this, "actions"),
      new ScriptStep(this),
      new CodegenStep(this),
      new RunStep(this),
      new ReportStep(this),
    ];
    $("#rp-back").addEventListener("click", () => this.go(this.step - 1));
    $("#rp-next").addEventListener("click", () => this.go(this.step + 1));
    $("#route").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-step]");
      if (b) this.go(Number(b.dataset.step));
    });
  }

  get step() {
    return Math.max(0, Math.min(this.app.state.step, this.steps.length - 1));
  }

  /** Show the saved step (once the tab has restored its state). */
  async start() {
    this.started = true;
    await this.render();
  }

  async go(step: number) {
    if (!this.started || step < 0 || step >= this.steps.length) return;
    this.app.state.step = step;
    this.app.save();
    await this.render();
  }

  /** Load and draw the current step. */
  async render() {
    const seq = ++this.seq;
    const i = this.step;
    const s = this.steps[i];
    this.drawNav();
    this.showPane(s);
    if (this.drawnStep !== i && !s.pane) this.body.innerHTML = `<p class="muted pad">Loading…</p>`;
    try {
      await s.load();
    } catch (e) {
      if (seq !== this.seq) return;
      this.body.innerHTML = isBackendDown(e)
        ? `<div class="card"><p class="muted">Waiting for the ReproCrowd backend (see the message at the top). This step loads again when it's back.</p><button id="rp-retry">Retry</button></div>`
        : `<div class="card"><p class="warn">Couldn't load this step: ${esc((e as Error).message)}</p><button id="rp-retry">Retry</button></div>`;
      $("#rp-retry", this.body).addEventListener("click", () => this.render());
      return;
    }
    if (seq !== this.seq) return; // a newer render started meanwhile
    this.draw();
  }

  /** Redraw the current step from what it already loaded (keeps the scroll position). */
  draw() {
    const i = this.step;
    const same = this.drawnStep === i;
    const top = this.body.scrollTop;
    this.drawNav();
    this.showPane(this.steps[i]);
    this.steps[i].draw(this.body);
    if (same) this.body.scrollTop = top;
    this.drawnStep = i;
    this.managePolling();
  }

  /** The text step shows its own pane, all others the wizard body. */
  private showPane(s: Step) {
    for (const t of this.steps) if (t.pane) t.pane.hidden = t !== s;
    this.body.hidden = !!s.pane;
  }

  private drawNav() {
    const i = this.step;
    $("#route").innerHTML = this.steps.map((s, k) => `<button data-step="${k}" class="${k === i ? "on" : k < i ? "done" : ""}">${k + 1}. ${esc(s.title)}</button>`).join(`<span class="route-sep">›</span>`);
    const back = $<HTMLButtonElement>("#rp-back");
    back.disabled = i === 0;
    back.textContent = i > 0 ? `← Back: ${this.steps[i - 1].title}` : "← Back";
    const next = $<HTMLButtonElement>("#rp-next");
    next.disabled = i === this.steps.length - 1;
    next.textContent = i < this.steps.length - 1 ? `Next: ${this.steps[i + 1].title} →` : "Next →";
  }

  private managePolling() {
    clearInterval(this.pollTimer);
    this.pollTimer = undefined;
    const s = this.steps[this.step];
    if (!this.started || !s.poll) return;
    const ms = s.pollMs?.() ?? 3000;
    const i = this.step;
    this.pollTimer = window.setInterval(() => {
      if (this.step === i) s.poll!().catch(() => {});
    }, ms);
  }

  /** Re-arm polling after a step changed its pace (e.g. a job started). */
  repoll() {
    this.managePolling();
  }

  setStatus(msg: string, kind: "info" | "error" = "info") {
    this.app.status(msg, kind);
  }

  /** Run fn with `key` marked as running (buttons disable), errors in a pop-up, then reload the step. */
  async busy<T>(key: string, label: string, fn: () => Promise<T>): Promise<T | undefined> {
    this.running.add(key);
    this.setStatus(`⏳ ${label}…`);
    this.draw();
    try {
      const r = await fn();
      this.setStatus(`${label}: done`);
      return r;
    } catch (e) {
      this.setStatus(isBackendDown(e) ? `${label} failed: the ReproCrowd backend isn't available` : `${label} failed (details in the pop-up)`, "error");
      showError(label, e);
      return undefined;
    } finally {
      this.running.delete(key);
      await this.render();
    }
  }
}
