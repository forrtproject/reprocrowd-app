"""
build_dataset.py
Session A of the gummi bear paper: data verification and construction of the
analysis-ready files.

Purpose. Reads the single source of truth, data/Gummibear_data.xlsx, runs a
fixed set of integrity checks against the codebook in PROJECT.md and
data/info_gummibear_data.txt, and writes four derived files. It fixes nothing
silently: any check that fails raises.

Inputs
    data/Gummibear_data.xlsx          sheet Tabelle1 (a priori, 4 rows)
                                      sheet Tabelle2 (tasting trials, 100 rows)

Outputs
    data/gummibear_trials.csv         100 trials, long format, derived variables
    data/gummibear_apriori.csv        4 rows, wide, one per person
    data/gummibear_apriori_long.csv   24 rows, person x flavor (reshape)

Scope decision (Mitja, 2026-08-17). Only the per-flavor a priori measures are
analysed: Pred_<flavor> and Like_<flavor>. Where an average is needed, it is the
average across the six flavors. The two general-question measures in Tabelle1,
the MPred_Ges round robin and Like_Ges, are not carried into the analysis files.
They remain in the source workbook, which is the version archived openly, and the
Method discloses that they were collected.

Reproducibility
    Python 3.11, pandas 2.3.3, numpy 2.2.6. No stochastic step, no seed needed.
    Run from the project root:  python analysis/build_dataset.py
"""

from pathlib import Path
import sys
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SRC = DATA / "Gummibear_data.xlsx"

# Flavor codes as used in the source file. Order fixed here once and reused
# everywhere so that column order in the outputs is stable.
FLAVORS = ["E", "O", "Ap", "H", "Z", "An"]
FLAVOR_EN = {"E": "strawberry", "O": "orange", "Ap": "apple",
             "H": "raspberry", "Z": "lemon", "An": "pineapple"}
FLAVOR_DE = {"E": "Erdbeere", "O": "Orange", "Ap": "Apfel",
             "H": "Himbeere", "Z": "Zitrone", "An": "Ananas"}

PERSONS = {1: "Ada", 2: "Ella", 3: "Natalie", 4: "Mitja"}
# Age in completed years at the time of data collection (August 2024),
# supplied by Mitja and pinned in PROJECT.md. Not in the source file.
AGE = {"Ada": 8, "Ella": 13, "Natalie": 42, "Mitja": 47}
ORDER = ["Ada", "Ella", "Natalie", "Mitja"]  # fixed test-person order per round

CHECKS = []


def check(label, condition, detail=""):
    """Record and enforce one integrity check."""
    CHECKS.append((label, bool(condition), detail))
    if not condition:
        print(f"FAILED  {label}  {detail}", file=sys.stderr)
    return bool(condition)


# ---------------------------------------------------------------------------
# 1. Read the source sheets
#    Both sheets carry a scale legend in the first rows, so the real header sits
#    below the top row. Header positions are hard-coded and then verified.
# ---------------------------------------------------------------------------

t1 = pd.read_excel(SRC, sheet_name="Tabelle1", header=2).dropna(how="all")
t1.columns = ["ID", "Name",
              "Pred_E", "Pred_O", "Pred_Ap", "Pred_H", "Pred_Z", "Pred_An",
              "MPred_Ges_Ada", "MPred_Ges_Ella", "MPred_Ges_Natalie", "MPred_Ges_Mitja",
              "Like_E", "Like_O", "Like_Ap", "Like_H", "Like_Z", "Like_An", "Like_Ges"]
t1 = t1.reset_index(drop=True)

t2 = pd.read_excel(SRC, sheet_name="Tabelle2", header=3)
t2.columns = ["Test_ID", "ID", "Date", "Round", "Name",
              "J_Sorte", "J_Sicher", "J_Lecker", "Crit_Sorte", "Correct", "package"]
t2["Date"] = pd.to_datetime(t2["Date"])

# ---------------------------------------------------------------------------
# 2. Integrity checks against PROJECT.md and info_gummibear_data.txt
# ---------------------------------------------------------------------------

check("t1 has 4 rows", len(t1) == 4, f"got {len(t1)}")
check("t2 has 100 rows", len(t2) == 100, f"got {len(t2)}")
check("no missing values anywhere",
      t1.isna().sum().sum() == 0 and t2.isna().sum().sum() == 0)
check("Test_ID is 1..100 in order", list(t2.Test_ID) == list(range(1, 101)))
check("person ID to name mapping matches the codebook",
      all(t2.loc[t2.ID == i, "Name"].eq(n).all() for i, n in PERSONS.items()))
check("flavor codes in judged and true columns are the six known flavors",
      set(t2.J_Sorte) <= set(FLAVORS) and set(t2.Crit_Sorte) <= set(FLAVORS))
check("Correct equals judged == true on every trial",
      (t2.Correct == (t2.J_Sorte == t2.Crit_Sorte).astype(int)).all())
check("certainty in 1..6", t2.J_Sicher.between(1, 6).all())
check("liking in 1..6", t2.J_Lecker.between(1, 6).all())
check("package in {1, 2}", set(t2.package) == {1, 2})
check("a priori predictions in 0..100",
      t1[[f"Pred_{f}" for f in FLAVORS]].astype(float).stack().between(0, 100).all())
check("a priori likings in 1..6",
      t1[[f"Like_{f}" for f in FLAVORS] + ["Like_Ges"]].astype(float).stack().between(1, 6).all())
# The general-question measures are range-checked for completeness even though
# they are not carried into the output files.
check("round-robin expectations in 0..100 (source only, not exported)",
      t1[[f"MPred_Ges_{p}" for p in ORDER]].astype(float).stack().between(0, 100).all())
check("Like_Ges has no variance, as recorded (source only, not exported)",
      t1["Like_Ges"].astype(float).nunique() == 1)

# Session index from the date. Five distinct dates, one session each.
dates = sorted(t2.Date.unique())
check("five distinct session dates", len(dates) == 5, f"got {len(dates)}")
t2["session"] = t2.Date.map({d: i + 1 for i, d in enumerate(dates)})

check("20 trials per session", (t2.groupby("session").size() == 20).all())
check("25 trials per person", (t2.groupby("Name").size() == 25).all())
check("5 rounds per session, 4 trials per round",
      (t2.groupby(["session", "Round"]).size() == 4).all())
check("fixed test-person order Ada, Ella, Natalie, Mitja in all 25 rounds",
      all(list(g.Name) == ORDER for _, g in t2.groupby(["session", "Round"])))
check("package 1 covers sessions 1-3 and the first 12 trials of session 4",
      (t2.loc[t2.Test_ID <= 72, "package"] == 1).all())
check("package 2 covers the last 8 trials of session 4 and all of session 5",
      (t2.loc[t2.Test_ID >= 73, "package"] == 2).all())

# ---------------------------------------------------------------------------
# 3. Build data/gummibear_trials.csv
#    Column blocks: identifiers, design, source measures, derived exposure and
#    feedback variables, derived response-bias variables, merged a priori values.
# ---------------------------------------------------------------------------

tr = pd.DataFrame({
    "trial_id": t2.Test_ID.astype(int),
    "person_id": t2.ID.astype(int),
    "person": t2.Name,
    "age": t2.Name.map(AGE).astype(int),
    "date": t2.Date.dt.strftime("%Y-%m-%d"),
    "session": t2.session.astype(int),
    "country": np.where(t2.session <= 4, "Italy", "Germany"),
    "round": t2.Round.astype(int),
    "position_in_round": t2.ID.astype(int),   # identical to person_id by design
    "package": t2.package.astype(int),
    "true_flavor": t2.Crit_Sorte,
    "true_flavor_en": t2.Crit_Sorte.map(FLAVOR_EN),
    "judged_flavor": t2.J_Sorte,
    "judged_flavor_en": t2.J_Sorte.map(FLAVOR_EN),
    "correct": t2.Correct.astype(int),
    "certainty": t2.J_Sicher.astype(int),
    "liking": t2.J_Lecker.astype(int),
})

# --- design position within person ---
# Rounds run 1..5 inside each of 5 sessions, so trial k for a person is
# (session - 1) * 5 + round. Verified against the within-person rank of trial_id.
tr["trial_within_person"] = (tr.session - 1) * 5 + tr["round"]
check("trial_within_person equals the within-person rank of trial_id",
      (tr.groupby("person").trial_id.rank(method="first").astype(int)
       == tr.trial_within_person).all())

# --- feedback exposure ---
# The correct flavor was announced after every trial, in front of everyone, so
# two counters are meaningful: feedback on one's own trials, and feedback
# observed in total. Within a person the two are a linear function of each other
# (observed = 4 * own + person_id - 1); they are kept apart because the
# substantive question differs, and the collinearity is documented in the codebook.
tr["feedback_prior_own"] = tr.trial_within_person - 1
tr["feedback_prior_observed"] = tr.trial_id - 1
check("observed feedback is 4 * own feedback + position - 1",
      (tr.feedback_prior_observed
       == 4 * tr.feedback_prior_own + tr.position_in_round - 1).all())

# --- flavor-specific prior exposure ---
# How often the person had already tasted this flavor (with feedback), and how
# often this flavor had already been drawn in the whole experiment. Both are
# strictly prior to the current trial.
tr["prior_own_exposures_flavor"] = (
    tr.groupby(["person", "true_flavor"]).cumcount()
)
tr["prior_observed_exposures_flavor"] = (
    tr.groupby("true_flavor").cumcount()
)

# --- running own performance before the current trial ---
cum_hits = tr.groupby("person").correct.cumsum() - tr.correct
tr["prior_own_hits"] = cum_hits.astype(int)
tr["prior_own_accuracy"] = np.where(
    tr.feedback_prior_own > 0, cum_hits / tr.feedback_prior_own.replace(0, np.nan), np.nan
)

# --- response-bias indicators ---
# (a) How much of a person's 25 responses went to the flavor named here.
resp_count = tr.groupby(["person", "judged_flavor"]).size().rename("n")
tr = tr.merge(resp_count.rename("resp_count_person_flavor"),
              left_on=["person", "judged_flavor"], right_index=True, how="left")
tr["resp_prop_person_flavor"] = tr.resp_count_person_flavor / 25

# (b) Signed over- or under-use of that flavor as a response, relative to how
#     often the person actually received it. Positive = named more often than
#     administered. Zero-filled where the person never received the flavor.
true_count = tr.groupby(["person", "true_flavor"]).size()
tr["true_count_person_judged_flavor"] = [
    int(true_count.get((p, f), 0)) for p, f in zip(tr.person, tr.judged_flavor)
]
tr["resp_bias_person_flavor"] = (
    tr.resp_count_person_flavor - tr.true_count_person_judged_flavor
) / 25

# (c) Sequential dependencies. Each is defined only where the reference trial exists.
prev_own_resp = tr.groupby("person").judged_flavor.shift(1)
prev_own_true = tr.groupby("person").true_flavor.shift(1)
prev_any_true = tr.true_flavor.shift(1)          # the bear revealed immediately before
tr["repeat_prev_own_response"] = np.where(
    prev_own_resp.isna(), np.nan, (tr.judged_flavor == prev_own_resp).astype(float))
tr["echo_prev_own_true"] = np.where(
    prev_own_true.isna(), np.nan, (tr.judged_flavor == prev_own_true).astype(float))
tr["echo_prev_trial_true"] = np.where(
    prev_any_true.isna(), np.nan, (tr.judged_flavor == prev_any_true).astype(float))

# --- person-centered judgment scales, for convenience in later models ---
tr["certainty_c"] = tr.certainty - tr.groupby("person").certainty.transform("mean")
tr["liking_c"] = tr.liking - tr.groupby("person").liking.transform("mean")

# --- merged a priori values for the flavor actually administered ---
pred_map = {(r.Name, f): float(r[f"Pred_{f}"]) for _, r in t1.iterrows() for f in FLAVORS}
like_map = {(r.Name, f): float(r[f"Like_{f}"]) for _, r in t1.iterrows() for f in FLAVORS}
tr["apriori_pred_true_flavor"] = [pred_map[(p, f)] for p, f in zip(tr.person, tr.true_flavor)]
tr["apriori_like_true_flavor"] = [like_map[(p, f)] for p, f in zip(tr.person, tr.true_flavor)]

check("trials file has 100 rows", len(tr) == 100)
check("no unintended missing values in the trials file",
      tr.drop(columns=["prior_own_accuracy", "repeat_prev_own_response",
                       "echo_prev_own_true", "echo_prev_trial_true"]).isna().sum().sum() == 0)

tr.to_csv(DATA / "gummibear_trials.csv", index=False)

# ---------------------------------------------------------------------------
# 4. Build data/gummibear_apriori.csv (4 rows, wide)
# ---------------------------------------------------------------------------

ap = pd.DataFrame({
    "person_id": t1.ID.astype(int),
    "person": t1.Name,
    "age": t1.Name.map(AGE).astype(int),
})
for f in FLAVORS:
    ap[f"pred_{f}"] = t1[f"Pred_{f}"].astype(float)
for f in FLAVORS:
    ap[f"like_{f}"] = t1[f"Like_{f}"].astype(float)

# The single a priori accuracy measure the paper uses: the average of the six
# per-flavor predictions. The MPred_Ges self-estimate is deliberately not built.
ap["pred_mean_flavors"] = ap[[f"pred_{f}" for f in FLAVORS]].mean(axis=1)
ap["like_mean_flavors"] = ap[[f"like_{f}" for f in FLAVORS]].mean(axis=1)

# Achieved accuracy, carried over from the trials file so that calibration
# analyses in session B do not have to re-merge. Provenance is Tabelle2.
achieved = tr.groupby("person").correct.mean()
ap["achieved_accuracy"] = ap.person.map(achieved).astype(float)
ap["achieved_hits"] = ap.person.map(tr.groupby("person").correct.sum()).astype(int)

check("apriori file has 4 rows", len(ap) == 4)
check("no general-question measure leaked into the apriori file",
      not any(c.startswith("mpred") or c == "like_general" for c in ap.columns))
check("pred_mean_flavors matches the hand-computed values",
      np.allclose(ap.pred_mean_flavors.round(2), [60.00, 62.83, 31.33, 31.33]))

ap.to_csv(DATA / "gummibear_apriori.csv", index=False)

# ---------------------------------------------------------------------------
# 5. Person x flavor reshape for session B
#    Pure reshape of the same numbers, no new information. This is the shape the
#    awareness analysis needs.
# ---------------------------------------------------------------------------

long = []
for _, r in ap.iterrows():
    for f in FLAVORS:
        cell = tr[(tr.person == r.person) & (tr.true_flavor == f)]
        long.append({
            "person_id": int(r.person_id), "person": r.person, "age": int(r.age),
            "flavor": f, "flavor_en": FLAVOR_EN[f], "flavor_de": FLAVOR_DE[f],
            "apriori_pred_pct": r[f"pred_{f}"],
            "apriori_like": r[f"like_{f}"],
            "n_trials": len(cell),
            "n_correct": int(cell.correct.sum()),
            "accuracy": cell.correct.mean() if len(cell) else np.nan,
            "mean_liking_tasted": cell.liking.mean() if len(cell) else np.nan,
            "mean_certainty": cell.certainty.mean() if len(cell) else np.nan,
            "n_times_judged": int((tr[tr.person == r.person].judged_flavor == f).sum()),
        })
ap_long = pd.DataFrame(long)
check("apriori_long has 24 rows", len(ap_long) == 24)
check("every person x flavor cell has at least one trial", (ap_long.n_trials > 0).all(),
      f"min cell n = {ap_long.n_trials.min()}")
check("cell trial counts sum to 100", ap_long.n_trials.sum() == 100)
ap_long.to_csv(DATA / "gummibear_apriori_long.csv", index=False)

# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------

failed = [c for c in CHECKS if not c[1]]
print(f"{len(CHECKS)} integrity checks run, {len(failed)} failed")
for label, ok, detail in CHECKS:
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
print("\nwritten:")
for f in ["gummibear_trials.csv", "gummibear_apriori.csv",
          "gummibear_apriori_long.csv"]:
    print(f"  data/{f}")
if failed:
    raise SystemExit("integrity checks failed; no file should be trusted until resolved")
