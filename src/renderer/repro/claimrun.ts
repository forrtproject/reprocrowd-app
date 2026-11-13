// Running one claim end to end, from the Claims step: write its script files (step 5), let the coding agent implement
// them (step 6), run the pipeline (step 7) and make the report (step 8). Each stage is a backend call or job, so a
// window closed half-way leaves the package consistent: the claim then shows its real state when the package is
// opened again.
import { ApiError, pkg } from "../api";
import type { Job } from "../types";
import { choose, esc, showError } from "../util";
import type { Wizard } from "./wizard";

export type ClaimStage = "script" | "codegen" | "run" | "report";

const LABEL: Record<ClaimStage, string> = { script: "Writing the script files", codegen: "Implementing the claim (coding agent)", run: "Running the pipeline", report: "Creating the report" };

/** Wait until the job has finished; throws if it did not succeed. */
async function waitFor(job: Job, what: string): Promise<Job> {
  while (job.status === "running") {
    await new Promise((r) => setTimeout(r, 1000));
    job = await pkg.get<Job>(`/jobs/${job.id}`);
  }
  if (job.status !== "succeeded") throw new Error(`${what} ${job.status}${job.error ? `: ${job.error}` : ""}`);
  return job;
}

/** PUT /script; an edited monolithic master script is kept (moved aside) or overwritten only if the user says so. */
async function writeScript(template: string): Promise<boolean> {
  const put = (on_modified: string) => pkg.put("/script", { template, on_modified });
  try {
    await put("refuse");
    return true;
  } catch (e) {
    if (!(e instanceof ApiError && e.code === "script_modified")) throw e;
    const files = (e.problem?.files as string[] | undefined) ?? [];
    const choice = await choose(
      "Files were edited",
      `<p>${esc(e.message)}</p>${files.length ? `<ul>${files.map((f) => `<li><code>${esc(f)}</code></li>`).join("")}</ul>` : ""}
       <p>Running claims replaces the master script with a runner. <b>Keep</b> moves the edited script aside (as <code>…pre-claims…</code>); <b>Overwrite</b> replaces it, and the edited version stays in the package's git history.</p>`,
      [["cancel", "Cancel"], ["overwrite", "Overwrite", "danger"], ["keep", "Keep edited files", "primary"]],
    );
    if (choice !== "keep" && choice !== "overwrite") return false;
    await put(choice);
    return true;
  }
}

/**
 * Steps 5–8 for one claim, in sequence; stops at the first error and shows it (or when the user cancels keeping an
 * edited master script).
 */
export async function runClaim(w: Wizard, claimId: string, template: string, onStage: (s: ClaimStage) => void): Promise<void> {
  const st = w.app.state;
  let stage: ClaimStage = "script";
  const enter = (s: ClaimStage) => {
    stage = s;
    onStage(s);
  };
  // Reloads the step on screen; on the Claims step that refreshes claims:status, so the pill follows the stages.
  const refresh = () => w.render();
  try {
    enter("script");
    if (!(await writeScript(template))) return;
    await refresh();

    enter("codegen");
    const gen = await pkg.post<Job>("/jobs", { type: "codegen", claim: claimId, profile: st.agentProfile || undefined, allow_exec: st.allowExec, instructions: st.agentInstructions });
    await refresh();
    await waitFor(gen, "The coding agent");
    await refresh();

    enter("run");
    const run = await pkg.post<Job>("/jobs", { type: "run" });
    await refresh();
    await waitFor(run, "The run");
    await refresh();

    enter("report");
    await pkg.put("/report", { rounding: st.compare.rounding, threshold: st.compare.threshold });
    await refresh();
  } catch (e) {
    showError(`Running claim ${claimId}: ${LABEL[stage].toLowerCase()}`, e);
    await w.render();
  }
}

export const stageLabel = (s: ClaimStage) => LABEL[s];
