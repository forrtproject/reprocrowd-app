// What the UI may ask of the main process (renderer → preload → ipc "call"). Two scopes:
//   library   listing, creating and opening packages, settings, the backend, Zenodo account
//   package   requests to the one package the window shows (bound by openPackage), like a Zotero tab of the plugin
// Package requests are restricted to that package and a few read-only backend endpoints. The Zenodo token is added
// here, never handed to the renderer.
import { BrowserWindow, dialog, shell, WebContents } from "electron";
import fs from "node:fs";
import path from "node:path";
import * as backend from "./backend";
import { spawnDetached, splitCommand, which } from "./processes";
import { get, post, request, ResponseType, ServiceError } from "./service";
import { loadSettings, saveSettings, secretStorageKind, serverOf, setSecret, getSecret, userData, zenodoKey, ZenodoServer } from "./settings";
import * as updater from "./updater";

/** Read-only backend endpoints any window may call. */
const GLOBAL_GETS = new Set(["/v1/health", "/v1/templates", "/v1/llm-profiles", "/v1/agent-profiles", "/v1/zenodo"]);
export const JATS_ONLY = "Only articles with JATS XML full text (the open-access subset of PubMed Central) are supported at this time.";
const EDITORS = ["code", "codium", "cursor", "zed", "subl", "xdg-open"];
const LONG = 3 * 3600_000; // LLM calls and conversions are synchronous requests bounded by the backend's own timeouts

const bound = new Map<number, string>(); // webContents id → package id
const enc = encodeURIComponent;
const norm = (p: string) => path.resolve(p).replace(/[\\/]+$/, "");
const stateFile = (id: string) => path.join(userData(), "ui-state", `${id.replace(/[^\w.-]/g, "_")}.json`);

export const bindPackage = (wc: WebContents, id: string | null) => (id ? bound.set(wc.id, id) : bound.delete(wc.id));
export const boundPackage = (wc: WebContents) => bound.get(wc.id);

function pkgId(wc: WebContents): string {
  const id = bound.get(wc.id);
  if (!id) throw new Error("This window shows no package.");
  return id;
}

export function slug(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/^https?:\/\/(dx\.)?doi\.org\//, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "paper"
  );
}

export function normalizeDoi(raw: string): string {
  const s = raw.trim().replace(/^doi:\s*/i, "").replace(/^https?:\/\/(dx\.)?doi\.org\//i, "");
  return /^10\.\d{4,9}\/\S+$/.test(s) ? s.toLowerCase() : "";
}

function zenodoToken(sandbox: boolean) {
  return getSecret(zenodoKey(serverOf(sandbox)));
}

function creators() {
  const c = loadSettings().zenodo.creator;
  if (!c.name.trim()) return undefined;
  return [{ name: c.name.trim(), ...(c.orcid.trim() ? { orcid: c.orcid.trim() } : {}), ...(c.affiliation.trim() ? { affiliation: c.affiliation.trim() } : {}) }];
}

async function openOutside(url: string) {
  if (!/^https?:\/\//i.test(url)) throw new Error(`not a web address: ${url}`);
  await shell.openExternal(url);
}

/** A free folder for a new package under the packages folder. */
function suggestFolder(name: string): string {
  const root = loadSettings().packagesRoot;
  let p = path.join(root, slug(name));
  for (let i = 2; fs.existsSync(p) && fs.readdirSync(p).length; i++) p = path.join(root, `${slug(name)}-${i}`);
  return p;
}

export interface Work {
  doi: string;
  title: string | null;
  authors: string[];
  year: number | null;
  venue: string | null;
  license: string | null;
  open_access: boolean | null;
}

/** The paper's OpenAlex record, to confirm a DOI before creating the package. */
async function lookupDoi(raw: string): Promise<Work> {
  const doi = normalizeDoi(raw);
  if (!doi) throw new Error(`“${raw.trim()}” doesn't look like a DOI (10.xxxx/…).`);
  let r: Response;
  try {
    r = await fetch(`https://api.openalex.org/works/doi:${encodeURIComponent(doi)}`, { signal: AbortSignal.timeout(15_000) });
  } catch (e) {
    throw new Error(`Couldn't reach OpenAlex: ${(e as Error).message}`);
  }
  if (r.status === 404) throw new Error(`OpenAlex has no work with the DOI ${doi}.`);
  if (!r.ok) throw new Error(`OpenAlex answered HTTP ${r.status}.`);
  const w = await r.json();
  const loc = w.best_oa_location ?? w.primary_location ?? {};
  return {
    doi,
    title: w.title ?? null,
    authors: (w.authorships ?? []).map((a: any) => a.author?.display_name).filter(Boolean),
    year: typeof w.publication_year === "number" ? w.publication_year : null,
    venue: w.primary_location?.source?.display_name ?? null,
    license: loc.license ?? null,
    open_access: w.open_access?.is_oa ?? null,
  };
}

async function uploadPath(id: string, file: string, fields: Record<string, unknown> = {}) {
  return (await request("POST", `/v1/packages/${enc(id)}/inputs`, { json: { path: file, ...fields }, timeout: LONG })).data;
}

/**
 * The paper's text: its JATS XML from Europe PMC, parsed into the common document schema. Returns the extraction's name,
 * retrieving and parsing the XML first if the package has none yet. Throws `jats_unavailable` if there is no JATS XML.
 */
export async function ensureJats(id: string): Promise<string> {
  const P = `/v1/packages/${enc(id)}`;
  const [fulltexts, extractions] = await Promise.all([get<any[]>(`${P}/fulltexts`), get<any[]>(`${P}/extractions`)]);
  const jatsFt = fulltexts.filter((f) => f.reprocrowd?.flavor === "jats").map((f) => f.name as string);
  const parsed = extractions.find((x) => x.reprocrowd?.method === "parse" && (x.reprocrowd.fulltexts ?? []).some((n: string) => jatsFt.includes(n)));
  if (parsed) return parsed.name;
  let ft = jatsFt[0];
  if (!ft) {
    const { candidates, notes } = await get<{ candidates: any[]; notes: string[] }>(`${P}/fulltexts/candidates`, undefined, 60_000);
    const c = candidates.find((x) => x.format === "jats");
    if (!c) throw new ServiceError(`${JATS_ONLY} ${notes.find((n) => /PMCID/.test(n)) ?? "There is no JATS XML for this paper."}`, { status: 409, code: "jats_unavailable" });
    try {
      ft = ((await request("POST", `${P}/fulltexts`, { json: { candidate: c.id, name: "ft-jats" }, timeout: 300_000 })).data as any).name;
    } catch (e) {
      const code = (e as ServiceError).problem?.code;
      if (code === "download_failed" || code === "integrity_check_failed") throw new ServiceError(`${JATS_ONLY} Europe PMC has no JATS XML for this paper (${(e as Error).message}).`, { status: 409, code: "jats_unavailable" });
      throw e;
    }
  }
  return ((await request("POST", `${P}/extractions`, { json: { method: "parse", fulltext: ft }, timeout: 300_000 })).data as any).name;
}

export function makeApi(wc: WebContents) {
  const win = () => BrowserWindow.fromWebContents(wc)!;
  const base = () => `/v1/packages/${enc(pkgId(wc))}`;

  async function pkg(method: string, sub: string, json?: unknown, query?: Record<string, string | number | boolean>, responseType: ResponseType = "json") {
    if (!/^([/:][\w./:%-]*)?$/.test(sub) || sub.split("/").includes("..")) throw new Error(`invalid package path ${sub}`);
    if (!["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) throw new Error(`invalid method ${method}`);
    if (method !== "GET" && /^\/(fulltexts|extractions)\b/.test(sub)) throw new Error("The paper's text is its JATS XML, retrieved by the app.");
    return request(method, base() + sub, { json, query, responseType, timeout: method === "GET" ? 120_000 : LONG });
  }

  async function summary(id = pkgId(wc)) {
    return get<any>(`/v1/packages/${enc(id)}`);
  }

  async function folder() {
    return (await summary()).path as string;
  }

  return {
    // ---------- library ----------
    appInfo: async () => ({ version: (await import("electron")).app.getVersion(), platform: process.platform, backend: backend.status() }),
    checkForUpdates: () => updater.checkForUpdates(),
    listPackages: async () => {
      const all = await get<any[]>("/v1/packages");
      return all.map((p) => ({ ...p, missing: !fs.existsSync(path.join(p.path, "datapackage.json")) }));
    },
    packageDeposits: async (id: string) => get<any[]>(`/v1/packages/${enc(id)}/deposits`),
    lookupDoi,
    jatsAvailability: async (doi: string) => get<any>("/v1/jats", { doi }, 60_000),
    zenodoRecords: async (doi: string) => get<any[]>("/v1/zenodo/records", { doi, sandbox: loadSettings().zenodo.sandbox }, 20_000),
    suggestFolder: async (name: string) => suggestFolder(name),
    chooseFolder: async (defaultPath?: string) => {
      const r = await dialog.showOpenDialog(win(), { title: "Folder for the package", defaultPath, properties: ["openDirectory", "createDirectory"] });
      return r.canceled ? null : r.filePaths[0];
    },
    /** A package for the paper's DOI, with its JATS XML retrieved and parsed. Undone if that fails. */
    createPackage: async (opts: { doi: string; folder: string }) => {
      const doi = normalizeDoi(opts.doi);
      if (!doi) throw new Error("A DOI is needed: the paper's JATS XML is found through it.");
      const target = path.resolve(opts.folder);
      const existed = fs.existsSync(target);
      if (existed && fs.readdirSync(target).length) throw new Error(`${target} isn't empty; choose another folder.`);
      const created = (await request("POST", "/v1/packages", { json: { path: target, identifier: doi }, timeout: 60_000 })).data as any;
      try {
        await request("POST", `/v1/packages/${enc(created.id)}/metadata`, { json: {}, timeout: 60_000 });
        await ensureJats(created.id);
      } catch (e) {
        await request("DELETE", `/v1/packages/${enc(created.id)}`).catch(() => {});
        if (existed) for (const f of fs.readdirSync(target)) fs.rmSync(path.join(target, f), { recursive: true, force: true });
        else fs.rmSync(target, { recursive: true, force: true });
        throw e;
      }
      return { id: created.id as string };
    },
    adoptFolder: async () => {
      const r = await dialog.showOpenDialog(win(), { title: "Open a package folder (with datapackage.json)", properties: ["openDirectory"] });
      if (r.canceled) return null;
      const dir = r.filePaths[0];
      if (!fs.existsSync(path.join(dir, "datapackage.json"))) throw new Error(`${dir} has no datapackage.json, so it isn't a ReproCrowd package.`);
      const known = (await get<any[]>("/v1/packages")).find((p) => norm(p.path) === norm(dir));
      if (known) return known.id as string;
      return ((await request("POST", "/v1/packages", { json: { path: dir, adopt: true } })).data as any).id as string;
    },
    removePackage: async (id: string) => {
      await request("DELETE", `/v1/packages/${enc(id)}`);
    },
    revealPackage: async (id: string) => shell.showItemInFolder(path.join((await summary(id)).path, "datapackage.json")),
    openPackage: async (id: string) => {
      await summary(id); // exists
      bindPackage(wc, id);
      return id;
    },
    openLibrary: async () => {
      bindPackage(wc, null);
    },
    openUrl: openOutside,

    // ---------- settings, backend, Zenodo account ----------
    getConfig: async () => {
      const s = loadSettings();
      return {
        ...s,
        tokens: { zenodo: !!(await getSecret(zenodoKey("zenodo"))), sandbox: !!(await getSecret(zenodoKey("sandbox"))) },
        secretStorage: await secretStorageKind(),
        backend: backend.status(),
        backendConfig: backend.readUserConfig(),
        defaultBackendConfig: backend.DEFAULT_USER_CONFIG,
      };
    },
    setConfig: async (patch: Parameters<typeof saveSettings>[0]) => {
      const before = loadSettings();
      const after = saveSettings(patch);
      // The backend gets the Zenodo choices and its start command at start.
      if (before.backendCmd !== after.backendCmd || before.zenodo.sandbox !== after.zenodo.sandbox || before.zenodo.community !== after.zenodo.community) await backend.restart().catch(() => {});
    },
    setZenodoToken: async (server: ZenodoServer, token: string | null) => setSecret(zenodoKey(server), token?.trim() || null),
    verifyZenodoToken: async (sandbox: boolean, token?: string) => {
      const t = token?.trim() || (await zenodoToken(sandbox));
      if (!t) throw new ServiceError("No token stored for this server.", { status: 400, code: "zenodo_token_missing" });
      return post("/v1/zenodo:verify-token", { zenodo_token: t, sandbox }, 20_000);
    },
    saveBackendConfig: async (text: string) => {
      backend.writeUserConfig(text);
      await backend.restart();
    },
    backendStatus: async () => backend.status(),
    backendLog: async () => backend.logTail(200),
    restartBackend: async () => {
      await backend.restart();
      return backend.status();
    },
    checkBackend: async () => {
      try {
        const h = await get<{ version: string }>("/v1/health", undefined, 5000);
        return { ok: true, url: backend.status().url ?? "", version: h.version };
      } catch (e) {
        return { ok: false, url: "", code: (e as ServiceError).problem?.code ?? "backend_error", message: (e as Error).message };
      }
    },

    // ---------- the window's package ----------
    info: async () => {
      const s = await summary();
      const doi = s.identifiers?.doi ?? (s.identifier?.scheme === "doi" ? s.identifier.value : "");
      return { key: s.id, title: s.title ?? path.basename(s.path), doi, folder: s.path, apiUrl: backend.status().url ?? "" };
    },
    connect: async () => {
      let s = await summary();
      const jats = await ensureJats(s.id);
      let metadata_error: string | undefined;
      const doi = s.identifier?.scheme === "doi" ? s.identifier.value : "";
      if (doi && !s.metadata_fetched_at) {
        try {
          await pkg("POST", "/metadata", {});
          s = await summary();
        } catch (e) {
          metadata_error = (e as Error).message;
        }
      }
      return { ...s, metadata_error, jats };
    },
    pkg,
    pkgBytes: async (sub: string) => (await pkg("GET", sub, undefined, undefined, "bytes")).data as ArrayBuffer,
    pkgText: async (sub: string) => (await pkg("GET", sub, undefined, undefined, "text")).data as string,
    service: async <T>(p: string, query?: Record<string, string>) => {
      if (!GLOBAL_GETS.has(p)) throw new Error(`not allowed: ${p}`);
      return get<T>(p, query);
    },
    /** A file from the renderer: by its path on disk (the backend runs here), or its bytes. */
    /** Input files from the renderer: by path on disk (the backend runs here), or their bytes. */
    upload: async (kind: "inputs", file: { name: string; path?: string; data?: Uint8Array }, fields: Record<string, string | boolean | undefined> = {}) => {
      if (kind !== "inputs") throw new Error("only input files are added from this computer");
      const clean = Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined && v !== ""));
      if (file.path) return uploadPath(pkgId(wc), file.path, clean);
      if (!file.data) throw new Error("no file content");
      return (await request("POST", `${base()}/inputs`, { form: { file: { name: file.name, data: file.data }, fields: clean }, timeout: LONG })).data;
    },
    addFiles: async (kind: "inputs", directory = false) => {
      if (kind !== "inputs") throw new Error("only input files are added from this computer");
      const r = await dialog.showOpenDialog(win(), { title: directory ? "Add a folder of input files" : "Add input files", properties: directory ? ["openDirectory"] : ["openFile", "multiSelections"] });
      if (r.canceled) return [];
      const out = [];
      for (const p of r.filePaths) out.push(await uploadPath(pkgId(wc), p));
      return out;
    },
    openInEditor: async () => {
      let cmd = loadSettings().editorCmd.trim();
      if (!cmd) cmd = EDITORS.find((c) => which(c)) ?? "";
      if (!cmd) throw new Error("No code editor found; set one in Settings.");
      const argv = splitCommand(cmd);
      const dir = await folder();
      spawnDetached([...argv, dir], dir);
      return argv[0];
    },
    reveal: async (rel = "") => {
      const parts = rel.split("/").filter(Boolean);
      if (parts.some((p) => p === ".." || p.includes("\\"))) throw new Error(`invalid path ${rel}`);
      const dir = await folder();
      if (parts.length) shell.showItemInFolder(path.join(dir, ...parts));
      else shell.openPath(dir);
    },
    loadState: async () => {
      try {
        return JSON.parse(fs.readFileSync(stateFile(pkgId(wc)), "utf8"));
      } catch {
        return null;
      }
    },
    saveState: async (state: unknown) => {
      const f = stateFile(pkgId(wc));
      fs.mkdirSync(path.dirname(f), { recursive: true });
      fs.writeFileSync(f, JSON.stringify(state, null, 2));
    },

    // ---------- Zenodo, for the window's package ----------
    zenodoAccount: async () => {
      const s = loadSettings();
      return { sandbox: s.zenodo.sandbox, hasToken: !!(await zenodoToken(s.zenodo.sandbox)), creator: s.zenodo.creator, community: s.zenodo.community };
    },
    zenodoStatus: async () => get<any>("/v1/zenodo", { sandbox: loadSettings().zenodo.sandbox }, 10_000),
    depositPreview: async (opts: { note: string; exclude?: string[] }) =>
      post("/v1/packages/" + enc(pkgId(wc)) + "/deposits:preview", { note: opts.note, exclude: opts.exclude, creators: creators(), community: loadSettings().zenodo.community }, 60_000),
    deposit: async (opts: { note: string; exclude?: string[]; publish: boolean }) => {
      const s = loadSettings();
      const token = await zenodoToken(s.zenodo.sandbox);
      if (!token) throw new ServiceError(`No Zenodo token for ${s.zenodo.sandbox ? "the sandbox" : "zenodo.org"}; add one in Settings.`, { status: 400, code: "zenodo_token_missing" });
      const r = await request("POST", `${base()}/deposits`, {
        json: { zenodo_token: token, sandbox: s.zenodo.sandbox, note: opts.note, exclude: opts.exclude, creators: creators(), community: s.zenodo.community, publish: opts.publish },
        timeout: LONG,
      });
      return r.data;
    },
    depositAction: async (dep: string, action: "refresh" | "publish" | "discard") => {
      if (!["refresh", "publish", "discard"].includes(action) || !/^zen-[0-9a-f]+$/.test(dep)) throw new Error("invalid deposit action");
      const d = await get<any>(`${base()}/deposits/${dep}`);
      const token = await zenodoToken(d.sandbox);
      if (!token) throw new ServiceError(`No Zenodo token for ${d.sandbox ? "the sandbox" : "zenodo.org"}; add one in Settings.`, { status: 400, code: "zenodo_token_missing" });
      return post(`${base()}/deposits/${dep}:${action}`, { zenodo_token: token }, 600_000);
    },
  };
}

export type Api = ReturnType<typeof makeApi>;
