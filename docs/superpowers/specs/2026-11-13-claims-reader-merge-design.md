# Claims and analysis plan: one Reader view

Date: 2026-11-13 · Branch: based on `claude/claims-breakdown` (f0736b3)

## Intent

The wizard has a Claims step (step 3) and an Analysis plan step (step 4). Since claims exist, the analysis plan is a
leftover: a claim's *method* is an action in `actions.json`, and the claims extraction writes those actions itself.
The plan step is now just a second, disconnected view of the same data. It shows every action field but not which
claim uses which action. The claims screen shows the link but only a method's summary, and never shows preparation
(transform) steps.

Goal: one Claims step where the paper's claims and the methods that test them are seen together on the paper. The
priority is **a clean, calm UI**. Code size and effort are not constraints.

## Goals

- Remove the Analysis plan step from the wizard. Claims becomes the only place to see and fix methods.
- In the Claims step's **Reader** view, show claims *and* methods on the paper, with the claim → method link visible.
- Fix a missing or wrong method by **marking the right methods passage in the paper and attaching it to the claim**.
- Keep **Review** as the place for detailed editing. Its method section gets the action fields the plan step used to
  host.
- Keep packages made before claims (no claims, so the backend uses the old single master script) editable.

## Non-goals

- No backend changes. Everything uses existing routes (see Constraints).
- No LLM call when attaching a method.
- No change to the Table and JSON views, the extraction dialog, banners, Run, or results.
- No new way to *create* preparation steps from the reader (the legacy table still allows it).

## Anti-goals (a failure even if it works)

- A crowded margin: a 30-card wall where cards drift far from their passages.
- An attach that silently lands on the wrong claim or sub-claim.
- Edits that untick claims the user wasn't working on, without telling them.
- Losing anything: an LLM-made method must never be deleted by an attach.

## Constraints (from the code)

- `AlignedView` (`src/renderer/repro/aligned.ts`) renders the paper with margin cards (`{id, anchors, html, cls}`),
  pushes overlapping cards down, draws gutter lines, and highlights a *list* of ids on hover (`hot(ids)`). Clicking
  sentences marks them, and a bar shows while anything is marked.
- Claims: `method: A… | null` and `evidence: T…[]` on each atomic claim and each sub-claim (a claim with sub-claims has
  neither of its own). JSON merge patch replaces arrays, so changing a sub-claim's method means sending
  `PATCH /claims/{id}` with the whole `sub_claims` list.
- Actions: `POST /actions` creates a manual action (`kind`, `summary` (min length 1), `quote`, `refs`, `inputs`,
  `depends_on`, `produces`). `PATCH /actions/{id}` edits one. Only manual items can be deleted. LLM entries are
  switched off with `PATCH … {disabled: true}`. Deleting an action is refused while any claim (including a
  switched-off one) uses it as a method, or another action lists it in `depends_on` (`curation.py` `_users`).
- In claims mode, the generated prepare file always holds every load step plus the preparation steps
  (`scripts.py` `_write_claims`), so a method gets its data whatever its `inputs` say.
- Any edit to an action unticks every claim whose method it is (backend `content_hash` / `uncheck_users`).
- In claims mode the script runs the ticked claims' methods plus **every** effective transform action
  (`claims.claim_scope`). An analysis action no claim uses is not run. The run's targets come from claims'
  `evidence`. `produces` only fills `analysis_plan.json`.
- Precedent: the Claims header already has a **"Targets table"** link that opens the old `CurationStep("targets")`
  inside step 3 with "← Back to the claims".

## Approaches

1. **Extend the claims Reader (chosen).** Build method, preparation and unused-method cards into the claims step's
   reader, add card-to-card links to `AlignedView`, and remove the plan step. Only the claims screen knows which
   claim uses which method, so the merged view belongs there.
2. **Put the old plan reader inside the claims Reader.** Reuses its mark → new-step code, but means two copies of the
   paper or two margins, and no claim ↔ method link, which is the point of the merge. Rejected as the least clean UI.
3. **Turn `CurationStep` into a shared reader for targets, actions and claims.** Cleaner code but no cleaner screen, and
   it rebuilds the part being removed. Rejected: the user ranked UI cleanliness first, and this only buys code
   structure.

Within (1), two margin densities were considered: *show everything* vs *focus on one claim*. The user chose
**focus on one claim**.

## Design (decided with the user)

### Wizard

- `new CurationStep(this, "actions")` is removed from the wizard's steps. Steps become: Paper, Inputs, Claims, Script,
  Code, Run, Report.
- Text that names step numbers is updated (e.g. `script.ts`: "No analysis steps in use (step 4)").
- The Claims header gets an **"Analysis steps table"** link next to "Targets table". It opens
  `CurationStep("actions")` inside step 3 the same way, with "← Back to the claims". This is how packages without
  claims edit their steps.

### Reader: focus on one claim

```
 Abstract   ──┤ [C2 ▸ selected]   [C1]  [C3]
 Methods    ──┤ ·A01 ·A02  [A03 method · C2.1, C1.1]  ·A04  [A05 method · C2.2]
 [2 marked · Attach to  C2.1 | C2.2 · Clear]
```

- **Claim cards** are always shown, at the claim's passages (as today: id, mode, tick, status pill, short text,
  **Review** button).
- **Methods start as small chips** (`A03`) next to the passages they cite: analysis methods, preparation steps and
  unused methods alike.
- **Selecting a claim** (clicking its card) opens its methods (its own and its sub-claims') into full method cards
  next to their methods-section passages. The link is shown by **highlighting only**: the claim's method cards and
  their passages light up and the other chips fade. There are no lines between cards. The only gutter lines are the
  existing passage → card ones.
- A **method card** shows: id, kind, summary, "used by C2.1, C1.1", and **Review**, which opens Review on the selected
  claim.
- **Preparation** chips/cards are labelled "preparation · used by all claims". They aren't linked to any claim.
- An **unused method** (an analysis action no claim uses) has a faded chip/card labelled "not used by any claim", with
  **Delete** (manual) or **Switch off** (LLM).
- **Hover:** hovering a claim card lights up its method cards/chips and their passages, and the other way round.
- The selected claim is the same `selected` state the Review view uses, so switching views keeps the focus.

### Attaching a method (mark → attach)

1. With a claim selected, mark one or more sentences in the paper.
2. The bar shows **"N marked · Attach to [leaf buttons] · Clear"**: one button per testable leaf of the selected claim
   (the claim itself if atomic, otherwise each sub-claim).
3. Attaching:
   - creates a **manual analysis action**: `refs` = the marked ids, `quote` = the marked sentences' text, `summary` =
     the same text (**prefilled with the quote, no LLM call**), `produces` = that leaf's evidence;
   - points the leaf's `method` at the new action (the backend unticks the claim);
   - leaves the leaf's **old method untouched**. If nothing else uses it, it shows as a faded unused card.
4. Marks clear, the new method card appears at the marked passage, and the status line says the claim was unticked and
   the summary should be rewritten in Review.

### Review: method section

- Each method (own or per sub-claim) shows the full action fields: **summary, quote, inputs, depends on**, with
  kind shown alongside.
- If the method is **shared**, a warning names the other claims, e.g. "Also used by C2: editing this unticks it
  too".

## Calls I made (the user can override)

- **Selection:** the Reader shares Review's `selected` claim, which today falls back to the first claim, so the Reader
  usually opens with a claim already focused. To keep that from turning into a wrong attach, the bar **names the
  claim**: "Attach to C1 · *X increases Y…*: [C1]" (for sub-claims, each button shows the sub-claim's id and a short
  text). Clicking the selected claim card again **deselects** it, in the Reader only. With nothing selected, marking
  still works and the bar says "Select a claim to attach a method". Review keeps its fallback to the first claim.
- **Cleanup controls on every method card, not just unused ones.** Any expanded method or preparation card has
  **Switch off** (LLM entries) or **Delete** (manual items; the backend may refuse while the action is in use, so
  show its reason). *Why:* with the plan step gone, the Reader is where duplicate preparation steps get cleaned up,
  for example after "Keep both" on a new extraction leaves two sets of preparation that would both run.
- **"Used by"** counts live claims only. A method used only by switched-off claims shows "used only by switched-off
  claims" with Switch off (not Delete, which the backend refuses), so turning the claim back on never finds its
  method deleted.
- **A new method's `inputs`** are copied from the leaf's old method, or left empty if it had none. *Why:* in claims
  mode the prepare file loads every data input anyway, so `inputs` only affects ordering in `analysis_plan.json`.
- **Clicking a chip** (preparation, unused, or another claim's method) opens that one card on its own. If it's an
  analysis method used by claims, the click also selects its first claim. Preparation cards are **edited in place**
  in the reader (summary, quote, inputs, depends on), because Review is organised by claim and has no place for them.
- **A new method's `depends_on`** = every current preparation (transform) action, matching what the extraction does.
- **The old method's `produces` is not changed** when a leaf is reattached. *Why:* editing it would untick every
  other claim using it. In claims mode, `produces` only fills `analysis_plan.json`, so the cost is a slightly stale
  list there.
- **Card look:** method cards get their own compact style rather than reusing the old plan step's cards.
- **Table and JSON views** are unchanged.

## Left to the builder

- How card-to-card links are added to `AlignedView` (e.g. an optional `links: string[]` on `AlignedItem` used by
  `hot()`). Whether chips are a variant of `.al-card` or a separate element, and how they're laid out (they still go
  through the overlap layout).
- How the reader keeps the selected claim visible when expanding cards pushes others down.
- Inputs / depends-on editors in Review (pickers over input resources and actions, as the old plan step did).
- Removing plan-step-only code paths left dead in `curation.ts` (the `"actions"` kind must keep working for the
  legacy link).
- Tests: extend `test/e2e.mjs` to cover the reader focus, attach, and the legacy link.
