// Shapes of the ReproCrowd backend's JSON (see backend/docs/schemas.md and backend/docs/openapi.json).

export interface Box { page: number; x0: number; y0: number; x1: number; y1: number; origin: "top" | "bottom" }
export interface Sentence { id: string; text: string; boxes?: Box[] | null }
export interface Paragraph { id: string; sentences: Sentence[]; boxes?: Box[] | null }
export interface Section { id: string; title: string; level: number; paragraphs: Paragraph[] }
export interface Figure { id: string; label: string; section: string | null; caption: Sentence[]; boxes?: Box[] | null }
export interface Table extends Figure { rows: string[][] }
export interface Document {
  schema_version: string;
  title: string | null;
  source: { method: "parse" | "reconcile"; format: string | null; fulltexts: string[]; llm_call?: string | null };
  sections: Section[];
  figures: Figure[];
  tables: Table[];
}

export interface CheckResult { passed: boolean; checked_at: string; notes: string[] }
export type Origin =
  | { type: "local"; path: string }
  | { type: "url"; url: string; candidate: string; label: string }
  | { type: "conversion"; source: string; converter: string; detail: string }
  | { type: "upload"; filename: string }
  | { type: "fetched"; url: string; repo: string; id: string; label: string };
export type ResourceMeta =
  | { kind: "fulltext"; added_at: string; flavor: "pdf" | "tei" | "jats" | "docling" | "docx" | "md"; origin: Origin; check: CheckResult }
  | { kind: "extraction"; added_at: string; method: "parse" | "reconcile"; fulltexts: string[]; llm_call?: string | null }
  | { kind: "input"; added_at: string; role: Role; note: string; used: boolean; archive?: string | null; origin: Origin }
  | { kind: "script"; added_at: string; template: string; generated_hash: string }
  | { kind: "metadata" | "curation" | "export" | "schema" | "code" | "output" | "report" | "figure"; added_at: string };
export interface Resource {
  name: string;
  path: string;
  type: "file" | "json";
  format: string;
  mediatype: string;
  bytes: number;
  hash: string;
  jsonSchema?: string | null;
  reprocrowd: ResourceMeta;
}
export type Role = "data" | "code" | "other";
export const ROLES: Role[] = ["data", "code", "other"];

export interface AcquireLink { repo: string; id: string; kind: string; label: string; url: string }
/** A link whose repository couldn't be read (no `file`), or one file of it that couldn't be downloaded. */
export interface AcquireFailure { repo: string; id: string; url: string; code: string; error: string; file?: string; file_url?: string }
/** One file of a detected link; `present`: fetched before (same record and download URL), so a fetch skips it. */
export interface AcquireFile { repo: string; id: string; path: string; name: string; size: number; key: string; download_url: string; present?: boolean }
/** What an acquire would download (`size` 0: unknown); `limit_bytes` is the download limit, per link. */
export interface AcquirePreview { links: AcquireLink[]; files: AcquireFile[]; total_bytes: number; present_bytes?: number; limit_bytes: number; failures: AcquireFailure[] }
export interface AcquireResult { links: AcquireLink[]; resources: Resource[]; existing: Resource[]; failures: AcquireFailure[] }

export interface Identifier { scheme: string; value: string }
export interface PackageSummary {
  track?: "recreation" | null;
  id: string;
  path: string;
  title: string | null;
  identifier: Identifier | null;
  identifiers: Record<string, string>;
  metadata_fetched_at: string | null;
  resources: Record<string, number>;
  metadata_error?: string;
}

export interface Ref { extraction: string; id: string }
export type ResultType = "number" | "categorical" | "table" | "figure";
export const COMPARATORS = ["=", "<", "<=", ">", ">=", "~"] as const;
export type Comparator = (typeof COMPARATORS)[number];
interface Referenced { refs: Ref[]; unreferenced?: boolean; note?: string | null }
export interface Target extends Referenced {
  id: string;
  type: ResultType;
  description: string;
  printed?: string | null;
  expected?: number | string | string[][] | null;
  comparator?: Comparator | null;
  decimals?: number | null;
  unit?: string | null;
}
export interface Action extends Referenced {
  id: string;
  kind: "transform" | "analysis";
  summary: string;
  inputs: string[];
  depends_on: string[];
  produces: string[];
}
export type Item = Target | Action;

export interface LlmCall {
  id: string;
  task: string;
  profile: string;
  argv: string[];
  model: string;
  /** The model the CLI reports having run (Claude Code: claude-sonnet-5-5 for "sonnet"); null if it doesn't say. */
  model_used?: string | null;
  effort: string;
  template: "default" | "custom";
  instructions: string;
  prompt: string;
  output: string;
  output_format?: string;
  output_schema?: string | null;
  started_at: string;
  duration_ms: number;
  exit_code: number | null;
}
/** What a call would send (POST …:preview); nothing was run. */
export interface PromptPreview { available: boolean; output_protocol: string; exec_restriction: "tool_allowlist" | "cli_settings" | "unsupported" | "not_applicable"; timeout_s: number; structured_output: boolean; prompt: string; argv: string[]; profile: string; model: string; effort: string; output_schema?: string | null; env?: Record<string, string> }
export interface LlmEntry<T> { base: T; edit?: Partial<T> | null; disabled?: boolean }
export interface LlmRun<T> {
  id: string;
  disabled?: boolean;
  request: { extraction?: string; scope?: { sections?: string[]; items?: string[] }; instructions?: string; [k: string]: unknown };
  call: LlmCall;
  entries: LlmEntry<T>[];
  rejected?: { item: unknown; errors: string[] }[];
}
export interface CurationFile<T> { schema_version: string; kind: "targets" | "actions"; manual: T[]; llm_runs: LlmRun<T>[] }

export interface TemplateView { id: string; label: string; language: string; command: string; master: string }
export interface LlmProfile { available: boolean; name: string; default: boolean; argv: string[]; model: string; effort: string; timeout_s: number; output: string }
export interface AgentProfile extends LlmProfile { exec_restriction: "tool_allowlist" | "cli_settings" | "unsupported"; tools: string[]; exec_tools: string[]; events: string; no_exec_argv?: string[]; no_exec_env?: Record<string, string> }
export interface Converter { name: string; configured: boolean; output: string; detail: string }
export interface Candidate { id: string; label: string; url: string; format: string; filename: string; openalex_auth: boolean }

export interface TodoStatus { remaining: number; files: { path: string; remaining: number; lines: { line: number; text: string }[] }[] }
export interface ScriptState { template: string | null; command: string | null; master: string | null; agent_prompt: string | null; todo: TodoStatus | null; files: Resource[] }

export type JobStatus = "running" | "succeeded" | "failed" | "cancelled" | "timeout" | "interrupted";
export interface Job {
  id: string;
  type: "codegen" | "run";
  status: JobStatus;
  profile?: string | null;
  argv: string[];
  request: Record<string, unknown>;
  created_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  exit_code?: number | null;
  stdout: string;
  stderr: string;
  /** The model the agent CLI reports having run; null if it doesn't say. */
  model_used?: string | null;
  result?:
    | { type: "codegen"; commit_before?: string | null; commit_after?: string | null; changed_files: string[]; todo: TodoStatus; git_note?: string | null }
    | { type: "run"; command: string; log: string; results_file: boolean; output_errors: string[]; output_warnings: string[]; commit_after?: string | null }
    | null;
  error?: string | null;
}
export interface JobEvent { seq: number; kind: "start" | "text" | "tool_use" | "tool_result" | "result" | "log"; text: string; tool?: string | null; is_error?: boolean | null }

export type Status = "match" | "within" | "deviation" | "missing" | "figure" | "unparsable";
export interface Cell { expected: string; reproduced: string; status: Status; deviation?: number | null }
export interface CompareRow {
  target: string;
  type: ResultType;
  description: string;
  status: Status;
  expected_text: string;
  reproduced_text: string;
  deviation?: number | null;
  cells?: Cell[][] | null;
  matched?: number | null;
  total?: number | null;
  note?: string | null;
  precision?: "printed" | "explicit" | "inferred" | null;
  quantum?: number | null;
  refs: Ref[];
}
export interface CompareSettings { rounding: "reported" | number; threshold: number }
export interface ComparisonBasis { results_sha256: string; results_schema_sha256: string; created_at: string; policy: ComparePolicy }
export type ComparePolicy = "reprocrowd-compare/3" | "reprocrowd-compare/2";
export interface Comparison { policy: ComparePolicy | null; rounding_mode: "either_direction_at_ties" | "half_even_exact_binary" | null; basis?: ComparisonBasis | null; settings: CompareSettings; summary: Record<string, number>; rows: CompareRow[]; output_errors: string[]; output_warnings: string[] }

export interface FileEntry { path: string; size: number; modified: number; resource: string | null; kind: string | null }
export interface Issue { stage: string; resource?: string | null; message: string }
export interface ValidationReport { valid: boolean; errors: Issue[]; warnings: Issue[] }
export interface ReportResult { matched: number; within: number; path: string; comparison: string; summary: Record<string, number>; reproduced: string }
export interface ArchiveInfo { path: string; bytes: number; sha256: string; created_at: string; manifest_sha256: string; files: number }
export interface CropOutcome { target: string; ok: boolean; path?: string | null; page?: number | null; method?: "published" | "crop" | "page" | null; note?: string | null; error?: string | null; code?: string | null }
export interface FigureOriginal { target: string; path: string; method: "published" | "crop" | "page"; page?: number | null; source?: string | null }
export interface CoordinateReport { fulltext: string; format: string; has_coordinates: boolean; figures: number; tables: number; pdf: string | null; pdf_match: "verified" | "provenance" | "mismatch" | "unknown" | null }
export interface ZenodoDeposit {
  id: string;
  server: string;
  sandbox: boolean;
  deposition_id: number;
  status: "draft" | "published" | "discarded" | "gone";
  title: string;
  doi: string | null;
  url: string;
  file: string;
  sha256: string;
  archive_sha256: string;
  exclude: ("paper" | "inputs")[];
  excluded: string[];
  community: string | null;
  creators: { name: string; orcid?: string | null; affiliation?: string | null }[];
  note: string;
  created_at: string;
  published_at: string | null;
  checked_at: string | null;
}

export interface GithubPilot {
  id: string;
  repo: string;
  reproduction_id: string;
  run_key: string;
  version: number | null;
  /** partial: a step failed; submitted: waiting for the pilots repository to copy the files; published: copied */
  status: "partial" | "submitted" | "published";
  tag: string;
  issue_number: number | null;
  issue_url: string | null;
  files_repo: string;
  files_url: string | null;
  release_url: string | null;
  comment_url: string | null;
  archive_sha256: string;
  exclude: ("paper" | "inputs")[];
  excluded: string[];
  assets: { name: string; bytes: number; sha256: string; url: string | null }[];
  synthetic: boolean;
  note: string;
  error: string | null;
  created_at: string;
  published_at: string | null;
}
