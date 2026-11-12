// App settings (userData/settings.json) and secrets (userData/secrets.json, encrypted with the OS keychain through
// Electron's safeStorage). The Zenodo tokens are secrets; one per server, since zenodo.org and its sandbox have
// separate accounts.
import { app, safeStorage } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface Creator {
  name: string; // "Family, Given"
  orcid: string;
  affiliation: string;
}

export interface Settings {
  /** Where new packages are created (one folder per paper). */
  packagesRoot: string;
  editorCmd: string;
  zenodo: {
    sandbox: boolean; // deposit to sandbox.zenodo.org (testing) instead of zenodo.org
    community: string;
    creator: Creator;
  };
  /** Advanced: start the backend with this command instead of the bundled one ({host}, {port}). */
  backendCmd: string;
  /** LLM models (from OpenCode / T3 Code) the user turned off, so they don't accidentally spend tokens on them. */
  llmDisabled: string[];
}

export const userData = () => app.getPath("userData");
const settingsFile = () => path.join(userData(), "settings.json");
const secretsFile = () => path.join(userData(), "secrets.json");

export const DEFAULTS: Settings = {
  packagesRoot: path.join(os.homedir(), "ReproCrowd"),
  editorCmd: "",
  zenodo: { sandbox: true, community: "", creator: { name: "", orcid: "", affiliation: "" } },
  backendCmd: "",
  llmDisabled: [],
};

function readJson(file: string): any {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

function writeJson(file: string, data: unknown, mode = 0o644) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode });
  fs.renameSync(tmp, file);
}

const str = (v: unknown, d: string) => (typeof v === "string" ? v : d);

export function loadSettings(): Settings {
  const s = readJson(settingsFile());
  const z = s.zenodo ?? {};
  const c = z.creator ?? {};
  return {
    packagesRoot: str(s.packagesRoot, "") || DEFAULTS.packagesRoot,
    editorCmd: str(s.editorCmd, ""),
    zenodo: {
      sandbox: typeof z.sandbox === "boolean" ? z.sandbox : DEFAULTS.zenodo.sandbox,
      community: str(z.community, ""),
      creator: { name: str(c.name, ""), orcid: str(c.orcid, ""), affiliation: str(c.affiliation, "") },
    },
    backendCmd: str(s.backendCmd, ""),
    llmDisabled: Array.isArray(s.llmDisabled) ? s.llmDisabled.map(String) : [],
  };
}

export function saveSettings(patch: Partial<Omit<Settings, "zenodo">> & { zenodo?: Partial<Omit<Settings["zenodo"], "creator">> & { creator?: Partial<Creator> } }) {
  const cur = loadSettings();
  const next: Settings = {
    ...cur,
    ...patch,
    zenodo: { ...cur.zenodo, ...patch.zenodo, creator: { ...cur.zenodo.creator, ...patch.zenodo?.creator } },
  };
  next.packagesRoot = next.packagesRoot.trim().replace(/^~(?=$|\/)/, os.homedir()) || DEFAULTS.packagesRoot;
  next.llmDisabled = Array.isArray(next.llmDisabled) ? next.llmDisabled.filter(Boolean) : [];
  writeJson(settingsFile(), next);
  return next;
}

// ---------- secrets ----------

export type ZenodoServer = "zenodo" | "sandbox";

// Electron >= 40 initialises the keyring asynchronously; the synchronous API reports "unavailable" until it's ready.
const ss = safeStorage as typeof safeStorage & {
  isAsyncEncryptionAvailable?: () => Promise<boolean>;
  encryptStringAsync?: (s: string) => Promise<Buffer>;
  decryptStringAsync?: (b: Buffer) => Promise<{ result: string; shouldReEncrypt: boolean }>;
  getSelectedStorageBackend?: () => string;
};
const available = async () => (ss.isAsyncEncryptionAvailable ? ss.isAsyncEncryptionAvailable() : safeStorage.isEncryptionAvailable());
const encrypt = async (v: string) => (ss.encryptStringAsync ? ss.encryptStringAsync(v) : safeStorage.encryptString(v));
const decrypt = async (b: Buffer) => (ss.decryptStringAsync ? (await ss.decryptStringAsync(b)).result : safeStorage.decryptString(b));

export async function getSecret(key: string): Promise<string | undefined> {
  const v = readJson(secretsFile())[key];
  if (typeof v !== "string") return undefined;
  try {
    return (await available()) ? await decrypt(Buffer.from(v, "base64")) : undefined;
  } catch {
    return undefined;
  }
}

export async function setSecret(key: string, value: string | null) {
  const all = readJson(secretsFile());
  if (value) {
    if (!(await available())) throw new Error("This system offers no secure storage for the token (no keyring).");
    all[key] = (await encrypt(value)).toString("base64");
  } else delete all[key];
  writeJson(secretsFile(), all, 0o600);
}

export const zenodoKey = (server: ZenodoServer) => `zenodo-token:${server}`;
export const serverOf = (sandbox: boolean): ZenodoServer => (sandbox ? "sandbox" : "zenodo");

/** "weak" when Linux has no keyring and Electron falls back to a fixed key. */
export async function secretStorageKind(): Promise<"secure" | "weak" | "none"> {
  if (!(await available())) return "none";
  if (process.platform === "linux" && ss.getSelectedStorageBackend?.() === "basic_text") return "weak";
  return "secure";
}
