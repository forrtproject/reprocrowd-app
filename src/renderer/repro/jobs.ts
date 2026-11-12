// Following a job of the backend (code generation, pipeline run): its record and its progress events. Events are
// fetched incrementally (GET …/events?after=N) while the job runs.
import { pkg } from "../api";
import type { Job, JobEvent } from "../types";
import { esc, nearEnd, trunc } from "../util";

export class JobFollower {
  job?: Job;
  events: JobEvent[] = [];

  constructor(private type: "codegen" | "run") {}

  get running() {
    return this.job?.status === "running";
  }

  /** The latest job of this type (or none). */
  async load() {
    const jobs = await pkg.get<Job[]>("/jobs");
    const last = jobs.filter((j) => j.type === this.type).pop();
    if (last?.id !== this.job?.id) this.events = [];
    this.job = last;
    await this.more();
  }

  async start(body: object) {
    this.job = await pkg.post<Job>("/jobs", { type: this.type, ...body });
    this.events = [];
  }

  /** Fetch new events and the job's status; true if anything changed. */
  async poll(): Promise<boolean> {
    if (!this.job) return false;
    const before = this.job.status;
    const n = this.events.length;
    this.job = await pkg.get<Job>(`/jobs/${this.job.id}`);
    await this.more();
    return this.events.length !== n || this.job.status !== before;
  }

  private async more() {
    if (!this.job) return;
    const after = this.events.at(-1)?.seq ?? 0;
    const fresh = await pkg.get<JobEvent[]>(`/jobs/${this.job.id}/events`, { after });
    this.events.push(...fresh);
  }

  async cancel() {
    if (this.job) this.job = await pkg.del(`/jobs/${this.job.id}`).then(() => pkg.get<Job>(`/jobs/${this.job!.id}`));
  }
}

const ICON: Record<JobEvent["kind"], string> = { start: "▶", text: "💬", tool_use: "🔧", tool_result: "↳", result: "■", log: "" };

export function eventLine(e: JobEvent): string {
  if (e.kind === "log") return esc(e.text);
  const icon = e.kind === "result" ? (e.is_error ? "✗" : "✓") : ICON[e.kind];
  const text = e.kind === "tool_use" ? trunc(e.text, 300) : trunc(e.text.replace(/\s+/g, " "), e.kind === "tool_result" ? 200 : 600);
  return `<span class="ev ev-${e.kind}${e.is_error ? " ev-err" : ""}">${icon} ${esc(text)}</span>`;
}

/** Append the events not yet shown; follow the end unless the user scrolled up. */
export function appendEvents(el: HTMLElement, events: JobEvent[], asText: boolean) {
  const atEnd = el.childElementCount === 0 || nearEnd(el);
  if (events.length < el.childElementCount) el.replaceChildren();
  for (const e of events.slice(el.childElementCount)) {
    const line = document.createElement(asText ? "div" : "li");
    line.innerHTML = eventLine(e);
    el.append(line);
  }
  if (atEnd) {
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => (el.scrollTop = el.scrollHeight));
  }
}

export const statusBadge = (j?: Job) =>
  j ? `<span class="badge job-${j.status}">${j.status}</span>${j.exit_code != null ? ` exit ${j.exit_code}` : ""}${j.duration_ms != null ? ` · ${Math.round(j.duration_ms / 1000)} s` : ""}` : "";
