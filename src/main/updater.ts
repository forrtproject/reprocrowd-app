// Updates from the GitHub Releases of forrtproject/reprocrowd-app (the `publish` config in electron-builder.yml, which
// electron-builder writes into resources/app-update.yml).
//
// How an update arrives depends on how ReproCrowd was installed:
//   auto    Windows (NSIS installer) and Linux AppImage: electron-updater downloads the new version in the background
//           and installs it on restart (or when the app quits).
//   notify  macOS (the builds aren't signed with an Apple Developer ID, and macOS only lets signed apps replace
//           themselves) and Linux installs made by scripts/install.sh: we read the latest release from the GitHub API
//           and tell the user where to get it.
//   off     a development checkout, or REPROCROWD_UPDATES=off.
// An auto check that can't run (the release lacks this platform's latest*.yml, or electron-updater reports itself
// inactive) falls back to the notify check, so the user still learns about a new version. Nothing here waits without
// a timeout, and every failure ends as a status with a readable message.
//
// Testing: REPROCROWD_UPDATE_SERVER=http://host:port serves electron-updater's generic feed (latest*.yml and the
// installers) and GET /releases/latest in the GitHub API's shape; it also enables the notify check in a development
// checkout.
import { app, BrowserWindow, dialog, net, shell } from "electron";
import { autoUpdater, ProgressInfo, UpdateInfo } from "electron-updater";
import fs from "node:fs";
import path from "node:path";
import { loadSettings, saveSettings } from "./settings";

export type UpdateMode = "auto" | "notify" | "off";
export type UpdateStatus =
  | { state: "idle"; current: string }
  | { state: "disabled"; current: string; reason: string }
  | { state: "checking"; current: string }
  | { state: "not-available"; current: string; note?: string }
  | { state: "downloading"; current: string; version: string; percent: number }
  | { state: "ready"; current: string; version: string }
  | { state: "manual"; current: string; version: string; url: string; howTo: string }
  | { state: "error"; current: string; message: string; detail?: string; url?: string };
export interface UpdateInfoForUi {
  mode: UpdateMode;
  /** One sentence on how updates work in this installation. */
  explain: string;
  status: UpdateStatus;
}

const CHECK_TIMEOUT = 30_000;
const STARTUP_DELAY = 15_000;
const server = () => process.env.REPROCROWD_UPDATE_SERVER?.replace(/\/+$/, "") || "";
const current = () => app.getVersion();

// ---------- where the releases are ----------

function githubRepo(): { owner: string; repo: string } {
  try {
    const yml = fs.readFileSync(path.join(process.resourcesPath, "app-update.yml"), "utf8");
    const owner = yml.match(/^owner:\s*(\S+)/m)?.[1];
    const repo = yml.match(/^repo:\s*(\S+)/m)?.[1];
    if (owner && repo) return { owner, repo };
  } catch {}
  return { owner: "forrtproject", repo: "reprocrowd-app" };
}
const releasesPage = () => (server() ? `${server()}/releases` : `https://github.com/${githubRepo().owner}/${githubRepo().repo}/releases/latest`);
const latestReleaseApi = () => (server() ? `${server()}/releases/latest` : `https://api.github.com/repos/${githubRepo().owner}/${githubRepo().repo}/releases/latest`);

// ---------- what this installation can do ----------

const installedByScript = () => process.platform === "linux" && /[/\\]reprocrowd[/\\]app[/\\]/.test(process.execPath);

export function updateMode(): { mode: UpdateMode; explain: string } {
  if (/^(0|off|false|no)$/i.test(process.env.REPROCROWD_UPDATES ?? "")) return { mode: "off", explain: "Updates are turned off (REPROCROWD_UPDATES=off)." };
  if (!app.isPackaged) {
    return server()
      ? { mode: "notify", explain: "A development checkout: new versions are reported, but not installed." }
      : { mode: "off", explain: "This is a development checkout; update it with git. Installed copies of ReproCrowd update from GitHub Releases." };
  }
  if (process.platform === "win32") return { mode: "auto", explain: "New versions download in the background and install when you restart ReproCrowd." };
  if (process.platform === "linux" && process.env.APPIMAGE) return { mode: "auto", explain: "New versions download in the background and replace this AppImage when you restart ReproCrowd." };
  if (process.platform === "darwin") return { mode: "notify", explain: "On macOS, ReproCrowd tells you about new versions; install them by downloading the new .dmg." };
  if (installedByScript()) return { mode: "notify", explain: "ReproCrowd tells you about new versions; this copy was installed with scripts/install.sh, so update it by running that again." };
  return { mode: "notify", explain: "ReproCrowd tells you about new versions; install them by downloading the new AppImage." };
}

function howTo(): string {
  if (process.platform === "darwin") return "Download the .dmg from the release page, open it, and drag ReproCrowd into Applications, replacing the old copy. Your reproductions and settings stay.";
  if (installedByScript()) return "In your ReproCrowd source folder, run:  git pull && scripts/install.sh   Your reproductions and settings stay.";
  if (process.platform === "win32") return "Download the Setup .exe from the release page and run it. Your reproductions and settings stay.";
  return "Download the .AppImage from the release page and use it instead of this copy. Your reproductions and settings stay.";
}

// ---------- status ----------

let status: UpdateStatus = { state: "idle", current: "" };
let inFlight: Promise<UpdateStatus> | null = null;
const set = (s: UpdateStatus) => (status = s);
export const updateInfo = (): UpdateInfoForUi => ({ ...updateMode(), status: status.state === "idle" ? { state: "idle", current: current() } : status });

// ---------- errors people can act on ----------

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err ?? "unknown error"));

function describe(err: unknown): { message: string; detail: string } {
  const detail = errText(err);
  const d = detail.toLowerCase();
  const say = (message: string) => ({ message, detail });
  if (/err_internet_disconnected|enotfound|eai_again|err_name_not_resolved|econnrefused|econnreset|err_connection|err_network_changed|err_proxy|enetunreach|socket hang up/.test(d))
    return say("Couldn't reach GitHub to check for updates. Check your internet connection (or proxy) and try again.");
  if (/timed? ?out|etimedout|err_timed_out/.test(d)) return say("GitHub didn't answer in time. Try again later.");
  if (/rate limit|\b403\b|\b429\b/.test(d)) return say("GitHub is limiting requests from your network right now. Try again in an hour.");
  if (/sha512 checksum mismatch|checksum/.test(d)) return say("The downloaded update was damaged in transit. Check for updates again to retry.");
  if (/eacces|eperm|erofs|access is denied|operation not permitted/.test(d)) return say("ReproCrowd isn't allowed to replace its own files here. Download the new version from the release page instead.");
  if (/enospc|no space/.test(d)) return say("There isn't enough disk space to download the update.");
  return say("Couldn't check for updates.");
}
const errorStatus = (err: unknown): UpdateStatus => ({ state: "error", current: current(), ...describe(err), url: releasesPage() });
// The release exists but has no update file for this platform (only some platforms were built), or none at all.
const noFeedForPlatform = (err: unknown) => /cannot find (latest|channel)|latest[-\w]*\.yml|\b404\b/i.test(errText(err));
const noReleases = (err: unknown) => /no published versions|unable to find latest version/i.test(errText(err));

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: NodeJS.Timeout;
  return Promise.race([p, new Promise<never>((_, reject) => (timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms / 1000} s`)), ms)))]).finally(() =>
    clearTimeout(timer),
  );
}

// ---------- versions ----------

/** Compare x.y.z[-pre] versions (a leading "v" is ignored): <0, 0, >0. A prerelease sorts before its release. */
export function compareVersions(a: string, b: string): number {
  const parse = (v: string) => {
    const [core, pre = ""] = v.trim().replace(/^v/i, "").split("+")[0].split(/-(.*)/s);
    return { nums: core.split(".").map((n) => parseInt(n, 10) || 0), pre };
  };
  const x = parse(a);
  const y = parse(b);
  for (let i = 0; i < Math.max(x.nums.length, y.nums.length, 3); i++) {
    const d = (x.nums[i] ?? 0) - (y.nums[i] ?? 0);
    if (d) return d;
  }
  if (x.pre === y.pre) return 0;
  if (!x.pre) return 1;
  if (!y.pre) return -1;
  return x.pre.localeCompare(y.pre, undefined, { numeric: true });
}

// ---------- notify: read the latest release ----------

async function checkRelease(): Promise<UpdateStatus> {
  let res: Response;
  try {
    res = await net.fetch(latestReleaseApi(), {
      headers: { Accept: "application/vnd.github+json", "User-Agent": `ReproCrowd/${current()}` },
      signal: AbortSignal.timeout(CHECK_TIMEOUT),
    });
  } catch (err) {
    return errorStatus((err as Error)?.name === "TimeoutError" ? new Error("timed out") : err);
  }
  if (res.status === 404) return { state: "not-available", current: current(), note: "No version has been released on GitHub yet." };
  if (res.status === 403 || res.status === 429) return errorStatus(new Error(`HTTP ${res.status} rate limit`));
  if (!res.ok) return errorStatus(new Error(`GitHub answered HTTP ${res.status} ${res.statusText}`));
  let rel: { tag_name?: string; name?: string; html_url?: string };
  try {
    rel = (await res.json()) as typeof rel;
  } catch (err) {
    return errorStatus(new Error(`unreadable answer from GitHub: ${errText(err)}`));
  }
  const version = (rel.tag_name || rel.name || "").replace(/^v/i, "");
  if (!/^\d+\.\d+/.test(version)) return errorStatus(new Error(`the latest release has no version number (tag “${rel.tag_name ?? ""}”)`));
  if (compareVersions(version, current()) <= 0) return { state: "not-available", current: current() };
  return { state: "manual", current: current(), version, url: rel.html_url || releasesPage(), howTo: howTo() };
}

// ---------- auto: electron-updater ----------

let autoReady = false;
function setupAuto() {
  if (autoReady) return;
  autoReady = true;
  if (server()) autoUpdater.setFeedURL({ provider: "generic", url: server() });
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true; // "Later" still installs it when ReproCrowd quits
  autoUpdater.logger = null;
  // electron-updater also emits "error"; without a listener an EventEmitter "error" would throw.
  autoUpdater.on("error", (err) => {
    console.error("reprocrowd update:", errText(err));
    if (status.state === "downloading") set(errorStatus(err));
  });
  autoUpdater.on("download-progress", (p: ProgressInfo) => {
    if (status.state === "downloading") set({ ...status, percent: Math.round(p.percent) });
  });
  autoUpdater.on("update-downloaded", (info: UpdateInfo) => {
    set({ state: "ready", current: current(), version: info.version });
    void offerRestart(info.version);
  });
}

async function checkAuto(): Promise<UpdateStatus> {
  setupAuto();
  if (status.state === "ready" || status.state === "downloading") return status;
  let result: Awaited<ReturnType<typeof autoUpdater.checkForUpdates>>;
  try {
    result = await withTimeout(autoUpdater.checkForUpdates(), CHECK_TIMEOUT, "The update check");
  } catch (err) {
    if (noReleases(err)) return { state: "not-available", current: current(), note: "No version has been released on GitHub yet." };
    // No auto-update file for this platform in the release: the release page may still have a newer version.
    if (noFeedForPlatform(err)) return checkRelease();
    return errorStatus(err);
  }
  if (!result) return checkRelease(); // electron-updater considers itself inactive here
  if (!result.isUpdateAvailable) return { state: "not-available", current: current() };
  const version = result.updateInfo.version;
  // `ready` may already be set (update-downloaded fires during the check when the file is in electron-updater's cache).
  if ((status as UpdateStatus).state !== "ready") set({ state: "downloading", current: current(), version, percent: 0 });
  result.downloadPromise?.catch((err) => set(errorStatus(err)));
  return status;
}

// ---------- the check ----------

/** Check for a newer version (one check at a time; a second caller gets the running one). Never rejects. */
export function checkForUpdates(): Promise<UpdateStatus> {
  const { mode, explain } = updateMode();
  if (mode === "off") return Promise.resolve(set({ state: "disabled", current: current(), reason: explain }));
  if (status.state === "ready" || status.state === "downloading") return Promise.resolve(status);
  if (inFlight) return inFlight;
  set({ state: "checking", current: current() });
  inFlight = (mode === "auto" ? checkAuto() : checkRelease())
    .catch((err) => errorStatus(err))
    .then((s) => set(s))
    .finally(() => (inFlight = null));
  return inFlight;
}

export function installUpdate() {
  if (status.state !== "ready") throw new Error("No update has been downloaded yet.");
  setImmediate(() => autoUpdater.quitAndInstall(false, true));
}

// ---------- dialogs ----------

const parent = () => BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
const box = (opts: Electron.MessageBoxOptions) => {
  const w = parent();
  return w ? dialog.showMessageBox(w, opts) : dialog.showMessageBox(opts);
};

let offering = false;
async function offerRestart(version: string) {
  if (offering) return;
  offering = true;
  try {
    const { response } = await box({
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      title: "Update ready",
      message: `ReproCrowd ${version} is ready to install.`,
      detail: "Restart now to install it, or choose Later: it is then installed when you quit ReproCrowd. Your reproductions, settings and packages stay.",
    });
    if (response === 0) installUpdate();
  } finally {
    offering = false;
  }
}

async function offerDownload(s: Extract<UpdateStatus, { state: "manual" }>) {
  const { response } = await box({
    type: "info",
    buttons: ["Open the release page", "Later"],
    defaultId: 0,
    cancelId: 1,
    title: "Update available",
    message: `ReproCrowd ${s.version} is available (you have ${s.current}).`,
    detail: s.howTo,
  });
  if (response === 0) await shell.openExternal(s.url);
}

/** Help → Check for Updates…: check and say what happened, whatever it is. */
export async function promptUpdate() {
  const s = await checkForUpdates();
  switch (s.state) {
    case "disabled":
      await box({ type: "info", title: "Updates", message: "Updates aren't available here.", detail: s.reason });
      break;
    case "not-available":
      await box({ type: "info", title: "Updates", message: "You're up to date.", detail: `ReproCrowd ${s.current} is the newest version.${s.note ? ` ${s.note}` : ""}` });
      break;
    case "downloading":
      await box({ type: "info", title: "Updates", message: `ReproCrowd ${s.version} is downloading (${s.percent} %).`, detail: "You'll be asked to restart when it's ready." });
      break;
    case "ready":
      await offerRestart(s.version);
      break;
    case "manual":
      await offerDownload(s);
      break;
    case "error": {
      const { response } = await box({ type: "warning", title: "Updates", message: s.message, detail: `${s.detail ?? ""}\n\nYou can also get new versions from the release page.`.trim(), buttons: ["OK", "Open the release page"], defaultId: 0, cancelId: 0 });
      if (response === 1 && s.url) await shell.openExternal(s.url);
      break;
    }
  }
}

// ---------- at start ----------

/** Check once, quietly, a little after start: auto mode downloads and then offers a restart; notify mode tells about
 * each new version once. Failures are only logged. */
export function initUpdater() {
  const { mode } = updateMode();
  if (mode === "off" || !loadSettings().checkUpdates) return;
  setTimeout(async () => {
    const s = await checkForUpdates();
    if (s.state === "error") console.error("reprocrowd update:", s.message, s.detail ?? "");
    if (s.state === "manual" && loadSettings().updateNotified !== s.version) {
      saveSettings({ updateNotified: s.version });
      await offerDownload(s);
    }
  }, STARTUP_DELAY).unref?.();
}
