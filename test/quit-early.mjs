// Quitting while the backend is still starting (#27): the app must close promptly and leave no backend behind.
// Runs the dev build (npm run build first), or a packaged one given as the first argument.
//
//   node test/quit-early.mjs [dist/linux-unpacked/reprocrowd-app]
import { _electron as electron } from "playwright-core";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const exe = process.argv[2];
const RUNS = Number(process.env.RUNS ?? 3);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

let failures = 0;
for (let i = 1; i <= RUNS; i++) {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "reprocrowd-quit-"));
  const { ELECTRON_RUN_AS_NODE, ...env } = process.env;
  const app = await electron.launch({
    ...(exe ? { executablePath: path.resolve(exe), args: [] } : { args: [APP] }),
    env: { ...env, REPROCROWD_USER_DATA: userData, REPROCROWD_MULTI_INSTANCE: "1", REPROCROWD_UPDATES: "off" },
  });
  const win = await app.firstWindow();
  await win.waitForSelector("#settings-btn");
  // the backend process, once spawned (its pid file is written right after the spawn)
  const pidFile = path.join(userData, "backend", "pid");
  for (let t = 0; t < 100 && !fs.existsSync(pidFile); t++) await sleep(50);
  const pid = fs.existsSync(pidFile) ? Number(fs.readFileSync(pidFile, "utf8")) : undefined;
  const t0 = Date.now();
  const closed = await Promise.race([app.close().then(() => true), sleep(20_000).then(() => false)]);
  const took = Date.now() - t0;
  await sleep(500);
  const left = pid !== undefined && alive(pid);
  const ok = closed && !left;
  if (!ok) failures++;
  console.log(`run ${i}: backend pid ${pid ?? "(not spawned yet)"}, close ${closed ? `took ${took} ms` : "HUNG"}, backend ${left ? "LEFT RUNNING" : "gone"} — ${ok ? "ok" : "FAIL"}`);
  if (left) process.kill(pid, "SIGKILL");
  if (!closed) app.process().kill("SIGKILL");
  fs.rmSync(userData, { recursive: true, force: true });
}
console.log(failures ? `${failures}/${RUNS} failed` : "all ok");
process.exit(failures ? 1 : 0);
