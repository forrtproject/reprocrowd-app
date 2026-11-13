# Codebook: gummi bear perception study

Built in session A, 2026-08-17. Documents every variable in the analysis-ready
files, its coding, its provenance, and the formula where a variable is derived.

## Files

| File | Rows | Unit | Content |
|---|---|---|---|
| `Gummibear_data.xlsx` | 4 + 100 | mixed | Source of truth. Two sheets, recorded during data collection in August 2024. Never edited. |
| `info_gummibear_data.txt` | | | Source codebook as written by Mitja. |
| `gummibear_trials.csv` | 100 | trial | Tasting trials, long format, with derived design, exposure, and response-bias variables. |
| `gummibear_apriori.csv` | 4 | person | A priori per-flavor predictions and likings, wide. |
| `gummibear_apriori_long.csv` | 24 | person x flavor | Reshape of the a priori per-flavor values with the matching trial aggregates. |

All three derived files are produced by `analysis/build_dataset.py` from the
source workbook alone. The script runs 31 integrity checks and raises if any
fails; it does not repair anything silently. Reproduce with
`python analysis/build_dataset.py` from the project root.

**Scope decision (Mitja, 2026-08-17).** Only the per-flavor a priori measures are
analysed: `Pred_<flavor>` and `Like_<flavor>`. Where the analyses need an average
a priori value, it is the average across the six flavors. The two
general-question measures in `Tabelle1` are not carried into the derived files:
the `MPred_Ges` round robin of overall accuracy expectations, and `Like_Ges`,
general gummi bear liking. Both were collected as additional questions, both are
less precise than the per-flavor items, and neither is needed. They remain in the
source workbook, which is the version archived openly, and the Method discloses
that they were collected. The build script still range-checks them so that a
future change of mind starts from verified values.

Software: Python 3.11, pandas 2.3.3, numpy 2.2.6. No stochastic step.

## Fixed coding conventions

**Flavor codes** (as in the source file; the same six-flavor set was known to all
participants and printed on the package, so chance accuracy is 1/6 = 16.7%):

| Code | German | English |
|---|---|---|
| E | Erdbeere | strawberry |
| O | Orange | orange |
| Ap | Apfel | apple |
| H | Himbeere | raspberry |
| Z | Zitrone | lemon |
| An | Ananas | pineapple |

**Persons.** 1 = Ada (age 8), 2 = Ella (13), 3 = Natalie (42), 4 = Mitja (47).
Ages are completed years in August 2024, supplied by Mitja; they are not in the
source file.

**Sessions.** 1 = 2024-08-05, 2 = 2024-08-06, 3 = 2024-08-08, 4 = 2024-08-09,
5 = 2024-08-18. Sessions 1 to 4 in Italy, session 5 in Germany. The five days
are not consecutive: one day was skipped between sessions 2 and 3, and nine days
elapsed between sessions 4 and 5.

**Packages.** Package 1 supplied sessions 1 to 3 and trials 61 to 72 (the first
three rounds of session 4). Package 2 supplied trials 73 to 80 and all of
session 5. Both packages were bought in Italy.

---

## `gummibear_trials.csv` (100 rows, one per tasting trial)

### Identifiers and design

| Variable | Coding | Provenance |
|---|---|---|
| `trial_id` | 1 to 100, chronological | `Tabelle2!Test_ID`, verbatim |
| `person_id` | 1 to 4 | `Tabelle2!ID`, verbatim |
| `person` | Ada, Ella, Natalie, Mitja | `Tabelle2!Name`, verbatim |
| `age` | 8, 13, 42, 47 | derived: `person` mapped to age in August 2024 |
| `date` | ISO date | `Tabelle2!Date`, reformatted to `YYYY-MM-DD` |
| `session` | 1 to 5 | derived: rank of `date` among the five distinct dates |
| `country` | Italy, Germany | derived: `Italy` if `session <= 4`, else `Germany` |
| `round` | 1 to 5 within session | `Tabelle2!Round`, verbatim |
| `position_in_round` | 1 to 4 | derived: equals `person_id`, because the test-person order Ada, Ella, Natalie, Mitja was fixed in all 25 rounds (verified). Kept as a separate column so that order effects can be modelled without overloading the person variable. |
| `package` | 1, 2 | `Tabelle2!package`, verbatim |

### Source measures

| Variable | Coding | Provenance |
|---|---|---|
| `true_flavor` | E, O, Ap, H, Z, An | `Tabelle2!Crit_Sorte`, verbatim. The flavor actually administered. |
| `true_flavor_en` | English label | derived: code mapped to English |
| `judged_flavor` | E, O, Ap, H, Z, An | `Tabelle2!J_Sorte`, verbatim. The test person's forced-choice flavor judgment. |
| `judged_flavor_en` | English label | derived: code mapped to English |
| `correct` | 0, 1 | `Tabelle2!Correct`, verbatim. Verified to equal `judged_flavor == true_flavor` on all 100 trials. |
| `certainty` | 1 (sehr unsicher) to 6 (sehr sicher) | `Tabelle2!J_Sicher`, verbatim |
| `liking` | 1 (gar nicht lecker) to 6 (super lecker) | `Tabelle2!J_Lecker`, verbatim. Liking of the bear just tasted. |

### Derived: position and feedback exposure

Feedback was public: after each trial the correct flavor was announced before the
next person's turn, so every participant observed the outcome of every trial.

| Variable | Formula | Note |
|---|---|---|
| `trial_within_person` | `(session - 1) * 5 + round`, range 1 to 25 | Verified to equal the within-person rank of `trial_id`. |
| `feedback_prior_own` | `trial_within_person - 1`, range 0 to 24 | Own trials completed, with feedback, before this trial. |
| `feedback_prior_observed` | `trial_id - 1`, range 0 to 99 | All trials in the study observed before this trial. |
| `prior_own_exposures_flavor` | count of this person's earlier trials with the same `true_flavor` | Range 0 to 7. Flavor-specific learning opportunity. |
| `prior_observed_exposures_flavor` | count of all earlier trials with the same `true_flavor` | Range 0 to 22. |
| `prior_own_hits` | cumulative `correct` before this trial, within person | |
| `prior_own_accuracy` | `prior_own_hits / feedback_prior_own` | Missing on each person's first trial (division by zero). The only intended missingness in the file, alongside the three sequential indicators below. |

**Collinearity warning.** `feedback_prior_observed = 4 * feedback_prior_own +
position_in_round - 1` holds exactly, by construction of the fixed round
structure. Within a person the two counters are a perfect linear function of each
other, so they must not enter the same model together. They are kept as separate
columns because the substantive question differs: own-trial feedback indexes
personal corrective experience, observed feedback indexes total exposure to
flavor-label pairings.

### Derived: response-bias indicators

| Variable | Formula | Note |
|---|---|---|
| `resp_count_person_flavor` | number of this person's 25 responses that named `judged_flavor` | |
| `resp_prop_person_flavor` | `resp_count_person_flavor / 25` | Compare against 1/6 for an unbiased response distribution. |
| `true_count_person_judged_flavor` | number of this person's 25 trials on which `judged_flavor` was the flavor actually administered | Base rate for the same flavor within this person. |
| `resp_bias_person_flavor` | `(resp_count_person_flavor - true_count_person_judged_flavor) / 25` | Signed over- or under-use. Positive: the person named the flavor more often than they received it. Range in these data -0.16 to +0.16 (corrected in session B; the +0.20 stated here through session A was a prose error, the column itself was always correct). |
| `repeat_prev_own_response` | 1 if `judged_flavor` equals this person's judgment on their own previous trial, else 0 | Missing on each person's first trial. |
| `echo_prev_own_true` | 1 if `judged_flavor` equals the true flavor revealed on this person's own previous trial, else 0 | Missing on each person's first trial. Captures whether the just-corrected label is repeated or avoided. |
| `echo_prev_trial_true` | 1 if `judged_flavor` equals the true flavor of the immediately preceding trial in the study, else 0 | Missing on `trial_id` 1. The preceding trial is the neighbour's, and its correct flavor had just been announced. |

These are trial-level carriers of person-level quantities where the definition is
a person aggregate (`resp_count_person_flavor`, `resp_prop_person_flavor`,
`true_count_person_judged_flavor`, `resp_bias_person_flavor`). They repeat within
person and flavor and carry no trial-level variance; they are provided for
convenient merging, not as trial-level predictors.

### Derived: centered scales and merged a priori values

| Variable | Formula |
|---|---|
| `certainty_c` | `certainty` minus this person's mean certainty across their 25 trials |
| `liking_c` | `liking` minus this person's mean liking across their 25 trials |
| `apriori_pred_true_flavor` | this person's a priori predicted percentage correct for the flavor administered on this trial (`Tabelle1!Pred_<flavor>`) |
| `apriori_like_true_flavor` | this person's a priori liking rating of the flavor administered on this trial (`Tabelle1!Like_<flavor>`) |

`certainty_c` and `liking_c` are perceiver-centered only. They are **not** social
relations model relationship effects; the double centering is done in session B
on the 24 person x flavor cell means, not at trial level, because the number of
trials per cell varies from 1 to 8.

---

## `gummibear_apriori.csv` (4 rows, one per person)

All a priori values were collected once, before the first tasting session.

| Variable | Coding | Provenance |
|---|---|---|
| `person_id`, `person`, `age` | as above | `Tabelle1!ID`, `Tabelle1!Name`, age supplied |
| `pred_E` ... `pred_An` | 0 to 100, predicted percentage of own correct identifications for that flavor | `Tabelle1!Pred_<flavor>`, verbatim |
| `like_E` ... `like_An` | 1 (gar nicht) to 6 (super gerne) | `Tabelle1!Like_<flavor>`, verbatim |
| `pred_mean_flavors` | mean of the six `pred_*` values | derived. The a priori self-predicted accuracy the paper uses: Ada 60.0, Ella 62.8, Natalie 31.3, Mitja 31.3. |
| `like_mean_flavors` | mean of the six `like_*` values | derived |
| `achieved_accuracy` | proportion correct over 25 trials | derived from `gummibear_trials.csv`. Provenance is Tabelle2, carried here so calibration analyses do not need a re-merge. |
| `achieved_hits` | count correct out of 25 | derived from `gummibear_trials.csv` |

**One a priori accuracy measure, not two.** Self-predicted accuracy is
`pred_mean_flavors` throughout. The `MPred_Ges` diagonal, which asked the same
question in a single overall item, is not exported and not analysed, so the
calibration analysis has no measure choice to justify.

---

## `gummibear_apriori_long.csv` (24 rows, person x flavor)

Pure reshape of the per-flavor a priori values plus the matching trial-level
aggregates. No new information.

| Variable | Content |
|---|---|
| `person_id`, `person`, `age` | as above |
| `flavor`, `flavor_en`, `flavor_de` | flavor code and labels |
| `apriori_pred_pct` | `Tabelle1!Pred_<flavor>` for this person |
| `apriori_like` | `Tabelle1!Like_<flavor>` for this person |
| `n_trials` | number of trials on which this person received this flavor. Range 1 to 8, all 24 cells occupied. |
| `n_correct`, `accuracy` | hits and hit rate within the cell |
| `mean_liking_tasted` | mean `liking` over this person's trials with this flavor |
| `mean_certainty` | mean `certainty` over the same trials |
| `n_times_judged` | number of this person's 25 responses that named this flavor |

**Precision warning for session B.** Cell sizes are highly unequal: Ada received
lemon once and apple seven times, Mitja received apple eight times and raspberry
twice. The 24 relationship effects that the awareness analysis correlates are
therefore estimated with very different precision, and one of them
(Ada x lemon) rests on a single trial. Any correlation over the 24 cells needs
either a precision weight or an explicit statement that unweighted values were
used.

---

## Collected but not analysed

Two measures in `Tabelle1` are documented here for completeness and stay in the
source workbook only.

`MPred_Ges_<target>`, a complete 4 x 4 round robin of overall accuracy
expectations on a 0 to 100 scale. Rows are judges, columns are targets, so the
value in row Ella, column `MPred_Ges_Ada` is Ella's expectation of Ada's overall
accuracy, and the diagonal holds the self-estimates (Ada 60, Ella 70, Natalie 30,
Mitja 28). The diagonal is superseded by `pred_mean_flavors`. The 12 off-diagonal
cells have no per-flavor counterpart, since nobody predicted another person's
accuracy flavor by flavor, and 12 cells from 4 judges could carry only a
descriptive reputation analysis that the rest of the paper does not use.

`Like_Ges`, general gummi bear liking on the 1 to 6 scale. All four gave 3, so
the variable has no variance.

---

## Verification log (session A, 2026-08-17)

31 checks run, all passed. The substantive ones:

- 100 trials, 4 persons x 25, no missing values in either sheet.
- `Correct` reproduces `judged_flavor == true_flavor` on all 100 trials.
- Test-person order Ada, Ella, Natalie, Mitja held in all 25 rounds; 5 rounds in
  each of 5 sessions; 20 trials per session.
- Package assignment matches PROJECT.md exactly: package 1 for trials 1 to 72,
  package 2 for trials 73 to 100.
- All scale ranges within bounds (predictions 20 to 82 within 0 to 100, likings
  and certainty within 1 to 6, round-robin expectations 25 to 70).
- Accuracy per person reproduces the pinned values: Ella .68, Mitja .60,
  Natalie .28, Ada .12. Overall 42/100.
- True-flavor frequencies Ap 23, O 17, H 17, Z 17, An 15, E 11; judged-flavor
  frequencies O 22, Z 20, H 18, Ap 15, An 14, E 11.
- All 24 person x flavor cells occupied, minimum cell size 1.

Discrepancies and structural points found, none corrected in the source:

1. **Session days are not consecutive.** The five sessions ran on 5, 6, 8, and 9
   August and then 18 August 2024. Descriptions saying "five days" are accurate;
   descriptions implying five consecutive days are not.
2. **The two general-question measures do not match the per-flavor ones.** The
   `MPred_Ges` self-estimates (60, 70, 30, 28) differ from the per-flavor means
   (60.0, 62.8, 31.3, 31.3) for three of four people. This is why the
   general-question items were dropped rather than reconciled: they ask the same
   question less precisely.
3. **`Like_Ges` has zero variance** (all four rated 3), so it could not enter any
   analysis as a predictor or outcome even if it were retained.
4. **Both source sheets carry a legend row above the header row.** Reading either
   sheet with a default header setting produces unusable column names. Header
   rows are hard-coded in `build_dataset.py` and verified against the expected
   column names.
5. **Ada x lemon has one trial**, so one of the 24 awareness cells is a single
   observation.
