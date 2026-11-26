// The progress of creating or opening a package: which step of how many (a bar of segments, one per step), the step's
// name, and how long it has been running. The steps come from the main process, which does the work
// (host.textProgress); the slow ones wait on OpenAlex and Europe PMC, so they can't report a percentage of their own.
// The running segment fills from its elapsed time instead, towards but never reaching its end, and the bar as a whole
// never moves backward: not within a step, not between steps, and not when an unplanned step is added.
import { host, TextProgress } from "./api";
import { esc } from "./util";

const POLL = 250;
/** Work that is done sooner shows no progress at all, rather than a flash of it. */
const DELAY = 300;
/** The running step's fill starts at FROM and approaches FROM + SPAN, about two thirds of the way there after TAU ms. */
const FROM = 0.08, SPAN = 0.84, TAU = 6000;

/** How full the running step's segment is after `elapsed` ms: from 0.08, rising ever more slowly towards 0.92. */
export function stepFill(elapsed: number): number {
  return FROM + SPAN * (1 - Math.exp(-Math.max(0, elapsed || 0) / TAU));
}

/** What the bar shows: step `step` of `steps`, with the running segment `fill` (0–1) full; `overall` is the whole bar's. */
export interface ShownProgress { step: number; steps: number; fill: number; overall: number }

/** The next state of the bar for the snapshot `p`, never behind `prev` (the state shown last, if any). */
export function advanceProgress(prev: ShownProgress | null, p: TextProgress): ShownProgress {
  const steps = Math.max(1, p.steps), step = Math.min(Math.max(1, p.step), steps);
  let fill = stepFill(p.elapsed);
  if (prev) {
    if (step === prev.step && steps === prev.steps) fill = Math.max(fill, prev.fill);
    // The whole bar stays where it was, even if a step was put in (which shortens each segment).
    fill = Math.max(fill, prev.overall * steps - (step - 1));
  }
  fill = Math.min(1, fill);
  const overall = Math.max(prev?.overall ?? 0, (step - 1 + fill) / steps);
  return { step, steps, fill, overall };
}

const pct = (fill: number) => `${(fill * 100).toFixed(1)}%`;
const segClass = (i: number, step: number) => (i + 1 < step ? "done" : i + 1 === step ? "now" : "");
const labelText = (p: TextProgress) => `${p.cancelling ? "Cancelling after this step: " : ""}${p.label}…`;
const metaText = (p: TextProgress) => {
  const secs = Math.floor(p.elapsed / 1000);
  return `step ${p.step} of ${p.steps}${secs >= 2 ? ` · ${secs} s` : ""}`;
};
const segsHtml = (s: ShownProgress) =>
  Array.from({ length: s.steps }, (_, i) => {
    const c = segClass(i, s.step);
    return `<span class="${c}"${c === "now" ? ` style="--pct: ${pct(s.fill)}"` : ""}></span>`;
  }).join("");

export function progressHtml(p: TextProgress, shown: ShownProgress = advanceProgress(null, p)): string {
  return `<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${shown.steps}" aria-valuenow="${(shown.overall * shown.steps).toFixed(2)}" aria-valuetext="${esc(p.label)}">
    <div class="progress-bar">${segsHtml(shown)}</div>
    <p class="progress-label"><span class="spinner"></span><span class="progress-name">${esc(labelText(p))}</span>
      <span class="muted">${esc(metaText(p))}</span></p></div>`;
}

/** Updates the bar mounted by progressHtml in place, so the running segment's fill and the spinner keep moving smoothly. */
function update(el: HTMLElement, p: TextProgress, shown: ShownProgress) {
  const root = el.querySelector<HTMLElement>(".progress")!;
  root.setAttribute("aria-valuemax", String(shown.steps));
  root.setAttribute("aria-valuenow", (shown.overall * shown.steps).toFixed(2));
  root.setAttribute("aria-valuetext", p.label);
  const bar = root.querySelector<HTMLElement>(".progress-bar")!;
  if (bar.children.length !== shown.steps) bar.innerHTML = segsHtml(shown);
  Array.from(bar.children as HTMLCollectionOf<HTMLElement>).forEach((seg, i) => {
    const c = segClass(i, shown.step);
    if (seg.className !== c) seg.className = c;
    if (c === "now") seg.style.setProperty("--pct", pct(shown.fill));
    else seg.style.removeProperty("--pct");
  });
  const name = root.querySelector<HTMLElement>(".progress-name")!, meta = root.querySelector<HTMLElement>(".progress-label .muted")!;
  if (name.textContent !== labelText(p)) name.textContent = labelText(p);
  if (meta.textContent !== metaText(p)) meta.textContent = metaText(p);
}

/** Shows the progress of `work` (a call to the main process that tracks its steps) in `el` until it is settled. */
export async function withProgress<T>(el: HTMLElement, work: Promise<T>): Promise<T> {
  let settled = false;
  let timer = 0;
  let shown: ShownProgress | null = null;
  const poll = async () => {
    const p = await host.textProgress().catch(() => null);
    if (settled) return;
    if (p) {
      const mounted = shown !== null && el.querySelector(".progress") !== null;
      shown = advanceProgress(shown, p);
      if (mounted) update(el, p, shown);
      else el.innerHTML = progressHtml(p, shown);
      el.hidden = false;
    }
    timer = window.setTimeout(poll, POLL);
  };
  timer = window.setTimeout(poll, DELAY);
  try {
    return await work;
  } finally {
    settled = true;
    clearTimeout(timer);
    el.hidden = true;
    el.innerHTML = "";
  }
}
