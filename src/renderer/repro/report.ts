// Step 8: the report, the package archive, and the deposit on Zenodo. A deposit is uploaded as a draft (with a
// reserved DOI) that you can look at on Zenodo; publishing registers the DOI for good and is a separate step here or
// on Zenodo. What goes into the upload can be chosen: the paper's own material only when its license allows.
import { ApiError, DepositPreview, host, pkg, ZenodoAccount, ZenodoRecord } from "../api";
import { settingsDialog } from "../settings";
import type { ArchiveInfo, Job, PackageSummary, ReportResult, ValidationReport, ZenodoDeposit } from "../types";
import { confirmDialog, dataUrl, esc, modal, showError, sizeText, when } from "../util";
import { STATUS_LABEL } from "./run";
import type { Step, Wizard } from "./wizard";

/**
 * The report links its figures relative to the package folder, but the preview is a sandboxed srcdoc frame, where
 * they would resolve against the UI's own address. So the images go into the previewed HTML; report.html stays as is.
 */
async function inlineImages(html: string): Promise<string> {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const local = [...doc.images].filter((img) => !/^[a-z][a-z0-9+.-]*:/i.test(img.getAttribute("src") ?? ""));
  await Promise.all(
    local.map(async (img) => {
      const path = img.getAttribute("src")!.split("/").map(encodeURIComponent).join("/");
      // An image that can't be read shows as its alt text, as it would in report.html itself.
      img.src = await pkg.bytes(`/files/${path}`).then((b) => dataUrl(b, "image/png")).catch(() => "");
    }),
  );
  return `<!doctype html>${doc.documentElement.outerHTML}`;
}

/**
 * The full texts that came from the user's disk (a local file, an upload, or a conversion of one), as the backend
 * judges them for `local_paper_text`: such a package may be unpublished research and isn't deposited on Zenodo.
 */
function localFulltexts(resources: any[]): string[] {
  const fts = new Map<string, any>(resources.filter((r) => r.reprocrowd?.kind === "fulltext").map((r) => [r.name, r]));
  const local = (r: any, seen: Set<string>): boolean => {
    const o = r.reprocrowd.origin;
    if (o?.type === "local" || o?.type === "upload") return true;
    if (o?.type !== "conversion") return false;
    const src = fts.get(o.source);
    return !src || seen.has(src.name) || local(src, new Set([...seen, r.name]));
  };
  return [...fts.values()].filter((r) => local(r, new Set())).map((r) => r.name);
}

const DEPOSIT_STATUS: Record<ZenodoDeposit["status"], string> = { draft: "Draft", published: "Published", discarded: "Discarded", gone: "Deleted on Zenodo" };
const GROUP_LABEL: Record<string, string> = { paper: "The paper's full texts, extractions and original figures", inputs: "The input files (the original data and code)" };

export class ReportStep implements Step {
  title = "Report & deposit";
  private report?: string;
  private reportResult?: ReportResult;
  private manifest?: { resources?: any[]; reprocrowd: { archive?: ArchiveInfo | null; identifiers?: Record<string, string> } };
  private validation?: ValidationReport;
  private deposits: ZenodoDeposit[] = [];
  private lastRun?: Job;
  private account?: ZenodoAccount;
  private preview?: DepositPreview | Error;
  private records?: ZenodoRecord[] | Error;
  /** Groups left out, as chosen here (undefined: the default from the preview). */
  private exclude?: Set<string>;
  private note = "";

  constructor(private w: Wizard) {}

  async load() {
    const [manifest, deposits, jobs, validation, report] = await Promise.all([
      pkg.get<any>("/manifest"),
      pkg.get<ZenodoDeposit[]>("/deposits"),
      pkg.get<Job[]>("/jobs"),
      pkg.get<ValidationReport>("/validation").catch(() => undefined),
      pkg.text("/report").catch(() => undefined),
    ]);
    this.manifest = manifest;
    this.deposits = deposits;
    this.lastRun = jobs.filter((j) => j.type === "run").pop();
    this.validation = validation;
    this.report = report && (await inlineImages(report));
    const doi = this.doi();
    // Account, preview and existing records are shown, but never block the step.
    [this.account, this.preview, this.records] = await Promise.all([
      host.zenodoAccount().catch(() => undefined),
      this.manifest?.reprocrowd.archive ? host.depositPreview({ note: "" }).catch((e) => e as Error) : Promise.resolve(undefined),
      doi ? host.zenodoRecords(doi).catch((e) => e as Error) : Promise.resolve([]),
    ]);
    if (!this.exclude && this.preview && !(this.preview instanceof Error)) this.exclude = new Set(this.preview.exclude);
  }

  private doi(): string {
    const summary: PackageSummary = this.w.app.summary();
    return this.manifest?.reprocrowd.identifiers?.doi ?? (summary.identifier?.scheme === "doi" ? summary.identifier.value : "");
  }

  draw(body: HTMLElement) {
    const st = this.w.app.state;
    const archive = this.manifest?.reprocrowd.archive;
    const doi = this.doi();
    const a = this.account;
    const errs = this.validation?.errors ?? [];
    const server = a?.sandbox ? "the Zenodo sandbox" : "Zenodo";
    const local = localFulltexts(this.manifest?.resources ?? []);
    const checks: [boolean, string, string?][] = [
      local.length
        ? [false, "The paper's text comes from a file on your computer. Only reproductions of published papers, started from their DOI, can be deposited on Zenodo; the report and the ZIP are yours to keep."]
        : [!!doi, doi ? `DOI ${doi}` : "The paper has a DOI (the record links to it, and OpenAlex provides its metadata)"],
      [this.lastRun?.status === "succeeded", this.lastRun ? `Last run: ${this.lastRun.status}${this.lastRun.status === "succeeded" ? "" : " (step 7)"}` : "The master script ran successfully (step 7)"],
      [!!this.report, "Report created"],
      [!!archive, archive ? `ZIP created ${when(archive.created_at)}` : "ZIP created"],
      [!!a?.creator.name, a?.creator.name ? `Signed as ${a.creator.name}${a.creator.orcid ? ` (ORCID ${a.creator.orcid})` : ""}` : "Your name for the record (Settings)", "you"],
      [!!a?.hasToken, a?.hasToken ? `Zenodo token for ${a.sandbox ? "the sandbox" : "zenodo.org"}` : `A Zenodo token for ${a?.sandbox ? "the sandbox" : "zenodo.org"} (Settings)`, "zenodo"],
    ];
    const pv = this.preview instanceof Error ? undefined : this.preview;
    const stale = this.preview instanceof ApiError && this.preview.code === "archive_stale";
    // The same archive with the same files left out is already on Zenodo (the backend would refuse it).
    const exNow = [...(this.exclude ?? [])].sort().join();
    const already = archive && this.deposits.find((d) => (d.status === "draft" || d.status === "published") && d.archive_sha256 === archive.sha256 && [...d.exclude].sort().join() === exNow);
    const ready = checks.every(([ok]) => ok) && !!pv && !already && !this.w.running.has("deposit");
    const c = st.compare;
    const others = Array.isArray(this.records) ? this.records.filter((r) => !this.deposits.some((d) => d.deposition_id === Number(r.id))) : [];
    const groups = pv ? Object.entries(pv.contents.groups).filter(([, g]) => g.resources.length) : [];
    const lic = pv?.contents.paper_license;
    body.innerHTML = `
    <div class="cols cols-report">
      <div class="col-flex">
        <section class="card">
          <h3>Report</h3>
          <p class="hint">The comparison (rounding ${c.rounding === "reported" ? "as reported" : `${c.rounding} decimals`}, tolerance ${c.threshold} %, as set in step 7), the provenance of every target, the sources, LLM calls, inputs, plan and every file with its hash.</p>
          <div class="actions"><button id="rp-report" class="primary" ${this.w.running.has("report") ? "disabled" : ""}>${this.report ? "Regenerate report" : "Create report"}</button>${this.report ? `<button id="rp-report-show">Show file</button>` : ""}</div>
          ${this.reportResult ? `<p class="ok">✓ ${esc(this.reportResult.reproduced)} reproduced · ${Object.entries(this.reportResult.summary).map(([k, v]) => `${STATUS_LABEL[k as keyof typeof STATUS_LABEL] ?? k}: ${v}`).join(", ")}</p>` : ""}
        </section>
        <section class="card">
          <h3>Package archive</h3>
          ${errs.length ? `<details class="warn" open><summary>The package doesn't validate (${errs.length} problem${errs.length > 1 ? "s" : ""}); the ZIP needs a valid package</summary><ul>${errs.slice(0, 40).map((e) => `<li><code>${esc(e.stage)}</code> ${esc(e.resource ?? "")} ${esc(e.message)}</li>`).join("")}</ul></details>` : this.validation ? `<p class="ok">✓ The package validates (manifest, file hashes, schemas, references).</p>` : ""}
          <div class="actions"><button id="rp-zip" ${this.report && !this.w.running.has("zip") ? "" : "disabled"}>${archive ? "Recreate ZIP" : "Create ZIP"}</button>${archive ? `<button id="rp-zip-show">Show file</button>` : ""}</div>
          ${archive ? `<p class="small"><code>${esc(archive.path)}</code> · ${sizeText(archive.bytes)} · ${archive.files} files · SHA-256 <code title="${esc(archive.sha256)}">${esc(archive.sha256.slice(0, 12))}…</code></p>` : `<p class="hint">A Frictionless zipped data package: <code>datapackage.json</code> and every file it lists.</p>`}
          ${stale ? `<p class="warn-inline small">The package changed since the ZIP was made; recreate it before depositing.</p>` : ""}
        </section>
        <section class="card submit-card">
          <h3>Deposit on Zenodo ${a?.sandbox ? `<span class="badge sandbox" title="sandbox.zenodo.org: for testing, DOIs aren't real">Sandbox</span>` : `<span class="badge live">zenodo.org</span>`}</h3>
          <ul class="checklist">${checks.map(([ok, label, fix]) => `<li class="${ok ? "ok" : "no"}">${ok ? "✓" : "✗"} ${esc(label)}${!ok && fix ? ` <a href="#" data-settings="${fix}">Settings…</a>` : ""}</li>`).join("")}</ul>
          ${pv ? `
          <h4>What is uploaded</h4>
          <p class="hint">A Zenodo record is public and permanent. The ZIP always holds the targets, plan, script, outputs, comparison and report.</p>
          <div class="groups">${groups.map(([g, info]) => `<label class="inline"><input type="checkbox" data-group="${esc(g)}" ${this.exclude?.has(g) ? "" : "checked"} /> ${esc(GROUP_LABEL[g] ?? info.description)} <span class="muted small">(${info.resources.length} file${info.resources.length === 1 ? "" : "s"}, ${sizeText(info.bytes)})</span></label>`).join("") || `<span class="muted small">Nothing optional in this package.</span>`}</div>
          ${pv.contents.groups.paper?.resources.length ? `<p class="small ${lic && !pv.contents.default_exclude.includes("paper") ? "ok" : "warn-soft"}">${lic ? `OpenAlex reports the license <b>${esc(lic)}</b> for the paper.` : "OpenAlex reports no license for the paper."} ${pv.contents.default_exclude.includes("paper") ? "Include its material only if you may redistribute it." : "Its material may be redistributed with attribution."}</p>` : ""}
          ${pv.contents.groups.inputs?.resources.length ? `<p class="hint">Include the input files only if their license allows redistribution (most open data repositories use CC-BY or CC0).</p>` : ""}
          <label>Note for the record (optional) <textarea id="rp-note" rows="2" placeholder="e.g. what didn't reproduce and why">${esc(this.note)}</textarea></label>
          <div class="actions"><button id="rp-deposit" class="primary big" ${ready ? "" : "disabled"}>${this.w.running.has("deposit") ? "Uploading…" : `Upload to ${server} as a draft`}</button><button id="rp-metadata">Show metadata</button></div>
          ${already ? `<p class="hint">This ZIP is already on Zenodo (${already.status === "draft" ? "as a draft" : "published"}). A changed package (new run or report, then a new ZIP) or another choice of files makes a new deposit.</p>` : ""}
          ` : this.preview instanceof Error && !stale && !local.length ? `<p class="hint">${esc(this.preview.message)}</p>` : ""}
          ${this.deposits.length ? `<h4>Deposits of this package</h4><ul class="deposits">${this.deposits.map((d) => this.depositRow(d)).join("")}</ul>` : ""}
          ${others.length ? `<h4>Other ReproCrowd records for this paper</h4><ul class="small">${others.map((r) => `<li><a href="#" data-open="${esc(r.url)}">${esc(r.creators.join("; ") || "unknown")}, ${esc(r.published ?? "")}</a>${r.doi ? ` · <code>${esc(r.doi)}</code>` : ""}</li>`).join("")}</ul>` : this.records instanceof Error ? `<p class="warn-soft small">Couldn't search ${server} for other records of this paper.</p>` : ""}
        </section>
      </div>
      <iframe id="rp-preview" class="preview" sandbox="" title="Report preview"></iframe>
    </div>`;
    body.querySelector<HTMLIFrameElement>("#rp-preview")!.srcdoc = this.report ?? `<p style="font:15px 'Source Sans 3',system-ui;color:#6b7280;padding:2em">No report yet.</p>`;
    body.querySelector("#rp-report")!.addEventListener("click", async () => {
      const r = await this.w.busy("report", "Creating the report", () => pkg.put<ReportResult>("/report", { rounding: c.rounding, threshold: c.threshold }));
      if (r) {
        this.reportResult = r.data;
        this.w.draw();
      }
    });
    body.querySelector("#rp-report-show")?.addEventListener("click", () => host.reveal("report.html"));
    body.querySelector("#rp-zip")!.addEventListener("click", () => this.w.busy("zip", "Creating the ZIP", () => pkg.put("/archive", {})));
    body.querySelector("#rp-zip-show")?.addEventListener("click", () => archive && host.reveal(archive.path));
    body.querySelectorAll<HTMLInputElement>("[data-group]").forEach((cb) =>
      cb.addEventListener("change", () => {
        this.exclude ??= new Set();
        if (cb.checked) this.exclude.delete(cb.dataset.group!);
        else this.exclude.add(cb.dataset.group!);
        this.w.draw();
      }),
    );
    body.querySelector<HTMLTextAreaElement>("#rp-note")?.addEventListener("input", (e) => (this.note = (e.target as HTMLTextAreaElement).value));
    body.querySelector("#rp-metadata")?.addEventListener("click", () => this.showMetadata());
    body.querySelector("#rp-deposit")?.addEventListener("click", () => this.deposit());
    body.querySelectorAll<HTMLElement>("[data-settings]").forEach((el) =>
      el.addEventListener("click", (e) => {
        e.preventDefault();
        settingsDialog(() => this.w.render(), "zenodo");
      }),
    );
    body.querySelectorAll<HTMLElement>("[data-open]").forEach((el) =>
      el.addEventListener("click", (e) => {
        e.preventDefault();
        host.openUrl(el.dataset.open!).catch((err) => showError("Open", err));
      }),
    );
    body.querySelectorAll<HTMLButtonElement>("[data-dep]").forEach((b) => b.addEventListener("click", () => this.action(b.dataset.dep!, b.dataset.action as "refresh" | "publish" | "discard")));
  }

  private depositRow(d: ZenodoDeposit) {
    const busy = this.w.running.has(`dep-${d.id}`) ? "disabled" : "";
    return `<li class="deposit ${d.status}">
      <div><span class="badge st-${d.status}">${DEPOSIT_STATUS[d.status]}</span>${d.sandbox ? ` <span class="badge sandbox">Sandbox</span>` : ""} ${esc(when(d.published_at ?? d.created_at))}
        ${d.doi ? ` · DOI <code>${esc(d.doi)}</code>${d.status === "draft" ? ` <span class="muted small">(reserved)</span>` : ""}` : ""}</div>
      <div class="small muted">${esc(d.file)}${d.exclude.length ? ` · without ${esc(d.exclude.join(", "))}` : ""}${d.note ? ` · “${esc(d.note)}”` : ""}</div>
      <div class="actions">${d.status !== "discarded" && d.status !== "gone" ? `<a href="#" data-open="${esc(d.url)}">${d.status === "draft" ? "Review the draft on Zenodo" : "Open the record"}</a>` : ""}
        ${d.status === "draft" ? `<button data-dep="${esc(d.id)}" data-action="publish" class="primary" ${busy}>Publish…</button><button data-dep="${esc(d.id)}" data-action="discard" ${busy}>Discard draft</button>` : ""}
        ${d.status === "draft" || d.status === "published" ? `<button data-dep="${esc(d.id)}" data-action="refresh" ${busy}>Check status</button>` : ""}</div>
    </li>`;
  }

  private excludeList() {
    return this.exclude ? [...this.exclude] : undefined;
  }

  private async showMetadata() {
    try {
      const pv = await host.depositPreview({ note: this.note.trim(), exclude: this.excludeList() });
      modal("dlg-wide", `<h3>What the deposit sends to Zenodo</h3>
        <p class="hint">Nothing has been sent. Bibliographic details come from the paper's OpenAlex record; you are the creator.</p>
        <textarea class="mono big" readonly>${esc(JSON.stringify(pv.metadata, null, 2))}</textarea>
        <form method="dialog"><menu><button class="primary">Close</button></menu></form>`);
    } catch (e) {
      showError("Showing the metadata", e);
    }
  }

  private async deposit() {
    const a = this.account!;
    const ex = this.excludeList() ?? [];
    const ok = await confirmDialog(
      "Upload to Zenodo",
      `<p>The package ZIP${ex.length ? ` (without ${esc(ex.map((g) => GROUP_LABEL[g]?.toLowerCase() ?? g).join("; "))})` : ""} is uploaded to <b>${a.sandbox ? "the Zenodo sandbox" : "zenodo.org"}</b> as a <b>draft</b> by <b>${esc(a.creator.name)}</b>, with a reserved DOI.</p>
       <p class="hint">Nothing is public yet: review the draft on Zenodo, then publish it there or here.</p>`,
      "Upload draft",
    );
    if (!ok) return;
    this.w.running.add("deposit");
    this.w.setStatus(`⏳ Uploading to ${a.sandbox ? "the Zenodo sandbox" : "Zenodo"}…`);
    this.w.draw();
    try {
      const d = await host.deposit({ note: this.note.trim(), exclude: this.excludeList(), publish: false });
      this.w.setStatus("Uploaded as a draft on Zenodo");
      modal("dlg-confirm", `<form method="dialog"><h3>Draft on Zenodo</h3>
        <p>The draft is on ${d.sandbox ? "the Zenodo sandbox" : "Zenodo"}${d.doi ? `, with the reserved DOI <code>${esc(d.doi)}</code>` : ""}. Review it there; publish it there or with <b>Publish…</b> in this step.</p>
        <menu><button value="close">Close</button><button value="view" class="primary">Review on Zenodo</button></menu></form>`).addEventListener("close", function (this: HTMLDialogElement) {
        if (this.returnValue === "view") host.openUrl(d.url);
      });
    } catch (e) {
      this.w.setStatus("Not uploaded", "error");
      if (e instanceof ApiError && e.code === "duplicate_submission") {
        const d = e.problem?.existing as ZenodoDeposit | undefined;
        modal("dlg-confirm", `<form method="dialog"><h3>Already on Zenodo</h3><p>${esc(e.message)}</p>
          <p class="hint">Any change to the package (for example a new run or report, then a new ZIP), or a different choice of files, makes it a new deposit.</p>
          <menu><button value="close">Close</button>${d?.url ? `<button value="view" class="primary">Open on Zenodo</button>` : ""}</menu></form>`).addEventListener("close", function (this: HTMLDialogElement) {
          if (this.returnValue === "view" && d?.url) host.openUrl(d.url);
        });
      } else showError("Uploading to Zenodo", e);
    } finally {
      this.w.running.delete("deposit");
      await this.w.render();
    }
  }

  private async action(id: string, action: "refresh" | "publish" | "discard") {
    const d = this.deposits.find((x) => x.id === id);
    if (!d) return;
    if (action === "publish") {
      const ok = await confirmDialog(
        "Publish on Zenodo",
        `<p>Publishing makes the record public${d.sandbox ? " on the sandbox" : ""} and registers its DOI${d.doi ? ` <code>${esc(d.doi)}</code>` : ""}.</p>
         <p><b>${d.sandbox ? "Sandbox records are for testing only." : "A published record can't be deleted; later changes become new versions."}</b></p>`,
        "Publish",
        !d.sandbox,
      );
      if (!ok) return;
    }
    if (action === "discard" && !(await confirmDialog("Discard the draft", "<p>The draft and its file are deleted on Zenodo, and the reserved DOI is released.</p>", "Discard", true))) return;
    const label = { refresh: "Checking the deposit on Zenodo", publish: "Publishing on Zenodo", discard: "Discarding the draft" }[action];
    await this.w.busy(`dep-${id}`, label, () => host.depositAction(id, action));
  }
}
