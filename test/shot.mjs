// Launch the built app with a temporary data folder, wait for the library, and take screenshots.
// Usage: node test/shot.mjs <outdir> [userData]
import { _electron as electron } from "playwright-core";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] ?? "test-output";
const userData = process.argv[3] ?? fs.mkdtempSync(path.join(os.tmpdir(), "rc-app-"));
fs.mkdirSync(out, { recursive: true });
const app = await electron.launch({ args: ["."], env: { ...Object.fromEntries(Object.entries(process.env).filter(([k]) => k !== "ELECTRON_RUN_AS_NODE")), REPROCROWD_USER_DATA: userData, REPROCROWD_MULTI_INSTANCE: "1" } });
const win = await app.firstWindow();
win.on("console", (m) => console.log("console:", m.type(), m.text()));
win.on("pageerror", (e) => console.log("pageerror:", e.message));
await win.setViewportSize({ width: 1400, height: 900 });
await win.waitForSelector(".lib-list h2, .offline-card", { timeout: 120_000 });
await win.screenshot({ path: path.join(out, "library.png") });
console.log("userData", userData);
console.log(await win.locator("#lib-body").innerText());
await app.close();
