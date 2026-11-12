// Step 1: the inputs of the reproduction.
//   The paper's text: its JATS XML from Europe PMC, parsed (retrieved by the app; shown here, nothing to choose).
//   Input files:      the study's data and, if available, the authors' code (POST …/inputs; zip archives are unpacked).
import { ApiError, host, pkg } from "../api";
import { countSentences } from "../doc";
import { ROLES, type Document, type Resource, type Role } from "../types";
import { confirmDialog, esc, sizeText } from "../util";
import type { Step, Wizard } from "./wizard";

type InputRes = Resource & { reprocrowd: Extract<Resource["reprocrowd"], { kind: "input" }> };

export class InputsStep implements Step {
  title = "Inputs";
  private inputs: InputRes[] = [];
  private docs = new Map<string, Document | Error>();

  constructor(private w: Wizard) {}

  async load() {
    this.inputs = (await pkg.get<Resource[]>("/inputs")) as InputRes[];
    const name = this.w.app.jats();
    if (!(this.docs.get(name) && !(this.docs.get(name) instanceof Error))) this.docs.set(name, await this.w.app.loadDoc(name).catch((e) => e as Error));
  }

  draw(body: HTMLElement) {
    const app = this.w.app;
    const d = this.docs.get(app.jats());
    const ids = app.summary().identifiers ?? {};
    const contents = d instanceof Error ? `<span class="warn-inline">${esc(d.message)}</span>` : d ? `${d.sections.length} sections · ${countSentences(d)} sentences · ${d.figures.length} figures · ${d.tables.length} tables` : "";
    const archives = this.inputs.filter((r) => this.inputs.some((m) => m.reprocrowd.archive === r.name));
    const loose = this.inputs.filter((r) => !r.reprocrowd.archive && !archives.includes(r));
    const roleSel = (r: InputRes) => `<select data-f="role" ${r.reprocrowd.used ? "" : "disabled"}>${ROLES.map((x) => `<option ${x === r.reprocrowd.role ? "selected" : ""}>${x}</option>`).join("")}</select>`;
    const row = (r: InputRes, nested: string) => `<tr data-name="${esc(r.name)}" class="${nested} ${r.reprocrowd.used ? "" : "rp-disabled"}">
        <td><input type="checkbox" data-f="used" ${r.reprocrowd.used ? "checked" : ""} title="Use this file in the reproduction" /></td>
        <td><code>${esc(nested ? r.path.split("/").slice(2).join("/") || r.path : r.path.replace(/^inputs\//, ""))}</code></td><td>${roleSel(r)}</td>
        <td><input data-f="note" value="${esc(r.reprocrowd.note)}" placeholder="note" ${r.reprocrowd.used ? "" : "disabled"} /></td>
        <td class="nowrap">${sizeText(r.bytes)}</td><td>${nested ? "" : `<button data-act="del" title="Delete from the package">✕</button>`}</td></tr>`;
    let rows = loose.map((r) => row(r, "")).join("");
    for (const a of archives) {
      const members = this.inputs.filter((m) => m.reprocrowd.archive === a.name);
      const off = members.filter((m) => !m.reprocrowd.used).length;
      rows += `<tr class="rp-archive" data-name="${esc(a.name)}"><td></td><td colspan="3"><span class="badge">zip</span> <b>${esc(a.path.replace(/^inputs\//, ""))}</b> <span class="muted">${members.length} files unpacked${off ? `, ${off} not used` : ""}</span></td><td class="nowrap">${sizeText(a.bytes)}</td><td><button data-act="del" title="Remove the archive and all its files">✕</button></td></tr>`;
      rows += members.map((m) => row(m, "rp-nested")).join("");
    }
    const counts = ROLES.map((r) => `${this.inputs.filter((i) => i.reprocrowd.used && i.reprocrowd.role === r && !archives.includes(i)).length} ${r}`).join(" · ");
    const uploading = this.w.running.has("upload");

    body.innerHTML = `
    <div class="card">
      <div class="card-head"><h3>The paper's text</h3><span class="ok small">✓ JATS XML${ids.pmcid ? ` from Europe PMC (${esc(ids.pmcid)})` : ""}</span></div>
      <p class="small">${contents}</p>
      <p class="hint">Targets (step 2) and analysis steps (step 3) are extracted from this text and point into it.</p>
    </div>
    <div class="card rp-drop">
      <div class="card-head"><h3>Input files</h3><span class="muted">${counts}</span><span class="spacer"></span>
        <label class="btn primary ${uploading ? "disabled" : ""}">Add files…<input type="file" multiple hidden id="rp-add-files" /></label><button id="rp-add-dir" ${uploading ? "disabled" : ""}>Add a folder…</button></div>
      <p class="hint">The study's data and, if available, the authors' original code. The backend guesses the role from the file type; notes are passed on to the LLM and the report. Zip archives are unpacked; files in an archive can be left out (unticked) without changing it. You can also drop files here.</p>
      <table class="tbl-list rp-inputs"><thead><tr><th title="Used">Use</th><th>File</th><th>Role</th><th>Note</th><th>Size</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">No input files yet.</td></tr>`}</tbody></table>
    </div>`;

    const add = (files: File[]) =>
      this.w.busy("upload", `Adding ${files.length} file(s)`, async () => {
        for (const f of files) await pkg.upload("inputs", f);
      });
    body.querySelector<HTMLInputElement>("#rp-add-files")!.addEventListener("change", (e) => {
      const files = Array.from((e.target as HTMLInputElement).files ?? []);
      if (files.length) add(files);
    });
    body.querySelector("#rp-add-dir")?.addEventListener("click", () => this.w.busy("upload", "Adding a folder", () => host.addFiles("inputs", true)));
    const card = body.querySelector<HTMLElement>(".rp-drop")!;
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      card.classList.add("over");
    });
    card.addEventListener("dragleave", () => card.classList.remove("over"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("over");
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) add(files);
    });

    const table = body.querySelector<HTMLElement>(".rp-inputs")!;
    table.addEventListener("change", async (e) => {
      const el = e.target as HTMLInputElement | HTMLSelectElement;
      const name = el.closest<HTMLElement>("tr[data-name]")?.dataset.name;
      const f = el.dataset.f;
      if (!name || !f) return;
      const value = f === "used" ? (el as HTMLInputElement).checked : f === "role" ? (el.value as Role) : el.value.trim();
      try {
        const r = (await pkg.patch<Resource>(`/inputs/${encodeURIComponent(name)}`, { [f]: value })) as InputRes;
        this.inputs = this.inputs.map((x) => (x.name === name ? r : x));
        if (f === "used") this.w.draw();
      } catch (err) {
        this.w.setStatus(`Changing ${name} failed: ${(err as Error).message}`, "error");
        this.w.render();
      }
    });
    table.addEventListener("click", async (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-act=del]");
      const name = b?.closest<HTMLElement>("tr[data-name]")?.dataset.name;
      if (!name) return;
      const n = this.inputs.filter((m) => m.reprocrowd.archive === name).length;
      const what = n ? `the archive <code>${esc(name)}</code> and all ${n} files unpacked from it` : `<code>${esc(name)}</code>`;
      if (!(await confirmDialog("Delete input", `Delete ${what} from the package?`, "Delete", true))) return;
      await this.w.busy("delete", `Deleting ${name}`, async () => {
        try {
          await pkg.del(`/inputs/${encodeURIComponent(name)}`);
        } catch (err) {
          if (err instanceof ApiError && err.code === "in_use") throw new ApiError(`${name} is used by analysis steps (step 4); remove it there first.`, err.problem);
          throw err;
        }
      });
    });
  }
}
