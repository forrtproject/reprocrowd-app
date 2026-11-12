// Auto-update: check the GitHub Releases feed (written into app-update.yml by electron-builder from the `publish`
// config) and, when a newer version is downloaded, offer to restart and install. Only meaningful in a packaged build;
// in a development checkout the feed doesn't exist, so updates are reported as disabled.
import { app, dialog } from "electron";
import { autoUpdater } from "electron-updater";

export type UpdateStatus =
  | { state: "disabled"; reason: string }
  | { state: "not-available"; current: string }
  | { state: "available"; version: string }
  | { state: "error"; message: string };

const enabled = () => app.isPackaged;

export function initUpdater() {
  if (!enabled()) return;
  autoUpdater.autoDownload = true;
  autoUpdater.on("error", (err) => console.error("reprocrowd update:", err?.message ?? err));
  autoUpdater.on("update-downloaded", async (info) => {
    const { response } = await dialog.showMessageBox({
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      title: "Update ready",
      message: `ReproCrowd ${info.version} is downloaded and ready to install.`,
      detail: "Restart to apply the update. Your reproductions, settings and packages stay.",
    });
    if (response === 0) autoUpdater.quitAndInstall();
  });
}

export function checkForUpdates(): Promise<UpdateStatus> {
  if (!enabled()) {
    return Promise.resolve({ state: "disabled", reason: "Updates are only available in an installed build." });
  }
  return new Promise((resolve) => {
    const finish = (status: UpdateStatus) => {
      autoUpdater.removeListener("update-available", onAvailable);
      autoUpdater.removeListener("update-not-available", onNotAvailable);
      autoUpdater.removeListener("error", onError);
      resolve(status);
    };
    const onAvailable = (info: { version: string }) => finish({ state: "available", version: info.version });
    const onNotAvailable = () => finish({ state: "not-available", current: app.getVersion() });
    const onError = (err: Error) => finish({ state: "error", message: err?.message ?? String(err) });
    autoUpdater.once("update-available", onAvailable);
    autoUpdater.once("update-not-available", onNotAvailable);
    autoUpdater.once("error", onError);
    autoUpdater.checkForUpdates().catch((err) => finish({ state: "error", message: err?.message ?? String(err) }));
  });
}

/** For the Help menu: run a check and show the outcome in a dialog. */
export async function promptUpdate() {
  if (!enabled()) {
    await dialog.showMessageBox({ type: "info", title: "Updates", message: "Updates are only available in an installed build." });
    return;
  }
  const status = await checkForUpdates();
  if (status.state === "not-available") {
    await dialog.showMessageBox({ type: "info", title: "Updates", message: "You're up to date.", detail: `ReproCrowd ${status.current} is the latest version.` });
  } else if (status.state === "available") {
    await dialog.showMessageBox({ type: "info", title: "Updates", message: `Version ${status.version} is available.`, detail: "It is downloading in the background; you'll be asked to restart when it's ready." });
  } else if (status.state === "error") {
    await dialog.showMessageBox({ type: "error", title: "Updates", message: "Couldn't check for updates.", detail: status.message });
  }
}
