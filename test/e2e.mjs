// End-to-end check of the built app (npm run build first): library, a new reproduction from a DOI (real OpenAlex
// lookup), every workflow step, and the Zenodo deposit (draft, then publish) against a fake Zenodo server in this
// process. The package is brought to "ran successfully" through the backend API, with the fixtures and the fake
// LLM of ../reprocrowd-cli/tests. Screenshots go to test-output/.
//
//   node test/e2e.mjs
import { _electron as electron } from "playwright-core";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "..");
const CLI_TESTS = path.resolve(APP, "../reprocrowd-cli/tests");
const OUT = path.join(APP, "test-output");
const DOI = "10.1371/journal.pbio.1002106";
fs.mkdirSync(OUT, { recursive: true });

// ---------- a fake Zenodo ----------
const zenodo = { depositions: new Map(), files: new Map(), requests: [] };
const fake = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const url = new URL(req.url, "http://x");
  const base = `http://127.0.0.1:${fake.address().port}`;
  zenodo.requests.push({ method: req.method, path: url.pathname, auth: req.headers.authorization });
  const send = (status, json) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(json === undefined ? "" : JSON.stringify(json));
  };
  const authed = req.headers.authorization === "Bearer test-token";
  if (req.method === "GET" && url.pathname === "/api/records") return send(200, { hits: { hits: [] } });
  if (url.pathname.startsWith("/api/deposit") || url.pathname.startsWith("/api/files")) {
    if (!authed) return send(401, { message: "The server could not verify that you are authorized" });
  }
  if (req.method === "GET" && url.pathname === "/api/deposit/depositions") return send(200, []);
  if (req.method === "POST" && url.pathname === "/api/deposit/depositions") {
    const id = 1000 + zenodo.depositions.size;
    const md = JSON.parse(body.toString()).metadata;
    const dep = { id, submitted: false, state: "unsubmitted", metadata: { ...md, prereserve_doi: { doi: `10.5072/zenodo.${id}`, recid: id } },
                  links: { bucket: `${base}/api/files/b${id}`, html: `${base}/deposit/${id}` } };
    zenodo.depositions.set(id, dep);
    return send(201, dep);
  }
  let m;
  if (req.method === "PUT" && (m = url.pathname.match(/^\/api\/files\/b(\d+)\/(.+)$/))) {
    zenodo.files.set(Number(m[1]), { name: decodeURIComponent(m[2]), bytes: body.length, head: body.subarray(0, 2).toString() });
    return send(201, { key: m[2], size: body.length });
  }
  if ((m = url.pathname.match(/^\/api\/deposit\/depositions\/(\d+)(\/actions\/publish)?$/))) {
    const dep = zenodo.depositions.get(Number(m[1]));
    if (!dep) return send(404, { message: "not found" });
    if (req.method === "GET") return send(200, dep);
    if (req.method === "DELETE") return zenodo.depositions.delete(dep.id), send(204);
    if (req.method === "POST" && m[2]) {
      Object.assign(dep, { submitted: true, state: "done", doi: `10.5072/zenodo.${dep.id}` });
      dep.links.record_html = `${base}/records/${dep.id}`;
      return send(202, dep);
    }
  }
  send(404, { message: `no route ${req.method} ${url.pathname}` });
});
await new Promise((r) => fake.listen(0, "127.0.0.1", r));
const ZURL = `http://127.0.0.1:${fake.address().port}`;

// ---------- the app, with its own data folder ----------
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "rc-e2e-"));
const packagesRoot = path.join(userData, "packages");
const py = path.join(APP, "../reprocrowd-cli/.venv/bin/python");
fs.writeFileSync(path.join(userData, "backend.toml"), `
[zenodo]
url = "${ZURL}"
sandbox_url = "${ZURL}"

[llm]
default_profile = "fake"

[llm.profiles.fake]
argv = ["${py}", "${path.join(CLI_TESTS, "fake_llm.py")}", "ok"]
model = "fake-1"

[agent]
default_profile = "fake"

[agent.profiles.fake]
argv = ["${py}", "${path.join(CLI_TESTS, "fake_agent.py")}", "ok", "--tools", "{tools}"]
model = "fake-agent-1"
`);
fs.writeFileSync(path.join(userData, "settings.json"), JSON.stringify({ packagesRoot, zenodo: { sandbox: true, creator: { name: "Doe, Jane", orcid: "0000-0002-1825-0097", affiliation: "" } } }));

const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => k !== "ELECTRON_RUN_AS_NODE"));
// APP_EXECUTABLE: check a packaged build instead (e.g. dist/linux-unpacked/reprocrowd-app).
const exe = process.env.APP_EXECUTABLE;
const app = await electron.launch({ ...(exe ? { executablePath: path.resolve(exe), args: [] } : { args: [APP] }), env: { ...env, REPROCROWD_USER_DATA: userData, REPROCROWD_MULTI_INSTANCE: "1" } });
let win = await app.firstWindow();
const errors = [];
const watch = (w) => {
  w.on("pageerror", (e) => errors.push(e.message));
  // The report preview is a sandboxed frame without scripts: its own scripts being blocked is expected.
  const expected = /Failed to load resource|about:srcdoc|inline event handler/;
  w.on("console", (m) => m.type() === "error" && !expected.test(m.text()) && errors.push(m.text()));
};
watch(win);
await win.setViewportSize({ width: 1440, height: 900 });
const shot = (name) => win.screenshot({ path: path.join(OUT, `${name}.png`) });
const call = (method, ...args) =>
  win.evaluate(async ([m, a]) => {
    const r = await window.reprocrowd.call(m, a);
    if (r.err !== undefined) throw new Error(`${m}: ${r.err} ${JSON.stringify(r.problem ?? {})}`);
    return r.ok;
  }, [method, args]);
const step = (msg) => console.log(`• ${msg}`);

try {
  step("library");
  await win.waitForSelector(".lib-list h2", { timeout: 180_000 });
  await call("setZenodoToken", "sandbox", "test-token");
  assert.equal((await call("verifyZenodoToken", true)).ok, true);
  await win.reload();
  await win.waitForSelector(".lib-list h2");
  assert.ok(await win.locator("#setup-note").isHidden(), "name and token are set");
  await shot("01-library-empty");

  step("new reproduction: DOI lookup in OpenAlex");
  await win.click("#new-btn");
  await win.fill(".dlg-new [name=doi]", `https://doi.org/${DOI}`);
  await win.click("#nd-lookup");
  await win.waitForSelector("#nd-work b.serif", { timeout: 30_000 });
  const title = await win.locator("#nd-work b").innerText();
  assert.match(title, /p-hacking/i);
  assert.equal(await win.inputValue(".dlg-new [name=folder]"), path.join(packagesRoot, "10-1371-journal-pbio-1002106"));
  await shot("02-new-dialog");
  await Promise.all([win.waitForEvent("load"), win.click("#nd-create")]);
  await win.waitForSelector("#route button", { timeout: 60_000 });
  await win.waitForFunction(() => document.querySelector("#paper-title")?.textContent?.match(/p-hacking/i));
  const pkgId = new URLSearchParams((await win.evaluate(() => location.hash)).slice(1)).get("pkg");
  assert.ok(pkgId);

  step("bring the package to a successful run (backend API)");
  const P = (sub, method = "GET", body) => call("pkg", method, sub, body);
  const folder = (await call("info")).folder;
  await call("upload", "fulltexts", { name: "paper.tei.xml", path: path.join(CLI_TESTS, "fixtures/paper.tei.xml") });
  await P("/extractions", "POST", { method: "parse", fulltext: "ft-paper-tei" });
  const REF = [{ extraction: "ex-paper-tei", id: "sec3.p1.s1" }];
  for (const t of [{ type: "number", description: "t", expected: 2.31, decimals: 2 }, { type: "categorical", description: "significant?", expected: "yes" },
                   { type: "table", description: "descriptives", expected: [["Group", "M"], ["A", "512.4"]] }])
    await P("/targets", "POST", { ...t, refs: REF });
  await P("/actions", "POST", { kind: "analysis", summary: "everything", refs: REF, produces: ["T001", "T002", "T003"] });
  await P("/script", "PUT", { template: "python" });
  fs.writeFileSync(path.join(folder, "analysis.py"), `import json, os
os.makedirs("outputs", exist_ok=True)
json.dump({"results": [{"id": "T001", "value": 2.3149}, {"id": "T002", "value": "Yes"}, {"id": "T003", "table": [["A", 512.38]]}]}, open("outputs/results.json", "w"))
`);
  const job = (await P("/jobs", "POST", { type: "run" })).data;
  for (let i = 0; i < 120; i++) {
    const j = (await P(`/jobs/${job.id}`)).data;
    if (j.status !== "running" && j.status !== "queued") {
      assert.equal(j.status, "succeeded", JSON.stringify(j));
      break;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  await win.reload();
  await win.waitForSelector("#route button");

  step("every step renders");
  const steps = await win.locator("#route button").count();
  assert.equal(steps, 8);
  for (let k = 0; k < steps; k++) {
    await win.locator("#route button").nth(k).click();
    await win.waitForFunction((n) => document.querySelectorAll("#route button")[n]?.classList.contains("on"), k);
    await win.waitForTimeout(k === 0 ? 1500 : 700);
    await shot(`03-step-${k + 1}`);
  }

  step("report, ZIP and deposit (step 8)");
  await win.click("#rp-report");
  await win.waitForSelector("#rp-report-show", { timeout: 60_000 });
  await win.click("#rp-zip");
  await win.waitForSelector("#rp-zip-show", { timeout: 60_000 });
  await win.waitForSelector("#rp-deposit:not([disabled])", { timeout: 30_000 });
  const groups = await win.locator("[data-group]").evaluateAll((els) => els.map((e) => [e.dataset.group, e.checked]));
  assert.deepEqual(Object.fromEntries(groups), { paper: true }, "PLOS Biology is CC-BY, so the paper's material is included by default; no inputs");
  await win.fill("#rp-note", "Checked by the end-to-end test.");
  await shot("04-deposit-ready");
  await win.click("#rp-metadata");
  await win.waitForSelector(".dlg-wide textarea");
  const md = JSON.parse(await win.locator(".dlg-wide textarea").inputValue());
  assert.deepEqual(md.creators, [{ name: "Doe, Jane", orcid: "0000-0002-1825-0097" }]);
  assert.match(md.description, /end-to-end test/);
  await win.click(".dlg-wide menu button");
  await win.click("#rp-deposit");
  await win.click(".dlg-confirm button[value=ok]");
  await win.waitForSelector(".dlg-confirm button[value=view]", { timeout: 60_000 });
  await shot("05-draft-uploaded");
  await win.click(".dlg-confirm button[value=close]");
  await win.waitForSelector(".deposit.draft");
  const [dep] = [...zenodo.depositions.values()];
  const file = zenodo.files.get(dep.id);
  assert.ok(file && file.head === "PK" && file.name.endsWith("-reproduction.zip"), JSON.stringify(file));
  assert.equal(dep.metadata.related_identifiers[0].identifier, DOI);
  assert.ok(zenodo.requests.filter((r) => r.path.startsWith("/api/deposit")).every((r) => r.auth === "Bearer test-token"));
  assert.match(await win.locator(".deposit.draft").innerText(), /10\.5072\/zenodo\.1000/);

  step("publish");
  await win.click(".deposit.draft button[data-action=publish]");
  await win.click(".dlg-confirm button[value=ok]");
  await win.waitForSelector(".deposit.published", { timeout: 30_000 });
  assert.equal(zenodo.depositions.get(dep.id).submitted, true);
  await shot("06-published");

  step("settings and the library afterwards");
  await win.click("#settings-btn");
  await win.waitForSelector(".dlg-settings .backend-line .ok", { timeout: 20_000 });
  await shot("07-settings");
  const line = await win.locator(".dlg-settings .backend-line").innerText();
  if (exe) assert.match(line, /bundled with the app/);
  console.log(`  backend: ${line}`);
  await win.click(".dlg-settings button[value=cancel]");
  await Promise.all([win.waitForEvent("load"), win.click("#library-btn")]);
  await win.waitForSelector(".pkg-card .badge.st-published", { timeout: 20_000 });
  await shot("08-library");

  assert.deepEqual(errors, [], "no errors in the page");
  console.log("✓ end-to-end check passed");
} catch (e) {
  await shot("failure").catch(() => {});
  console.error(e);
  console.error("page errors:", errors);
  process.exitCode = 1;
} finally {
  await app.close();
  fake.close();
}
