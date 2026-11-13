// The UI runs in a sandboxed window; every call goes to the main process (src/main/api.ts) through the preload's
// bridge. Errors keep the backend's problem details (code, detail, extra members) so callers can react to specific
// problems.
import type { AcquireResult, PackageSummary, ZenodoDeposit } from "./types";

export interface Problem { status: number; code: string; title?: string; detail?: string; [k: string]: unknown }

export class ApiError extends Error {
  constructor(message: string, public problem?: Problem) {
    super(message);
  }
  get code() {
    return this.problem?.code;
  }
}

/** Codes meaning the backend can't be used at all (see main/service.ts); the window shows one banner for them. */
export const BACKEND_DOWN = new Set(["backend_start_failed", "backend_unreachable", "backend_timeout"]);
export const isBackendDown = (e: unknown) => e instanceof ApiError && BACKEND_DOWN.has(e.code ?? "");

interface Bridge {
  call(method: string, args: unknown[]): Promise<{ ok?: unknown; err?: string; problem?: Problem }>;
  pathForFile(file: File): string | null;
}
const bridge = (window as unknown as { reprocrowd: Bridge }).reprocrowd;

interface Reply<T> { status: number; etag: string | null; location: string | null; total: number | null; data: T }

async function call<T>(method: string, ...args: unknown[]): Promise<T> {
  const r = await bridge.call(method, args);
  if (r.err === undefined) return r.ok as T;
  const error = new ApiError(r.err, r.problem);
  if (isBackendDown(error)) window.dispatchEvent(new CustomEvent("reprocrowd-backend-down", { detail: { code: error.code, message: error.message } }));
  throw error;
}

type Query = Record<string, string | number | boolean>;
const data = async <T>(p: Promise<Reply<T>>) => (await p).data;

export interface Info { key: string; title: string; doi: string; folder: string; apiUrl: string }
export interface Creator { name: string; orcid: string; affiliation: string }
export interface BackendStatus {
  state: "stopped" | "starting" | "running" | "failed";
  url: string | null;
  command: string | null;
  kind: "custom" | "bundled" | "development" | null;
  pid: number | null;
  message: string | null;
  configFile: string;
  userConfigFile: string;
}
export interface Config {
  packagesRoot: string;
  editorCmd: string;
  zenodo: { sandbox: boolean; community: string; creator: Creator };
  backendCmd: string;
  checkUpdates: boolean;
  tokens: { zenodo: boolean; sandbox: boolean };
  secretStorage: "secure" | "weak" | "none";
  backend: BackendStatus;
  backendConfig: string;
  defaultBackendConfig: string;
}
export type BackendCheck = { ok: true; url: string; version: string } | { ok: false; url: string; code: string; message: string };
export type UpdateStatus =
  | { state: "idle"; current: string }
  | { state: "disabled"; current: string; reason: string }
  | { state: "checking"; current: string }
  | { state: "not-available"; current: string; note?: string }
  | { state: "downloading"; current: string; version: string; percent: number }
  | { state: "ready"; current: string; version: string }
  | { state: "manual"; current: string; version: string; url: string; howTo: string }
  | { state: "error"; current: string; message: string; detail?: string; url?: string };
export interface UpdateInfo { mode: "auto" | "notify" | "off"; explain: string; status: UpdateStatus }
export interface JatsAvailability { doi: string; available: boolean; pmcid: string | null; url: string | null; title: string | null; reason: string | null }
export const JATS_ONLY = "Only articles with JATS XML full text (the open-access subset of PubMed Central) are supported at this time.";
export interface Work { doi: string; title: string | null; authors: string[]; year: number | null; venue: string | null; license: string | null; open_access: boolean | null; type: string | null; refersTo: { doi: string; title: string | null } | null }
export interface ZenodoRecord { id: number | string | null; doi: string | null; title: string | null; creators: string[]; published: string | null; url: string }
export interface ZenodoAccount { sandbox: boolean; hasToken: boolean; creator: Creator; community: string }
export interface DepositPreview {
  metadata: Record<string, any>;
  contents: { groups: Record<string, { description: string; resources: string[]; bytes: number }>; paper_license: string | null; default_exclude: string[] };
  exclude: string[];
}
export type LibraryEntry = PackageSummary & { missing: boolean };

/** A file the user picked or dropped: by path (the backend runs on this computer), or its bytes if it has none. */
async function fileArg(file: File) {
  const path = bridge.pathForFile(file);
  return path ? { name: file.name, path } : { name: file.name, data: new Uint8Array(await file.arrayBuffer()) };
}

/** Requests below /v1/packages/{this window's package}: `sub` like "/targets" or ":sync". */
export const pkg = {
  get: <T>(sub: string, query?: Query) => data(call<Reply<T>>("pkg", "GET", sub, undefined, query)),
  post: <T>(sub: string, body: unknown = {}) => data(call<Reply<T>>("pkg", "POST", sub, body)),
  put: <T>(sub: string, body: unknown = {}) => call<Reply<T>>("pkg", "PUT", sub, body),
  patch: <T>(sub: string, body: unknown) => data(call<Reply<T>>("pkg", "PATCH", sub, body)),
  del: (sub: string) => call<Reply<null>>("pkg", "DELETE", sub),
  bytes: (sub: string) => call<ArrayBuffer>("pkgBytes", sub),
  text: (sub: string) => call<string>("pkgText", sub),
  upload: async <T>(kind: "inputs", file: File, fields: Record<string, string | boolean | undefined> = {}) => call<T>("upload", kind, await fileArg(file), fields),
  /** Scan a full text for data/code repository links (Zenodo, GitHub, GitLab, OSF, Figshare) and fetch them as inputs. */
  acquire: (opts: { fulltext?: string; role?: string; note?: string; unpack?: boolean; repos?: string[] } = {}) => pkg.post<AcquireResult>("/acquire", opts),
};

export const host = {
  // library
  appInfo: () => call<{ version: string; platform: string; backend: BackendStatus }>("appInfo"),
  checkForUpdates: () => call<UpdateStatus>("checkForUpdates"),
  updateInfo: () => call<UpdateInfo>("updateInfo"),
  installUpdate: () => call<void>("installUpdate"),
  listPackages: () => call<LibraryEntry[]>("listPackages"),
  packageDeposits: (id: string) => call<ZenodoDeposit[]>("packageDeposits", id),
  lookupDoi: (doi: string) => call<Work>("lookupDoi", doi),
  zenodoRecords: (doi: string) => call<ZenodoRecord[]>("zenodoRecords", doi),
  suggestFolder: (name: string) => call<string>("suggestFolder", name),
  chooseFolder: (defaultPath?: string) => call<string | null>("chooseFolder", defaultPath),
  jatsAvailability: (doi: string) => call<JatsAvailability>("jatsAvailability", doi),
  createPackage: (opts: { doi: string; folder: string }) => call<{ id: string }>("createPackage", opts),
  adoptFolder: () => call<string | null>("adoptFolder"),
  removePackage: (id: string) => call<void>("removePackage", id),
  revealPackage: (id: string) => call<void>("revealPackage", id),
  openPackage: (id: string) => call<string>("openPackage", id),
  openLibrary: () => call<void>("openLibrary"),
  openUrl: (url: string) => call<void>("openUrl", url),
  // settings and backend
  getConfig: () => call<Config>("getConfig"),
  setConfig: (patch: { packagesRoot?: string; editorCmd?: string; backendCmd?: string; checkUpdates?: boolean; zenodo?: { sandbox?: boolean; community?: string; creator?: Partial<Creator> } }) => call<void>("setConfig", patch),
  setZenodoToken: (server: "zenodo" | "sandbox", token: string | null) => call<void>("setZenodoToken", server, token),
  verifyZenodoToken: (sandbox: boolean, token?: string) => call<{ ok: boolean; url: string; sandbox: boolean }>("verifyZenodoToken", sandbox, token),
  saveBackendConfig: (text: string) => call<void>("saveBackendConfig", text),
  backendStatus: () => call<BackendStatus>("backendStatus"),
  backendLog: () => call<string>("backendLog"),
  restartBackend: () => call<BackendStatus>("restartBackend"),
  checkBackend: () => call<BackendCheck>("checkBackend"),
  // the window's package
  info: () => call<Info>("info"),
  connect: () => call<PackageSummary & { metadata_error?: string; jats: string }>("connect"),
  service: <T>(path: string, query?: Record<string, string>) => call<T>("service", path, query),
  addFiles: <T>(kind: "inputs", directory = false) => call<T[]>("addFiles", kind, directory),
  openInEditor: () => call<string>("openInEditor"),
  reveal: (rel = "") => call<void>("reveal", rel),
  loadState: () => call<any>("loadState"),
  saveState: (state: unknown) => call<void>("saveState", state),
  // Zenodo
  zenodoAccount: () => call<ZenodoAccount>("zenodoAccount"),
  zenodoStatus: () => call<{ url: string; sandbox: boolean; reachable: boolean; error: string | null }>("zenodoStatus"),
  depositPreview: (opts: { note: string; exclude?: string[] }) => call<DepositPreview>("depositPreview", opts),
  deposit: (opts: { note: string; exclude?: string[]; publish: boolean }) => call<ZenodoDeposit>("deposit", opts),
  depositAction: (id: string, action: "refresh" | "publish" | "discard") => call<ZenodoDeposit>("depositAction", id, action),
};

/** OpenAlex stores abstracts as an inverted index { word: [positions] }. */
export function reconstructAbstract(inv: Record<string, number[]> | null | undefined): string {
  if (!inv) return "";
  const words: string[] = [];
  for (const [w, ps] of Object.entries(inv)) for (const p of ps) words[p] = w;
  return words.filter((w) => w !== undefined).join(" ");
}
