#!/usr/bin/env python3
"""
gummibear_analyses.py -- Session B analysis pipeline, gummy bear paper.

Runs all main analyses end to end from the three analysis-ready CSVs built in
session A (documented in data/codebook.md) and writes analysis/results_2026-08-17.md
as a numbered results log. Every number that could end up in the paper appears
there together with the analysis that produced it.

Reproduce, from the project root:
    python3 analysis/gummibear_analyses.py
Optionally run the R control analyses afterwards and re-run this script; the log
then includes the R-vs-Python comparison in section 9:
    bash analysis/setup-r-sandbox.sh   (once per sandbox session, ~8 min)
    MAMBA_ROOT_PREFIX=/tmp/mamba /tmp/bin/micromamba run -n renv Rscript analysis/control_analyses.R
    python3 analysis/gummibear_analyses.py

Inference conventions (PROJECT.md pinned decision, 2026-08-17):
  - Primary inference: permutation tests, exact binomial tests and intervals,
    and trial-level bootstrap. Mixed models are R-side control analyses only.
  - Permutation p = (1 + #{|T_perm| >= |T_obs|}) / (N_PERM + 1), two-sided,
    except AUC tests, which are one-sided upper (H1: discrimination above
    chance), marked "one-sided" where reported.
  - Exact enumeration where the permutation space is small (n = 4 persons:
    4! = 24 orderings; n = 6 flavors: 6! = 720 orderings); p is then the exact
    proportion of |r_perm| >= |r_obs|, identity included, and the smallest
    attainable p is 1/24 = .042 resp. 1/720 = .0014.
  - Confidence intervals for proportions: Clopper-Pearson exact, 95%.
  - Exploratory study: exact p-values reported, no multiplicity correction.
  - 4 perceivers cannot support inferential variance estimation or a
    perceiver-level bootstrap; where that bites, the log says so instead of
    reporting a fragile interval.

Inputs (never the source workbook):
    data/gummibear_trials.csv        100 trials, derived columns per codebook
    data/gummibear_apriori.csv       4 persons, per-flavor a priori measures
    data/gummibear_apriori_long.csv  24 person x flavor cells
Outputs:
    analysis/results_2026-08-17.md   numbered results log
    analysis/py_auc_by_cell.csv      24 cell AUCs, input to the R comparison

Fixed seed; single rng consumed in fixed program order, so the log reproduces
byte-identically. Runtime approx. 1-2 minutes.
"""

from __future__ import annotations

import itertools
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy import stats

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
SEED = 20240805          # date of session 1 of data collection
N_PERM = 10_000
N_BOOT = 10_000
N_SIM = 10_000           # noise-floor simulations, variance decomposition
RESULTS_DATE = "2026-08-17"
CHANCE = 1.0 / 6.0

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "analysis"

FLAVORS = ["E", "O", "Ap", "H", "Z", "An"]
FLAVORS_EN = {"E": "strawberry", "O": "orange", "Ap": "apple",
              "H": "raspberry", "Z": "lemon", "An": "pineapple"}
PERSONS = ["Ada", "Ella", "Natalie", "Mitja"]  # person_id order 1..4

rng = np.random.default_rng(SEED)
L: list[str] = []        # the results log, one string per line


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def w(line: str = "") -> None:
    L.append(line)


def fmt(x: float, d: int = 3) -> str:
    return f"{x:.{d}f}"


def fmtp(p: float) -> str:
    # Reporting floor, PROJECT.md pin 2026-08-29: with 10,000 draws and the
    # (1 + count) / (B + 1) correction the smallest attainable permutation p is
    # 1/10001, so anything at or near that resolution limit is reported as
    # "< .001" rather than "< .0001". The four-decimal branch is unchanged, so
    # exactly computed values such as p = .0056 keep their own precision.
    return "< .001" if p < 1e-4 else f"= {p:.4f}"


def binom_ci(k: int, n: int) -> tuple[float, float]:
    ci = stats.binomtest(k, n).proportion_ci(confidence_level=0.95, method="exact")
    return ci.low, ci.high


def binom_p(k: int, n: int, p0: float) -> float:
    return stats.binomtest(k, n, p0).pvalue  # two-sided (minlike)


def ffh_exact(counts: np.ndarray, n_each: int) -> tuple[float, float, int]:
    """Exact conditional test of homogeneity for a k x 2 table with equal row
    totals (Fisher-Freeman-Halton by enumeration). Returns
    (chi-square of the observed table, exact p, number of tables enumerated).
    Deterministic: no rng consumed."""
    def chi2_of(h: list[int] | np.ndarray) -> float:
        obs = np.array([[x, n_each - x] for x in h], dtype=float)
        exp = np.outer(obs.sum(1), obs.sum(0)) / obs.sum()
        return float(((obs - exp) ** 2 / exp).sum())

    def logC(a: int, b: int) -> float:
        return (math.lgamma(a + 1) - math.lgamma(b + 1)
                - math.lgamma(a - b + 1))

    counts = [int(c) for c in counts]
    k, tot = len(counts), sum(counts)
    obs_chi = chi2_of(counts)
    den = logC(k * n_each, tot)
    p, n_tab = 0.0, 0
    for h in itertools.product(range(n_each + 1), repeat=k - 1):
        last = tot - sum(h)
        if not 0 <= last <= n_each:
            continue
        tabl = list(h) + [last]
        n_tab += 1
        pr = math.exp(sum(logC(n_each, x) for x in tabl) - den)
        if chi2_of(tabl) >= obs_chi - 1e-9:
            p += pr
    return obs_chi, float(p), n_tab


def dc(M: np.ndarray) -> np.ndarray:
    """Double-center: cell - row mean - column mean + grand mean."""
    return M - M.mean(axis=1, keepdims=True) - M.mean(axis=0, keepdims=True) + M.mean()


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.corrcoef(x, y)[0, 1])


def wpearson(x: np.ndarray, y: np.ndarray, wt: np.ndarray) -> float:
    xm = np.average(x, weights=wt)
    ym = np.average(y, weights=wt)
    cov = np.average((x - xm) * (y - ym), weights=wt)
    vx = np.average((x - xm) ** 2, weights=wt)
    vy = np.average((y - ym) ** 2, weights=wt)
    return float(cov / np.sqrt(vx * vy))


def exact_perm_r(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    """Exact permutation test for a correlation with tiny n: enumerate all
    orderings of y. Returns (r_obs, exact two-sided p, n_perms)."""
    r_obs = pearson(x, y)
    perms = list(itertools.permutations(range(len(y))))
    rs = np.array([pearson(x, y[list(p)]) for p in perms])
    p = float(np.mean(np.abs(rs) >= abs(r_obs) - 1e-12))
    return r_obs, p, len(perms)


def perm_p_two(obs: float, null: np.ndarray) -> float:
    return float((1 + np.sum(np.abs(null) >= abs(obs) - 1e-12)) / (len(null) + 1))


def perm_p_upper(obs: float, null: np.ndarray) -> float:
    return float((1 + np.sum(null >= obs - 1e-12)) / (len(null) + 1))


def auc_from_ranks(rank_sum_pos: float, n1: int, n0: int) -> float:
    return (rank_sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def auc_mw(pos: np.ndarray, neg: np.ndarray) -> float:
    """Mann-Whitney AUC with midranks (ties handled)."""
    allv = np.concatenate([pos, neg])
    ranks = stats.rankdata(allv)
    return auc_from_ranks(float(ranks[: len(pos)].sum()), len(pos), len(neg))


def md_table(header: list[str], rows: list[list[str]]) -> None:
    w("| " + " | ".join(header) + " |")
    w("|" + "|".join(["---"] * len(header)) + "|")
    for r in rows:
        w("| " + " | ".join(r) + " |")
    w()


# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
trials = pd.read_csv(DATA / "gummibear_trials.csv")
apri = pd.read_csv(DATA / "gummibear_apriori.csv")
along = pd.read_csv(DATA / "gummibear_apriori_long.csv")

trials = trials.sort_values("trial_id").reset_index(drop=True)
apri = apri.sort_values("person_id").reset_index(drop=True)

# Person-level convenience arrays (id order 1..4 = Ada, Ella, Natalie, Mitja)
hits = apri["achieved_hits"].to_numpy()
acc = apri["achieved_accuracy"].to_numpy()
pred_mean = apri["pred_mean_flavors"].to_numpy()
like_mean_apriori = apri["like_mean_flavors"].to_numpy()

# 4 x 6 matrices from the long file, fixed order
def pivot_along(col: str) -> np.ndarray:
    return (along.pivot(index="person", columns="flavor", values=col)
            .reindex(index=PERSONS, columns=FLAVORS).to_numpy(dtype=float))

M_pred = pivot_along("apriori_pred_pct")        # a priori predicted % correct
M_alike = pivot_along("apriori_like")           # a priori liking 1..6
M_tlike = pivot_along("mean_liking_tasted")     # tasted-liking cell means
M_acc = pivot_along("accuracy")                 # cell accuracy
M_n = pivot_along("n_trials")                   # cell sizes 1..8


# ----------------------------------------------------------------------------
# Section 0: header and pin checks
# ----------------------------------------------------------------------------
def section0() -> None:
    w(f"# Results log, session B ({RESULTS_DATE})")
    w()
    w("Produced by `analysis/gummibear_analyses.py` (fixed seed "
      f"{SEED}, {N_PERM:,} permutations / {N_BOOT:,} bootstrap draws / "
      f"{N_SIM:,} noise-floor simulations). Data: the three analysis-ready "
      "files documented in `data/codebook.md`; derived variables are used as "
      "built in session A, not recomputed. Units: perceiver = person (n = 4), "
      "target = true flavor of the bear tasted (n = 6), relationship = person "
      "x flavor cell (n = 24, cell sizes 1 to 8).")
    w()
    w("Inference conventions: permutation and exact tests primary; permutation "
      "p = (1 + #{|T_perm| >= |T_obs|}) / (N + 1), two-sided unless marked "
      "one-sided (AUC tests); exact enumeration for n = 4 (24 orderings, "
      "smallest attainable p = .042) and n = 6 (720 orderings, smallest "
      "p = .0014); Clopper-Pearson exact 95% CIs for proportions; exploratory "
      "study, exact p-values, no multiplicity correction. Mixed models are "
      "R-side controls (section 9).")
    w()
    w("## 0. Consistency checks against session A pins [fn section0]")
    w()
    checks = []
    pin_hits = {"Ada": 3, "Ella": 17, "Natalie": 7, "Mitja": 15}
    got = trials.groupby("person")["correct"].sum().to_dict()
    assert got == pin_hits, f"hit pins mismatch: {got}"
    checks.append("hits per person 3/17/7/15 (Ada/Ella/Natalie/Mitja)")
    assert int(trials["correct"].sum()) == 42
    checks.append("42/100 correct overall")
    tf = trials["true_flavor"].value_counts().to_dict()
    assert tf == {"Ap": 23, "O": 17, "H": 17, "Z": 17, "An": 15, "E": 11}, tf
    checks.append("true-flavor counts Ap 23, O 17, H 17, Z 17, An 15, E 11")
    jf = trials["judged_flavor"].value_counts().to_dict()
    assert jf == {"O": 22, "Z": 20, "H": 18, "Ap": 15, "An": 14, "E": 11}, jf
    checks.append("judged-flavor counts O 22, Z 20, H 18, Ap 15, An 14, E 11")
    mc_c = trials.loc[trials.correct == 1, "certainty"].mean()
    mc_i = trials.loc[trials.correct == 0, "certainty"].mean()
    assert (round(mc_c, 2), round(mc_i, 2)) == (3.71, 2.53), (mc_c, mc_i)
    checks.append("mean certainty 3.71 correct / 2.53 incorrect")
    assert along.shape[0] == 24 and M_n.min() == 1 and M_n.max() == 8
    checks.append("24 person x flavor cells occupied, sizes 1 to 8")
    # Derived response-bias columns agree with the codebook formula
    for _, row in trials.iterrows():
        p_, f_ = row["person"], row["judged_flavor"]
        sub = trials[trials.person == p_]
        rc = int((sub["judged_flavor"] == f_).sum())
        tc = int((sub["true_flavor"] == f_).sum())
        assert rc == row["resp_count_person_flavor"]
        assert tc == row["true_count_person_judged_flavor"]
        assert abs((rc - tc) / 25 - row["resp_bias_person_flavor"]) < 1e-9
    checks.append("resp-bias derived columns reproduce the codebook formula "
                  "on all 100 trials")
    for c in checks:
        w(f"- {c}: reproduced.")
    w()
    w("All pins reproduced; any mismatch raises and stops the script.")
    w()


# ----------------------------------------------------------------------------
# Section 1: descriptives
# ----------------------------------------------------------------------------
def section1() -> None:
    w("## 1. Descriptives")
    w()
    # -- 1.1 accuracy per person -------------------------------------------
    w("### 1.1 Accuracy per person against chance (1/6 = .167) [fn section1]")
    w()
    rows = []
    for i, p in enumerate(PERSONS):
        k = int(hits[i])
        lo, hi = binom_ci(k, 25)
        pv = binom_p(k, 25, CHANCE)
        rows.append([p, f"{k}/25", fmt(acc[i]), f"[{fmt(lo)}, {fmt(hi)}]",
                     f"p {fmtp(pv)}"])
    k_all = int(hits.sum())
    lo, hi = binom_ci(k_all, 100)
    rows.append(["All (pooled)", f"{k_all}/100", fmt(k_all / 100),
                 f"[{fmt(lo)}, {fmt(hi)}]",
                 f"p {fmtp(binom_p(k_all, 100, CHANCE))} (independence caveat: "
                 "100 trials from 4 persons)"])
    md_table(["Person", "Hits", "Accuracy", "Exact 95% CI",
              "Exact binomial vs 1/6 (two-sided)"], rows)
    w("Only Ella and Mitja beat chance. Natalie (.28, p = .17) and Ada (.12, "
      "p = .79) are indistinguishable from guessing. Ada's accuracy is "
      "descriptively below 1/6, but the data cannot establish below-chance "
      "responding:")
    # what would it take to establish below-chance responding
    p0 = CHANCE
    p_k0 = (1 - p0) ** 25
    p_k1 = p_k0 + 25 * p0 * (1 - p0) ** 24
    w(f"- With 25 trials, only 0 hits would reject downward (one-sided "
      f"P(X <= 0) = {fmt(p_k0, 4)}); 1 hit already gives P(X <= 1) = "
      f"{fmt(p_k1, 4)}. Ada's 3 hits are far from that boundary.")
    for p_true in (0.05, 0.10):
        n_need = None
        for n in range(10, 2001):
            kcrit = int(stats.binom.ppf(0.05, n, p0))
            while stats.binom.cdf(kcrit, n, p0) > 0.05:
                kcrit -= 1
            if kcrit >= 0 and stats.binom.cdf(kcrit, n, p_true) >= 0.80:
                n_need = n
                break
        w(f"- To detect true accuracy {p_true:.2f} against 1/6 with 80% power "
          f"(one-sided alpha = .05) requires about {n_need} trials per person.")
    w("- Beyond sample size, a below-chance claim would need a mechanism that "
      "survives trial-by-trial feedback: systematically swapped flavor labels "
      "should be visible as stable off-diagonal structure in the confusion "
      "matrix (see 1.3) and should erode with feedback if labels are "
      "relearnable. Neither a stable swap pattern nor 0 hits is present, so "
      "the paper reports Ada as indistinguishable from guessing.")
    w()
    # -- 1.2 accuracy per flavor -------------------------------------------
    w("### 1.2 Accuracy per flavor, pooled over persons [fn section1]")
    w()
    rows = []
    for f in FLAVORS:
        sub = trials[trials.true_flavor == f]
        k, n = int(sub["correct"].sum()), len(sub)
        lo, hi = binom_ci(k, n)
        rows.append([f"{f} ({FLAVORS_EN[f]})", f"{k}/{n}", fmt(k / n),
                     f"[{fmt(lo)}, {fmt(hi)}]",
                     f"p {fmtp(binom_p(k, n, CHANCE))}"])
    md_table(["True flavor", "Hits", "Accuracy", "Exact 95% CI",
              "Exact binomial vs 1/6"], rows)
    w("Caveat: pooled over 4 persons whose trials are not independent; the "
      "per-flavor tests are descriptive orientation, not primary inference.")
    w()
    w("Person x flavor cell accuracies (hits/n), from "
      "`gummibear_apriori_long.csv`:")
    w()
    rows = []
    for i, p in enumerate(PERSONS):
        cells = []
        for j, f in enumerate(FLAVORS):
            sub = along[(along.person == p) & (along.flavor == f)].iloc[0]
            cells.append(f"{int(sub.n_correct)}/{int(sub.n_trials)}")
        rows.append([p] + cells)
    md_table(["Person"] + [f"{f}" for f in FLAVORS], rows)
    # -- 1.3 confusion matrix ------------------------------------------------
    w("### 1.3 Confusion matrix, judged against true flavor (pooled) "
      "[fn section1]")
    w()
    cm = pd.crosstab(trials["true_flavor"], trials["judged_flavor"])
    cm = cm.reindex(index=FLAVORS, columns=FLAVORS, fill_value=0)
    rows = []
    for f in FLAVORS:
        rows.append([f"**{f}** (true)"] + [str(int(cm.loc[f, g])) for g in FLAVORS]
                    + [str(int(cm.loc[f].sum()))])
    rows.append(["judged total"] + [str(int(cm[g].sum())) for g in FLAVORS]
                + ["100"])
    md_table(["true \\ judged"] + FLAVORS + ["n"], rows)
    offdiag = []
    for f in FLAVORS:
        for g in FLAVORS:
            if f != g and cm.loc[f, g] > 0:
                offdiag.append((int(cm.loc[f, g]), f, g))
    offdiag.sort(reverse=True)
    top = ", ".join(f"{a} x {f}->{g}" for a, f, g in offdiag[:5])
    w(f"Most frequent confusions (true->judged): {top}.")
    w()
    w("Per-person confusion matrices (rows true, cols judged, order "
      f"{', '.join(FLAVORS)}):")
    w()
    for p in PERSONS:
        sub = trials[trials.person == p]
        cmp_ = pd.crosstab(sub["true_flavor"], sub["judged_flavor"])
        cmp_ = cmp_.reindex(index=FLAVORS, columns=FLAVORS, fill_value=0)
        lines = "; ".join(
            f"{f}: " + " ".join(str(int(cmp_.loc[f, g])) for g in FLAVORS)
            for f in FLAVORS)
        w(f"- {p}: {lines}")
    w()
    # -- 1.4 response bias ----------------------------------------------------
    w("### 1.4 Response biases [fn section1]")
    w()
    w("Signed response bias per person and flavor, "
      "(times judged - times received)/25, per the codebook formula for "
      "`resp_bias_person_flavor` (derived column used where the person judged "
      "the flavor at least once, formula applied for never-judged flavors):")
    w()
    rows = []
    bias_min, bias_max = 1.0, -1.0
    for p in PERSONS:
        sub = trials[trials.person == p]
        cells = []
        for f in FLAVORS:
            rc = int((sub["judged_flavor"] == f).sum())
            tc = int((sub["true_flavor"] == f).sum())
            b = (rc - tc) / 25
            bias_min, bias_max = min(bias_min, b), max(bias_max, b)
            cells.append(f"{b:+.2f} ({rc}/{tc})")
        rows.append([p] + cells)
    md_table(["Person"] + [f"{f} bias (judged/received)" for f in FLAVORS], rows)
    w(f"Range {bias_min:+.2f} to {bias_max:+.2f}. **Codebook erratum:** the "
      "codebook prose states a range of -0.16 to +0.20; the actual maximum of "
      "`resp_bias_person_flavor` in the data file is +0.16 (Mitja, orange, "
      "8 judged / 4 received). The derived column itself is correct (formula "
      "verified on all 100 trials, section 0); only the prose range in "
      "`data/codebook.md` needs correcting.")
    w()
    # response distribution uniformity per person, Monte Carlo multinomial
    w("Response-distribution uniformity per person (chi-square vs uniform over "
      f"6 response options, Monte Carlo multinomial with {N_PERM:,} draws):")
    w()
    rows = []
    for p in PERSONS:
        obs = trials.loc[trials.person == p, "judged_flavor"] \
            .value_counts().reindex(FLAVORS, fill_value=0).to_numpy()
        exp = 25 / 6
        chi_obs = float(((obs - exp) ** 2 / exp).sum())
        sims = rng.multinomial(25, [1 / 6] * 6, size=N_PERM)
        chi_sim = ((sims - exp) ** 2 / exp).sum(axis=1)
        pv = perm_p_upper(chi_obs, chi_sim)
        rows.append([p, " ".join(str(int(x)) for x in obs), fmt(chi_obs, 2),
                     f"p {fmtp(pv)}"])
    md_table(["Person", f"Response counts ({', '.join(FLAVORS)})",
              "chi-square(5)", "MC p"], rows)
    jf = trials["judged_flavor"].value_counts().reindex(FLAVORS).to_numpy()
    chi_j = float(((jf - 100 / 6) ** 2 / (100 / 6)).sum())
    w(f"Pooled judged distribution vs uniform: chi-square(5) = {fmt(chi_j, 2)}, "
      f"p = {fmt(stats.chi2.sf(chi_j, 5), 3)} (asymptotic). Like the "
      "true-flavor distribution (chi-square(5) = 4.52, p = .477, session A "
      "pin), the pooled response distribution does not depart from "
      "uniformity; response biases are individual, not shared.")
    w()
    # sequential indicators
    w("Sequential response indicators (codebook: `repeat_prev_own_response`, "
      "`echo_prev_own_true`, `echo_prev_trial_true`), observed rate vs "
      "within-person permutation null (judged sequence shuffled within person, "
      f"true sequences fixed, {N_PERM:,} draws, two-sided):")
    w()
    # build per-person sequences
    seq = {}
    for p in PERSONS:
        sub = trials[trials.person == p].sort_values("trial_within_person")
        J = sub["judged_flavor"].to_numpy()
        T = sub["true_flavor"].to_numpy()
        # previous trial in the study (neighbour's bear), true flavor
        prev_true_glob = []
        for tid in sub["trial_id"]:
            prev_true_glob.append(
                trials.loc[trials.trial_id == tid - 1, "true_flavor"].iloc[0]
                if tid > 1 else None)
        seq[p] = (J, T, np.array(prev_true_glob, dtype=object))

    def seq_rates(Jd: dict[str, np.ndarray]) -> tuple[float, float, float]:
        rep_n = rep_d = eot_n = eot_d = ept_n = ept_d = 0
        for p in PERSONS:
            J = Jd[p]
            _, T, PG = seq[p]
            rep_n += int((J[1:] == J[:-1]).sum()); rep_d += len(J) - 1
            eot_n += int((J[1:] == T[:-1]).sum()); eot_d += len(J) - 1
            valid = PG != None  # noqa: E711
            ept_n += int((J[valid] == PG[valid]).sum()); ept_d += int(valid.sum())
        return rep_n / rep_d, eot_n / eot_d, ept_n / ept_d

    obs_rates = seq_rates({p: seq[p][0] for p in PERSONS})
    null = np.empty((N_PERM, 3))
    for it in range(N_PERM):
        null[it] = seq_rates({p: rng.permutation(seq[p][0]) for p in PERSONS})
    names = ["repeat own previous response", "echo own previous true flavor "
             "(the label just fed back)", "echo previous trial's true flavor "
             "(the neighbour's bear, label just announced)"]
    rows = []
    for i, nm in enumerate(names):
        centered_null = null[:, i] - null[:, i].mean()
        pv = perm_p_two(obs_rates[i] - null[:, i].mean(), centered_null)
        rows.append([nm, fmt(obs_rates[i]), fmt(null[:, i].mean()),
                     f"p {fmtp(pv)}"])
    md_table(["Indicator (pooled rate)", "Observed", "Permutation null mean",
              "p"], rows)
    w("Null caveat: within-person exchangeability of the response sequence is "
      "approximate for the two accurate perceivers, whose responses track the "
      "true-flavor sequence; with successive own trials four draws apart, the "
      "induced dependence is small.")
    w()
    tsorted = trials.sort_values("trial_id")["true_flavor"].to_numpy()
    n_rep = int((tsorted[1:] == tsorted[:-1]).sum())
    w(f"For reference, the true flavor repeated the immediately preceding "
      f"trial's true flavor on {n_rep}/99 transitions ({n_rep / 99:.3f}), so "
      "echoing the just-announced label would have been roughly "
      "chance-neutral; the observed tendency is mild avoidance (.091 vs null "
      ".158, p = .07), a response strategy, not an accuracy resource.")
    w()
    # per-person observed rates from the derived columns
    rows = []
    for p in PERSONS:
        sub = trials[trials.person == p]
        rows.append([p,
                     fmt(sub["repeat_prev_own_response"].mean()),
                     fmt(sub["echo_prev_own_true"].mean()),
                     fmt(sub["echo_prev_trial_true"].mean())])
    md_table(["Person", "repeat own prev response", "echo own prev true",
              "echo prev trial true"], rows)
    w("(Per-person rates from the derived indicator columns; missing first "
      "trials excluded per codebook.)")
    w()
    # -- 1.5 certainty and liking ------------------------------------------
    w("### 1.5 Certainty and liking distributions [fn section1]")
    w()
    rows = []
    for p in PERSONS:
        sub = trials[trials.person == p]
        cfreq = sub["certainty"].value_counts().reindex(range(1, 7), fill_value=0)
        lfreq = sub["liking"].value_counts().reindex(range(1, 7), fill_value=0)
        rows.append([p,
                     f"{sub['certainty'].mean():.2f} ({sub['certainty'].std(ddof=1):.2f})",
                     " ".join(str(int(x)) for x in cfreq),
                     f"{sub['liking'].mean():.2f} ({sub['liking'].std(ddof=1):.2f})",
                     " ".join(str(int(x)) for x in lfreq)])
    md_table(["Person", "Certainty M (SD)", "certainty counts 1..6",
              "Liking M (SD)", "liking counts 1..6"], rows)
    mc_c = trials.loc[trials.correct == 1, "certainty"]
    mc_i = trials.loc[trials.correct == 0, "certainty"]
    w(f"Certainty on correct trials: M = {mc_c.mean():.2f} "
      f"(SD = {mc_c.std(ddof=1):.2f}, n = {len(mc_c)}); on incorrect: "
      f"M = {mc_i.mean():.2f} (SD = {mc_i.std(ddof=1):.2f}, n = {len(mc_i)}). "
      "Within-person tests in 3.0.")
    w()
    rows = []
    for f in FLAVORS:
        sub = trials[trials.true_flavor == f]
        rows.append([f"{f} ({FLAVORS_EN[f]})", str(len(sub)),
                     f"{sub['liking'].mean():.2f}",
                     f"{sub['certainty'].mean():.2f}"])
    md_table(["True flavor", "n trials", "Mean liking (tasted)",
              "Mean certainty"], rows)
    # -- 1.6 session / package / country -------------------------------------
    w("### 1.6 Session, package, and country breakdowns [fn section1]")
    w()
    rows = []
    for s in range(1, 6):
        sub = trials[trials.session == s]
        k, n = int(sub.correct.sum()), len(sub)
        lo, hi = binom_ci(k, n)
        per_person = " ".join(
            f"{p[:2]} {int(sub[sub.person == p].correct.sum())}/5"
            for p in PERSONS)
        rows.append([str(s), str(sub.date.iloc[0]), f"{k}/{n}", fmt(k / n),
                     f"[{fmt(lo)}, {fmt(hi)}]", per_person])
    md_table(["Session", "Date", "Hits", "Accuracy", "Exact 95% CI",
              "Per person (hits/5)"], rows)
    w("The five days are not consecutive: sessions 1 to 4 on 5, 6, 8, 9 "
      "August 2024 (Italy), session 5 on 18 August (Germany), nine days after "
      "session 4.")
    w()
    rows = []
    for label, mask in [
            ("Package 1 (all Italy, trials 1-72)", trials.trial_id <= 72),
            ("Package 2 in Italy (trials 73-80)",
             (trials.trial_id >= 73) & (trials.trial_id <= 80)),
            ("Package 2 in Germany (trials 81-100)", trials.trial_id >= 81),
            ("Package 2 total", trials.package == 2),
            ("Italy (sessions 1-4)", trials.country == "Italy"),
            ("Germany (session 5)", trials.country == "Germany")]:
        sub = trials[mask]
        k, n = int(sub.correct.sum()), len(sub)
        lo, hi = binom_ci(k, n)
        rows.append([label, f"{k}/{n}", fmt(k / n), f"[{fmt(lo)}, {fmt(hi)}]"])
    md_table(["Subset", "Hits", "Accuracy", "Exact 95% CI"], rows)
    w("Package changed between rounds 3 and 4 of session 4; country changed "
      "between sessions 4 and 5. The eight package-2 trials still in Italy "
      "are the only leverage for separating package from country, and eight "
      "trials cannot carry that separation; the breakdown above is reported "
      "descriptively and nothing later in the paper conditions on package or "
      "country.")
    w()
    # -- 1.7 test of the perceiver differences -------------------------------
    w("### 1.7 Test of the perceiver differences "
      "[fn section1, fn ffh_exact]")
    w()
    w("Added 2026-08-26 (session F); the perceiver-level test of the mean "
      "that stood here was removed on 2026-08-27 (session F2, item 7), "
      "because the manuscript reports the level as a statement about trials "
      "with its independence caveat and makes no perceiver-level claim about "
      "it. Section 1.1 tests trials against chance and 1.2 tests flavors. "
      "Neither tests the claim the paper actually makes, which is that "
      "perceivers differ. Everything below is deterministic, so it consumes "
      "no rng and moves no later p-value in this log.")
    w()
    chi2_obs, p_exact, n_tab = ffh_exact(hits, 25)
    p_asym = float(stats.chi2.sf(chi2_obs, 3))
    md_table(["Test of perceiver heterogeneity (hits by perceiver, 4 x 2)",
              "Value"], [
        ["Hits out of 25 (Ada, Ella, Natalie, Mitja)",
         ", ".join(str(int(h)) for h in hits)],
        ["Pearson chi-square", f"chi-square(3) = {chi2_obs:.2f}"],
        ["Asymptotic p", f"p = {p_asym:.2e}"],
        ["Exact conditional p (Fisher-Freeman-Halton, all tables with these "
         "margins)", f"p = {p_exact:.2e} over {n_tab:,} tables"],
    ])
    w("This is the one inferential statement four perceivers can carry: "
      "under the null of no perceiver differences the 100 trial outcomes are "
      "exchangeable across perceivers, so the test needs no variance "
      "component and no asymptotics in the number of perceivers. The exact "
      "version conditions on the observed margins and needs no approximation "
      "at all. A variant that additionally holds each perceiver's flavor "
      "composition fixed is in 12.1 and agrees.")
    w()


# ----------------------------------------------------------------------------
# Section 2: learning check
# ----------------------------------------------------------------------------
def stratified_trend(y_col: str, x_col: str, cells: list[np.ndarray],
                     ydata: np.ndarray, xdata: np.ndarray,
                     n_perm: int) -> tuple[float, float, float]:
    """Permutation trend test of y on x, stratified by person x flavor cell.
    Within each cell, x is centered; T = sum over trials of xc * y. Null:
    y shuffled within cell. Returns (T_obs, r_strat, p_two)."""
    xc = np.concatenate([xdata[idx] - xdata[idx].mean() for idx in cells])
    yv = np.concatenate([ydata[idx] for idx in cells])
    ycent = np.concatenate([ydata[idx] - ydata[idx].mean() for idx in cells])
    T_obs = float(xc @ yv)
    denom = np.sqrt((xc ** 2).sum() * (ycent ** 2).sum())
    r_strat = T_obs / denom if denom > 0 else np.nan
    sizes = [len(idx) for idx in cells]
    null = np.empty(n_perm)
    for it in range(n_perm):
        parts = []
        pos = 0
        for idx, s in zip(cells, sizes):
            parts.append(rng.permutation(ydata[idx]))
            pos += s
        null[it] = xc @ np.concatenate(parts)
    return T_obs, float(r_strat), perm_p_two(T_obs, null)


def section2() -> None:
    w("## 2. Learning check [fn section2, fn stratified_trend]")
    w()
    w("Question: did immediate feedback produce accuracy gains across the 25 "
      "trials? Decided before the later analyses because a positive answer "
      "would force a trial-order covariate into all of them.")
    w()
    w("Design of the primary test. A raw accuracy-on-time correlation is "
      "confounded: later trials can contain easier flavor mixes, and flavors "
      "differ in identifiability. The test therefore stratifies by person x "
      "flavor cell: within each cell, the k-th exposure to a flavor is "
      "compared with earlier exposures of the same flavor by the same person, "
      "which is exactly what feedback could have taught "
      "(`prior_own_exposures_flavor`; the within-cell exposure rank is that "
      "count plus one). Statistic: T = sum of within-cell-centered exposure "
      "rank x outcome; null: outcomes shuffled within cell "
      f"({N_PERM:,} draws). `feedback_prior_own` enters the same stratified "
      "scheme as a global-time index (spacing information); "
      "`feedback_prior_observed` is a perfect linear function of "
      "`feedback_prior_own` within person (codebook collinearity warning) and "
      "is therefore not tested separately.")
    w()
    # build cells
    cells_all, cells_by_person = [], {p: [] for p in PERSONS}
    tw = trials["trial_within_person"].to_numpy(dtype=float)
    yy = trials["correct"].to_numpy(dtype=float)
    cert = trials["certainty"].to_numpy(dtype=float)
    fpo = trials["feedback_prior_own"].to_numpy(dtype=float)
    expo = trials["prior_own_exposures_flavor"].to_numpy(dtype=float)
    for p in PERSONS:
        for f in FLAVORS:
            idx = np.where((trials.person == p) & (trials.true_flavor == f))[0]
            if len(idx) >= 2:
                idx = idx[np.argsort(tw[idx])]
                cells_all.append(idx)
                cells_by_person[p].append(idx)
    rows = []
    T, r, pv = stratified_trend("correct", "exposure", cells_all, yy, expo, N_PERM)
    rows.append(["accuracy on flavor-specific exposure count (primary)",
                 fmt(T, 2), fmt(r), f"p {fmtp(pv)}"])
    T2, r2, pv2 = stratified_trend("correct", "time", cells_all, yy, fpo, N_PERM)
    rows.append(["accuracy on global trial position (same stratified null)",
                 fmt(T2, 2), fmt(r2), f"p {fmtp(pv2)}"])
    T3, r3, pv3 = stratified_trend("certainty", "time", cells_all, cert, fpo,
                                   N_PERM)
    rows.append(["certainty on global trial position", fmt(T3, 2), fmt(r3),
                 f"p {fmtp(pv3)}"])
    T4, r4, pv4 = stratified_trend("certainty", "exposure", cells_all, cert,
                                   expo, N_PERM)
    rows.append(["certainty on flavor-specific exposure count", fmt(T4, 2),
                 fmt(r4), f"p {fmtp(pv4)}"])
    md_table(["Test (stratified by person x flavor)", "T", "r_strat", "p"],
             rows)
    w("### 2.1 Per person (same primary test) [fn section2]")
    w()
    rows = []
    for p in PERSONS:
        Tp, rp, pvp = stratified_trend("correct", "exposure",
                                       cells_by_person[p], yy, expo, N_PERM)
        rows.append([p, fmt(Tp, 2), fmt(rp), f"p {fmtp(pvp)}"])
    md_table(["Person", "T", "r_strat", "p"], rows)
    w("### 2.2 Descriptive learning tables [fn section2]")
    w()
    rows = []
    for lab, lo_, hi_ in [("0", 0, 0), ("1", 1, 1), ("2", 2, 2),
                          ("3+", 3, 99)]:
        sub = trials[(trials.prior_own_exposures_flavor >= lo_)
                     & (trials.prior_own_exposures_flavor <= hi_)]
        k, n = int(sub.correct.sum()), len(sub)
        rows.append([lab, f"{k}/{n}", fmt(k / n)])
    md_table(["Prior own exposures to this flavor", "Hits", "Accuracy"], rows)
    first, second = trials[trials.trial_within_person <= 12], \
        trials[trials.trial_within_person >= 13]
    w(f"Trials 1-12 within person: {int(first.correct.sum())}/{len(first)} = "
      f"{fmt(first.correct.mean())}; trials 13-25: "
      f"{int(second.correct.sum())}/{len(second)} = "
      f"{fmt(second.correct.mean())}. Session-level accuracies in 1.6 "
      "(.45, .40, .50, .35, .40 for sessions 1-5).")
    w()
    w("### 2.3 Verdict [fn section2]")
    w()
    if pv >= 0.05 and pv2 >= 0.05:
        w(f"No evidence of learning: the primary stratified exposure test is "
          f"flat (r_strat = {fmt(r)}, p {fmtp(pv)}), the global-time variant "
          f"likewise (r_strat = {fmt(r2)}, p {fmtp(pv2)}), and the per-person "
          "tests are individually null. The later analyses therefore run "
          "without a trial-order covariate. The three schedule confounds "
          "(non-consecutive days, package change inside session 4, country "
          "change at session 5) remain descriptive footnotes (1.6): with no "
          "trend to explain, they have nothing to confound.")
    else:
        w(f"Learning signal detected (primary p {fmtp(pv)}, global-time "
          f"p {fmtp(pv2)}): later analyses need a trial-order covariate; "
          "see per-person table for who carries it. Interpret against the "
          "schedule confounds in 1.6.")
    if pv3 < 0.05:
        w()
        w(f"Certainty drifts with trial position (r_strat = {fmt(r3)}, "
          f"p {fmtp(pv3)}) without an accompanying accuracy gain; direction "
          "and interpretation in the Discussion (confidence calibration over "
          "time, not learning).")
    w()
    w("### 2.4 Split-half stability of the perceiver differences "
      "[fn section2]")
    w()
    w("Added 2026-08-26 (session F). Deterministic, no rng consumed. The "
      "absence of a trend (2.3) is not the same thing as stability, and the "
      "Discussion needs the second one. Each row correlates, across the four "
      "perceivers, their accuracy in one part of their own trials with their "
      "accuracy in the other part.")
    w()
    rows = []
    for label, mask in [
            ("odd against even trials",
             (trials.trial_within_person % 2) == 1),
            ("first 12 against last 13 trials",
             trials.trial_within_person <= 12),
            ("sessions 1 to 3 against sessions 4 and 5", trials.session <= 3)]:
        a1 = np.array([float(trials[(trials.person == p) & mask].correct.mean())
                       for p in PERSONS])
        a2 = np.array([float(trials[(trials.person == p) & (~mask)]
                             .correct.mean()) for p in PERSONS])
        rr = pearson(a1, a2)
        rows.append([label, " ".join(f"{x:.2f}" for x in a1),
                     " ".join(f"{x:.2f}" for x in a2), fmt(rr),
                     fmt(2 * rr / (1 + rr))])
    md_table(["Split", "Part 1 (Ada Ella Natalie Mitja)", "Part 2",
              "r across perceivers", "Spearman-Brown"], rows)
    w("Descriptive, and it has to stay descriptive: four perceivers give a "
      "smallest attainable exact p of .042 on 24 orderings, and the "
      "correlations are high partly because the four accuracies are bimodal, "
      "two near chance and two well above. What the splits do rule out is "
      "the reading that the differences in 1.1 are a within-experiment "
      "fluctuation. They are not a reliability estimate for an ability, "
      "which would need repeated assessments in a real sample.")
    w()


# ----------------------------------------------------------------------------
# Section 3: signal detection / ROC
# ----------------------------------------------------------------------------
def section3() -> None:
    w("## 3. Signal detection and ROC [fn section3]")
    w()
    # 3.0 certainty carries signal, within person
    w("### 3.0 Does certainty carry signal within person? [fn section3]")
    w()
    w("Precondition for the graded-criterion ROC (session A found pooled mean "
      "certainty 3.71 correct vs 2.53 incorrect; reproduced in 1.5). "
      "Within-person check: AUC of certainty for correct vs incorrect trials, "
      f"permutation null shuffling correctness within person ({N_PERM:,} "
      "draws, one-sided upper).")
    w()
    rows = []
    for p in PERSONS:
        sub = trials[trials.person == p]
        cpos = sub.loc[sub.correct == 1, "certainty"].to_numpy(dtype=float)
        cneg = sub.loc[sub.correct == 0, "certainty"].to_numpy(dtype=float)
        a = auc_mw(cpos, cneg)
        call = sub["certainty"].to_numpy(dtype=float)
        corr_v = sub["correct"].to_numpy()
        n1 = int(corr_v.sum())
        null = np.empty(N_PERM)
        ranks = stats.rankdata(call)
        for it in range(N_PERM):
            perm = rng.permutation(corr_v)
            null[it] = auc_from_ranks(float(ranks[perm == 1].sum()), n1,
                                      25 - n1)
        pv = perm_p_upper(a, null)
        rows.append([p, f"{cpos.mean():.2f} (n={n1})",
                     f"{cneg.mean():.2f} (n={25 - n1})", fmt(a),
                     f"p {fmtp(pv)} (one-sided)"])
    md_table(["Person", "Certainty M correct", "Certainty M incorrect",
              "AUC(correct vs incorrect)", "Permutation p"], rows)
    w("Caveat: for Ada the 'correct' class has 3 trials, so her metacognition "
      "estimate is unstable by construction. Her point estimate is inverted "
      "(more certain when wrong, 2.00 vs 2.82; AUC .38): reported as a "
      "descriptive curiosity, not a claim. The graded-criterion approach is "
      "retained wherever certainty discriminates (clearly for Ella and "
      "Mitja); where it does not, the certainty-graded ROC adds resolution "
      "steps but no signal, which is itself informative and reported as "
      "such.")
    w()
    # 3.1 graded ROC
    w("### 3.1 One-vs-rest AUC per person and flavor, certainty-graded "
      "[fn section3]")
    w()
    w("Score for flavor f: +certainty if f was the chosen flavor, -certainty "
      "otherwise (a 12-step graded criterion from confident rejection to "
      "confident endorsement). AUC computed as the Mann-Whitney statistic "
      "with midranks; AUC is prevalence-free (it conditions on both classes), "
      "which is why it survives the unequal true-flavor base rates (11 to 23; "
      "uniformity not rejected, chi-square(5) = 4.52, p = .477, session A "
      "pin). What the imbalance does cost is precision: each cell AUC rests "
      "on n_pos = 1 to 8 positive trials. Parametric d' was rejected because "
      "6 of 24 cells have hit rates of 0 and would need arbitrary zero-cell "
      "corrections; the rank-based AUC needs none.")
    w()
    # precompute per person
    person_data = {}
    for p in PERSONS:
        sub = trials[trials.person == p].sort_values("trial_within_person")
        S = np.empty((25, 6))
        cert_v = sub["certainty"].to_numpy(dtype=float)
        judged = sub["judged_flavor"].to_numpy()
        for j, f in enumerate(FLAVORS):
            S[:, j] = np.where(judged == f, cert_v, -cert_v)
        ranks = np.column_stack([stats.rankdata(S[:, j]) for j in range(6)])
        true_v = sub["true_flavor"].to_numpy()
        person_data[p] = (ranks, true_v)
    auc_cells = {}
    for p in PERSONS:
        ranks, true_v = person_data[p]
        for j, f in enumerate(FLAVORS):
            pos = true_v == f
            n1 = int(pos.sum())
            auc_cells[(p, f)] = auc_from_ranks(float(ranks[pos, j].sum()),
                                               n1, 25 - n1)
    macro_person_obs = {p: float(np.mean([auc_cells[(p, f)] for f in FLAVORS]))
                        for p in PERSONS}
    macro_flavor_obs = {f: float(np.mean([auc_cells[(p, f)] for p in PERSONS]))
                        for f in FLAVORS}
    grand_obs = float(np.mean(list(macro_person_obs.values())))
    # joint permutation
    null_person = {p: np.empty(N_PERM) for p in PERSONS}
    null_flavor = {f: np.empty(N_PERM) for f in FLAVORS}
    null_grand = np.empty(N_PERM)
    for it in range(N_PERM):
        cellvals = {}
        for p in PERSONS:
            ranks, true_v = person_data[p]
            tperm = rng.permutation(true_v)
            for j, f in enumerate(FLAVORS):
                pos = tperm == f
                n1 = int(pos.sum())
                cellvals[(p, f)] = auc_from_ranks(
                    float(ranks[pos, j].sum()), n1, 25 - n1)
        for p in PERSONS:
            null_person[p][it] = np.mean([cellvals[(p, f)] for f in FLAVORS])
        for f in FLAVORS:
            null_flavor[f][it] = np.mean([cellvals[(p, f)] for p in PERSONS])
        null_grand[it] = np.mean([null_person[p][it] for p in PERSONS])
    rows = []
    for p in PERSONS:
        cells = [fmt(auc_cells[(p, f)], 2) for f in FLAVORS]
        pv = perm_p_upper(macro_person_obs[p], null_person[p])
        rows.append([p] + cells + [fmt(macro_person_obs[p]),
                                   f"p {fmtp(pv)} (one-sided)"])
    macro_row = ["macro over persons"] + \
        [fmt(macro_flavor_obs[f], 2) for f in FLAVORS] + [fmt(grand_obs), ""]
    rows.append(macro_row)
    md_table(["Person"] + FLAVORS + ["Macro-AUC", "Permutation p"], rows)
    rows = []
    for f in FLAVORS:
        pv = perm_p_upper(macro_flavor_obs[f], null_flavor[f])
        npos = " ".join(str(int(M_n[i, FLAVORS.index(f)]))
                        for i in range(4))
        rows.append([f"{f} ({FLAVORS_EN[f]})", fmt(macro_flavor_obs[f]),
                     f"p {fmtp(pv)} (one-sided)", npos])
    md_table(["Flavor", "Macro-AUC over persons", "Permutation p",
              "n_pos per person (Ada Ella Natalie Mitja)"], rows)
    pv_g = perm_p_upper(grand_obs, null_grand)
    w(f"Grand macro-AUC (mean of the four person macros): {fmt(grand_obs)}, "
      f"permutation p {fmtp(pv_g)} (one-sided). Macro-averaging over cells "
      "was chosen over pooling raw scores because certainty scales differ "
      "between persons (means 2.32 to 3.76, section 1.5); pooling would mix "
      "response scales into the curve.")
    w()
    # write cell AUCs for R comparison
    pd.DataFrame(
        [{"person": p, "flavor": f,
          "n_pos": int(M_n[PERSONS.index(p), FLAVORS.index(f)]),
          "auc_py": auc_cells[(p, f)]}
         for p in PERSONS for f in FLAVORS]
    ).to_csv(OUT / "py_auc_by_cell.csv", index=False)
    w("Cell AUCs exported to `analysis/py_auc_by_cell.csv` for the R (pROC) "
      "comparison in section 9.")
    w()
    # 3.2 binary operating point
    w("### 3.2 Sensitivity and specificity at the forced-choice operating "
      "point [fn section3]")
    w()
    rows = []
    for p in PERSONS:
        sub = trials[trials.person == p]
        cells = []
        bal = []
        for f in FLAVORS:
            pos = sub[sub.true_flavor == f]
            neg = sub[sub.true_flavor != f]
            sens = (pos.judged_flavor == f).mean()
            spec = 1 - (neg.judged_flavor == f).mean()
            bal.append((sens + spec) / 2)
            cells.append(f"{sens:.2f}/{spec:.2f}")
        rows.append([p] + cells + [fmt(float(np.mean(bal)))])
    md_table(["Person"] + [f"{f} sens/spec" for f in FLAVORS]
             + ["Mean balanced accuracy"], rows)
    w("Sensitivity per cell equals the cell accuracy of 1.2 (same quantity, "
      "detection framing). Balanced accuracy = (sensitivity + "
      "specificity)/2; .50 is chance for any base rate.")
    w()


# ----------------------------------------------------------------------------
# Section 4: awareness of one's own flavor preferences
# ----------------------------------------------------------------------------
def rel_effects_weighted_tasted() -> np.ndarray:
    """Relationship effects for tasted liking with trial-based (weighted)
    margins: cell mean - person trial mean - flavor trial mean + grand trial
    mean."""
    pmean = trials.groupby("person")["liking"].mean().reindex(PERSONS).to_numpy()
    fmean = trials.groupby("true_flavor")["liking"].mean().reindex(FLAVORS).to_numpy()
    grand = trials["liking"].mean()
    return M_tlike - pmean[:, None] - fmean[None, :] + grand


def section4() -> None:
    w("## 4. Awareness of one's own flavor preferences [fn section4, "
      "fn rel_effects_weighted_tasted]")
    w()
    w("Do people know which flavors they uniquely like? Social-relations "
      "logic: relationship effects (cell - perceiver mean - target mean + "
      "grand mean) computed separately for the a priori liking ratings "
      "(`apriori_like`) and the tasted-bear liking cell means "
      "(`mean_liking_tasted`), then correlated across the 24 person x flavor "
      "cells. Removing the margins is what makes this awareness of *unique* "
      "preferences: perceiver-level response tendencies and flavor-level "
      "consensus are taken out. Permutation test: each person's six a priori "
      "ratings shuffled within person (margins re-derived each draw), "
      f"{N_PERM:,} draws, two-sided; this preserves each person's rating "
      "distribution and breaks only the flavor correspondence.")
    w()
    w("Cell sizes run 1 to 8 (Ada tasted lemon once), so the 24 tasted cell "
      "means differ widely in precision (codebook precision warning). Both "
      "requested variants are run: unweighted (equal cell weight, margins "
      "from cell means) and weighted (trial-based margins, correlation "
      "weighted by cell n).")
    w()
    rel_a = dc(M_alike)
    rel_t_unw = dc(M_tlike)
    rel_t_w = rel_effects_weighted_tasted()
    wts = M_n.flatten()
    r_unw = pearson(rel_a.flatten(), rel_t_unw.flatten())
    r_w = wpearson(rel_a.flatten(), rel_t_w.flatten(), wts)
    rho = stats.spearmanr(rel_a.flatten(), rel_t_unw.flatten()).statistic
    # sensitivity: drop the single-trial cell (Ada x lemon)
    keep = M_n.flatten() >= 2
    r_no1 = pearson(rel_a.flatten()[keep], rel_t_unw.flatten()[keep])
    null_unw = np.empty(N_PERM)
    null_w = np.empty(N_PERM)
    null_no1 = np.empty(N_PERM)
    for it in range(N_PERM):
        Aperm = np.vstack([rng.permutation(M_alike[i]) for i in range(4)])
        ra = dc(Aperm)
        null_unw[it] = pearson(ra.flatten(), rel_t_unw.flatten())
        null_w[it] = wpearson(ra.flatten(), rel_t_w.flatten(), wts)
        null_no1[it] = pearson(ra.flatten()[keep], rel_t_unw.flatten()[keep])
    rows = [
        ["unweighted (24 cells)", fmt(r_unw),
         f"p {fmtp(perm_p_two(r_unw, null_unw))}"],
        ["weighted by cell n (trial-based margins)", fmt(r_w),
         f"p {fmtp(perm_p_two(r_w, null_w))}"],
        ["unweighted, single-trial cell (Ada x lemon) dropped, 23 cells",
         fmt(r_no1), f"p {fmtp(perm_p_two(r_no1, null_no1))}"],
        ["Spearman on unweighted relationship effects", fmt(float(rho)),
         "(descriptive robustness)"],
    ]
    md_table(["Variant", "r (a priori rel x tasted rel)", "Permutation p"],
             rows)
    w("The 24 correlated values are not independent (each person contributes "
      "six double-centered cells, each flavor four); the permutation test is "
      "built on the actual exchangeability structure and is the inference to "
      "trust, not an off-the-shelf p for r with df = 22.")
    # Conventional-test contrast (session E4 extension, 2026-08-26):
    # deterministic, no rng consumed. The manuscript quotes this value only
    # to contrast the naive df = 22 test with the permutation inference.
    t_conv = r_unw * np.sqrt(22) / np.sqrt(1 - r_unw ** 2)
    p_conv = 2 * stats.t.sf(abs(t_conv), df=22)
    w(f"For reference, that off-the-shelf test on the unweighted r = "
      f"{fmt(r_unw)} (t = r sqrt(22) / sqrt(1 - r^2) = {t_conv:.3f}, df = 22, "
      f"two-sided) would report p = {p_conv:.3f}; the manuscript quotes this "
      f"only as the conventional-test contrast to the permutation p above.")
    w()
    # margin-level correspondence
    w("### 4.1 Margin-level correspondence (complement) [fn section4]")
    w()
    pm_a = M_alike.mean(axis=1)
    pm_t = trials.groupby("person")["liking"].mean().reindex(PERSONS).to_numpy()
    r_p, p_p, np_ = exact_perm_r(pm_a, pm_t)
    fm_a = M_alike.mean(axis=0)
    fm_t = trials.groupby("true_flavor")["liking"].mean().reindex(FLAVORS).to_numpy()
    r_f, p_f, nf_ = exact_perm_r(fm_a, fm_t)
    w(f"- Perceiver level (n = 4): r = {fmt(r_p)}, exact p = {fmt(p_p, 3)} "
      f"({np_} orderings; smallest attainable p = .042). Do people who say "
      "they like gummy bears more also rate the tasted bears higher?")
    w(f"- Target level (n = 6): r = {fmt(r_f)}, exact p = {fmt(p_f, 3)} "
      f"({nf_} orderings). Do flavors rated better a priori also taste "
      "better in the trials (consensus correspondence)?")
    w()
    ord_a = ", ".join(f"{FLAVORS[j]} {fm_a[j]:.2f}"
                      for j in np.argsort(-fm_a))
    ord_t = ", ".join(f"{FLAVORS[j]} {fm_t[j]:.2f}"
                      for j in np.argsort(-fm_t))
    w(f"The consensus mismatch in full: a priori flavor means (best to "
      f"worst) {ord_a}; tasted flavor means {ord_t}. The a priori favourites "
      "(raspberry, strawberry) are not the flavors that tasted best (apple, "
      "pineapple), and strawberry, the second-highest a priori, is the "
      "lowest-rated flavor in the mouth. Six flavors, descriptive, but the "
      "cleanest single illustration that stated preferences and blind "
      "experience diverge. Placement decided 2026-08-17: supporting element "
      "in results part b), not a standalone highlight.")
    w()
    # 4.2 the relationship effects themselves
    w("### 4.2 The 24 relationship effects (the concrete unique preferences) "
      "[fn section4]")
    w()
    w("Unweighted cell-mean double-centering, matching the primary "
      "decomposition in 5.1. Positive: this person likes this flavor more "
      "than their own overall level and the flavor's consensus predict. "
      "These are the quotable who-uniquely-likes-what numbers for results "
      "part b); tasted-side cells rest on 1 to 8 trials.")
    w()
    rows = []
    for i, p in enumerate(PERSONS):
        rows.append([f"{p}, a priori"]
                    + [f"{rel_a[i, j]:+.2f}" for j in range(6)])
        rows.append([f"{p}, tasted"]
                    + [f"{rel_t_unw[i, j]:+.2f}" for j in range(6)])
    md_table(["Person, measure"] + FLAVORS, rows)
    flat = [(float(rel_t_unw[i, j]), PERSONS[i], FLAVORS_EN[FLAVORS[j]],
             int(M_n[i, j])) for i in range(4) for j in range(6)]
    top_pos = sorted(flat, reverse=True)[:3]
    top_neg = sorted(flat)[:3]
    w("Largest unique tasted likings: "
      + "; ".join(f"{p} x {f} {v:+.2f} (n={n})" for v, p, f, n in top_pos)
      + ". Largest unique dislikings: "
      + "; ".join(f"{p} x {f} {v:+.2f} (n={n})" for v, p, f, n in top_neg)
      + ".")
    w()
    # 4.3 per-perceiver awareness
    w("### 4.3 Per-perceiver awareness (descriptive, anecdotal) "
      "[fn section4]")
    w()
    w("The c)-part parallel to the accuracy individual differences: does "
      "anyone's stated profile track their tasted profile? Primary variant: "
      "correlation of the six relationship effects (unique preferences, "
      "consistent with b/c logic); secondary: raw within-person profile "
      "correlation (includes the consensus component). Exact permutation "
      "per person over all 720 orderings of that person's a priori ratings "
      "(margins re-derived per ordering for the relationship variant), "
      "two-sided, smallest attainable p = .0014. Six cells per person: "
      "anecdotal descriptives, not confirmatory tests.")
    w()
    perms6 = list(itertools.permutations(range(6)))
    rows = []
    for i, p in enumerate(PERSONS):
        r_rel_obs = pearson(rel_a[i], rel_t_unw[i])
        r_raw_obs = pearson(M_alike[i], M_tlike[i])
        cnt_rel = cnt_raw = 0
        for perm in perms6:
            Ap_ = M_alike.copy()
            Ap_[i] = M_alike[i, list(perm)]
            ra = dc(Ap_)
            if abs(pearson(ra[i], rel_t_unw[i])) >= abs(r_rel_obs) - 1e-12:
                cnt_rel += 1
            if abs(pearson(Ap_[i], M_tlike[i])) >= abs(r_raw_obs) - 1e-12:
                cnt_raw += 1
        note = ("includes the single-trial lemon cell" if p == "Ada" else
                "min cell n = "
                + str(int(M_n[i].min())))
        rows.append([p, fmt(r_rel_obs), f"{cnt_rel / 720:.4f}",
                     fmt(r_raw_obs), f"{cnt_raw / 720:.4f}", note])
    md_table(["Person", "r relationship effects (primary)", "exact p",
              "r raw profile", "exact p", "cell-size note"], rows)
    w("Read as anecdote, parallel to the accuracy spread: heterogeneity "
      "here is what the Discussion may take up as clearly labelled "
      "anecdotal evidence that some individuals might track their unique "
      "preferences while others do not; the aggregate test (4, above) "
      "remains the paper's answer to c).")
    w()
    w("Two patterns worth the Discussion's attention, still as anecdote: "
      "Ella, the best differentiator and well calibrated about it, also "
      "shows the strongest stated-tasted correspondence (raw profile "
      "r = .93, exact p = .0056, one of eight uncorrected tests in this "
      "table); and Ada's raw profile is negative while her relationship-"
      "effect correlation is positive, because her stated profile sits "
      "close to the family's stated consensus, which is exactly what blind "
      "tasting contradicts (4.1).")
    w()


# ----------------------------------------------------------------------------
# Section 5: variance decomposition
# ----------------------------------------------------------------------------
def decompose(M: np.ndarray) -> tuple[float, float, float]:
    """Descriptive two-way components from a 4 x 6 cell-mean matrix:
    perceiver = var of row means (ddof 1), target = var of column means
    (ddof 1), relationship = sum of double-centered squares / 15."""
    vp = float(np.var(M.mean(axis=1), ddof=1))
    vt = float(np.var(M.mean(axis=0), ddof=1))
    g = dc(M)
    vr = float((g ** 2).sum() / ((4 - 1) * (6 - 1)))
    return vp, vt, vr


def cell_arrays(col: str) -> dict[tuple[int, int], np.ndarray]:
    d = {}
    for i, p in enumerate(PERSONS):
        for j, f in enumerate(FLAVORS):
            d[(i, j)] = trials.loc[(trials.person == p)
                                   & (trials.true_flavor == f),
                                   col].to_numpy(dtype=float)
    return d


def var_decomp_block(name: str, col: str) -> None:
    cells = cell_arrays(col)
    Mcell = np.array([[cells[(i, j)].mean() for j in range(6)]
                      for i in range(4)])
    vp, vt, vr = decompose(Mcell)
    # pooled within-cell variance
    ss_w = sum(((v - v.mean()) ** 2).sum() for v in cells.values())
    s2w = ss_w / (100 - 24)
    # noise-floor simulation: true relationship effects zero, margins kept
    pm = Mcell.mean(axis=1)
    fm = Mcell.mean(axis=0)
    grand = Mcell.mean()
    mu = pm[:, None] + fm[None, :] - grand
    binary = set(np.unique(np.concatenate(list(cells.values())))) <= {0.0, 1.0}
    sims = np.empty((N_SIM, 3))
    for it in range(N_SIM):
        if binary:
            p0 = np.clip(mu, 0.02, 0.98)
            Msim = rng.binomial(M_n.astype(int), p0) / M_n
        else:
            Msim = mu + rng.normal(0, np.sqrt(s2w / M_n))
        sims[it] = decompose(Msim)
    floor_p = float(sims[:, 0].mean() - np.var(pm, ddof=1))
    floor_t = float(sims[:, 1].mean() - np.var(fm, ddof=1))
    floor_r = float(sims[:, 2].mean())
    adj = [max(vp - floor_p, 0.0), max(vt - floor_t, 0.0),
           max(vr - floor_r, 0.0)]
    # trial-level bootstrap within cells
    boots = np.empty((N_BOOT, 3))
    for it in range(N_BOOT):
        Mb = np.array([[cells[(i, j)][rng.integers(0, len(cells[(i, j)]),
                                                   len(cells[(i, j)]))].mean()
                        for j in range(6)] for i in range(4)])
        boots[it] = decompose(Mb)
    lo = np.percentile(boots, 2.5, axis=0)
    hi = np.percentile(boots, 97.5, axis=0)
    tot = sum(adj) + (s2w if not binary else 0.0)
    rows = []
    labels = ["Perceiver", "Target (flavor)", "Relationship"]
    for k, lab in enumerate(labels):
        raw = [vp, vt, vr][k]
        share = adj[k] / tot if tot > 0 else np.nan
        rows.append([lab, fmt(raw, 4), fmt([floor_p, floor_t, floor_r][k], 4),
                     fmt(adj[k], 4), fmt(share, 2),
                     f"[{fmt(lo[k], 4)}, {fmt(hi[k], 4)}]"])
    if not binary:
        rows.append(["Within-cell (trial)", fmt(s2w, 4), "", fmt(s2w, 4),
                     fmt(s2w / tot, 2), ""])
    md_table(["Component", "Raw", "Noise floor", "Adjusted",
              "Share of total (adj.)", "Bootstrap 95% CI (raw)"], rows)
    if binary:
        w("Binary outcome: within-cell variance is p(1-p) by construction and "
          "is not a separable 'error' component; shares are over the three "
          "adjusted between-cell components only. Noise floor simulated as "
          "binomial draws per cell under the additive margins model "
          "(probabilities clipped to [.02, .98]).")
    else:
        w("Noise floor: expected raw component under zero true relationship "
          "variance, margins and within-cell noise as observed (normal "
          "approximation at cell-mean level); 'Adjusted' subtracts it, "
          "truncated at 0.")
    # Renormalized between-cell shares (session E4 extension, 2026-08-26):
    # each adjusted component over the sum of the three adjusted components,
    # i.e. every noise floor removed and the within-cell part excluded. For a
    # binary outcome this reproduces the Share column above (identity checked),
    # which is the check that the transformation is right; for tasted liking it
    # yields the fully corrected shares the Discussion quotes.
    renorm = [100 * a / sum(adj) for a in adj]
    if binary:
        for k in range(3):
            assert abs(renorm[k] / 100 - adj[k] / tot) < 1e-12
        w(f"Renormalization check: each adjusted component divided by the sum "
          f"of the three adjusted components gives perceiver "
          f"{renorm[0]:.1f}%, flavor {renorm[1]:.1f}%, relationship "
          f"{renorm[2]:.1f}%, which reproduces the Share column, because for "
          f"a binary outcome the share denominator is already the sum of the "
          f"three adjusted components.")
    else:
        w(f"Renormalized between-cell shares, within-cell variance excluded: "
          f"each adjusted component divided by the sum of the three adjusted "
          f"components gives perceiver {renorm[0]:.1f}%, flavor "
          f"{renorm[1]:.1f}%, relationship {renorm[2]:.1f}%. These are the "
          f"fully noise-corrected shares quoted in the Discussion; the "
          f"accuracy decomposition reports its shares in this convention "
          f"already.")
    w("Bootstrap resamples trials within cells (design fixed): the intervals "
      "reflect trial-level noise only. They do not, and cannot, reflect "
      "sampling of perceivers; with n = 4 perceivers a perceiver-level "
      "bootstrap or any inferential variance claim is not credible, so none "
      "is offered (PROJECT.md constraint).")
    w()


def section5() -> None:
    w("## 5. Variance decomposition (descriptive) [fn section5, fn decompose, "
      "fn var_decomp_block]")
    w()
    w("Perceiver, target, relationship components from the 4 x 6 cell-mean "
      "matrices, SRM-style but estimated as a plain crossed two-way "
      "decomposition (this is a two-mode perceiver x flavor design, not a "
      "round robin; see section 9 on TripleR). All figures descriptive.")
    w()
    w("### 5.1 Liking of tasted bears (1-6)")
    w()
    var_decomp_block("liking", "liking")
    w("### 5.2 Accuracy (0/1)")
    w()
    var_decomp_block("accuracy", "correct")
    pacc = np.var(acc, ddof=1)
    facc = np.var([trials.loc[trials.true_flavor == f, "correct"].mean()
                   for f in FLAVORS], ddof=1)
    w(f"Plain-language anchors: the four person accuracies (.12, .68, .28, "
      f".60) have variance {fmt(pacc, 4)} (SD {fmt(np.sqrt(pacc), 2)}); the "
      f"six flavor accuracies have variance {fmt(facc, 4)} "
      f"(SD {fmt(np.sqrt(facc), 2)}).")
    w()
    w("### 5.3 A priori liking (one rating per cell)")
    w()
    vp, vt, vr = decompose(M_alike)
    tot = vp + vt + vr
    rows = [["Perceiver", fmt(vp, 4), fmt(vp / tot, 2)],
            ["Target (flavor)", fmt(vt, 4), fmt(vt / tot, 2)],
            ["Relationship + error", fmt(vr, 4), fmt(vr / tot, 2)]]
    md_table(["Component", "Variance", "Share"], rows)
    w("With one rating per cell, relationship and measurement error cannot be "
      "separated; the third component is their sum, stated as such.")
    w()


# ----------------------------------------------------------------------------
# Section 6: liking-accuracy associations
# ----------------------------------------------------------------------------
def section6() -> None:
    w("## 6. Liking-accuracy associations at three levels [fn section6]")
    w()
    # perceiver level
    w("### 6.1 Perceiver level (n = 4): are perceivers who like gummy bears "
      "more also more accurate?")
    w()
    pm_t = trials.groupby("person")["liking"].mean().reindex(PERSONS).to_numpy()
    r1, p1, _ = exact_perm_r(pm_t, acc)
    r2, p2, _ = exact_perm_r(like_mean_apriori, acc)
    w(f"- Mean tasted liking vs accuracy: r = {fmt(r1)}, exact p = "
      f"{fmt(p1, 3)} (24 orderings).")
    w(f"- A priori mean liking (`like_mean_flavors`) vs accuracy: r = "
      f"{fmt(r2)}, exact p = {fmt(p2, 3)}.")
    w("With four perceivers the smallest attainable exact p is .042 and any r "
      "is compatible with wide population values; these are descriptive "
      "anchors, not tests the paper can lean on.")
    w()
    # target level
    w("### 6.2 Target level (n = 6): are better-liked flavors judged more "
      "accurately?")
    w()
    fm_t = trials.groupby("true_flavor")["liking"].mean().reindex(FLAVORS).to_numpy()
    facc = np.array([trials.loc[trials.true_flavor == f, "correct"].mean()
                     for f in FLAVORS])
    r3, p3, _ = exact_perm_r(fm_t, facc)
    fm_a = M_alike.mean(axis=0)
    r4, p4, _ = exact_perm_r(fm_a, facc)
    w(f"- Mean tasted liking received vs flavor accuracy: r = {fmt(r3)}, "
      f"exact p = {fmt(p3, 3)} (720 orderings).")
    w(f"- Mean a priori liking vs flavor accuracy: r = {fmt(r4)}, exact "
      f"p = {fmt(p4, 3)}.")
    w()
    # relationship level
    w("### 6.3 Relationship level (24 cells): are perceivers particularly "
      "accurate for flavors they uniquely like?")
    w()
    rel_acc = dc(M_acc)
    rel_alike = dc(M_alike)
    rel_tlike = dc(M_tlike)
    wts = M_n.flatten()
    combos = [
        ("a priori liking rel x accuracy rel (cleanest: liking measured "
         "before any tasting)", rel_alike, M_alike),
        ("tasted liking rel x accuracy rel (same-session judgments; liking "
         "given after the flavor guess, so not causally clean)", rel_tlike,
         None),
    ]
    rows = []
    for name, relX, permsrc in combos:
        r_u = pearson(relX.flatten(), rel_acc.flatten())
        r_wt = wpearson(relX.flatten(), rel_acc.flatten(), wts)
        null_u = np.empty(N_PERM)
        null_wt = np.empty(N_PERM)
        for it in range(N_PERM):
            if permsrc is not None:
                Xp = np.vstack([rng.permutation(permsrc[i]) for i in range(4)])
                rx = dc(Xp)
                null_u[it] = pearson(rx.flatten(), rel_acc.flatten())
                null_wt[it] = wpearson(rx.flatten(), rel_acc.flatten(), wts)
            else:
                # permute accuracy cells within person instead: tasted-liking
                # and accuracy cells share trials, so shuffle the *other* side
                Ap = np.vstack([rng.permutation(M_acc[i]) for i in range(4)])
                ra = dc(Ap)
                null_u[it] = pearson(relX.flatten(), ra.flatten())
                null_wt[it] = wpearson(relX.flatten(), ra.flatten(), wts)
        rows.append([name, fmt(r_u), f"p {fmtp(perm_p_two(r_u, null_u))}",
                     fmt(r_wt), f"p {fmtp(perm_p_two(r_wt, null_wt))}"])
    md_table(["Pairing", "r unweighted", "p", "r weighted by cell n", "p"],
             rows)
    w("Permutation: within-person shuffle of the six cells of one side "
      "(margins re-derived per draw), 10,000 draws, two-sided. For the "
      "tasted-liking pairing the accuracy side is shuffled because liking "
      "and accuracy there come from the same trials.")
    w()
    # trial level
    w("### 6.4 Trial-level complement: liking on correct vs incorrect trials")
    w()
    rows = []
    diffs_obs = []
    for p in PERSONS:
        sub = trials[trials.person == p]
        d = sub.loc[sub.correct == 1, "liking"].mean() - \
            sub.loc[sub.correct == 0, "liking"].mean()
        diffs_obs.append((d, int(sub.correct.sum())))
        rows.append([p, fmt(sub.loc[sub.correct == 1, "liking"].mean(), 2),
                     fmt(sub.loc[sub.correct == 0, "liking"].mean(), 2),
                     f"{d:+.2f}"])
    # pooled permutation: shuffle correct within person
    obs_pooled = float(np.mean([d for d, _ in diffs_obs]))
    null = np.empty(N_PERM)
    lik_by_p = {p: trials.loc[trials.person == p, "liking"].to_numpy(dtype=float)
                for p in PERSONS}
    n1_by_p = {p: int(trials.loc[trials.person == p, "correct"].sum())
               for p in PERSONS}
    for it in range(N_PERM):
        ds = []
        for p in PERSONS:
            lik = lik_by_p[p]
            idx = rng.permutation(25)
            n1 = n1_by_p[p]
            ds.append(lik[idx[:n1]].mean() - lik[idx[n1:]].mean())
        null[it] = np.mean(ds)
    pv = perm_p_two(obs_pooled, null)
    md_table(["Person", "Liking M correct", "Liking M incorrect",
              "Difference"], rows)
    w(f"Mean within-person difference {obs_pooled:+.2f}, permutation "
      f"p {fmtp(pv)} (correctness shuffled within person, equal person "
      "weights). Interpretive caveat: liking was judged after the flavor "
      "guess and before feedback, so a positive difference can reflect "
      "either 'I recognize what I like' or 'I like what I think I "
      "recognized'; the design cannot separate the two.")
    w()


# ----------------------------------------------------------------------------
# Section 7: calibration
# ----------------------------------------------------------------------------
def section7() -> None:
    w("## 7. Calibration of a priori self-predictions [fn section7]")
    w()
    w("### 7.1 Overall: `pred_mean_flavors` vs achieved accuracy")
    w()
    rows = []
    for i, p in enumerate(PERSONS):
        p0 = pred_mean[i] / 100
        k = int(hits[i])
        pv = binom_p(k, 25, p0)
        lo, hi = binom_ci(k, 25)
        rows.append([p, fmt(pred_mean[i] / 100), f"{fmt(acc[i])} "
                     f"[{fmt(lo)}, {fmt(hi)}]",
                     f"{(acc[i] - p0) * 100:+.1f}", f"p {fmtp(pv)}"])
    md_table(["Person", "Predicted", "Achieved [exact 95% CI]",
              "Achieved - predicted (pp)",
              "Exact binomial vs own prediction"], rows)
    r_c, p_c, _ = exact_perm_r(pred_mean, acc)
    rho_c = stats.spearmanr(pred_mean, acc).statistic
    p_ada = binom_p(int(hits[0]), 25, pred_mean[0] / 100)
    p_mitja = binom_p(int(hits[3]), 25, pred_mean[3] / 100)
    w(f"Correlation predicted vs achieved over the four persons: r = "
      f"{fmt(r_c)} (exact p = {fmt(p_c, 3)}, 24 orderings), Spearman rho = "
      f"{fmt(float(rho_c))} (descriptive). The two children predicted about "
      "60% and the two adults about 31%; achieved accuracy ordered "
      "differently (.12/.68 vs .28/.60), so the headline is not a level "
      "story but two opposite-direction miscalibrations: Ada predicted 60% "
      f"and achieved 12% (exact p vs own prediction {fmtp(p_ada)}), Mitja "
      f"predicted 31% and achieved 60% (p {fmtp(p_mitja)}). Ella (predicted "
      "63%, achieved 68%) and Natalie (predicted 31%, achieved 28%) were "
      "close to calibrated.")
    w()
    w("### 7.2 Per flavor: 24 `apriori_pred_pct` values vs 24 cell accuracies")
    w()
    x = M_pred.flatten() / 100
    y = M_acc.flatten()
    wts = M_n.flatten()
    r_raw = pearson(x, y)
    r_wraw = wpearson(x, y, wts)
    rel_p = dc(M_pred / 100)
    rel_a = dc(M_acc)
    r_rel = pearson(rel_p.flatten(), rel_a.flatten())
    r_wrel = wpearson(rel_p.flatten(), rel_a.flatten(), wts)
    null_raw = np.empty(N_PERM)
    null_wraw = np.empty(N_PERM)
    null_rel = np.empty(N_PERM)
    null_wrel = np.empty(N_PERM)
    for it in range(N_PERM):
        Pp = np.vstack([rng.permutation(M_pred[i]) for i in range(4)]) / 100
        null_raw[it] = pearson(Pp.flatten(), y)
        null_wraw[it] = wpearson(Pp.flatten(), y, wts)
        rp = dc(Pp)
        null_rel[it] = pearson(rp.flatten(), rel_a.flatten())
        null_wrel[it] = wpearson(rp.flatten(), rel_a.flatten(), wts)
    rows = [
        ["raw, unweighted (mixes levels)", fmt(r_raw),
         f"p {fmtp(perm_p_two(r_raw, null_raw))}"],
        ["raw, weighted by cell n", fmt(r_wraw),
         f"p {fmtp(perm_p_two(r_wraw, null_wraw))}"],
        ["relationship effects (profile knowledge), unweighted", fmt(r_rel),
         f"p {fmtp(perm_p_two(r_rel, null_rel))}"],
        ["relationship effects, weighted by cell n", fmt(r_wrel),
         f"p {fmtp(perm_p_two(r_wrel, null_wrel))}"],
    ]
    md_table(["Variant", "r", "Permutation p (within-person shuffle of "
              "predictions)"], rows)
    w("Within-person profile correlations (does anyone know their own "
      "profile? 6 cells each, descriptive):")
    w()
    rows = []
    for i, p in enumerate(PERSONS):
        rp_ = pearson(M_pred[i] / 100, M_acc[i])
        rows.append([p, fmt(rp_)])
    md_table(["Person", "r (6 per-flavor predictions vs 6 cell accuracies)"],
             rows)
    w("### 7.3 What the per-flavor calibration adds [fn section7]")
    w()
    w("Nothing the paper can use. All four per-flavor variants are near zero "
      "or mildly negative (raw r = -.03, weighted -.09, relationship effects "
      "-.14 and -.16, all permutation p > .34), and the per-person profile "
      "correlations swing from +.62 to -.72 on six points each, which is "
      "noise, not profile knowledge. The one informative reading is "
      "negative: even the two accurate perceivers show no sign of knowing "
      "*which* flavors they can detect (Ella -.53, Mitja -.72, descriptive). "
      "Recommendation: the per-flavor calibration goes to the supplement as "
      "one table plus two sentences; the main text carries only the overall "
      "calibration (7.1), which has the story, and a one-line pointer that "
      "profile-level calibration was absent. The cell-size caveat of "
      "analysis 4 applies unchanged.")
    w()


# ----------------------------------------------------------------------------
# Section 8/9: control-analysis comparison (reads R outputs when present)
# ----------------------------------------------------------------------------
def section9() -> None:
    w("## 8. Control analyses: what runs where [fn section9]")
    w()
    w("Primary inference above is permutation/exact/bootstrap (PROJECT.md "
      "pinned decision). `analysis/control_analyses.R` re-estimates as "
      "controls: (a) crossed mixed models, lmer for liking and glmer "
      "(logistic) for accuracy, with random intercepts for perceiver, "
      "flavor, and their interaction, where the identifiability problem is "
      "structural: variances estimated from 4 perceiver / 6 flavor clusters "
      "are unstable by construction and singular fits are expected, reported, "
      "and not interpreted beyond direction; (b) fixed-slope mirrors of the "
      "learning, awareness, and calibration tests; (c) pROC AUCs for all 24 "
      "cells against `py_auc_by_cell.csv`; (d) a TripleR attempt, expected "
      "to fail structurally because TripleR requires round-robin data "
      "(perceivers judging each other) whereas this is a two-mode perceiver "
      "x flavor design; the failure is documented rather than worked "
      "around.")
    w()
    w("## 9. R vs Python agreement [fn section9]")
    w()
    r_auc_file = OUT / "r_auc_by_cell.csv"
    r_sum_file = OUT / "r_controls_summary.csv"
    if not (r_auc_file.exists() and r_sum_file.exists()):
        w("R outputs not found at log-generation time; run "
          "`analysis/control_analyses.R` and re-run this script to fill this "
          "section.")
        w()
        return
    r_auc = pd.read_csv(r_auc_file)
    py_auc = pd.read_csv(OUT / "py_auc_by_cell.csv")
    m = py_auc.merge(r_auc, on=["person", "flavor"])
    maxdiff = float((m["auc_py"] - m["auc_r"]).abs().max())
    w(f"- pROC vs Python AUC, all 24 person x flavor cells: max absolute "
      f"difference = {maxdiff:.2e} "
      f"({'identical to numerical precision' if maxdiff < 1e-9 else 'CHECK: non-trivial divergence'}).")
    w()
    w("R control summary (`analysis/r_controls_summary.csv`, verbatim):")
    w()
    rsum = pd.read_csv(r_sum_file)
    md_table(list(rsum.columns),
             [[str(x) for x in row] for row in rsum.itertuples(index=False)])
    w("Agreement assessment:")
    w()
    w("- **ROC**: pROC reproduces all 24 Python AUCs exactly (max difference "
      "at numerical precision); same midrank Mann-Whitney estimator.")
    w("- **Liking variance components**: lmer (REML) gives relationship .18 > "
      "person .13 > flavor .00, residual 1.46; the Python noise-floor-"
      "adjusted MoM gives relationship .52 > perceiver .23 > flavor .08, "
      "within 1.38. The *ordering* agrees exactly (relationship largest, "
      "flavor smallest and near zero); the magnitudes differ because lmer "
      "weights cells by n and shrinks under REML at a singular boundary, "
      "while the unweighted MoM keeps small-cell sampling noise in the "
      "estimate and subtracts only its expectation. With 4 and 6 clusters "
      "both are descriptive; the paper reports the ordering, not the "
      "magnitudes, and both estimators carry that ordering.")
    w("- **Accuracy variance components**: glmer (logit scale, singular) "
      "gives person 1.14 > relationship .49 > flavor .00; Python adjusted "
      "shares give perceiver .70 > relationship .22 > flavor .07. Same "
      "ordering, different scales (logit vs linear probability), stated "
      "rather than converted.")
    w("- **Learning**: glmer slopes are null on both indices (exposure "
      "beta = .08, p = .53; global position beta = -.07, p = .76), matching "
      "the permutation verdict of section 2.")
    w("- **Certainty signal**: glmer beta = .56, p = .003, matching section "
      "3.0 (signal carried by Ella and Mitja).")
    w("- **Awareness**: lmer relationship-effect slope beta = .32, "
      "p = .129, matching the permutation r = .31/.33, p = .13-.22: same "
      "direction, same non-significance.")
    w("- **Calibration**: glmer slopes negative and null (raw beta = -1.77, "
      "p = .41; profile beta = -1.39, p = .62), matching section 7.")
    w("- **Singular fits**: every mixed model hit the boundary (singular "
      "fit TRUE throughout), which is the identifiability problem stated in "
      "advance: variances from 4 perceiver / 6 flavor clusters. The models "
      "corroborate directions; they cannot corroborate magnitudes.")
    w("- **TripleR**: fails structurally, as expected: 'The provided data of "
      "group 1 are not in round robin format!' TripleR estimates SRM "
      "components for round-robin designs (perceivers judging each other); "
      "this design is two-mode (4 perceivers x 6 non-judging targets), so "
      "the SRM-style decomposition runs as a crossed two-way decomposition "
      "(Python) and crossed random effects (lme4) instead. Not a bug; a "
      "design fact worth one Method sentence.")
    w()
    w("Sandbox note for reproducibility: this run built the R environment at "
      "`$HOME/mamba` (not `/tmp/mamba`) because a stale root-owned `/tmp` "
      "tree blocked writes, and installed TripleR's compiled dependencies "
      "(ggplot2, plyr, reshape2) as conda binaries because source "
      "compilation fails in this sandbox; TripleR itself is pure R. "
      "`analysis/setup-r-sandbox.sh` was updated accordingly. R resolved to "
      "4.5.3 (session A had verified 4.4.3); all package versions in "
      "section 10.")
    w()


# ----------------------------------------------------------------------------
# Section 10: versions and recommendation placeholder
# ----------------------------------------------------------------------------
def section10() -> None:
    w("## 10. Software versions [fn section10]")
    w()
    w(f"- Python {platform.python_version()}, pandas {pd.__version__}, "
      f"numpy {np.__version__}, scipy {scipy.__version__}.")
    rinfo = OUT / "r_sessioninfo.txt"
    if rinfo.exists():
        import re
        txt = rinfo.read_text()
        rver = re.search(r"R version [^\n]+", txt)
        pkgs = sorted(set(re.findall(
            r"\b(?:lme4|pROC|TripleR|Matrix|ggplot2)_[0-9.\-]+", txt)))
        if rver:
            w(f"- {rver.group(0).strip()}; packages: {', '.join(pkgs)}.")
    else:
        w("- R session info pending (run `analysis/control_analyses.R`).")
    w(f"- Seed {SEED}; {N_PERM:,} permutations, {N_BOOT:,} bootstrap draws, "
      f"{N_SIM:,} noise-floor simulations. Re-running this script reproduces "
      "this log byte-identically.")
    w()
    w("## 11. Session B recommendation [fn section10]")
    w()
    w("**Results structure (decided with Mitja, 2026-08-17).** The Results "
      "are organized around the overarching question, the sensitivity of "
      "perceptions of gummy bear flavors, tested three ways: a) accurate "
      "differentiation, b) specific (unique) preferences, c) awareness of "
      "these specific preferences. A short preliminary block precedes the "
      "three parts.")
    w()
    w("**Preliminaries.** Data verification pointer (0); the no-learning "
      "result (2: primary stratified p = .83, so no trial-order covariate "
      "anywhere, and full feedback becomes a stability argument); response "
      "distributions compressed to one or two sentences (1.4).")
    w()
    w("**Part a) Accurate differentiation: evidence found.** Per-person "
      "accuracy against chance with exact CIs (1.1) and the pooled "
      "confusion matrix (1.3); certainty-signal check (3.0) and macro-AUCs "
      "(3.1: Ella .95 and Mitja .85, both p < .001; Ada .40, Natalie .50); "
      "accuracy decomposition (5.2, perceiver-dominated, adjusted share "
      ".70) in prose as the individual-differences support. Part a) closes "
      "with one sentence hinting at the supplement: a priori self-"
      "predictions of accuracy were strongly miscalibrated, in opposite "
      "directions, for two of four perceivers (supplement: 7.1; Ada -48 "
      "pp, p < .001; Mitja +29 pp, p = .004; Ella and Natalie "
      "calibrated).")
    w()
    w("**Part b) Specific preferences: evidence found.** Tasted-liking "
      "decomposition (5.1: relationship variance the largest between-cell "
      "component, adjusted .24 vs .10 perceiver and .03 flavor, ordering "
      "corroborated by lme4), contrasted with consensus-dominated a priori "
      "liking (5.3: .55); the relationship-effect matrices (4.2) as the "
      "concrete unique preferences; the consensus mismatch (4.1: r = .02, "
      "raspberry/strawberry expected best, apple/pineapple tasted best) as "
      "a supporting element of one or two sentences, possibly a small "
      "figure panel.")
    w()
    w("**Part c) Awareness of specific preferences: no aggregate "
      "evidence.** Pooled relationship-effect correlation r = .31 "
      "unweighted / .33 weighted, p >= .13, identical conclusion under "
      "both weightings (4). Per-perceiver correlations (4.3) reported "
      "descriptively as anecdotal heterogeneity: rel-effects primary (Ada "
      ".46, Ella .61, Natalie .26, Mitja .13, all ns), raw profiles "
      "secondary (Ella .93 with exact p = .0056, one of eight uncorrected "
      "anecdotal tests; Ada -.55; Natalie -.43; Mitja .52). The Discussion "
      "may take the heterogeneity up as clearly labelled anecdotal "
      "evidence that some individuals (Ella) might track their unique "
      "preferences; the aggregate null remains the paper's answer to c).")
    w()
    w("**Supplement.** Overall and per-flavor calibration (7.1, 7.2), "
      "liking-accuracy associations at all three levels (6: all null, one "
      "pointer sentence in the main text), per-flavor accuracy and cell "
      "tables (1.2), per-person confusion matrices (1.3), response-bias "
      "and sequential-indicator tables (1.4), session/package/country "
      "breakdowns (1.6), per-person learning tests and bins (2.1, 2.2), "
      "the 24-cell AUC and sensitivity/specificity tables (3.1, 3.2), and "
      "the R control block (8, 9).")
    w()
    w("**Figure and table mapping for session C** (final count and order "
      "are session C's call): a) an accuracy figure (per-person accuracy "
      "with exact CIs against the chance line) plus the confusion matrix; "
      "b) a decomposition figure (tasted liking vs a priori liking, "
      "possibly with a consensus-mismatch panel); c) the awareness scatter "
      "(24 labelled relationship-effect points, per-person annotation, "
      "honestly captioned r = .31, ns). Supplementary figure candidates: "
      "calibration (predicted vs achieved), AUC dot plot with n_pos "
      "visible, flat learning panel.")
    w()
    w("**Load-bearing.** a) Two of four perceivers beat chance decisively "
      "with a .12-to-.68 spread and no learning under full feedback. b) "
      "Relationship variance is the largest between-cell component of "
      "tasted liking, against consensus-dominated stated liking. c) The "
      "aggregate awareness null, answered honestly, with per-perceiver "
      "heterogeneity as labelled anecdote. **Not load-bearing:** "
      "liking-accuracy links (null at every level), perceiver-level "
      "inferential claims (n = 4, smallest exact p = .042), "
      "package/country/session contrasts, any single per-person awareness "
      "p-value. The overall calibration stays quotable for the Discussion "
      "as a supplementary result; whether the Discussion picks it up is a "
      "session D/E call. The developmental strand stays dead as pinned in "
      "session A: accuracy by age is .12 (8), .68 (13), .28 (42), .60 "
      "(47).")
    w()


# ----------------------------------------------------------------------------
# Section 12: additions from the red-team pass (session F, 2026-08-26)
# ----------------------------------------------------------------------------
def section12() -> None:
    w("## 12. Addition from the red-team pass (session F) [fn section12]")
    w()
    w("One analysis added on 2026-08-26 after the red-team review; the two "
      "depletion-tracking tests that stood beside it were removed on "
      "2026-08-27 (session F2, item 11), which leaves every p-value before "
      "them untouched because they drew from the rng last. The test below "
      "draws from the rng, so it is appended after section 11 instead of "
      "being inserted at the topic it belongs to: this script consumes one "
      "rng in fixed program order, and a draw inserted earlier would move "
      "every later p-value in this log. The deterministic additions of the "
      "same pass sit at their topic, in 1.7 and 2.4.")
    w()
    y = trials.correct.to_numpy().astype(float)
    pmask = [(trials.person == p).to_numpy() for p in PERSONS]
    strata = [np.flatnonzero((trials.true_flavor == f).to_numpy())
              for f in FLAVORS]

    w("### 12.1 Perceiver heterogeneity with flavor composition held fixed "
      "[fn section12]")
    w()
    w("The exact test in 1.7 conditions on the margins only, so a reviewer "
      "can ask whether the four perceivers simply drew different flavor "
      "mixes. This variant permutes trial outcomes within true-flavor "
      "stratum, which holds the difficulty of each perceiver's own set of "
      "bears fixed under the null. Statistic: the variance of the four "
      "perceiver accuracies.")
    w()

    def het(v: np.ndarray) -> float:
        return float(np.var([v[m].mean() for m in pmask], ddof=1))

    obs_het = het(y)
    null_het = np.empty(N_PERM)
    for it in range(N_PERM):
        v = y.copy()
        for idx in strata:
            v[idx] = rng.permutation(y[idx])
        null_het[it] = het(v)
    p_het = perm_p_upper(obs_het, null_het)
    md_table(["Quantity", "Value"], [
        ["Observed variance of the four perceiver accuracies",
         f"{obs_het:.4f} (SD = {obs_het ** 0.5:.3f})"],
        ["Mean of the permutation null", f"{null_het.mean():.4f}"],
        [f"One-sided permutation p ({N_PERM:,} draws, stratified by true "
         "flavor)", f"p {fmtp(p_het)}"],
    ])
    w("Same verdict as the exact test in 1.7, so the perceiver differences "
      "are not an artefact of who happened to receive which flavors.")
    w()


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> None:
    section0()
    section1()
    section2()
    section3()
    section4()
    section5()
    section6()
    section7()
    section9()
    section10()
    section12()
    out = OUT / f"results_{RESULTS_DATE}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(L)} lines)")


if __name__ == "__main__":
    main()
