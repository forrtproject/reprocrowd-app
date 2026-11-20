// Offline check of a packaged build, for each operating system (CI runs it on Windows, macOS and Linux):
//   1. the app starts and its bundled backend answers (the library loads)
//   2. a bundled example opens (the packaged resources are found and the backend adopts the copy)
//   3. a reproduction is created from a local Markdown paper, which the backend parses
//   4. targets and a plan are added and a small Python analysis runs as a job (bash + python3, as for users)
//   5. every workflow step renders; the report and the ZIP are built
//   6. quitting leaves no backend behind; no page errors throughout
// Nothing goes over the network; test/e2e.mjs covers DOI lookups, Zenodo and GitHub. Screenshots go to test-output/,
// named after the platform and SMOKE_LABEL (CI: "unpacked", then "installed" for the copy the installer put in place).
//
//   node test/smoke.mjs [path/to/executable]    (default: the unpacked build in dist/)
import { _electron as electron } from "playwright-core";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(APP, "dist");
const OUT = path.join(APP, "test-output");
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** The executable electron-builder leaves in dist/<platform>-unpacked (or the .app on macOS). */
function findExecutable() {
  const dirs = fs.existsSync(DIST) ? fs.readdirSync(DIST) : [];
  for (const d of dirs) {
    const full = path.join(DIST, d);
    if (process.platform === "darwin" && d.startsWith("mac")) {
      const app = fs.readdirSync(full).find((f) => f.endsWith(".app"));
      if (!app) continue;
      const macos = path.join(full, app, "Contents", "MacOS");
      return path.join(macos, fs.readdirSync(macos)[0]);
    }
    if (process.platform === "win32" && d === "win-unpacked") {
      const exe = fs.readdirSync(full).find((f) => f.endsWith(".exe") && !/uninstall/i.test(f));
      if (exe) return path.join(full, exe);
    }
    if (process.platform === "linux" && d === "linux-unpacked") {
      const exe = path.join(full, JSON.parse(fs.readFileSync(path.join(APP, "package.json"), "utf8")).name);
      if (fs.existsSync(exe)) return exe;
    }
  }
  console.error(`smoke test FAILED: no unpacked build for ${process.platform} in ${DIST} (found: ${dirs.join(", ") || "nothing"})`);
  process.exit(1);
}

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

const exe = path.resolve(process.argv[2] ?? findExecutable());
console.log(`launching ${exe}`);
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "reprocrowd-smoke-"));
const packagesRoot = path.join(userData, "packages");
fs.writeFileSync(path.join(userData, "settings.json"), JSON.stringify({ packagesRoot, checkUpdates: false }));
const { ELECTRON_RUN_AS_NODE, ...env } = process.env;
const app = await electron.launch({
  executablePath: exe,
  args: [],
  env: { ...env, REPROCROWD_USER_DATA: userData, REPROCROWD_MULTI_INSTANCE: "1", REPROCROWD_UPDATES: "off" },
  timeout: 120_000,
});
const errors = [];
const win = await app.firstWindow();
win.on("pageerror", (e) => errors.push(e.message));
// The report preview is a sandboxed frame without scripts: its own scripts being blocked is expected.
win.on("console", (m) => m.type() === "error" && !/Failed to load resource|about:srcdoc|inline event handler/.test(m.text()) && errors.push(m.text()));
await win.setViewportSize({ width: 1440, height: 900 });

const tag = [process.platform, process.env.SMOKE_LABEL].filter(Boolean).join("-");
const shot = (name) => win.screenshot({ path: path.join(OUT, `smoke-${tag}-${name}.png`) });
const call = (method, ...args) =>
  win.evaluate(async ([m, a]) => {
    const r = await window.reprocrowd.call(m, a);
    if (r.err !== undefined) throw new Error(`${m}: ${r.err} ${JSON.stringify(r.problem ?? {})}`);
    return r.ok;
  }, [method, args]);
const P = (sub, method = "GET", body) => call("pkg", method, sub, body);
let current = "";
const step = (msg) => console.log(`• ${(current = msg)}`);
const t0 = Date.now();

let failed;
let pid;
try {
  step("the app starts and the backend answers");
  // "Your reproductions" / "How it works": the library came from the backend. Without it the page shows an offline card.
  await win.waitForSelector("#lib-body h2.section-title", { timeout: 180_000 });
  console.log(`  library loaded after ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  const pidFile = path.join(userData, "backend", "pid");
  pid = fs.existsSync(pidFile) ? Number(fs.readFileSync(pidFile, "utf8")) : undefined;
  await shot("1-library");

  step("a bundled example opens");
  const examples = await call("listExamples");
  assert.ok(examples.length >= 1, "the packaged app finds its bundled examples");
  const exId = await call("openExample", examples[0].id);
  assert.ok(exId, `opening example ${examples[0].id} returns a package id`);
  const listed = await win.evaluate(async () => (await window.reprocrowd.call("listPackages")).ok);
  assert.ok(listed.some((p) => p.id === exId), "the backend lists the opened example");

  step("new from file: a local Markdown paper is parsed");
  const paperMd = path.join(userData, "local-paper.md");
  fs.writeFileSync(paperMd, "# A Local Manuscript\n\n## Methods\n\nWe ran a t-test on 40 participants.\n\n## Results\n\nThe effect was 2.31, and it was significant.\n\nGroup A had a mean RT of 512.4 ms.\n");
  await win.click("#file-btn");
  await win.fill(".dlg-new [name=file]", paperMd);
  await win.fill(".dlg-new [name=folder]", path.join(packagesRoot, "local-paper"));
  await Promise.all([win.waitForEvent("load"), win.click("#nf-create:not([disabled])")]);
  await win.waitForSelector("#route button", { timeout: 120_000 });
  const { jats: local } = await call("connect");
  const doc = (await P(`/extractions/${local}`)).data;
  assert.deepEqual(doc.sections.map((s) => s.title), ["Methods", "Results"]);

  step("targets, a plan and a Python analysis that runs as a job");
  const sents = doc.sections.flatMap((sec) => sec.paragraphs.flatMap((pa) => pa.sentences.map((x) => x.id)));
  const targets = [{ type: "number", description: "t", expected: "2.31" }, { type: "categorical", description: "significant?", expected: "yes" },
                   { type: "table", description: "descriptives", expected: [["Group", "M"], ["A", "512.4"]] }];
  for (const [i, t] of targets.entries()) await P("/targets", "POST", { ...t, refs: [{ extraction: local, id: sents[Math.min(i + 1, sents.length - 1)] }] });
  await P("/actions", "POST", { kind: "analysis", summary: "everything", refs: [{ extraction: local, id: sents[1] }], produces: ["T001", "T002", "T003"] });
  await P("/script", "PUT", { template: "python" });
  const folder = (await call("info")).folder;
  fs.writeFileSync(path.join(folder, "analysis.py"), `import json, os
os.makedirs("outputs", exist_ok=True)
json.dump({"results": [{"id": "T001", "value": 2.3149}, {"id": "T002", "value": "Yes"}, {"id": "T003", "table": [["A", 512.38]]}]}, open("outputs/results.json", "w"))
print("analysis ran")
`);
  const job = (await P("/jobs", "POST", { type: "run" })).data;
  let j;
  for (let i = 0; i < 240; i++) {
    j = (await P(`/jobs/${job.id}`)).data;
    if (j.status !== "running" && j.status !== "queued") break;
    await sleep(500);
  }
  assert.equal(j.status, "succeeded", `the run job: ${JSON.stringify(j, null, 2)}`);
  await win.reload();
  await win.waitForSelector("#route button");

  step("every workflow step renders");
  const steps = await win.locator("#route button").count();
  for (let k = 0; k < steps; k++) {
    await win.locator("#route button").nth(k).click();
    await win.waitForFunction((n) => document.querySelectorAll("#route button")[n]?.classList.contains("on"), k);
    await win.waitForTimeout(500);
    await shot(`2-step-${k + 1}`);
  }

  step("the report and the ZIP are built");
  await win.click("#rp-report");
  await win.waitForSelector("#rp-report-show", { timeout: 60_000 });
  await win.click("#rp-zip");
  await win.waitForSelector("#rp-zip-show", { timeout: 60_000 });
  assert.match(await win.locator("body").innerText(), /3 of 3 match/, "the comparison found the three results");
  await shot("3-report");
} catch (e) {
  failed = e;
  await shot("failed").catch(() => {});
  const log = await win.evaluate(() => window.reprocrowd.call("backendLog")).catch((e) => ({ err: String(e) }));
  const page = await win.locator("body").innerText().catch(() => "(no page)");
  console.error(`\nFAILED at "${current}":\n${e.stack ?? e}\n\npage:\n${page.slice(0, 3000)}\n\nbackend log:\n${log.ok ?? log.err ?? JSON.stringify(log)}`);
}

step("quitting stops the backend");
const closed = await Promise.race([app.close().then(() => true), sleep(20_000).then(() => false)]);
await sleep(1000);
if (!closed) {
  app.process().kill("SIGKILL");
  errors.push("the app didn't close within 20 s");
}
if (pid !== undefined && alive(pid)) {
  process.kill(pid, "SIGKILL");
  errors.push(`the backend (pid ${pid}) was still running after the app quit`);
}
fs.rmSync(userData, { recursive: true, force: true });
if (errors.length) console.error(`\nerrors:\n${errors.join("\n")}`);
if (failed || errors.length) process.exit(1);
console.log(`smoke test ok (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
