// Step 6: code generation. Either in the user's own editor (the package folder plus AGENT_PROMPT.md), or by the
// backend's headless coding agent as a job: its messages and tool calls stream in while it works, the package's
// files are listed (most recently changed first) with the TODO(LLM) markers left.
import { CLI_LABELS, cliModel, cliOf, host, pkg, modelCompatible, RESTRICTION_LABELS } from "../api";
import type { LlmModel, LlmModels } from "../api";
import type { AgentProfile, FileEntry, PromptPreview, ScriptState } from "../types";
import { commandLine, esc, modal, showError, showPrompt, sizeText, when } from "../util";
import { appendEvents, JobFollower, statusBadge } from "./jobs";
import type { Step, Wizard } from "./wizard";

export class CodegenStep implements Step {
  title = "Code generation";
  private job = new JobFollower("codegen");
  private profiles: AgentProfile[] = [];
  private models?: LlmModels;
  private files: FileEntry[] = [];
  private script?: ScriptState;

  constructor(private w: Wizard) {}

  async load() {
    if (!this.profiles.length) this.profiles = await host.service<AgentProfile[]>("/v1/agent-profiles").catch(() => []);
    this.models = await host.llmModels().catch(() => undefined);
    await Promise.all([this.job.load(), this.loadFiles()]);
  }

  private async loadFiles() {
    [this.files, this.script] = await Promise.all([pkg.get<FileEntry[]>("/files"), pkg.get<ScriptState>("/script")]);
  }

  pollMs = () => (this.job.running ? 1200 : 4000);

  async poll() {
    const wasRunning = this.job.running;
    if (this.job.running) await this.job.poll();
    await this.loadFiles();
    if (wasRunning && !this.job.running) return this.w.render(); // finished: show the result
    this.drawLive();
  }

  draw(body: HTMLElement) {
    const st = this.w.app.state;
    const s = this.script;
    const j = this.job.job;
    const running = this.job.running;
    const ready = !!s?.template;
    const profile = this.profiles.find((p) => p.name === st.agentProfile) ?? this.profiles.find((p) => p.default) ?? this.profiles[0];
    const unsupported = profile?.exec_restriction === "unsupported";
    const res = j?.result?.type === "codegen" ? j.result : undefined;
    body.innerHTML = `
    <div class="cols cols-codegen">
      <section class="card col-flex">
        <h3>Implement the master script</h3>
        ${ready ? "" : `<p class="warn">Create the master script in step 5 first.</p>`}
        <div class="gen-row">
          <div><b>Your own editor</b><div class="hint">Opens the package folder in your code editor. AGENT_PROMPT.md tells a coding agent there what to implement; copy it into the agent's chat.</div></div>
          <button id="rp-editor" ${ready ? "" : "disabled"}>Open in editor</button>
          <button id="rp-copy" ${ready ? "" : "disabled"}>Copy agent prompt</button>
        </div>
        <div class="gen-row gen-agent">
          <div><b>Headless coding agent</b><div class="hint">Runs in the package folder without interaction and implements the TODO(LLM): markers. The package is committed to git before and after.</div>
            <div class="form-row">
              <label>Profile <select id="rp-profile" ${running ? "disabled" : ""}>${this.profiles.map((p) => `<option value="${esc(p.name)}" ${p === profile ? "selected" : ""}>${esc(p.name)}: ${esc(p.model || "default")}${p.effort ? `, ${esc(p.effort)}` : ""}</option>`).join("")}</select></label>
              <label>Model <select id="rp-model" ${running ? "disabled" : ""}><option value="">(the CLI's default)</option></select></label>
              <label class="inline" title="${esc(profile?.exec_tools.join(", ") ?? "")}"><input type="checkbox" id="rp-exec" ${st.allowExec ? "checked" : ""} ${running || unsupported ? "disabled" : ""} /> let it run the analysis (R, Python, Snakemake)</label>
            </div>
            <p class="hint">${unsupported ? "This profile cannot run editing-only: with execution allowed, the agent can edit files and run commands in the package folder. A Codex or OpenCode profile saved before no-analysis settings existed regains it when its preset is re-applied in Settings." : profile ? `Without “let it run the analysis”: ${RESTRICTION_LABELS[profile.exec_restriction] ?? profile.exec_restriction}.` : ""}${profile?.available === false ? " The executable was not found; install it or update the command in Settings." : ""}</p>
            ${this.models?.needsLogin?.includes(cliOf(profile?.argv) as LlmModel["cli"]) ? `<p class="hint warn-inline">${esc(CLI_LABELS[cliOf(profile?.argv)] ?? cliOf(profile?.argv))} is installed but not signed in. <button id="rp-login" type="button">Log in…</button> <button id="rp-login-check" type="button">Check again</button></p>` : ""}
            ${unsupported && !st.allowExec ? `<button id="rp-allow-exec" type="button">Let the agent run the analysis</button>` : ""}
            <div class="form-row"><button id="rp-prompt" ${ready ? "" : "disabled"}>Show prompt</button></div>
            <label>Extra instructions <textarea id="rp-instr" rows="2" ${running ? "disabled" : ""} placeholder="optional">${esc(st.agentInstructions)}</textarea></label>
          </div>
          <button id="rp-gen" class="primary" ${running || !ready || (unsupported && !st.allowExec) ? "disabled" : ""}>${running ? "Working…" : "Run agent…"}</button>
          <button id="rp-stop" ${running ? "" : "disabled"}>Stop</button>
        </div>
        <div class="log-head"><h4>Agent activity</h4><span class="muted small">${j ? `${statusBadge(j)} · started ${esc(when(j.created_at))}` : ""}</span></div>
        ${res ? `<p class="small">${res.todo.remaining ? `<span class="warn-inline">${res.todo.remaining} TODO(LLM) markers left</span>` : `<span class="ok">✓ all TODO(LLM) markers implemented</span>`} · ${res.changed_files.length} files changed${res.commit_after ? ` · commit <code>${esc(res.commit_after.slice(0, 10))}</code>` : ""}${res.git_note ? ` · ${esc(res.git_note)}` : ""}</p>` : ""}
        ${j?.error ? `<p class="warn">${esc(j.error)}</p>` : ""}
        ${j ? "" : `<p class="muted">No agent run yet.</p>`}
        <ul id="rp-events" class="events" ${j ? "" : "hidden"}></ul>
      </section>
      <section class="card col-flex">
        <div class="card-head"><h3>Package folder</h3><span class="muted small" title="Refreshed every few seconds while this step is open">● watching</span><span class="spacer"></span><button id="rp-reveal">Show folder</button></div>
        <p id="rp-todo"></p>
        <div id="rp-files" class="scroll"></div>
      </section>
    </div>`;
    this.drawLive();
    const prof = body.querySelector<HTMLSelectElement>("#rp-profile")!;
    const modelSel = body.querySelector<HTMLSelectElement>("#rp-model")!;
    prof.addEventListener("change", () => {
      st.agentProfile = prof.value;
      fillModels();
      this.w.app.save();
      this.w.render();
    });
    // Offer detected models only for the selected command-line tool.
    const fillModels = () => {
      const m = this.models;
      if (!m) return;
      const byProv = new Map<string, LlmModel[]>();
      for (const x of m.available) if (!m.disabled.includes(x.id) && modelCompatible(x, this.profiles.find((p) => p.name === prof.value)?.argv)) {
        const a = byProv.get(x.provider) ?? [];
        a.push(x);
        byProv.set(x.provider, a);
      }
      modelSel.innerHTML = `<option value="">(the CLI's default)</option>${[...byProv.entries()]
        .map(([p, arr]) => `<optgroup label="${esc(p)}">${arr.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</optgroup>`)
        .join("")}`;
      if (st.agentModel && [...modelSel.options].some((x) => x.value === st.agentModel)) modelSel.value = st.agentModel;
      else if (st.agentModel) {
        st.agentModel = "";
        this.w.setStatus("The saved model was cleared: it is turned off on the start page or not offered for this profile's CLI.");
      }
    };
    fillModels();
    modelSel.addEventListener("change", () => {
      st.agentModel = modelSel.value;
      this.w.app.save();
    });
    body.querySelector("#rp-login-check")?.addEventListener("click", async () => { this.models = await host.llmModels(true).catch(() => this.models); this.w.render(); });
    body.querySelector("#rp-login")?.addEventListener("click", () => host.cliLogin(cliOf(this.profiles.find((p) => p.name === prof.value)?.argv)).then((m) => this.w.setStatus(m), (e) => this.w.setStatus((e as Error).message, "error")));
    body.querySelector("#rp-allow-exec")?.addEventListener("click", () => { st.allowExec = true; this.w.app.save(); this.w.render(); });
    body.querySelector<HTMLInputElement>("#rp-exec")!.addEventListener("change", (e) => {
      st.allowExec = (e.target as HTMLInputElement).checked;
      this.w.app.save();
    });
    body.querySelector<HTMLTextAreaElement>("#rp-instr")!.addEventListener("change", (e) => {
      st.agentInstructions = (e.target as HTMLTextAreaElement).value.trim();
      this.w.app.save();
    });
    body.querySelector("#rp-editor")!.addEventListener("click", () => host.openInEditor().then((n) => this.w.setStatus(`Opened the package in ${n}`), (e) => this.w.setStatus((e as Error).message, "error")));
    body.querySelector("#rp-reveal")!.addEventListener("click", () => host.reveal());
    body.querySelector("#rp-copy")!.addEventListener("click", () => this.copyPrompt());
    body.querySelector("#rp-prompt")!.addEventListener("click", () =>
      showPrompt("Prompt for the coding agent", () =>
        pkg.post<PromptPreview>("/jobs:preview", {
          type: "codegen",
          profile: prof.value || undefined,
          allow_exec: st.allowExec,
          instructions: body.querySelector<HTMLTextAreaElement>("#rp-instr")!.value.trim(),
          model: st.agentModel ? cliModel(st.agentModel) : undefined,
        }),
        st.allowExec,
      ),
    );
    body.querySelector("#rp-gen")!.addEventListener("click", () => this.start());
    body.querySelector("#rp-stop")!.addEventListener("click", () => this.w.busy("stop", "Stopping the agent", () => this.job.cancel()));
  }

  /** Parts that change while the step is open: events, TODO count, files. */
  private drawLive() {
    const ev = document.querySelector<HTMLElement>("#rp-events");
    if (ev) appendEvents(ev, this.job.events, false);
    const todo = document.querySelector("#rp-todo");
    const s = this.script;
    if (todo) todo.innerHTML = s?.todo ? `<b>${esc(s.master ?? "")}</b>: ${s.todo.remaining ? `<span class="warn-inline">${s.todo.remaining} × TODO(LLM) left</span>` : `<span class="ok">✓ no TODO(LLM) markers left</span>`}${this.job.running ? ` · <span class="pulse">the agent is working…</span>` : ""}` : `<span class="muted">No master script yet.</span>`;
    const filesEl = document.querySelector("#rp-files");
    if (!filesEl) return;
    const now = Date.now() / 1000;
    const age = (t: number) => {
      const s = now - t;
      return s < 60 ? `${Math.max(0, Math.round(s))} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : when(t * 1000);
    };
    filesEl.innerHTML = `<table class="tbl-list small"><thead><tr><th>Path <span class="muted">(most recently changed first)</span></th><th>Kind</th><th>Size</th><th>Changed</th></tr></thead><tbody>${this.files
      .map((f) => `<tr class="${now - f.modified < 60 ? "fresh" : ""}"><td><code>${esc(f.path)}</code></td><td>${esc(f.kind ?? "new")}</td><td class="nowrap">${sizeText(f.size)}</td><td class="nowrap">${age(f.modified)}</td></tr>`)
      .join("")}</tbody></table>`;
  }

  private async copyPrompt() {
    const path = this.script?.agent_prompt ?? "AGENT_PROMPT.md";
    const prompt = await pkg.text(`/files/${encodeURIComponent(path)}`).catch((e) => `(could not read ${path}: ${(e as Error).message})`);
    try {
      await navigator.clipboard.writeText(prompt);
      this.w.setStatus("Agent prompt copied; paste it into the coding agent in your editor.");
    } catch {
      const dlg = modal("dlg-wide", `<h3>Agent prompt</h3><p class="hint">Copying to the clipboard failed; the prompt is selected below, press Ctrl+C.</p><textarea class="mono big" readonly>${esc(prompt)}</textarea><form method="dialog"><menu><button class="primary">Close</button></menu></form>`);
      dlg.querySelector("textarea")!.select();
    }
  }

  private starting = false;

  private async start() {
    if (this.starting) return; // a second click while the preview or confirmation is open
    this.starting = true;
    try {
      await this.confirmAndStart();
    } finally {
      this.starting = false;
    }
  }

  private async confirmAndStart() {
    const st = this.w.app.state;
    const profile = this.profiles.find((p) => p.name === (document.querySelector<HTMLSelectElement>("#rp-profile")?.value ?? ""));
    const model = document.querySelector<HTMLSelectElement>("#rp-model")?.value || "";
    st.agentModel = model;
    const cli = model ? cliModel(model) : "";
    st.agentInstructions = document.querySelector<HTMLTextAreaElement>("#rp-instr")?.value.trim() ?? "";
    // One snapshot for the preview, the confirmation and the start, so a toggle meanwhile can't change what runs.
    const allowExec = st.allowExec;
    // The exact command, added environment and restriction come from the backend's preview (the same as Show prompt).
    const request = { profile: profile?.name, allow_exec: allowExec, instructions: st.agentInstructions, model: cli || undefined };
    let pv: PromptPreview;
    try { pv = await pkg.post<PromptPreview>("/jobs:preview", { type: "codegen", ...request }); } catch (e) { return showError("Preparing the coding agent", e); }
    const env = Object.entries(pv.env ?? {}).map(([k, v]) => `${k}=${v}`).join(" ");
    const ok = await new Promise<boolean>((resolve) => {
      const dlg = modal("dlg-wide", `<h3>Run the coding agent?</h3>
        <p>The agent works in <code>${esc(this.w.app.summary().path)}</code> and may take minutes to hours. It can read and write files there${allowExec ? " and run the analysis" : ""}.</p>
        <div class="kv"><span>Profile</span><span>${esc(pv.profile)} · model ${esc(pv.model || "default")} · effort ${esc(pv.effort || "default")} · timeout ${Math.round(pv.timeout_s / 60)} min</span>
          <span>Command</span><code>${esc(commandLine(pv.argv))}</code>
          ${env ? `<span>Environment</span><code>${esc(env)}</code>` : ""}
          <span>Execution restriction</span><span>${esc(allowExec ? "none: the agent may run the analysis" : RESTRICTION_LABELS[pv.exec_restriction] ?? pv.exec_restriction)}</span></div>
        <form method="dialog"><menu><button value="cancel" formnovalidate>Cancel</button><button value="ok" class="primary">Run</button></menu></form>`);
      dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"));
    });
    if (!ok) return;
    this.w.app.save();
    await this.w.busy("codegen-start", "Starting the coding agent", () => this.job.start(request));
    this.w.repoll();
  }
}
