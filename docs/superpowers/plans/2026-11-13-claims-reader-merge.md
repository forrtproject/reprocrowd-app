# Claims Reader merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Analysis plan wizard step and show each claim's methods in the Claims step's Reader (focus on one
claim), with mark → attach to fix a method, and full method fields in Review.

**Architecture:** Frontend only (no backend changes). `AlignedView` (`src/renderer/repro/aligned.ts`) gains linked
cards, compact chips, a select callback and in-place item updates. A new `src/renderer/repro/claimsreader.ts` builds
the Reader's claim cards, method chips/cards and the attach bar from a pure index (`src/renderer/repro/methods.ts`).
`claims.ts` hosts it and gets the bigger Review method section. The wizard drops `CurationStep(this, "actions")`, which
stays reachable from a header link for packages without claims.

**Tech Stack:** TypeScript (tsc 7, `npm run typecheck`), plain DOM rendering with template strings, esbuild
(`npm run build`), Electron + playwright-core end-to-end test (`test/e2e.mjs`, `npm run test:e2e`).

**Spec:** `docs/superpowers/specs/2026-11-13-claims-reader-merge-design.md`

## Global Constraints

- No backend changes. Use only: `GET /actions?include_disabled=true`, `POST /actions`, `PATCH /actions/{id}`,
  `DELETE /actions/{id}`, `PATCH /claims/{id}`, `GET /inputs`.
- No LLM call when attaching. The new method's `summary` = its `quote` = the marked passages' text (`refText`).
- The link between a claim and its methods is shown by **highlighting only**: no new lines between cards. The only
  gutter lines stay passage → card.
- Never delete an LLM-made action. LLM entries get **Switch off/on** (`PATCH {disabled}`). Only manual ones get
  **Delete**. Show the backend's refusal message (e.g. `in_use`) as an error.
- "Used by" counts **live** claims only (not switched off). A method used only by switched-off claims is labelled
  "used only by switched-off claims" and gets Switch off, never Delete.
- Step order after the change: Paper, Inputs, Claims, Script, Code, Run, Report (7 steps). Every user-facing
  "step N" reference must match it.
- Copy (exact):
  - Header link: `Analysis steps table`
  - Preparation label: `preparation · used by all claims`
  - Unused label: `not used by any claim`
  - Off-only label: `used only by switched-off claims`
  - Bar with no claim focused: `Select a claim to attach a method`
  - Bar for an untestable claim: `<id> is not testable, so it has no method`
  - Shared warning: `Also used by <ids>: editing this unticks them too`
  - Status after attach: `Attached <A id> to <leaf id>; <claim id> is unticked. Rewrite the method's summary in Review.`

## Review Focus

1. **Selecting a claim or opening a chip must not move the page.** Today a card click calls `reveal()`, which scrolls
   to the passage. In the Reader the user is reading; the clicked card must stay within 40 px of where it was.
   (Test in Task 3.)
2. **Marks that produce no text** (a figure or table with no label or caption) would give an empty `summary`, which
   the backend refuses (`min_length=1`). Expect the fallback summary `Implement the marked passage for <leaf id>`.
   (Test in Task 4, using a marked figure or table when the e2e paper has one.)
3. **A switched-off LLM method must stay visible and reversible.** Expect a faded chip labelled `switched off` with
   Switch on. It must never show as "missing step", and a claim pointing at it must still link to it. (Test in Task 3.)
4. **Untestable or method-less claims in focus.** For an untestable claim (C3 in the fixture), expect the
   untestable bar text and no attach buttons. For a leaf with no method yet, attaching works with `inputs: []`.
   (Test in Task 4.)
5. **Attach is two writes** (`POST /actions`, then `PATCH /claims/{id}`). If the PATCH fails, expect the new manual
   action to be deleted again, and the backend's error shown. The e2e harness can't inject a failure, so the Task 4
   reviewer checks this in code.

---

### Task 1: Remove the Analysis plan step; renumber; legacy "Analysis steps table" link

**Files:**
- Modify: `src/renderer/repro/wizard.ts` (steps list ~line 55–62, header comment "eight reproduction steps")
- Modify: `src/renderer/repro/claims.ts` (header next to `#cl-targets`; `legacy` / `showLegacy` handling)
- Modify (step numbers): `src/renderer/repro/script.ts:46,80`, `codegen.ts:50`, `run.ts:37`, `report.ts:92,114`,
  `inputs.ts:198`, `claims.ts:40` (comment), `state.ts:6` (comment), `style.css:515` (comment)
- Test: `test/e2e.mjs`

**Interfaces:**
- Produces: `ClaimsStep` field `private legacy?: { kind: "targets" | "actions"; step: CurationStep }`. Either
  header link opens the matching `CurationStep` inside step 3 with the existing "← Back to the claims" link.
  Element ids: `#cl-targets` (existing), `#cl-actions-table` (new).

- [ ] **Step 1: Write the failing test.** In `test/e2e.mjs`:
  - In "every step renders": `assert.equal(steps, 7)`.
  - Replace the step "analysis plan: the reader too" with "analysis steps table: the legacy plan screen from step 3".
    It does: `#route button` nth(2) → if `#cl-back` is shown, click it → click `#cl-actions-table` → wait for
    `.al-cards .al-card[data-id=A01]`, `shot("04c-plan-reader")` → click `#cl-back` → wait for `#cl-extract`.
  - Change the claims step's opening to not depend on the targets table still being open: click `#cl-back` only
    if it's present.
  - Report step: `#route button` nth(7) → nth(6). Update the comment "steps 5–8" → "steps 4–7".
  - Add: `assert.doesNotMatch(await win.locator("#route").innerText(), /Analysis plan/)`.
- [ ] **Step 2: Run it and check it fails.** Run: `npm run build && npm run test:e2e`. Expected: FAIL at
  `assert.equal(steps, 7)` (actual 8).
- [ ] **Step 3: Implement.**
  - Drop `new CurationStep(this, "actions")` from `Wizard.steps`, and its import if unused.
  - Generalise the claims step's legacy view to the `legacy` shape above and add the `#cl-actions-table` link
    (copy: `Analysis steps table`, title: "The analysis steps, for packages made before claims").
  - Renumber the user-facing strings:
    - script 5→4, code 6→5, run/compare 7→6, report 8→7.
    - `script.ts:46`: `No analysis steps in use (step 4).` becomes `No methods in use (step 3, Claims).`
    - `inputs.ts:198`: `used by analysis steps (step 4); remove it there first` becomes
      `used by methods (step 3, Claims); remove it there first`.
- [ ] **Step 4: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: typecheck clean, and the e2e run ends without an assertion error (exit 0).
- [ ] **Step 5: Commit.** `git commit -am "app: drop the Analysis plan step; its table stays reachable from Claims"`

### Task 2: AlignedView: linked cards, chips, select callback, in-place item updates

**Files:**
- Modify: `src/renderer/repro/aligned.ts`
- Modify: `src/renderer/style.css` (`.al-chip`, `.al-card.dim`, `.al-card.open` next to the existing `.al-card` rules)
- Test: `test/e2e.mjs` (Task 1's legacy plan step keeps covering plain cards; chip and link coverage comes in Task 3)

**Interfaces:**
- Produces:
  - `AlignedItem.links?: string[]`: ids of other items lit up together with this one. `hot(ids)` adds each id's
    `links` (one level, not recursive) and highlights those cards and their passages too.
  - `AlignedItem.chip?: boolean`: rendered as `<div class="al-card al-chip …">`. It's still positioned by `layout()`
    and still gets a passage → chip gutter line.
  - `AlignedOptions.onSelect?(id: string): void`. When set, a card click without `[data-act]` calls it **instead of**
    `reveal()`. When not set, behaviour is unchanged.
  - `AlignedView.setItems(items: AlignedItem[]): void`: replaces `o.items`, re-renders only `.al-side` (unlinked list
    and cards), re-runs `markRefs()` and `schedule()`, and keeps the text, marks, bar and scroll position.
  - `AlignedView.refreshBar(): void`: re-renders the bar from `o.barHtml` (used when the focus changes while
    something is marked).
- Class `dim` on a card: faded (opacity), but unlike `off` it does **not** turn its passages into `ref-off`.

- [ ] **Step 1: Implement the interfaces above.** `markRefs()` must clear the old `ref`/`ref-off` classes before
  re-marking, since `setItems` calls it again.
- [ ] **Step 2: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: clean, and the targets reader and legacy plan steps still pass (no behaviour change without the new options).
- [ ] **Step 3: Commit.** `git commit -am "aligned view: linked cards, chips, select callback, in-place updates"`

### Task 3: The Reader: focus on one claim, method chips and cards, cleanup controls

**Files:**
- Create: `src/renderer/repro/methods.ts`: pure index over claims and actions, no DOM
- Create: `src/renderer/repro/claimsreader.ts`: builds the Reader's `AlignedItem`s and handles its card events
- Modify: `src/renderer/repro/claims.ts`: load actions with origin (`include_disabled: true`) and `/inputs`; replace
  `drawReader` with `ClaimsReader`; reader focus state
- Modify: `src/renderer/types.ts`: add `ItemOrigin`
- Modify: `src/renderer/style.css`
- Test: `test/e2e.mjs`

**Interfaces:**
- `types.ts`: `export interface ItemOrigin { source: "manual" | "llm"; run?: string | null; edited?: boolean; disabled?: boolean }`
- `methods.ts`:
  ```ts
  export interface Leaf { claim: string; id: string; text: string; method: string | null; evidence: string[] }
  export type MethodRole = "method" | "preparation" | "unused" | "off-only";
  export interface MethodInfo { action: Action; origin: ItemOrigin; role: MethodRole; usedBy: Leaf[]; offUsedBy: Leaf[] }
  /** Testable leaves: the claim itself if atomic, else its sub-claims; [] for an untestable claim. */
  export function leaves(c: Claim): Leaf[];
  /** Method action ids of a claim (own and sub-claims'), in order, without repeats. */
  export function claimMethods(c: Claim): string[];
  export function methodIndex(actions: { item: Action; origin: ItemOrigin }[], claims: { claim: Claim; off: boolean }[]): Map<string, MethodInfo>;
  ```
  Role rules: `kind === "transform"` → `preparation`. Otherwise, live users → `method`; only switched-off users →
  `off-only`; none → `unused`.
- `claims.ts` state: `private actions = new Map<string, { item: Action; origin: ItemOrigin }>()`,
  `private inputs: Resource[] = []`, `private readerNone = false` (true after a deselect in the Reader; any selection
  clears it; Review ignores it), `private openChip = ""` (id of a chip opened on its own).
- `claimsreader.ts`:
  ```ts
  export interface ReaderCtx {
    doc: Document | undefined; meta: any; jats: string;
    rows: { claim: Claim; off: boolean }[]; index: Map<string, MethodInfo>;
    status: Map<string, ClaimStatus>; marked: Set<string>;
    focus(): string;              // focused claim id, "" when none
    openChip(): string;
    select(claimId: string): void;   // "" deselects
    setOpenChip(actionId: string): void;
    review(claimId: string): void;   // switch to Review on that claim
    edit(label: string, fn: () => Promise<unknown>): Promise<unknown>;  // ClaimsStep.edit; reloads after
  }
  export class ClaimsReader { constructor(host: HTMLElement, ctx: ReaderCtx); refocus(): void }
  ```
  `refocus()` rebuilds the items and calls `AlignedView.setItems` and `refreshBar`, with no full redraw.
- Card `data-act`s: `review`, `switch-off`, `switch-on`, `delete`, `field` (with `data-f`), `attach` (bar, Task 4),
  `clear` (bar).

Rendering rules (from the spec):
- **Claim card:** as today (id, mode, tick, pill, short text, Review), plus `links` = `claimMethods(c)`. Clicking the
  card body selects it; clicking the focused claim again deselects it (`select("")`).
- **Every action is a chip** (`chip: true`) anchored at its refs in the JATS extraction. The chip reads
  `<id> · <role word>`, where the role word is `method`, `preparation`, `not used by any claim`,
  `used only by switched-off claims` or `switched off`.
- **The focused claim's methods** render as full cards (`cls: "open"`): kind, summary,
  `used by C2.1, C1` (live leaf ids), Review. The focused claim's other method chips get no `dim`; every other chip
  gets `dim`.
- **An opened chip** (`openChip`) renders as a full card on its own:
  - **Preparation:** `preparation · used by all claims`, with in-place editors: `<textarea data-act="field"
    data-f="summary">`, the same for `quote`, and `<input data-act="field" data-f="inputs|depends_on">` (comma lists via
    `util.list`, with a datalist of input names).
  - **Analysis method used by claims:** opening it also selects its first live using claim.
  - **Cleanup controls on every open card:** `Switch off` for live LLM entries, `Switch on` when off, `Delete` for
    manual ones (except `off-only`).
- `links` on a method card/chip = the claim ids of its live users.
- **Data edits go through `ctx.edit` (`Wizard.busy`), which redraws the whole step at the start and the end.** The
  Reader must survive that: focus, `openChip` and marks live in `ClaimsStep`, and `ClaimsReader` saves the scroll
  offset of the Reader's scroll container before a full redraw and restores it after. Focus and chip changes use
  `refocus()`, never a full redraw.

- [ ] **Step 1: Write the failing test.** New e2e step "claims reader: focus on one claim", right after the claims
  extraction (the 3-claim assertion) and before C1 is ticked. With the fake LLM, the fixture is:
  - A01: manual analysis "everything", used by no claim.
  - A02: LLM preparation (P1).
  - A03: LLM method M1, used by C1, C2.1 and C2.2.
  - C3: untestable.

  Assert, in order:
  ```js
  await win.click("#cl-view [data-view=reader]");
  await win.waitForSelector(".al-card[data-id=C1]");
  // C1 is focused by default (Review's fallback): its method is an open card, the rest are dimmed chips
  assert.ok(await win.locator(".al-card.open[data-id=A03]").count() === 1);
  assert.match(await win.locator(".al-chip[data-id=A02]").innerText(), /preparation/);
  assert.match(await win.locator(".al-chip[data-id=A01]").innerText(), /not used by any claim/);
  assert.ok(await win.locator(".al-chip[data-id=A01]").evaluate((e) => e.classList.contains("dim")));
  assert.match(await win.locator(".al-card.open[data-id=A03]").innerText(), /C1/);
  assert.match(await win.locator(".al-card.open[data-id=A03]").innerText(), /C2\.1/);
  // highlight-only links, both ways; no extra lines
  await win.locator(".al-card[data-id=C1]").hover();
  assert.ok(await win.locator(".al-card[data-id=A03]").evaluate((e) => e.classList.contains("hot")));
  await win.locator(".al-card[data-id=A03]").hover();
  assert.ok(await win.locator(".al-card[data-id=C2]").evaluate((e) => e.classList.contains("hot")));
  assert.equal(await win.locator(".al-lines path").count(), await win.locator(".al-cards > .al-card").count());
  // Review Focus 1: selecting doesn't move the page
  const y0 = await win.locator(".al-card[data-id=C2] .al-desc").evaluate((e) => e.getBoundingClientRect().top);
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  const y1 = await win.locator(".al-card[data-id=C2] .al-desc").evaluate((e) => e.getBoundingClientRect().top);
  assert.ok(Math.abs(y1 - y0) < 40, `C2 moved ${y1 - y0}px`);
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
  // preparation is edited in place
  await win.fill(".al-card.open[data-id=A02] textarea[data-f=summary]", "Exclude incomplete records (edited)");
  const p0 = await win.locator(".al-card[data-id=A02]").evaluate((e) => e.getBoundingClientRect().top);
  await win.locator(".al-card.open[data-id=A02] textarea[data-f=summary]").blur();
  // poll P("/actions/A02") until summary === "Exclude incomplete records (edited)"
  await win.waitForSelector(".al-card.open[data-id=A02]");   // the open chip survives the reload
  const p1 = await win.locator(".al-card[data-id=A02]").evaluate((e) => e.getBoundingClientRect().top);
  assert.ok(Math.abs(p1 - p0) < 40, `an edit moved the page by ${p1 - p0}px`);
  // the unused manual method can be deleted
  await win.locator(".al-chip[data-id=A01]").click();
  await win.click(".al-card.open[data-id=A01] [data-act=delete]");
  await win.waitForFunction(() => !document.querySelector('.al-card[data-id="A01"]'));
  assert.ok(!(await P("/actions")).data.some((a) => a.item.id === "A01"));
  await shot("04d-claims-reader");
  ```
  (Wait for the PATCH with a short `waitForFunction`/poll on `P("/actions/A02")` rather than a fixed timeout.)
- [ ] **Step 2: Run it and check it fails.** Run: `npm run build && npm run test:e2e`. Expected: FAIL at the first
  `.al-card.open[data-id=A03]` assertion.
- [ ] **Step 3: Implement `methods.ts`, `ItemOrigin`, `ClaimsReader` and the `claims.ts` wiring as specified above.**
  In `claims.ts`, `load()` fetches `/actions` with `{ include_disabled: true }` and `/inputs`. `methodHtml` reads
  `this.actions.get(id)?.item`. A switched-off action shows its chip, never "missing step".
- [ ] **Step 4: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: clean, exit 0.
- [ ] **Step 5: Commit.** `git commit -am "claims reader: focus on one claim with its methods; cleanup controls"`

### Task 4: Mark → attach a method to the focused claim

**Files:**
- Modify: `src/renderer/repro/claimsreader.ts` (bar HTML and the `attach` action)
- Test: `test/e2e.mjs`

**Interfaces:**
- Consumes: `leaves`, `methodIndex` (Task 3), `ReaderCtx.edit`, `refText(doc, ids)` from `src/renderer/doc.ts`,
  `AlignedView.refreshBar` (Task 2).
- Produces: `async function attachMethod(ctx: ReaderCtx, leaf: Leaf): Promise<string>` in `claimsreader.ts`. It
  returns the new action id and does:
  1. `POST /actions` with:
     - `kind: "analysis"`
     - `refs: [...marked].map(id => ({ extraction: ctx.jats, id }))`
     - `quote: refText(doc, ids)`
     - `summary`: the same text, or `Implement the marked passage for <leaf.id>` if that's empty
     - `inputs`: the old method's inputs, or `[]`
     - `depends_on`: the ids of every live `preparation` action
     - `produces: leaf.evidence`
  2. `PATCH /claims/<leaf.claim>`: `{ method: newId }` for an atomic claim, or `{ sub_claims: <full list with that
     sub-claim's method = newId> }`. If this fails, `DELETE /actions/<newId>` and rethrow.
  3. Clear the marks and set the focus's open chip to the new id. Show the status line from Global Constraints
     **after** `ctx.edit` resolves (`Wizard.busy` writes `<label>: done` at the end, which would overwrite it).

  The old method is never changed.
- Bar (`barHtml(n)`):
  - No focus: `<b>n</b> marked · Select a claim to attach a method · Clear`.
  - Untestable focus: `<id> is not testable, so it has no method · Clear`.
  - Otherwise: `<b>n</b> marked · Attach to <claim id> · <claim text, 60 chars>:`, then one
    `<button data-act="attach" data-leaf="<leaf id>">` per leaf (label: leaf id plus short text), then `Clear`.
  - Changing the focus calls `refreshBar()`.

- [ ] **Step 1: Write the failing test.** New e2e step "claims reader: mark a methods passage and attach it to
  C2.1", after Task 3's step:
  ```js
  const free2 = sents.find((id) => !picks.includes(id) && id !== free);   // a sentence no item cites
  // nothing focused (Task 3 ended deselected): the bar asks for a claim
  await win.locator(`.al-text .sent[data-hid="${free2}"]`).scrollIntoViewIfNeeded();
  await win.locator(`.al-text .sent[data-hid="${free2}"]`).click();
  assert.match(await win.locator(".al-bar").innerText(), /Select a claim to attach a method/);
  // Review Focus 4: an untestable claim
  await win.locator(".al-card[data-id=C3] .al-desc").click();
  assert.match(await win.locator(".al-bar").innerText(), /C3 is not testable/);
  assert.equal(await win.locator(".al-bar [data-act=attach]").count(), 0);
  // focus C2: one button per sub-claim
  await win.locator(".al-card[data-id=C2] .al-desc").click();
  assert.equal(await win.locator(".al-bar [data-act=attach]").count(), 2);
  const before = (await P("/actions")).data.map((a) => a.item.id);
  await win.click(".al-bar [data-act=attach][data-leaf='C2.1']");
  await win.waitForFunction(() => document.querySelector(".al-bar")?.hidden);
  const created = (await P("/actions")).data.map((a) => a.item).find((a) => !before.includes(a.id));
  assert.deepEqual(created.refs, [{ extraction: jats, id: free2 }]);
  assert.equal(created.summary, created.quote);
  assert.ok(created.quote.length > 0);
  assert.deepEqual(created.depends_on, ["A02"]);
  const c2 = (await P("/claims/C2")).data.item;
  assert.equal(c2.sub_claims.find((s) => s.id === "C2.1").method, created.id);
  assert.equal(c2.sub_claims.find((s) => s.id === "C2.2").method, "A03", "the other sub-claim keeps its method");
  assert.deepEqual(created.produces, c2.sub_claims.find((s) => s.id === "C2.1").evidence);
  assert.equal(c2.checked, false);
  assert.ok((await P("/actions")).data.some((a) => a.item.id === "A03"), "the old method is kept");
  assert.match(await win.locator("#status").innerText(), new RegExp(`Attached ${created.id} to C2\\.1`));
  assert.equal(await win.locator(`.al-card.open[data-id="${created.id}"]`).count(), 1);
  // Review Focus 2: a figure or table with no text (only if the paper has one)
  const fl = await win.locator(".al-text figure[data-hid]").first().getAttribute("data-hid").catch(() => null);
  if (fl) {
    await win.locator(`.al-text figure[data-hid="${fl}"]`).click();
    await win.click(".al-bar [data-act=attach][data-leaf='C2.2']");
    await win.waitForFunction(() => document.querySelector(".al-bar")?.hidden);
    const m = (await P("/claims/C2")).data.item.sub_claims.find((s) => s.id === "C2.2").method;
    assert.ok((await P(`/actions/${m}`)).data.item.summary.length > 0);
  }
  await shot("04e-claims-attach");
  ```
  (`#status` is the footer element `Wizard.setStatus` writes to, defined in `src/renderer/package.ts`.)
- [ ] **Step 2: Run it and check it fails.** Run: `npm run build && npm run test:e2e`. Expected: FAIL at the
  "Select a claim to attach a method" assertion.
- [ ] **Step 3: Implement `attachMethod`, the bar states and the `attach` action as specified.**
- [ ] **Step 4: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: clean, exit 0. C1's run later in the test is unaffected (C1 still uses A03).
- [ ] **Step 5: Commit.** `git commit -am "claims reader: mark a methods passage and attach it to a claim"`

### Task 5: Review: full method fields and the shared-method warning

**Files:**
- Modify: `src/renderer/repro/claims.ts` (`methodHtml`, `detailHtml`, the review `change` handler for `data-action`)
- Modify: `src/renderer/style.css` (`.cl-method`, `.cl-shared`)
- Test: `test/e2e.mjs`

**Interfaces:**
- Consumes: `methodIndex` (Task 3), `this.inputs`, `util.list`.
- Produces: `methodHtml(id: string | null | undefined, claimId: string)`. Inside
  `<div class="cl-method" data-action="<id>">` it renders:
  - the kind badge;
  - `<textarea data-action data-f="summary">` and `data-f="quote"`;
  - `<input data-action data-f="inputs">` (with a datalist of input names) and `data-f="depends_on"`;
  - when the method's live users include other claims than `claimId`:
    `<p class="cl-shared warn-inline">Also used by <ids>: editing this unticks them too</p>`, with the other
    claim ids, comma-separated.

  The existing change handler PATCHes `/actions/{id}`, sending lists for `inputs`/`depends_on`.

- [ ] **Step 1: Write the failing test.** New e2e step "claims review: the method's full fields" after Task 4's step:
  ```js
  await win.click("#cl-view [data-view=review]");
  await win.locator(".cl-tree .cl-item[data-id=C1]").click();
  await win.waitForSelector(".cl-detail .cl-method[data-action=A03] input[data-f=inputs]");
  assert.equal(await win.locator(".cl-detail .cl-method[data-action=A03] textarea[data-f=quote]").count(), 1);
  assert.equal(await win.locator(".cl-detail .cl-method[data-action=A03] input[data-f=depends_on]").count(), 1);
  assert.match(await win.locator(".cl-detail .cl-shared").innerText(), /Also used by C2: editing this unticks them too/);
  await win.fill(".cl-detail .cl-method[data-action=A03] input[data-f=depends_on]", "A02");
  await win.locator(".cl-detail .cl-method[data-action=A03] input[data-f=depends_on]").blur();
  // poll P("/actions/A03") until depends_on equals ["A02"]
  ```
  The existing step that ticks and runs C1 follows unchanged.
- [ ] **Step 2: Run it and check it fails.** Run: `npm run build && npm run test:e2e`. Expected: FAIL waiting for
  `input[data-f=inputs]`.
- [ ] **Step 3: Implement `methodHtml(id, claimId)` and the list handling in the change handler.**
- [ ] **Step 4: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: clean, exit 0, and the C1 run reaches a verdict as before.
- [ ] **Step 5: Commit.** `git commit -am "claims review: full method fields and the shared-method warning"`

### Task 6: Remove code only the plan step used

**Files:**
- Modify: `src/renderer/repro/curation.ts`

**Interfaces:**
- Consumes: Task 1 (the `"actions"` kind is now reached only from `#cl-actions-table`).

- [ ] **Step 1: Check the `"actions"` paths in `CurationStep`.** Delete only wording and branches that assume it is a
  wizard step of its own: the header comment ("Steps 2 and 3"), and the hints that cite "step 2" / "step 3" (lines 164,
  455), which become "Inputs" / "Claims". Keep everything the legacy table needs (add, edit, LLM run, reader, table).
- [ ] **Step 2: Run the checks and make sure they pass.** Run: `npm run typecheck && npm run build && npm run test:e2e`.
  Expected: clean, exit 0, including the legacy plan screen step.
- [ ] **Step 3: Commit.** `git commit -am "curation: the actions table is a legacy view of the Claims step"`
