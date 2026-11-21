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
  /** GitHub pilot submissions: the repository (owner/name) that receives them. The login is the GitHub CLI's. */
  github: { repo: string };
  /** Advanced: start the backend with this command instead of the bundled one ({host}, {port}). */
  backendCmd: string;
  /** Look for a new version of ReproCrowd a little after start. */
  checkUpdates: boolean;
  /** Where ReproCrowd can update itself: download new versions in the background, and install them on quit too. */
  autoUpdate: boolean;
}

export const userData = () => app.getPath("userData");
const settingsFile = () => path.join(userData(), "settings.json");
const secretsFile = () => path.join(userData(), "secrets.json");

export const DEFAULTS: Settings = {
  packagesRoot: path.join(os.homedir(), "ReproCrowd"),
  editorCmd: "",
  zenodo: { sandbox: true, community: "", creator: { name: "", orcid: "", affiliation: "" } },
  github: { repo: "forrtproject/reprocrowd-pilots" },
  backendCmd: "",
  checkUpdates: true,
  autoUpdate: false,
};

function readJson(file: string): any {
  try {
    if (process.platform !== "win32" && (fs.statSync(file).mode & 0o077) !== 0) {
      // Written 0644 by older versions. Best effort: a failed chmod must not make the file read as empty (setSecret would
      // then rewrite secrets.json without the other server's token).
      try {
        fs.chmodSync(file, 0o600);
      } catch {}
    }
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

/** On macOS and Linux only the user may read the app's settings (commands it runs, the GitHub repo) and secrets, whatever
 *  the umask. (On Windows the mode is ignored; the files are in the user's own AppData, which other users can't read.) */
function writeJson(file: string, data: unknown, mode = 0o600) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.rmSync(tmp, { force: true }); // a stale .tmp would keep its own mode: create it afresh
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode, flag: "wx" });
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
    github: { repo: str(s.github?.repo, "") || DEFAULTS.github.repo },
    backendCmd: str(s.backendCmd, ""),
    checkUpdates: typeof s.checkUpdates === "boolean" ? s.checkUpdates : DEFAULTS.checkUpdates,
    autoUpdate: typeof s.autoUpdate === "boolean" ? s.autoUpdate : DEFAULTS.autoUpdate,
  };
}

export function saveSettings(patch: Partial<Omit<Settings, "zenodo" | "github">> & { github?: Partial<Settings["github"]> } & { zenodo?: Partial<Omit<Settings["zenodo"], "creator">> & { creator?: Partial<Creator> } }) {
  const cur = loadSettings();
  const next: Settings = {
    ...cur,
    ...patch,
    zenodo: { ...cur.zenodo, ...patch.zenodo, creator: { ...cur.zenodo.creator, ...patch.zenodo?.creator } },
    github: { ...cur.github, ...patch.github },
  };
  next.github.repo = next.github.repo.trim() || DEFAULTS.github.repo;
  next.packagesRoot = next.packagesRoot.trim().replace(/^~(?=$|\/)/, os.homedir()) || DEFAULTS.packagesRoot;
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

// Tokens kept for this session only (no keyring, and the user declined to save them unprotected); gone on quit.
const sessionSecrets = new Map<string, string>();

export async function getSecret(key: string): Promise<string | undefined> {
  if (sessionSecrets.has(key)) return sessionSecrets.get(key);
  const v = readJson(secretsFile())[key];
  if (typeof v !== "string") return undefined;
  try {
    return (await available()) ? await decrypt(Buffer.from(v, "base64")) : undefined;
  } catch {
    return undefined;
  }
}

/** sessionOnly: keep the value in memory until the app quits, and drop any saved copy. */
export async function setSecret(key: string, value: string | null, { sessionOnly = false } = {}) {
  sessionSecrets.delete(key);
  const all = readJson(secretsFile());
  if (value && sessionOnly) {
    sessionSecrets.set(key, value);
    delete all[key];
  } else if (value) {
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
