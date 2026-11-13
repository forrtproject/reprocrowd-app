// End-to-end check of the built app (npm run build first): library, a new reproduction from a DOI (real OpenAlex
// lookup), every workflow step, and the Zenodo deposit (draft, then publish) against a fake Zenodo server in this
// process. The package is brought to "ran successfully" through the backend API, with the fixtures and the fake
// LLM of backend/tests. Screenshots go to test-output/.
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
const CLI_TESTS = path.join(APP, "backend/tests");
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
  // ---------- and a fake GitHub release, for the update check (REPROCROWD_UPDATE_SERVER) ----------
  if (req.method === "GET" && url.pathname === "/releases/latest") return send(200, { tag_name: "v99.0.0", html_url: `${base}/releases/tag/v99.0.0` });
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
// The fake LLM and agent need only the standard library: any Python will do.
const bundled = path.join(APP, "build/backend/python/bin/python3");
const py = fs.existsSync(bundled) ? bundled : "python3";
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
fs.writeFileSync(path.join(userData, "settings.json"), JSON.stringify({ packagesRoot, checkUpdates: false, zenodo: { sandbox: true, creator: { name: "Doe, Jane", orcid: "0000-0002-1825-0097", affiliation: "" } } }));

const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => k !== "ELECTRON_RUN_AS_NODE"));
// APP_EXECUTABLE: check a packaged build instead (e.g. dist/linux-unpacked/reprocrowd-app).
const exe = process.env.APP_EXECUTABLE;
const app = await electron.launch({ ...(exe ? { executablePath: path.resolve(exe), args: [] } : { args: [APP] }), env: { ...env, REPROCROWD_USER_DATA: userData, REPROCROWD_MULTI_INSTANCE: "1", REPROCROWD_UPDATE_SERVER: ZURL } });
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
/** Poll until fn() is true (the backend has caught up), or fail saying what was waited for. */
const until = async (what, fn, tries = 100) => {
  for (let i = 0; i < tries; i++) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.fail(`timed out waiting for ${what}`);
};
/** Hover `source` and wait for `target` to light up. The cards are positioned in a requestAnimationFrame, so a
 *  layout pass can move one out from under the cursor and clear the highlight again: re-hover on every try. */
const lights = (source, target, what) =>
  until(what, async () => {
    await win.locator(source).hover().catch(() => false);
    return win.locator(target).evaluate((e) => e.classList.contains("hot")).catch(() => false);
  }, 30);

try {
  step("library");
  await win.waitForSelector(".lib-list h2", { timeout: 180_000 });
  await call("setZenodoToken", "sandbox", "test-token");
  assert.equal((await call("verifyZenodoToken", true)).ok, true);
  await win.reload();
  await win.waitForSelector(".lib-list h2");
  assert.ok(await win.locator("#setup-note").isHidden(), "name and token are set");
  await shot("01-library-empty");

  step("new reproduction: a paper without JATS XML is refused");
  await win.click("#new-btn");
  await win.fill(".dlg-new [name=doi]", "10.1038/nature14539");
  await win.click("#nd-lookup");
  await win.waitForSelector("#nd-jats .refusal", { timeout: 60_000 });
  assert.match(await win.locator("#nd-jats").innerText(), /Only articles with JATS XML/);
  assert.ok(await win.locator("#nd-create").isDisabled());
  await shot("02a-refused");

  step("new reproduction: DOI lookup, JATS available");
  await win.fill(".dlg-new [name=doi]", `https://doi.org/${DOI}`);
  await win.click("#nd-lookup");
  await win.waitForSelector("#nd-jats .ok", { timeout: 60_000 });
  assert.match(await win.locator("#nd-work b").innerText(), /p-hacking/i);
  assert.match(await win.locator("#nd-jats").innerText(), /PMC4359000/);
  assert.equal(await win.inputValue(".dlg-new [name=folder]"), path.join(packagesRoot, "10-1371-journal-pbio-1002106"));
  await shot("02-new-dialog");
  await Promise.all([win.waitForEvent("load"), win.click("#nd-create:not([disabled])")]);
  await win.waitForSelector("#route button", { timeout: 120_000 });
  await win.waitForFunction(() => document.querySelector("#paper-title")?.textContent?.match(/p-hacking/i));
  const pkgId = new URLSearchParams((await win.evaluate(() => location.hash)).slice(1)).get("pkg");
  assert.ok(pkgId);

  step("the package has the JATS text; set it up to a successful run (backend API)");
  const P = (sub, method = "GET", body) => call("pkg", method, sub, body);
  const folder = (await call("info")).folder;
  const { jats } = await call("connect");
  const fts = (await P("/fulltexts")).data;
  assert.deepEqual(fts.map((f) => f.reprocrowd.flavor), ["jats"], "only the JATS XML, no PDF");
  const doc = (await P(`/extractions/${jats}`)).data;
  // Three sentences with numbers, from sections far apart, so the cards spread over the text.
  const sents = doc.sections.flatMap((sec) => sec.paragraphs.flatMap((pa) => pa.sentences.filter((x) => /\d/.test(x.text)).map((x) => x.id)));
  assert.ok(sents.length > 10, "the parsed JATS has sentences with numbers");
  const picks = [sents[Math.floor(sents.length * 0.2)], sents[Math.floor(sents.length * 0.5)], sents[Math.floor(sents.length * 0.8)]];
  const targets = [{ type: "number", description: "t", expected: 2.31, decimals: 2 }, { type: "categorical", description: "significant?", expected: "yes" },
                   { type: "table", description: "descriptives", expected: [["Group", "M"], ["A", "512.4"]] }];
  for (const [i, t] of targets.entries()) await P("/targets", "POST", { ...t, refs: [{ extraction: jats, id: picks[i] }] });
  await P("/targets", "POST", { type: "number", description: "a result without a passage", expected: 1, unreferenced: true, note: "from the supplement", refs: [] });
  await P("/actions", "POST", { kind: "analysis", summary: "everything", refs: [{ extraction: jats, id: picks[1] }], produces: ["T001", "T002", "T003"] });
  await P("/script", "PUT", { template: "python" });
  fs.writeFileSync(path.join(folder, "analysis.py"), `import json, os
os.makedirs("outputs", exist_ok=True)
json.dump({"results": [{"id": "T001", "value": 2.3149}, {"id": "T002", "value": "Yes"}, {"id": "T003", "table": [["A", 512.38]]}, {"id": "T004", "value": 1}]}, open("outputs/results.json", "w"))
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

  step("every step renders, and there is no PDF pane");
  const steps = await win.locator("#route button").count();
  assert.equal(steps, 7);
  assert.doesNotMatch(await win.locator("#route").innerText(), /Analysis plan/);
  assert.equal(await win.locator("#pdf, #pdf-pane, #sources-btn").count(), 0);
  for (let k = 0; k < steps; k++) {
    await win.locator("#route button").nth(k).click();
    await win.waitForFunction((n) => document.querySelectorAll("#route button")[n]?.classList.contains("on"), k);
    await win.waitForTimeout(800);
    await shot(`03-step-${k + 1}`);
  }

  step("paper: step 1 shows the extracted text, with sentence ids on request");
  await win.locator("#route button").nth(0).click();
  await win.waitForSelector(".tx-paper article.reader .sent[data-hid]");
  assert.match(await win.locator(".tx-paper").innerText(), /p-hacking/i);
  assert.equal(await win.locator(".tx-paper.tx-ids").count(), 0);
  await win.click("#tx-ids");
  await win.waitForSelector(".tx-paper.tx-ids");
  await shot("03a-paper-text");

  step("targets: the reader with the cards aligned (the targets table of step 3)");
  await win.locator("#route button").nth(2).click();
  await win.waitForSelector("#cl-targets");
  await win.click("#cl-targets");
  await win.waitForSelector(".al-cards .al-card");
  assert.equal(await win.locator(".al-cards .al-card").count(), 3);
  assert.equal(await win.locator(".al-unlinked .al-card").count(), 1, "T004 has no passage");
  // Each card is level with its passage, or below the card above it; never above its passage.
  const geo = await win.evaluate(() =>
    [...document.querySelectorAll(".al-cards .al-card")].map((c) => {
      const ref = document.querySelectorAll(".al-text .sent.ref");
      return { id: c.dataset.id, top: c.getBoundingClientRect().top };
    }),
  );
  const anchors = await win.evaluate((ids) => ids.map((id) => document.querySelector(`.al-text [data-hid="${CSS.escape(id)}"]`).getBoundingClientRect().top), picks);
  for (const [i, g] of geo.sort((a, b) => a.id.localeCompare(b.id)).entries()) assert.ok(g.top >= anchors[i] - 1 && g.top < anchors[i] + 200, `${g.id} at ${g.top}, its passage at ${anchors[i]}`);
  assert.equal(await win.locator(".al-lines path").count(), 3);
  await lights(`.al-cards .al-card[data-id="T002"]`, `.al-text [data-hid="${picks[1]}"]`, "hovering a card to light up its passage");
  await win.locator(`.al-cards .al-card[data-id="T002"]`).scrollIntoViewIfNeeded();
  await shot("04a-targets-reader");

  step("targets: mark a sentence and add a target from it");
  const free = sents.find((id) => !picks.includes(id));
  await win.locator(`.al-text .sent[data-hid="${free}"]`).scrollIntoViewIfNeeded();
  await win.locator(`.al-text .sent[data-hid="${free}"]`).click();
  await win.waitForSelector(".al-bar:not([hidden])");
  await shot("04b-marked");
  await win.click(".al-bar [data-act=add]");
  await win.waitForSelector(".dlg-wide [name=quote]");
  await win.waitForFunction(() => document.querySelector(".dlg-wide [name=quote]")?.value.length > 0);
  await win.fill(".dlg-wide [name=description]", "a result marked in the reader");
  await win.fill(".dlg-wide [name=expected]", "3");
  await win.click(".dlg-wide button[value=ok]");
  await win.waitForFunction(() => document.querySelectorAll(".al-cards .al-card").length === 4);
  const added = (await P("/targets")).data.find((x) => x.item.description === "a result marked in the reader").item;
  assert.deepEqual(added.refs, [{ extraction: jats, id: free }]);
  assert.ok(await win.locator(".al-bar").isHidden(), "the marks are cleared once used");

  step("targets: edit a card, and the table view");
  await win.click(`.al-card[data-id="${added.id}"] [data-act=edit]`);
  await win.waitForSelector(".dlg-edit textarea[data-f=description]");
  await win.fill(".dlg-edit textarea[data-f=description]", "a result marked in the reader, edited");
  await win.locator(".dlg-edit textarea[data-f=description]").blur();
  await win.waitForTimeout(500);
  await win.click(".dlg-edit button.primary");
  await win.waitForFunction((id) => document.querySelector(`.al-card[data-id="${id}"] .al-desc`)?.textContent?.includes("edited"), added.id);
  await win.click("#rp-view [data-view=table]");
  await win.waitForSelector(".rp-items tbody tr[data-id]");
  assert.equal(await win.locator(".rp-items tbody tr[data-id]").count(), 5);
  await win.click("#rp-view [data-view=reader]");
  await win.waitForSelector(".al-cards .al-card");
  await P(`/targets/${added.id}`, "DELETE");

  step("analysis steps table: the legacy plan screen from step 3");
  await win.locator("#route button").nth(2).click();
  if (await win.locator("#cl-back").count()) await win.click("#cl-back");
  await win.click("#cl-actions-table");
  await win.waitForSelector(".al-cards .al-card[data-id=A01]");
  await shot("04c-plan-reader");
  await win.click("#cl-back");
  await win.waitForSelector("#cl-extract");

  step("claims: extract, tick C1 and run it (steps 4–7 for that claim)");
  await win.locator("#route button").nth(2).click();
  if (await win.locator("#cl-back").count()) await win.click("#cl-back");
  await win.waitForSelector("#cl-extract:not([disabled])");
  await win.click("#cl-extract");
  await win.click(".dlg-wide button[value=ok]");
  await win.waitForSelector(".cl-tree .cl-item", { timeout: 60_000 });
  assert.equal(await win.locator(".cl-tree .cl-item").count(), 3);

  step("claims reader: focus on one claim, its methods, and the cleanup controls");
  // The fixture: A01 manual analysis ("everything"), used by no claim; A02 the extraction's preparation step (P1);
  // A03 its method (M1), used by C1, C2.1 and C2.2; C3 untestable.
  await win.click("#cl-view [data-view=reader]");
  await win.waitForSelector(".al-card[data-id=C1]");
  // C1 is focused by default (Review's fallback): its method is an open card, the rest are dimmed chips
  assert.equal(await win.locator(".al-card.open[data-id=A03]").count(), 1);
  assert.match(await win.locator(".al-chip[data-id=A02]").innerText(), /preparation/);
  assert.match(await win.locator(".al-chip[data-id=A01]").innerText(), /not used by any claim/);
  assert.ok(await win.locator(".al-chip[data-id=A01]").evaluate((e) => e.classList.contains("dim")));
  assert.match(await win.locator(".al-card.open[data-id=A03]").innerText(), /C1/);
  assert.match(await win.locator(".al-card.open[data-id=A03]").innerText(), /C2\.1/);
  // highlight-only links, both ways; no extra lines
  await lights(".al-card[data-id=C1]", ".al-card[data-id=A03]", "hovering a claim to light up its method");
  await lights(".al-card[data-id=A03]", ".al-card[data-id=C2]", "hovering a method to light up the other claim using it");
  assert.equal(await win.locator(".al-lines path").count(), await win.locator(".al-cards > .al-card").count());
  await win.locator(".al-card[data-id=C1]").scrollIntoViewIfNeeded();
  await shot("04d-claims-reader");
  // Review Focus 1: selecting doesn't move the page
  await win.locator(".al-card[data-id=C2] .al-desc").scrollIntoViewIfNeeded();
  const y0 = await win.locator(".al-card[data-id=C2] .al-desc").evaluate((e) => e.getBoundingClientRect().top);
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  const y1 = await win.locator(".al-card[data-id=C2] .al-desc").evaluate((e) => e.getBoundingClientRect().top);
  assert.ok(Math.abs(y1 - y0) < 40, `C2 moved ${y1 - y0}px`);
  // A method card of the focused claim belongs to that claim: clicking it must not hand the focus to another user
  // of the same step (A03 is C1's method too), and must not close the card either.
  assert.ok(await win.locator(".al-card[data-id=C2]").evaluate((e) => e.classList.contains("on")), "C2 is focused");
  await win.locator(".al-card[data-id=A03] .al-desc").click();
  assert.ok(await win.locator(".al-card[data-id=C2]").evaluate((e) => e.classList.contains("on")), "the focus stays on the claim whose method was clicked");
  assert.equal(await win.locator(".al-card.open[data-id=A03]").count(), 1, "the method card stays open");
  // deselect: clicking the focused claim again
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  assert.equal(await win.locator(".al-card.open").count(), 0);
  // Review Focus 3: switch the preparation step off and on from its card
  await win.locator(".al-chip[data-id=A02]").click();
  assert.match(await win.locator(".al-card.open[data-id=A02]").innerText(), /preparation · used by all claims/);
  await win.click(".al-card.open[data-id=A02] [data-act=switch-off]");
  await win.waitForFunction(() => /switched off/.test(document.querySelector('.al-card[data-id="A02"]')?.textContent ?? ""));
  await win.click('.al-card[data-id="A02"] [data-act=switch-on]');
  await win.waitForFunction(() => !/switched off/.test(document.querySelector('.al-card[data-id="A02"]')?.textContent ?? ""));
  // a click on a field's label (the usual way to focus it) must not collapse the card back to a chip
  await win.locator(".al-card.open[data-id=A02] .al-fields label").first().click({ position: { x: 4, y: 4 } });
  assert.equal(await win.locator(".al-card.open[data-id=A02]").count(), 1, "a click on a label keeps the card open");
  // preparation is edited in place
  await win.fill(".al-card.open[data-id=A02] textarea[data-f=summary]", "Exclude incomplete records (edited)");
  const p0 = await win.locator(".al-card[data-id=A02]").evaluate((e) => e.getBoundingClientRect().top);
  await win.locator(".al-card.open[data-id=A02] textarea[data-f=summary]").blur();
  await until("the edited preparation summary", async () => (await P("/actions/A02")).data.item.summary === "Exclude incomplete records (edited)");
  await win.waitForSelector(".al-card.open[data-id=A02]"); // the open chip survives the reload
  const p1 = await win.locator(".al-card[data-id=A02]").evaluate((e) => e.getBoundingClientRect().top);
  assert.ok(Math.abs(p1 - p0) < 40, `an edit moved the page by ${p1 - p0}px`);
  // the unused manual method can be deleted, after confirming
  await win.locator(".al-chip[data-id=A01]").click();
  await win.click(".al-card.open[data-id=A01] [data-act=delete]");
  await win.waitForSelector(".dlg-confirm button[value=ok]");
  await win.click(".dlg-confirm button[value=ok]");
  await win.waitForFunction(() => !document.querySelector('.al-card[data-id="A01"]'));
  assert.ok(!(await P("/actions")).data.some((a) => a.item.id === "A01"));

  step("claims reader: mark a methods passage and attach it to C2.1");
  const free2 = sents.find((id) => !picks.includes(id) && id !== free); // a sentence no item cites
  // nothing focused (the step above ended deselected): the bar asks for a claim
  await win.locator(`.al-text .sent[data-hid="${free2}"]`).scrollIntoViewIfNeeded();
  await win.locator(`.al-text .sent[data-hid="${free2}"]`).click();
  await win.waitForSelector(".al-bar:not([hidden])");
  assert.match(await win.locator(".al-bar").innerText(), /Select a claim to attach a method/);
  // Review Focus 4: an untestable claim has no method to attach one to
  await win.locator(".al-card[data-id=C3] .al-desc").click();
  assert.match(await win.locator(".al-bar").innerText(), /C3 is not testable/);
  assert.equal(await win.locator(".al-bar [data-act=attach]").count(), 0);
  // focus C2: one button per sub-claim
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  assert.equal(await win.locator(".al-bar [data-act=attach]").count(), 2);
  // Tick C2 first, so the unticking below is a real change: a new method means the claim needs reviewing again.
  await P("/claims/C2", "PATCH", { checked: true });
  assert.equal((await P("/claims/C2")).data.item.checked, true, "C2 is ticked before the attach");
  const before = (await P("/actions")).data.map((a) => a.item.id);
  await win.click(".al-bar [data-act=attach][data-leaf='C2.1']");
  await until("the marks to be used up", async () => await win.locator(".al-bar").isHidden());
  const created = (await P("/actions")).data.map((a) => a.item).find((a) => !before.includes(a.id));
  assert.ok(created, "a new analysis step was added");
  assert.deepEqual(created.refs, [{ extraction: jats, id: free2 }]);
  assert.equal(created.summary, created.quote);
  assert.ok(created.quote.length > 0);
  assert.deepEqual(created.depends_on, ["A02"], "it runs after the preparation steps");
  const c2 = (await P("/claims/C2")).data.item;
  assert.equal(c2.sub_claims.find((s) => s.id === "C2.1").method, created.id);
  assert.equal(c2.sub_claims.find((s) => s.id === "C2.2").method, "A03", "the other sub-claim keeps its method");
  assert.deepEqual(created.produces, c2.sub_claims.find((s) => s.id === "C2.1").evidence);
  assert.equal(c2.checked, false, "a new method unticks the claim");
  assert.ok((await P("/actions")).data.some((a) => a.item.id === "A03"), "the old method is kept");
  assert.match(await win.locator("#status").innerText(), new RegExp(`Attached ${created.id} to C2\\.1`));
  await win.waitForSelector(`.al-card.open[data-id="${created.id}"]`);
  // Review Focus 2: attaching again from a figure or table replaces the method, and the new step always has a summary
  // (refText renders a float as "<label>: <caption>", so a marked float never falls back to a generated summary).
  const fl = await win.locator(".al-text figure[data-hid]").first().getAttribute("data-hid").catch(() => null);
  if (fl) {
    await win.locator(`.al-text figure[data-hid="${fl}"]`).scrollIntoViewIfNeeded();
    await win.locator(`.al-text figure[data-hid="${fl}"]`).click();
    await win.waitForSelector(".al-bar:not([hidden])");
    await win.click(".al-bar [data-act=attach][data-leaf='C2.1']");
    await until("the marks to be used up", async () => await win.locator(".al-bar").isHidden());
    const m = (await P("/claims/C2")).data.item.sub_claims.find((s) => s.id === "C2.1").method;
    assert.notEqual(m, created.id, "C2.1 now uses the newer step");
    assert.ok((await P(`/actions/${m}`)).data.item.summary.length > 0, "the new step always has a summary");
  }
  await shot("04e-claims-attach");

  step("claims review: the method's full fields");
  // By now C2.1 has its own new method, so A03 is shared by C1 and C2.2 only: C1's pane warns about C2.
  await win.click("#cl-view [data-view=review]");
  await win.locator(".cl-tree .cl-item[data-id=C1]").click();
  const m3 = ".cl-detail .cl-method[data-action=A03]";
  await win.waitForSelector(`${m3} input[data-f=inputs]`);
  assert.equal(await win.locator(`${m3} textarea[data-f=quote]`).count(), 1);
  assert.equal(await win.locator(`${m3} input[data-f=depends_on]`).count(), 1);
  assert.equal(await win.locator(`${m3} .badge`).count(), 1);
  assert.equal((await win.locator(".cl-detail .cl-shared").innerText()).trim(), "Also used by C2: editing this unticks them too");
  // A03 already depends on A02 from the extraction, so edit its quote: a change only a real PATCH can produce.
  await win.fill(`${m3} textarea[data-f=quote]`, "Run the t-test (quote edited)");
  await win.locator(`${m3} textarea[data-f=quote]`).blur();
  await until("the edited method quote", async () => (await P("/actions/A03")).data.item.quote === "Run the t-test (quote edited)");
  await win.waitForSelector(`${m3} input[data-f=inputs]`);
  // The array-typed fields: depends_on goes to the backend as an array of real action ids. (The fixture has no input
  // resource to name in "inputs", so depends_on is the one covered here.) A03 depends on A02; add the other method.
  const other = (await P("/actions")).data.map((a) => a.item.id).find((id) => id !== "A02" && id !== "A03");
  assert.ok(other, "a second method exists to depend on");
  assert.deepEqual((await P("/actions/A03")).data.item.depends_on, ["A02"]);
  await win.fill(`${m3} input[data-f=depends_on]`, `A02, ${other}`);
  await win.locator(`${m3} input[data-f=depends_on]`).blur();
  await until("the edited depends_on", async () => (await P("/actions/A03")).data.item.depends_on.length === 2);
  const deps = (await P("/actions/A03")).data.item.depends_on;
  assert.ok(Array.isArray(deps));
  assert.deepEqual(deps, ["A02", other]);
  await win.waitForSelector(`${m3} input[data-f=depends_on]`);
  // A switched-off method is labelled in the Review pane, not shown as an ordinary one.
  assert.equal(await win.locator(`${m3} .badge`, { hasText: /^off$/ }).count(), 0, "A03 is in use: no off badge");
  await P("/actions/A03", "PATCH", { disabled: true });
  await win.locator("#route button").nth(0).click(); // leave the step and come back, so it loads the actions again
  await win.locator("#route button").nth(2).click();
  await win.click("#cl-view [data-view=review]");
  await win.locator(".cl-tree .cl-item[data-id=C1]").click();
  await win.waitForSelector(`${m3} .badge`);
  assert.equal(await win.locator(`${m3} .badge`, { hasText: /^off$/ }).count(), 1, "a switched-off method says so");
  // Review Focus 3, for a method (the round trip above it uses a preparation step): a switched-off method keeps its
  // card in the Reader, labelled, and the claim that points at it still links to it.
  await win.click("#cl-view [data-view=reader]");
  await win.waitForSelector(".al-card[data-id=A03]");
  assert.ok(await win.locator(".al-card[data-id=A03]").evaluate((e) => e.classList.contains("off")), "the switched-off method is faded");
  assert.match(await win.locator(".al-card[data-id=A03]").innerText(), /switched off/);
  await lights(".al-card[data-id=C1]", ".al-card[data-id=A03]", "C1 to still link to its switched-off method");
  await P("/actions/A03", "PATCH", { disabled: false });
  await win.locator("#route button").nth(0).click();
  await win.locator("#route button").nth(2).click();
  await win.click("#cl-view [data-view=review]");
  await win.locator(".cl-tree .cl-item[data-id=C1]").click();
  await win.waitForSelector(`${m3} input[data-f=depends_on]`);

  // Review saves a method's fields through the same compare-and-patch as the Reader: retyping the stored text with
  // a trailing space is not a change, and a stray PATCH would untick every claim using the method.
  await P("/claims/C1", "PATCH", { checked: true });
  assert.equal((await P("/claims/C1")).data.item.checked, true, "C1 is ticked before the no-op edit");
  const q3 = (await P("/actions/A03")).data.item.quote;
  await win.fill(`${m3} textarea[data-f=quote]`, `${q3}  `);
  await win.locator(`${m3} textarea[data-f=quote]`).blur();
  await win.waitForTimeout(1500); // a stray PATCH would land well within this
  assert.equal((await P("/actions/A03")).data.item.quote, q3, "retyping the same text with a trailing space saves nothing");
  assert.equal((await P("/claims/C1")).data.item.checked, true, "and so does not untick the claims using the method");

  // A bare click into a field whose stored text has stray whitespace must not save: the Reader compares the trimmed
  // value, and a stray PATCH would untick every claim using the method.
  const padded = "  Exclude incomplete records (padded)  ";
  await P("/actions/A02", "PATCH", { summary: padded });
  await P("/claims/C1", "PATCH", { checked: true });
  assert.equal((await P("/actions/A02")).data.item.summary, padded, "the backend keeps the padding");
  assert.equal((await P("/claims/C1")).data.item.checked, true);
  await win.locator("#route button").nth(0).click();
  await win.locator("#route button").nth(2).click();
  await win.click("#cl-view [data-view=reader]");
  await win.locator(".al-chip[data-id=A02]").click();
  const sum2 = ".al-card.open[data-id=A02] textarea[data-f=summary]";
  await win.waitForSelector(sum2);
  await win.locator(sum2).click();
  await win.locator(sum2).blur();
  await win.waitForTimeout(1500); // a stray PATCH would land well within this
  assert.equal((await P("/actions/A02")).data.item.summary, padded, "a click does not rewrite the field");
  assert.equal((await P("/claims/C1")).data.item.checked, true, "a click does not untick the claims using the method");

  step("claims reader: attach a method to a leaf that has none");
  // Review Focus 4's other half. Every leaf of the fixture has a method, so take C2.2's away first (the UI has no
  // way to do that; this is the setup, not what is under test). C2.2 is used rather than C1's, which is run below.
  const c2b = (await P("/claims/C2")).data.item;
  await P("/claims/C2", "PATCH", { sub_claims: c2b.sub_claims.map((s) => (s.id === "C2.2" ? { ...s, method: null } : s)) });
  assert.equal((await P("/claims/C2")).data.item.sub_claims.find((s) => s.id === "C2.2").method ?? null, null, "C2.2 has no method");
  await win.locator("#route button").nth(0).click(); // reload the step, so the reader sees it
  await win.locator("#route button").nth(2).click();
  await win.click("#cl-view [data-view=reader]");
  await win.waitForSelector(".al-card[data-id=C2]");
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  const free3 = sents.find((id) => !picks.includes(id) && id !== free && id !== free2);
  await win.locator(`.al-text .sent[data-hid="${free3}"]`).scrollIntoViewIfNeeded();
  await win.locator(`.al-text .sent[data-hid="${free3}"]`).click();
  await win.waitForSelector(".al-bar:not([hidden])");
  const before3 = (await P("/actions")).data.map((a) => a.item.id);
  await win.click(".al-bar [data-act=attach][data-leaf='C2.2']");
  await until("the marks to be used up", async () => await win.locator(".al-bar").isHidden());
  const made = (await P("/actions")).data.map((a) => a.item).find((a) => !before3.includes(a.id));
  assert.ok(made, "attaching to a leaf with no method adds the step");
  assert.deepEqual(made.inputs, [], "there is no old method to carry inputs over from");
  assert.equal((await P("/claims/C2")).data.item.sub_claims.find((s) => s.id === "C2.2").method, made.id);

  await win.click("#cl-view [data-view=review]");
  await win.waitForSelector(".cl-tree .cl-item[data-id=C1]");

  await win.locator(".cl-tree .cl-item[data-id=C1]").click();
  await win.waitForSelector(".cl-detail input[data-claim=C1][data-f=checked]");
  await win.check(".cl-detail input[data-claim=C1][data-f=checked]");
  await win.waitForSelector(".cl-detail button[data-act=run][data-claim=C1]:not([disabled])");
  await shot("04d-claims-ticked");
  await win.click(".cl-detail button[data-act=run][data-claim=C1]");
  // No data files in step 2 (the paper's supplementary file, fetched at creation, is a .docx): the app warns first and
  // offers the supplementary files; the fake agent records values anyway, so run anyway.
  await win.waitForSelector(".dlg-confirm button[value=anyway]", { timeout: 30_000 });
  assert.match(await win.locator(".dlg-confirm").innerText(), /No data to analyse/);
  assert.equal(await win.locator(".dlg-confirm button[value=fetch]").count(), 1);
  await win.click(".dlg-confirm button[value=anyway]");
  // The monolithic analysis.py written above was edited after it was generated: keep it (moved aside).
  await win.waitForSelector(".dlg-confirm button[value=keep]", { timeout: 30_000 });
  await win.click(".dlg-confirm button[value=keep]");
  const VERDICT = /^(Reproduced|Partly reproduced|Not reproduced)$/;
  // The verdict, or the error pop-up of a failed stage (fails at once, with its message).
  await win.waitForFunction(
    () => /Reproduced|Partly reproduced|Not reproduced/.test(document.querySelector(".cl-tree .cl-item[data-id=C1] .pill")?.textContent ?? "") || !!document.querySelector("dialog[open] h3")?.textContent?.startsWith("Running claim"),
    undefined,
    { timeout: 180_000 },
  );
  const failed = await win.locator("dialog[open]").count();
  assert.equal(failed, 0, failed ? await win.locator("dialog[open]").innerText() : "");
  const verdict = await win.locator(".cl-tree .cl-item[data-id=C1] .pill").innerText();
  assert.match(verdict, VERDICT, verdict);
  console.log(`  C1: ${verdict}`);
  const st = (await P("/claims:status")).data;
  assert.ok(st.find((x) => x.claim === "C1").ran_at, "C1 has run");
  assert.ok(fs.existsSync(path.join(folder, "claims", "C1.py")) && fs.existsSync(path.join(folder, "analysis.pre-claims.py")));
  await shot("04e-claim-ran");

  await win.locator("#route button").nth(6).click();
  await win.waitForSelector("#rp-report");
  step("report, ZIP and deposit (step 7)");
  await win.click("#rp-report");
  await win.waitForSelector("#rp-report-show", { timeout: 60_000 });
  await win.click("#rp-zip");
  await win.waitForSelector("#rp-zip-show", { timeout: 60_000 });
  await win.waitForSelector("#rp-deposit:not([disabled])", { timeout: 30_000 });
  const groups = await win.locator("[data-group]").evaluateAll((els) => els.map((e) => [e.dataset.group, e.checked]));
  assert.deepEqual(Object.fromEntries(groups), { paper: true }, "PLOS Biology is CC-BY, so the paper's material is included by default; no inputs");
  assert.match(await win.locator(".submit-card").innerText(), /C1\s+(Reproduced|Partly reproduced|Not reproduced)/, "the deposit lists the claims with their results");
  await win.fill("#rp-note", "Checked by the end-to-end test.");
  await shot("04-deposit-ready");
  await win.click("#rp-metadata");
  await win.waitForSelector(".dlg-wide textarea");
  const md = JSON.parse(await win.locator(".dlg-wide textarea").inputValue());
  assert.deepEqual(md.creators, [{ name: "Doe, Jane", orcid: "0000-0002-1825-0097" }]);
  assert.match(md.description, /end-to-end test/);
  assert.match(md.description, /C1/, "the description lists the claims");
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
  assert.match(dep.metadata.description, /C1/, "the deposited description lists the claims");
  assert.ok(zenodo.requests.filter((r) => r.path.startsWith("/api/deposit")).every((r) => r.auth === "Bearer test-token"));
  assert.match(await win.locator(".deposit.draft").innerText(), /10\.5072\/zenodo\.1000/);

  step("publish");
  await win.click(".deposit.draft button[data-action=publish]");
  await win.click(".dlg-confirm button[value=ok]");
  await win.waitForSelector(".deposit.published", { timeout: 30_000 });
  assert.equal(zenodo.depositions.get(dep.id).submitted, true);
  assert.equal(await win.locator("#rp-deposit").innerText(), "Deposit new version", "a published package with claims gets new versions");
  await shot("06-published");

  step("settings and the library afterwards");
  await win.click("#settings-btn");
  await win.waitForSelector(".dlg-settings .backend-line .ok", { timeout: 20_000 });
  await shot("07-settings");
  const line = await win.locator(".dlg-settings .backend-line").innerText();
  if (exe) assert.match(line, /bundled with the app/);
  console.log(`  backend: ${line}`);
  // Updates: the fake release (99.0.0) is newer; this build can't install it itself, so it says where to get it.
  assert.equal(await win.locator("[name=checkupd]").isChecked(), false);
  await win.click("#upd-check");
  await win.waitForSelector("#upd-status .ok", { timeout: 20_000 });
  assert.match(await win.locator("#upd-status").innerText(), /Version 99\.0\.0 is available/);
  assert.equal(await win.locator("#upd-page").isVisible(), true);
  await win.check("[name=checkupd]");
  await win.click(".dlg-settings button[value=save]");
  const saved = () => JSON.parse(fs.readFileSync(path.join(userData, "settings.json"), "utf8")).checkUpdates;
  for (let i = 0; i < 50 && saved() !== true; i++) await new Promise((r) => setTimeout(r, 100));
  assert.equal(saved(), true, "the update setting is saved");
  await win.click("#settings-btn");
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
