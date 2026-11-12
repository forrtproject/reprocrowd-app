// The only bridge between the UI and the main process: one call() into main/api.ts, and the path of a dropped or
// chosen file (the backend runs on this computer, so files are added by path instead of being copied through IPC).
import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("reprocrowd", {
  call: (method: string, args: unknown[]) => ipcRenderer.invoke("call", method, args),
  pathForFile: (file: File) => {
    try {
      return webUtils.getPathForFile(file) || null;
    } catch {
      return null;
    }
  },
});
