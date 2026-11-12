// Step 5: the master script. The templates are listed vertically as the backend reports them (GET /v1/templates), so
// templates added to the backend show up here without changes to the plugin. Creating the script writes the
// resolved plan (load steps → actions → results step), results_schema.json and the template's files.
import { ApiError, host, pkg } from "../api";
import type { ScriptState, TemplateView, ValidationReport } from "../types";
import { choose, esc } from "../util";
import type { Step, Wizard } from "./wizard";

const TODO_LINE = /TODO\(LLM\):/;

export class ScriptStep implements Step {
  title = "Master script";
  private templates: TemplateView[] = [];
  private script?: ScriptState;
  private master = "";
  private counts = { targets: 0, actions: 0 };
  private validation?: ValidationReport;

  constructor(private w: Wizard) {}

  async load() {
    const [templates, script, targets, actions] = await Promise.all([
      this.templates.length ? Promise.resolve(this.templates) : host.service<TemplateView[]>("/v1/templates"),
      pkg.get<ScriptState>("/script"),
      pkg.get<unknown[]>("/targets"),
      pkg.get<unknown[]>("/actions"),
    ]);
    this.templates = templates;
    this.script = script;
    this.counts = { targets: targets.length, actions: actions.length };
    this.master = script.master ? await pkg.text(`/files/${script.master.split("/").map(encodeURIComponent).join("/")}`).catch(() => "") : "";
    this.validation = await pkg.get<ValidationReport>("/validation").catch(() => undefined);
  }

  private get chosen(): string {
    const st = this.w.app.state;
    const ids = this.templates.map((t) => t.id);
    return ids.includes(st.template) ? st.template : this.script?.template && ids.includes(this.script.template) ? this.script.template : ids[0] ?? "";
  }

  draw(body: HTMLElement) {
    const s = this.script!;
    const chosen = this.chosen;
    const current = s.template;
    const busy = this.w.running.has("script");
    const issues = [!this.counts.targets && "No targets in use (step 3).", !this.counts.actions && "No analysis steps in use (step 4)."].filter(Boolean) as string[];
    const errs = this.validation?.errors ?? [];
    const masterHtml = this.master
      ? this.master
          .split("\n")
          .map((line, i) => `<span class="ln${TODO_LINE.test(line) ? " todo" : ""}" data-n="${i + 1}">${esc(line) || " "}</span>`)
          .join("")
      : "";
    body.innerHTML = `
    <div class="cols cols-script">
      <section class="card">
        <h3>Template</h3>
        <p class="hint">The language and layout of the generated pipeline. The list comes from the backend's templates.</p>
        <div class="tpl-list">${this.templates
          .map(
            (t) => `<label class="tpl ${t.id === chosen ? "on" : ""}"><input type="radio" name="tpl" value="${esc(t.id)}" ${t.id === chosen ? "checked" : ""} />
              <span class="tpl-main"><span class="tpl-title"><b>${esc(t.label)}</b> <span class="badge">${esc(t.language)}</span>${t.id === current ? ` <span class="badge on">current</span>` : ""}</span>
              <span class="tpl-meta">master <code>${esc(t.master)}</code> · runs <code>${esc(t.command)}</code></span></span></label>`,
          )
          .join("")}</div>
        <h3>Package</h3>
        ${issues.length ? `<div class="warn">${issues.map((i) => `⚠ ${esc(i)}`).join("<br>")}</div>` : ""}
        ${errs.length ? `<details class="warn-soft"><summary>${errs.length} validation problem(s) in the package</summary><ul>${errs.slice(0, 40).map((e) => `<li><code>${esc(e.stage)}</code> ${esc(e.resource ?? "")} ${esc(e.message)}</li>`).join("")}</ul></details>` : this.validation ? `<p class="ok">✓ The package validates.</p>` : ""}
        <p>Folder: <code class="path">${esc(this.w.app.summary().path)}</code></p>
        <div class="actions">
          <button id="rp-script" class="primary" ${busy || !this.counts.targets || !chosen ? "disabled" : ""}>${current ? (current === chosen ? "Update master script" : "Switch template") : "Create master script"}</button>
          <button id="rp-editor">Open in editor</button><button id="rp-reveal">Show folder</button>
        </div>
        <p class="hint">Creating the script also writes <code>analysis_plan.json</code>, <code>results_schema.json</code>, a README and <code>AGENT_PROMPT.md</code>. Files edited since they were generated (e.g. by the coding agent) are protected: you'll be asked whether to keep or overwrite them.</p>
        ${s.files.length ? `<details><summary>${s.files.length} generated files</summary><ul class="small">${s.files.map((f) => `<li><code>${esc(f.path)}</code></li>`).join("")}</ul></details>` : ""}
      </section>
      <section class="card editor">
        <div class="card-head"><h3>${esc(s.master ?? "Master script")}</h3>
          ${s.todo ? `<span class="badge ${s.todo.remaining ? "warn-badge" : "ok-badge"}">${s.todo.remaining} × TODO(LLM)</span>` : ""}<span class="spacer"></span>
          ${s.master ? `<span class="muted small">read-only here; edit it in your editor or let the agent implement it (step 6)</span>` : ""}</div>
        ${this.master ? `<pre class="code-view"><code>${masterHtml}</code></pre>` : `<p class="muted">No master script yet. Choose a template and create it.</p>`}
      </section>
    </div>`;
    body.querySelectorAll<HTMLInputElement>("input[name=tpl]").forEach((r) =>
      r.addEventListener("change", () => {
        this.w.app.state.template = r.value;
        this.w.app.save();
        this.w.draw();
      }),
    );
    body.querySelector("#rp-script")!.addEventListener("click", () => this.write(chosen));
    body.querySelector("#rp-editor")!.addEventListener("click", () => host.openInEditor().then((n) => this.w.setStatus(`Opened the package in ${n}`), (e) => this.w.setStatus((e as Error).message, "error")));
    body.querySelector("#rp-reveal")!.addEventListener("click", () => host.reveal());
  }

  private async write(template: string) {
    const put = (on_modified: string) => pkg.put("/script", { template, on_modified });
    await this.w.busy("script", "Writing the master script", async () => {
      try {
        await put("refuse");
      } catch (e) {
        if (!(e instanceof ApiError && e.code === "script_modified")) throw e;
        const files = (e.problem?.files as string[] | undefined) ?? [];
        const choice = await choose(
          "Files were edited",
          `<p>${esc(e.message)}</p>${files.length ? `<ul>${files.map((f) => `<li><code>${esc(f)}</code></li>`).join("")}</ul>` : ""}
           <p><b>Keep</b> leaves the edited files as they are and regenerates everything else (e.g. after targets changed). <b>Overwrite</b> replaces them with the template's placeholders; the edited versions stay in the package's git history.</p>`,
          [["cancel", "Cancel"], ["overwrite", "Overwrite", "danger"], ["keep", "Keep edited files", "primary"]],
        );
        if (choice !== "keep" && choice !== "overwrite") return;
        await put(choice);
      }
    });
  }
}
