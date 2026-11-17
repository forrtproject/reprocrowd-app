// Small DOM and formatting helpers shared by the views and dialogs.
import { ApiError, isBackendDown, type PilotRecord } from "./api";
import type { PromptPreview } from "./types";

export const $ = <T extends HTMLElement = HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector<T>(sel)!;
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
export const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
export const sizeText = (n: number) => (n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`);
export const when = (iso: string | number | null | undefined) => (iso ? new Date(iso).toLocaleString() : "");
export const seconds = (ms?: number | null) => (ms == null ? "" : ms < 10_000 ? `${(ms / 1000).toFixed(1)} s` : ms < 3_600_000 ? `${Math.round(ms / 1000)} s` : `${(ms / 3_600_000).toFixed(1)} h`);
export const nearEnd = (el: Element) => el.scrollHeight - el.scrollTop - el.clientHeight < 8;
export const list = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);

export function modal(cls: string, html: string): HTMLDialogElement {
  const dlg = document.createElement("dialog");
  dlg.className = `dlg ${cls}`;
  dlg.innerHTML = html;
  document.body.append(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.showModal();
  return dlg;
}

/** OK/Cancel; or with `buttons`, any set of choices (value → label); resolves with the value, "" when dismissed. */
export function choose(title: string, messageHtml: string, buttons: [string, string, string?][]): Promise<string> {
  return new Promise((resolve) => {
    const dlg = modal("dlg-confirm", `<h3>${esc(title)}</h3><div>${messageHtml}</div>
      <menu>${buttons.map(([v, label, cls]) => `<button value="${esc(v)}" class="${cls ?? ""}">${esc(label)}</button>`).join("")}</menu>`);
    dlg.querySelectorAll<HTMLButtonElement>("menu button").forEach((b) => b.addEventListener("click", () => dlg.close(b.value)));
    dlg.addEventListener("close", () => resolve(dlg.returnValue));
  });
}

export const confirmDialog = async (title: string, messageHtml: string, ok = "OK", danger = false) =>
  (await choose(title, messageHtml, [["cancel", "Cancel"], ["ok", ok, danger ? "danger" : "primary"]])) === "ok";

/** An error in a pop-up: the backend's problem code and extra details when there are any. When the backend itself
 * can't be reached, the tab's banner already says so, and no pop-up is added. */
export function showError(label: string, e: unknown) {
  if (isBackendDown(e)) return;
  const p = e instanceof ApiError ? e.problem : undefined;
  const html = e instanceof ApiError ? e.html : undefined;
  const msg = (e as Error)?.message ?? String(e);
  const extra = p ? Object.entries(p).filter(([k]) => !["type", "title", "status", "detail", "code", "instance", "request_id"].includes(k)) : [];
  // Lists of names (e.g. used_by) and validation messages are for the user; dumps (a response body, stderr, argv, …)
  // help with a bug report, like the problem code, HTTP status and request id: those are folded away.
  const shown: string[] = [];
  const folded: string[] = [];
  for (const [k, v] of extra) {
    const list = (items: string[]) => `<h4>${esc(k)}</h4><ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    if (Array.isArray(v) && v.every((x) => typeof x === "string")) shown.push(list(v));
    else if (k === "errors" && Array.isArray(v) && v.every((x) => typeof x?.msg === "string"))
      shown.push(list(v.map((x) => `${Array.isArray(x.loc) ? `${x.loc.filter((l: unknown) => l !== "body").join(".")}: ` : ""}${x.msg}`)));
    else folded.push(`<h4>${esc(k)}</h4><pre>${esc(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</pre>`);
  }
  // With an html explanation, it says what the extras would have: they are left out.
  const tech = p
    ? `<details class="err-tech"><summary>Technical details</summary>
        <p class="muted small">${esc(p.title ?? "")} · <code>${esc(p.code)}</code>${p.status ? ` · HTTP ${p.status}` : ""}${p.request_id ? ` · request ${esc(p.request_id)}` : ""}</p>
        ${html ? "" : folded.join("")}</details>`
    : "";
  // Title and Close stay in place; only the body scrolls.
  modal("dlg-error", `<h3>${esc(label)} failed</h3>
    <div class="err-body">${html ?? `<p>${esc(msg)}</p>${shown.join("")}`}${tech}</div>
    <form method="dialog"><menu><button class="primary">Close</button></menu></form>`);
}

/** Show the prompt a call would send. The backend renders it, from the same request the call itself would make. */
export async function showPrompt(title: string, preview: () => Promise<PromptPreview>) {
  let p: PromptPreview;
  try {
    p = await preview();
  } catch (e) {
    return showError("Showing the prompt", e);
  }
  modal("dlg-wide", `<h3>${esc(title)}</h3>
    <p class="hint">This is what would be sent now. Nothing has been run.</p>
    <div class="kv">
      <span>Profile</span><span>${esc(p.profile)} · model ${esc(p.model || "default")} · effort ${esc(p.effort || "default")}${p.output_schema ? ` · structured output (${esc(p.output_schema)})` : ""}</span>
      <span>Command</span><code>${esc(p.argv.join(" "))}</code>
    </div>
    <h4>Prompt</h4><textarea class="mono big" readonly>${esc(p.prompt)}</textarea>
    <form method="dialog"><menu><button class="primary">Close</button></menu></form>`);
}

export function blobUrl(data: ArrayBuffer, type: string) {
  return URL.createObjectURL(new Blob([data], { type }));
}

export function dataUrl(data: ArrayBuffer, type: string) {
  return new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(new Blob([data], { type }));
  });
}

/** One pilot issue in a list: number, who opened it, how far it got. Shown in New from DOI and in step 8. */
export function pilotLabel(p: PilotRecord) {
  const state = p.latest_version ? `v${p.latest_version}${p.latest_date ? `, ${p.latest_date}` : ""}` : "no run copied yet";
  return `#${p.number} by ${esc(p.author ?? "unknown")} (${esc(state)}${p.state === "closed" ? ", closed" : ""}${p.synthetic ? ", synthetic" : ""})`;
}
