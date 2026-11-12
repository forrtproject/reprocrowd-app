# Claims breakdown: design

Date: 2026-11-12. Status: ready for approval.

Every point below is tagged by where it came from:
- **[decided]**: the user said it.
- **[played back]**: I described it to the user and they did not correct it.
- **[call]**: the designer's choice; the user can overrule it.
- **[builder]**: left to whoever builds it, within the limits given here.

## Intent

A paper makes a few broad claims, usually in its abstract, and justifies each one with quantitative evidence. To
reproduce a paper is to check those claims by redoing the statistical methods behind them. **[decided]**

ReproCrowd today works one level lower: it extracts every reported number (targets) and every analysis step
(actions), with nothing above them. A reviewer can see which numbers reproduced, but not what that means for what the
paper says. This feature adds the claims layer:

    broad claim  →  sub-claims (unless the claim is small enough to keep whole)  →  quantitative evidence + method

Reproduction becomes something you do **one claim at a time**. **[decided]**

The user's words, which are the source of truth: *"We use an LLM to extract the broader claims (as quotes or
paraphrased quotes), then for each claim find the smaller 'sub-claims' that it can be broken down into (also
quotes)... unless the claim is small enough to not be broken down. The LLM will then find the quantitative methods
used to prove the claims. We will output JSON for each broad claim, noting down the trace from main claim - sub claims
- quantitative evidence."*

A clickable mockup of the review screen exists: claude.ai/artifact/NQZGyfKDghZ9e7Yj42suJX. It is built on the noma
physiotherapy paper, doi 10.1371/journal.pgph.0001995. Its "reproduced" numbers, tolerances, R code and run stages
are invented for the demo and are not requirements.

## The user's flow

1. **Paper in.** The paper's JATS XML, parsed into the common document schema (the `ex-…` extraction). This already
   happens when a package is created. **[decided; that it is the JATS extraction: played back]**
2. **The LLM finds the claims.** One JSON object per broad claim. Each claim is a quote or a paraphrase that keeps its
   original quote. Its sub-claims are verbatim quotes. Each sub-claim (or an atomic claim) carries its quantitative
   evidence and the statistical method used. **[decided]**
3. **The user reviews the claims** in a screen like the mockup. A list of claims; clicking one shows its numbers and
   then its quoted text. The user can edit the claim and its evidence, add notes, and tick it as checked. **[decided]**
4. **The user runs one claim.** Running it carries out the existing steps 3–7 for that claim: analysis plan, master
   script, code generation, run & compare, report & deposit. **[decided]**
5. **Deposit at any time** once at least one claim has been run. **[decided]**

## Goals

- An LLM step that turns the paper into broad claims → sub-claims → evidence + method. Its output is stored as the
  LLM produced it; the user's edits are kept apart and can be undone, like targets and actions today. **[call]**
- A claims review screen that replaces today's step 2 (Targets) in the workflow. **[played back]**
- Per-claim runs inside **one package per paper**. Each new claim adds to the same analysis plan, master script,
  results, report and Zenodo record. **[decided]**
- Results reported **per claim** (and per sub-claim and per evidence value) in the comparison, the report and the
  deposit. **[decided for the deposit; call elsewhere]**
- A JSON trace per broad claim, in the user's format (below), in the package and in the deposit. **[decided]**

## Non-goals

- One package, script, report or Zenodo record per claim. **[decided against]**
- A second LLM pass in step 3 to work the analysis out again: the claims' methods *are* the plan. **[decided]**
- Stopping the run to ask the user when the paper under-describes a method: the coding agent works it out and flags
  it. **[decided]**
- Several reviewers, assignment or comments on claims. **[call]**
- Papers without JATS, and the PDF path. These stay out, as today. **[call]**

## Anti-goals: failure even if it works

- Running code for a claim nobody has checked. **[decided: run only after the tick]**
- Counting the same evidence twice when two claims rest on the same result. Each result is traced once and linked
  from the other claims. **[call, from the mockup]**
- A deposit that reads as if unrun claims failed. Unrun claims must say "not run"; untestable ones say "not
  testable". **[call]**
- Adding a claim silently breaking or throwing away code the agent already wrote for an earlier claim. **[call]**
- Losing what the LLM originally extracted when the user edits it. **[call]**

## What a claim is

### The JSON trace (one object per broad claim)

The user's format is the base. **[decided]**

```json
{
  "id": "10.1371/journal.pgph.0001995",
  "title": "<paper title>",
  "main_claim": "...",
  "sub_claims": [
    { "claim": "...", "quantitative_evidence": [ { "label": "...", "value": 17.6, "unit": "mm", "source": "p.4" } ] }
  ]
}
```

Additions, so the trace can be checked, linked into the paper and reproduced. All are **[call]**; the field names are
**[builder]**:
- **A claim id** (C1, C2, …) next to the paper's DOI, since `id` is the DOI in the user's format and a paper has
  several claims. Sub-claim ids C2.1, … and evidence ids.
- **Extraction mode.** `quote` or `paraphrase`; a paraphrase keeps `original_quote`. Sub-claims are always quotes.
  **[decided]**
- **Refs into the extraction** (sentence, table or figure ids), as targets have today. `source` stays as a readable
  location.
- **Atomic claims.** A claim that is not broken down has `sub_claims: []` and carries its own evidence and method.
- **A method per sub-claim** (or per atomic claim): name, an implementation hint, the Methods-section passage it comes
  from, and a method id, so that sub-claims sharing one model (in the mockup, C2.1 and C2.2) share it.
- **Evidence values with a comparator**, so thresholds like "p < 0.001" work (`<`, `>`, `=`), plus `decimals` as
  printed.
- **Not testable.** Recommendations and interpretations are `testable: false`, have no evidence, and list the claims
  they rest on (`depends_on`).
- **Overlaps.** `evidence_traced_under` when a claim's evidence is traced under another claim (the anti-goal above).
- **Review state.** checked (the tick), notes.
- **Results**, once run: a verdict per claim and per sub-claim, matched / tested counts, the reported vs. reproduced
  value per evidence item, the run time, and whether the claim was edited after the run (stale).
- **Skipped sentences**, at paper level: the abstract sentences the LLM did not treat as claims (background, study
  design), with the reason. These help a reviewer spot a missed claim. **[call, from the mockup]**

### How it maps onto what exists **[call]**

| Claims idea | ReproCrowd today | Change |
|---|---|---|
| Quantitative evidence | **Targets** (`schemas/curation.py`): quote, expected, comparator, decimals, unit, refs | Each evidence item *is* a target, which gains a link to its claim and sub-claim |
| Method | **Actions** of kind `analysis`, with `produces: [target ids]` | Each method is an analysis action producing its evidence targets |
| Shared groundwork (load data, rebuild the cohort) | Actions of kind `transform` | Come from the claims extraction's preparation steps (see Rules); added to the plan the first time a claim runs, and reused (`depends_on`) |
| Broad claim, sub-claims | Nothing | New curated file `claims.json`: same `manual` + `llm_runs` + `edit` overlay as targets.json and actions.json |
| The tick, notes | Nothing | Stored on the claim (in the overlay) |

The existing guarantee of `Curation` (LLM base never changed, user edits in `edit`, restore undoes them) applies to
claims as it does to targets.

## Rules

### Extraction **[decided in substance; prompt wording is builder]**

- The LLM reads the parsed paper and finds the broad claims, mostly in the abstract. For each one it decides whether
  to break it down: one test and comparison stays whole; several findings or methods become sub-claims. **[call, from
  the mockup]**
- For each sub-claim it finds the evidence values and the method, citing ids in the extraction, like the target
  prompt does today (`prompts/targets.md`).
- Each result passage is assigned to one claim; other claims link to it instead of repeating it. **[call]**
- **Preparation steps** are part of the same extraction call, not a second pass. These are the cohort, exclusion and
  cleaning steps the Methods section describes (for example "patients with incomplete records were excluded"). They
  become the package's shared transform actions. The extraction does not need the input files: the coding agent
  connects these steps to the data files, and fills in whatever preparation the paper leaves out, flagged as a guess
  like any other gap. **[call]**
- Output is validated against a schema. Invalid items are kept as rejected, as targets do today.
- **Re-extraction.** The user can extract again. A new run's claims arrive switched off, shown beside the current ones,
  and the user switches on the ones to keep. **[call]**
  - A claim that has been run keeps its id, code and results. A new run can never replace it silently; the user
    removes it by hand.
  - Results, script sections and deposit rows are keyed by claim id, so ids must be stable within a package.

### Checking (the tick)

- The tick is required before a run. **[decided]**
- These reset the tick and mark earlier results stale: changes to the claim text, its original quote, quote ↔
  paraphrase, a sub-claim's text, a method, or any evidence value; adding or removing a sub-claim or evidence; breaking
  a claim into sub-claims. **[call, from the mockup]**
- Notes and readable locations do not reset it. **[call]**
- The user can untick by hand.

### Running a claim

"Run" on a checked, testable claim with at least one evidence value does the following:
1. **Analysis plan (step 3).** The claim's methods become analysis actions producing its evidence targets. Shared
   transform actions are added the first time and reused afterwards. **[decided: methods become the plan directly]**
2. **Master script (step 4).** The claim's steps are added to the package's one master script.
3. **Code generation (step 5).** The coding agent implements **only the claim's new sections** and keeps every section
   written for earlier claims as it is. **[call: anti-goal]**
4. **Run & compare (step 6).** The whole script is run, so every claim's results are recomputed. A claim run earlier
   whose results change shows that.
5. **Report (step 7).** The report is regenerated with the claims section.

Constraints:
- Jobs run one at a time per package, as today (`job_running`). A second claim waits.
- **Gaps in the paper.** When the paper does not describe a method fully enough to recreate (which covariates, an
  unmentioned cleaning step), the agent works it out from the study's data and code and **flags the guess**.
  **[decided]** The agent prompt already asks for `# ASSUMPTION:` comments. The flags must also reach the user in the
  app and in the report, next to the claim they affect. **[call]**
- **Only ticked claims run.** The master script runs the shared preparation, then the sections of ticked claims only.
  The sections of unticked claims are skipped, and those claims keep their last result marked stale. **[call: follows
  "run only after the tick"]**
- **Re-running an edited claim.** The agent rewrites that claim's section only. Every other claim's section stays as
  it is. **[call]**
- **Isolation.** Each claim's code handles its own errors, so a failure in one claim's section makes its values "not
  computed" without stopping the other claims. **[call]**
- **Keeping earlier code.** Today `PUT …/script` regenerates the template and either refuses, keeps or overwrites
  files the agent has modified. Adding a claim needs a way to **add new sections to an already implemented script**
  without losing its code. **[builder: the mechanism, e.g. per-claim files or a section-append mode]**

### Statuses and results **[call, from the mockup]**

Each claim shows one status:
- **Needs review** (not ticked)
- **Ready to run** (ticked)
- **Running**
- **Reproduced**, **Partly reproduced**, **Not reproduced** (the result of a current, non-stale run)
- **Not testable**

How results are judged:
- **Each evidence value** is compared with the existing comparison rules in `compare.py`: rounding to the decimals as
  printed, the comparator, and the deviation threshold. Values the script did not compute are "not computed" and are
  not counted as tested.
- **Each sub-claim:** reproduced if every tested value matches, not reproduced if none match or none were tested,
  otherwise partly reproduced.
- **Each claim:** the same rule over all its evidence values pooled together. **[decided]**

### Depositing

- Allowed **whenever the user wants, once at least one claim has been run.** **[decided]**
- The deposit shows **which claims were run** and **each claim's own result**. **[decided]**
  - The ZIP holds the claims JSON trace with results: per claim, per sub-claim and per evidence value.
  - The Zenodo description gets a short table: one row per claim with its status and matched/tested. Claims not run
    say "not run"; untestable ones say "not testable". **[call]**
- Depositing again after more claims have been run creates a **new version of the same Zenodo record**. Each version
  lists the claims it covers. **[call]** Today a second deposit of the same package is refused (`duplicate_submission`
  in `zenodo.py`), so this needs Zenodo's new-version flow. The existing draft → review → publish behaviour stays.

## The review screen **[decided in outline; layout from the mockup is call]**

This replaces step 2 (Targets) in the wizard (`src/renderer/repro/wizard.ts`).

- **Left:** the claim list as a tree. Broad claims with Quote/Paraphrase and Atomic/"N sub-claims" labels; sub-claims
  indented underneath. Each row shows its status. The skipped-sentences note sits at the foot of the list.
- **Right**, for the selected claim:
  1. its numbers (the evidence, editable)
  2. its quoted text (with the original quote kept visible under a paraphrase)
  3. per sub-claim: quote, method, evidence
  4. notes
  5. the tick and **Run this claim**
  6. the last run's result by sub-claim and value
  7. the agent's flagged guesses
- It reuses the Reader view (`repro/aligned.ts`), so each claim's quotes are shown against the paper's text, as targets
  are today. **[call]**
- A JSON tab shows each claim's trace.
- Steps 3–7 stay in the wizard as views of the shared package: the plan built from the claims, the script, agent
  progress, the run and comparison, report and deposit. **[call]**

## Answered by the user

- **The Zotero plugin and reprocrowd-cli. [decided]** The plugin stays consistent with this app's backend: the app's
  `backend/` is the reference. The builder must:
  - offer the backend changes to reprocrowd-cli (`git subtree push`, as in the README)
  - keep them additive, so a package without claims behaves exactly as today and the plugin keeps working on the same
    backend
  - leave changes to the plugin itself out of this project
- **How a claim's result rolls up. [decided]** Pool every evidence value of the claim. C2 at 14/18 is **Partly
  reproduced** even though sub-claim C2.4 fails outright. The per-sub-claim results stay visible beside the claim's,
  so the failed sub-claim is not hidden.
- **Tolerance. [decided]** Use ReproCrowd's existing comparison rule unchanged: round to the decimals printed in the
  paper, apply the comparator, and allow the package-wide deviation threshold. There is no per-value tolerance field;
  the `tolerance` key of the user's example format is dropped from the trace. The LLM is not asked to estimate
  tolerances.

## Left to the builder

- Field names, the `claims.json` schema and its API routes, following `backend/docs/design.md` (Pydantic models,
  generated schemas, problem codes, ETags, `:verb` custom methods).
- The extraction prompt's wording, and whether claims come from one LLM call or several.
- How new sections are added to an implemented script.
- How per-claim results are computed from `outputs/results.json` (targets already carry ids; their claim links give
  the grouping).
- What becomes of today's Targets and Analysis-plan screens: keep them as advanced views, or drop them.
- Migrating packages that already have targets but no claims.
