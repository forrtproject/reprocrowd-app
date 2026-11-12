// HTTP client of the app's backend. Errors keep the backend's problem details (RFC 9457: code, detail, extra members);
// failures to use the backend at all get one of a few codes the UI shows as a banner:
//   backend_start_failed   the backend couldn't be started (with the end of its output)
//   backend_unreachable    it was running, but stopped answering (crashed, or being restarted)
//   backend_timeout        it accepted the request but didn't answer in time
import { ensure, StartError } from "./backend";

export interface Problem {
  type?: string;
  title?: string;
  status: number;
  detail?: string;
  code: string;
  [extra: string]: unknown;
}

export class ServiceError extends Error {
  constructor(message: string, public problem: Problem) {
    super(message);
  }
}

export interface Reply {
  status: number;
  etag: string | null;
  location: string | null;
  total: number | null;
  data: unknown;
}

export type ResponseType = "json" | "text" | "bytes";

export interface RequestOptions {
  json?: unknown;
  form?: { file: { name: string; data: Uint8Array }; fields?: Record<string, string | boolean | undefined> };
  query?: Record<string, string | number | boolean | undefined | null>;
  responseType?: ResponseType;
  timeout?: number;
}

const fail = (code: string, message: string, extra: Record<string, unknown> = {}): never => {
  throw new ServiceError(message, { status: 0, code, ...extra });
};

export async function request(method: string, path: string, opts: RequestOptions = {}): Promise<Reply> {
  if (!path.startsWith("/v1/")) throw new Error(`not a backend path: ${path}`);
  let base: string, token: string;
  try {
    ({ base, token } = await ensure());
  } catch (e) {
    return fail("backend_start_failed", e instanceof StartError ? e.message : String(e));
  }
  const url = new URL(base + path);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  const headers: Record<string, string> = { Accept: "application/json, application/problem+json;q=0.9, */*;q=0.5", Authorization: `Bearer ${token}` };
  let body: BodyInit | undefined;
  if (opts.form) {
    const fd = new FormData();
    fd.append("file", new Blob([opts.form.file.data as Uint8Array<ArrayBuffer>]), opts.form.file.name);
    for (const [k, v] of Object.entries(opts.form.fields ?? {})) if (v !== undefined) fd.append(k, String(v));
    body = fd;
  } else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  let r: Response;
  try {
    r = await fetch(url, { method, headers, body, signal: AbortSignal.timeout(opts.timeout ?? 120_000) });
  } catch (e) {
    if ((e as Error)?.name === "TimeoutError") return fail("backend_timeout", "The backend accepted the request but didn't answer in time.");
    return fail("backend_unreachable", "The backend stopped answering. It is started again with the next request.");
  }
  const type = r.headers.get("content-type") ?? "";
  if (!r.ok) {
    const p = type.includes("json") ? await r.json().catch(() => undefined) : undefined;
    const problem: Problem = p && typeof p.code === "string" ? { ...p, status: r.status } : { status: r.status, code: "backend_error", detail: (await r.text().catch(() => "")).slice(0, 500) };
    throw new ServiceError(problem.detail || problem.title || `HTTP ${r.status}`, problem);
  }
  const rt = opts.responseType ?? "json";
  const data = r.status === 204 ? null : rt === "bytes" ? await r.arrayBuffer() : rt === "text" ? await r.text() : await r.json();
  const total = r.headers.get("x-total-count");
  return { status: r.status, etag: r.headers.get("etag"), location: r.headers.get("location"), total: total === null ? null : Number(total), data };
}

export const get = async <T>(path: string, query?: RequestOptions["query"], timeout?: number) => (await request("GET", path, { query, timeout })).data as T;
export const post = async <T>(path: string, json: unknown = {}, timeout?: number) => (await request("POST", path, { json, timeout })).data as T;
