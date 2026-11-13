// The progress of creating or opening a package: which step of how many (a bar of segments, one per step, the running
// one moving), the step's name, and how long it has been running. The steps come from the main process, which does the
// work (host.textProgress); the slow ones wait on OpenAlex and Europe PMC, so they can't report a percentage of their
// own, and the running segment shows only that something is happening.
import { host, TextProgress } from "./api";
import { esc } from "./util";

const POLL = 250;
/** Work that is done sooner shows no progress at all, rather than a flash of it. */
const DELAY = 300;

export function progressHtml(p: TextProgress): string {
  const secs = Math.floor(p.elapsed / 1000);
  const segs = Array.from({ length: p.steps }, (_, i) => `<span class="${i + 1 < p.step ? "done" : i + 1 === p.step ? "now" : ""}"></span>`).join("");
  return `<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${p.steps}" aria-valuenow="${p.step - 1}" aria-valuetext="${esc(p.label)}">
    <div class="progress-bar">${segs}</div>
    <p class="progress-label"><span class="spinner"></span><span>${p.cancelling ? "Cancelling after this step: " : ""}${esc(p.label)}…</span>
      <span class="muted">step ${p.step} of ${p.steps}${secs >= 2 ? ` · ${secs} s` : ""}</span></p></div>`;
}

/** Shows the progress of `work` (a call to the main process that tracks its steps) in `el` until it is settled. */
export async function withProgress<T>(el: HTMLElement, work: Promise<T>): Promise<T> {
  let settled = false;
  let timer = 0;
  const poll = async () => {
    const p = await host.textProgress().catch(() => null);
    if (settled) return;
    if (p) {
      el.innerHTML = progressHtml(p);
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
