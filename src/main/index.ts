// ReproCrowd desktop app: windows, the app:// protocol serving the UI, the IPC bridge to api.ts, and the backend's
// lifetime (it is stopped when the app quits; with jobs still running, the app asks first).
import { app, BrowserWindow, dialog, ipcMain, Menu, net, protocol, shell, WebContents } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";
import * as backend from "./backend";
import { Api, bindPackage, makeApi } from "./api";
import { get } from "./service";

const SCHEME = "app";
const ORIGIN = `${SCHEME}://ui`;
const rendererDir = () => path.join(app.getAppPath(), "build", "renderer");
const icon = () => path.join(app.isPackaged ? process.resourcesPath : path.join(app.getAppPath(), "resources"), "hex-sticker.png");

if (process.env.REPROCROWD_USER_DATA) app.setPath("userData", path.resolve(process.env.REPROCROWD_USER_DATA));
app.setName("ReproCrowd");

protocol.registerSchemesAsPrivileged([{ scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } }]);

if (!app.requestSingleInstanceLock() && !process.env.REPROCROWD_MULTI_INSTANCE) {
  app.quit();
}

const apis = new Map<number, Api>();

function apiFor(wc: WebContents): Api {
  let api = apis.get(wc.id);
  if (!api) {
    api = makeApi(wc);
    apis.set(wc.id, api);
    wc.once("destroyed", () => {
      apis.delete(wc.id);
      bindPackage(wc, null);
    });
  }
  return api;
}

export function createWindow(hash = "") {
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 900,
    minHeight: 600,
    title: "ReproCrowd",
    icon: icon(),
    backgroundColor: "#fbfaf8",
    show: false,
    webPreferences: {
      preload: path.join(app.getAppPath(), "build", "preload", "index.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: true,
    },
  });
  win.once("ready-to-show", () => win.show());
  const wc = win.webContents;
  // Links leave the app for the browser; the app itself never navigates elsewhere.
  wc.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (e, url) => {
    if (!url.startsWith(ORIGIN + "/")) {
      e.preventDefault();
      if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    }
  });
  win.loadURL(`${ORIGIN}/index.html${hash}`);
  return win;
}

function serveUi() {
  const root = rendererDir();
  protocol.handle(SCHEME, (req) => {
    const u = new URL(req.url);
    if (u.host !== "ui") return new Response("not found", { status: 404 });
    const rel = decodeURIComponent(u.pathname).replace(/^\/+/, "") || "index.html";
    const file = path.resolve(root, rel);
    if (!file.startsWith(root + path.sep)) return new Response("forbidden", { status: 403 });
    return net.fetch(pathToFileURL(file).toString());
  });
}

ipcMain.handle("call", async (e, method: string, args: unknown[]) => {
  if (!e.senderFrame?.url.startsWith(ORIGIN + "/")) return { err: "not allowed" };
  const api = apiFor(e.sender) as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>;
  try {
    const fn = Object.hasOwn(api, method) ? api[method] : undefined;
    if (typeof fn !== "function") throw new Error(`unknown method ${method}`);
    return { ok: (await fn(...(Array.isArray(args) ? args : []))) ?? null };
  } catch (err) {
    const problem = (err as any)?.problem;
    return { err: (err as Error)?.message ?? String(err), problem: problem ? JSON.parse(JSON.stringify(problem)) : undefined };
  }
});

function menu() {
  const isMac = process.platform === "darwin";
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      ...(isMac ? [{ role: "appMenu" as const }] : []),
      { label: "File", submenu: [{ label: "New Window", accelerator: "CmdOrCtrl+Shift+N", click: () => createWindow() }, { type: "separator" }, isMac ? { role: "close" } : { role: "quit" }] },
      { role: "editMenu" },
      { label: "View", submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }] },
      { role: "windowMenu" },
      {
        role: "help",
        submenu: [
          { label: "About ReproCrowd (forrt.org)", click: () => shell.openExternal("https://forrt.org/reprocrowd/") },
          { label: "Backend log", click: () => dialog.showMessageBox({ type: "info", title: "Backend log", message: "The last lines the backend printed", detail: backend.logTail(60) || "(nothing yet)" }) },
        ],
      },
    ]),
  );
}

// ---------- quitting ----------

let quitting = false;
app.on("before-quit", async (e) => {
  if (quitting) return;
  e.preventDefault();
  const st = backend.status();
  if (st.state === "running") {
    const jobs = await get<{ package: string; job: { id: string; type: string } }[]>("/v1/jobs", undefined, 3000).catch(() => []);
    if (jobs.length) {
      const { response } = await dialog.showMessageBox({
        type: "warning",
        buttons: ["Quit anyway", "Keep running"],
        defaultId: 1,
        cancelId: 1,
        title: "Jobs are running",
        message: `${jobs.length} job${jobs.length > 1 ? "s are" : " is"} still running (${jobs.map((j) => j.job.type).join(", ")}).`,
        detail: "Quitting stops the backend; the jobs are ended and recorded as interrupted.",
      });
      if (response === 1) return;
    }
  }
  quitting = true;
  await backend.stop().catch(() => {});
  app.quit();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("second-instance", () => {
  const w = BrowserWindow.getAllWindows()[0];
  if (w) {
    if (w.isMinimized()) w.restore();
    w.focus();
  } else createWindow();
});

app.whenReady().then(() => {
  serveUi();
  menu();
  createWindow();
  backend.ensure().catch(() => {}); // start early; the UI shows problems when it asks
  app.on("activate", () => BrowserWindow.getAllWindows().length === 0 && createWindow());
});
