#!/usr/bin/env python3
"""
make_figures.py -- Session C figure, table, and supplement pipeline.

Builds every figure, both main-text tables, and the assembled supplement from
the three analysis-ready CSVs in data/ (documented in data/codebook.md) plus
the session B results log. One command, from the project root:

    python3 analysis/make_figures.py

Outputs (each figure as .pdf and as .png at 600 dpi):
    figures/fig1_accuracy-differentiation.{pdf,png}   Results part a)
    figures/fig2_variance-components.{pdf,png}        Results part b)
    figures/fig3_awareness.{pdf,png}                  Results part c)
    figures/figS1_calibration.{pdf,png}               supplement
    figures/figS2_learning.{pdf,png}                  supplement
    figures/figS3_roc-curves.{pdf,png}                supplement
    figures/table1_descriptives.md                    main text, Table 1
    figures/supplement.md                             Tables S1 to S13

Relation to analysis/gummibear_analyses.py (session B). This script recomputes
every *deterministic* quantity it plots (accuracies, exact intervals, the
confusion matrix, cell AUCs, relationship effects, raw variance components,
correlations) straight from the data and checks each against the value printed
in analysis/results_2026-08-17.md before drawing anything; a mismatch raises
and stops. Quantities that come out of a simulation or a permutation null
(noise floors, bootstrap intervals, permutation p-values) are not recomputed,
because the session B script consumes one rng in fixed program order and a
second script cannot reproduce that stream. Those values are listed once, in
the LOG_* block below, each tagged with the results-log section it was read
from. The supplement tables are extracted verbatim from the results log, so
they cannot drift from it either.

Palette: authentic Haribo Goldbaeren colors, verified from the product images
in figures/source/ (PROJECT.md pinned decision, 2026-08-17). Colour never
carries information on its own: flavors are labelled directly in every panel,
which is what makes the two near-identical reds (raspberry, strawberry) and
colourblind reading safe. Colour is used for flavor identity only, never for
persons and never for statistical quantities.

Requires: numpy, pandas, scipy, matplotlib.
"""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib as mpl
import numpy as np
import pandas as pd
from matplotlib import patheffects as pe
from matplotlib import pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from scipy import stats

# ----------------------------------------------------------------------------
# Paths, constants
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "analysis"
FIG = ROOT / "figures"
FIG.mkdir(exist_ok=True)

RESULTS_DATE = "2026-08-17"
CHANCE = 1.0 / 6.0
PERSONS = ["Ada", "Ella", "Natalie", "Mitja"]      # person_id order 1..4
FLAVORS = ["E", "O", "Ap", "H", "Z", "An"]         # log order, kept everywhere
FLAVOR_NAME = {"E": "strawberry", "O": "orange", "Ap": "apple",
               "H": "raspberry", "Z": "lemon", "An": "pineapple"}
FLAVOR_SHORT = {"E": "Str", "O": "Ora", "Ap": "App",
                "H": "Ras", "Z": "Lem", "An": "Pin"}

# Haribo Goldbaeren product colors, median pixel extraction from
# figures/source/haribo-goldbaeren-colors-labelled.png (PROJECT.md, 2026-08-17)
FLAVOR_COLOR = {"E": "#D61F24",    # strawberry, bright red
                "O": "#D26535",    # orange
                "Ap": "#819F31",   # apple, green
                "H": "#A41A1A",    # raspberry, dark red
                "Z": "#D3B430",    # lemon, yellow
                "An": "#CCC5B9"}   # pineapple, ivory

INK = "#1A1A1A"
MID = "#595959"
SOFT = "#8C8C8C"
FAINT = "#D9D9D9"
PMARK = {"Ada": "o", "Ella": "s", "Natalie": "^", "Mitja": "D"}
# Greys for the variance figure: colour there would collide with the flavor
# palette, and this figure has no flavors in it.
VC_COLOR = {"perceiver": "#D9D9D9", "flavor": "#A6A6A6",
            "relationship": "#262626", "rel_error": "#737373"}

# ----------------------------------------------------------------------------
# Values taken from analysis/results_2026-08-17.md.
# Not recomputed here: each is a permutation p, a simulated noise floor, or a
# bootstrap interval. Section numbers refer to the results log.
# ----------------------------------------------------------------------------
LOG_P_ACC = {"Ada": "p = .79", "Ella": "p < .001",           # log 1.1
             "Natalie": "p = .17", "Mitja": "p < .001"}
LOG_MACRO_AUC_P = {"Ada": "p = .94", "Ella": "p < .001",     # log 3.1
                   "Natalie": "p = .46", "Mitja": "p < .001"}
LOG_VAR = {                                                   # log 5.1, 5.2, 5.3
    "tasted_liking": {"raw": (0.3011, 0.1811, 0.9424), "within": 1.3823,
                      "floor": (0.0702, 0.1045, 0.4192),
                      "adj": (0.2308, 0.0766, 0.5231),
                      "share": (0.10, 0.03, 0.24, 0.62),
                      "ci": ((0.1415, 0.6201), (0.0754, 0.4661),
                             (0.7410, 1.6599))},
    "apriori_liking": {"raw": (0.0556, 1.0917, 0.8333), "within": None,
                       "floor": None, "adj": (0.0556, 1.0917, 0.8333),
                       "share": (0.03, 0.55, 0.42, None), "ci": None},
    "accuracy": {"raw": (0.0906, 0.0190, 0.0700), "within": None,
                 "floor": (0.0051, 0.0101, 0.0427),
                 "adj": (0.0854, 0.0090, 0.0273),
                 "share": (0.70, 0.07, 0.22, None),
                 "ci": ((0.0533, 0.1430), (0.0102, 0.0475),
                        (0.0520, 0.1493))},
}
LOG_AWARE_P = {"unweighted": "p = .21", "weighted": "p = .13"}  # log 4
LOG_AWARE_R = {"unweighted": 0.310, "weighted": 0.331}          # log 4
LOG_AWARE_PERSON_P = {"Ada": "p = .69", "Ella": "p = .25",      # log 4.3
                      "Natalie": "p = .67", "Mitja": "p = .85"}
LOG_CONSENSUS_P = "exact p = .98"                               # log 4.1
LOG_LEARNING = "stratified permutation test, r = .04, p = .83"  # log 2
LOG_CALIB_P = {"Ada": "p < .001", "Ella": "p = .68",            # log 7.1
               "Natalie": "p = .83", "Mitja": "p = .004"}

# Deterministic values, printed in the log, checked against recomputation here.
LOG_HITS = {"Ada": 3, "Ella": 17, "Natalie": 7, "Mitja": 15}       # log 1.1
LOG_TRUE_N = {"E": 11, "O": 17, "Ap": 23, "H": 17, "Z": 17, "An": 15}
LOG_CONF_DIAG = {"E": 4, "O": 9, "Ap": 8, "H": 9, "Z": 7, "An": 5}  # log 1.3
LOG_MACRO_AUC = {"Ada": 0.400, "Ella": 0.949,                      # log 3.1
                 "Natalie": 0.503, "Mitja": 0.851}
LOG_REL_TASTED = {                                                  # log 4.2
    "Ada": [-0.57, 0.89, -0.08, -1.24, 1.60, -0.61],
    "Ella": [0.72, -0.58, -0.95, -0.11, -0.44, 1.35],
    "Natalie": [-0.03, 0.27, 0.87, -0.19, -0.35, -0.57],
    "Mitja": [-0.13, -0.58, 0.15, 1.54, -0.82, -0.17]}
LOG_REL_APRIORI = {                                                 # log 4.2
    "Ada": [-0.17, 0.58, -0.17, 0.33, 0.33, -0.92],
    "Ella": [-0.17, -0.42, -0.17, -0.67, 0.33, 1.08],
    "Natalie": [1.00, -0.25, 0.00, 0.50, 0.50, -1.75],
    "Mitja": [-0.67, 0.08, 0.33, -0.17, -1.17, 1.58]}
LOG_AWARE_PERSON_R = {"Ada": 0.459, "Ella": 0.613,                  # log 4.3
                      "Natalie": 0.255, "Mitja": 0.132}
LOG_CONSENSUS_R = 0.019                                             # log 4.1
LOG_PRED = {"Ada": 60.0, "Ella": 62.8, "Natalie": 31.3,             # log 7.1
            "Mitja": 31.3}
LOG_EXPOSURE_BINS = [(11, 24), (7, 23), (11, 21), (13, 32)]         # log 2.2

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


# ----------------------------------------------------------------------------
# Typography and figure style
# ----------------------------------------------------------------------------
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "Nimbus Sans",
                        "TeX Gyre Heros", "Liberation Sans", "DejaVu Sans"],
    "font.size": 10,
    "axes.labelsize": 10,
    "axes.titlesize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 8.5,
    "legend.frameon": False,
    "axes.linewidth": 0.8,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.major.size": 3.0,
    "ytick.major.size": 3.0,
    "lines.linewidth": 1.1,
    "grid.color": FAINT,
    "grid.linewidth": 0.6,
    "figure.dpi": 150,
    "savefig.dpi": 600,
    "savefig.bbox": "standard",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})

W_FULL = 7.09   # 180 mm, full journal page width
FS_ANNOT = 8.5  # in-panel annotation
FS_SMALL = 8.0  # dense tick labels (24-cell axis, matrix cells)


def panel(ax, letter: str, dx: float = -0.14, dy: float = 1.04) -> None:
    ax.text(dx, dy, letter, transform=ax.transAxes, fontsize=12,
            fontweight="bold", va="bottom", ha="left")


def save(fig, name: str) -> None:
    # CreationDate stripped so that re-running gives byte-identical files
    fig.savefig(FIG / f"{name}.pdf", metadata={"CreationDate": None})
    fig.savefig(FIG / f"{name}.png", dpi=600, metadata={"Software": None})
    plt.close(fig)
    print(f"  wrote figures/{name}.pdf and .png")


# ----------------------------------------------------------------------------
# Data and derived quantities
# ----------------------------------------------------------------------------
trials = pd.read_csv(DATA / "gummibear_trials.csv").sort_values("trial_id")
apri = pd.read_csv(DATA / "gummibear_apriori.csv").sort_values("person_id")
along = pd.read_csv(DATA / "gummibear_apriori_long.csv")


def pivot_along(col: str) -> np.ndarray:
    return (along.pivot(index="person", columns="flavor", values=col)
            .reindex(index=PERSONS, columns=FLAVORS).to_numpy(dtype=float))


M_pred = pivot_along("apriori_pred_pct")
M_alike = pivot_along("apriori_like")
M_tlike = pivot_along("mean_liking_tasted")
M_cert = pivot_along("mean_certainty")
M_acc = pivot_along("accuracy")
M_n = pivot_along("n_trials")
M_hits = pivot_along("n_correct")


def dc(M: np.ndarray) -> np.ndarray:
    """Double-centre: cell - row mean - column mean + grand mean."""
    return (M - M.mean(axis=1, keepdims=True)
            - M.mean(axis=0, keepdims=True) + M.mean())


def binom_ci(k: int, n: int) -> tuple[float, float]:
    lo = 0.0 if k == 0 else stats.beta.ppf(0.025, k, n - k + 1)
    hi = 1.0 if k == n else stats.beta.ppf(0.975, k + 1, n - k)
    return float(lo), float(hi)


def auc_from_ranks(rank_sum_pos: float, n1: int, n0: int) -> float:
    return (rank_sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def pearson(x, y) -> float:
    return float(np.corrcoef(np.asarray(x, float), np.asarray(y, float))[0, 1])


def ols(x, y) -> tuple[float, float]:
    b, a = np.polyfit(np.asarray(x, float), np.asarray(y, float), 1)
    return float(a), float(b)          # intercept, slope


def cell_aucs() -> tuple[np.ndarray, dict]:
    """Certainty-graded one-versus-rest AUC per cell (results log 3.1)."""
    A = np.zeros((4, 6))
    scores = {}
    for i, p in enumerate(PERSONS):
        sub = trials[trials.person == p].sort_values("trial_within_person")
        cert = sub["certainty"].to_numpy(float)
        judged = sub["judged_flavor"].to_numpy()
        true_v = sub["true_flavor"].to_numpy()
        S = np.column_stack([np.where(judged == f, cert, -cert)
                             for f in FLAVORS])
        scores[p] = (S, true_v)
        for j, f in enumerate(FLAVORS):
            pos = true_v == f
            n1 = int(pos.sum())
            r = stats.rankdata(S[:, j])
            A[i, j] = auc_from_ranks(float(r[pos].sum()), n1, 25 - n1)
    return A, scores


M_auc, SCORES = cell_aucs()
REL_T = dc(M_tlike)                     # tasted relationship effects
REL_A = dc(M_alike)                     # a priori relationship effects
MACRO_AUC = {p: float(M_auc[i].mean()) for i, p in enumerate(PERSONS)}
ACC = {p: float(apri.loc[apri.person == p, "achieved_accuracy"].iloc[0])
       for p in PERSONS}
HITS = {p: int(apri.loc[apri.person == p, "achieved_hits"].iloc[0])
        for p in PERSONS}
PRED = {p: float(apri.loc[apri.person == p, "pred_mean_flavors"].iloc[0])
        for p in PERSONS}
CONF = np.zeros((6, 6), dtype=int)      # rows true, cols judged
for a, t in enumerate(FLAVORS):
    for b, j in enumerate(FLAVORS):
        CONF[a, b] = int(((trials.true_flavor == t)
                          & (trials.judged_flavor == j)).sum())
FLAVOR_ACC = {f: (int(trials.loc[trials.true_flavor == f, "correct"].sum()),
                  int((trials.true_flavor == f).sum())) for f in FLAVORS}
# Flavor-level means over trials (results log 1.5 and 4.1 use the trial-level
# mean, not the unweighted mean of the four cell means).
TLIKE_FLAVOR = np.array([trials.loc[trials.true_flavor == f, "liking"].mean()
                         for f in FLAVORS])
CERT_FLAVOR = np.array([trials.loc[trials.true_flavor == f, "certainty"].mean()
                        for f in FLAVORS])
ALIKE_FLAVOR = M_alike.mean(axis=0)
# Correlations shown on the figures are the recomputed values, not the rounded
# ones in the log; verify() checks each against the log before anything is drawn.
R_AWARE = pearson(REL_A.ravel(), REL_T.ravel())
R_AWARE_PERSON = {p: pearson(REL_A[i], REL_T[i])
                  for i, p in enumerate(PERSONS)}


# ----------------------------------------------------------------------------
# Verification against the results log (runs before anything is drawn)
# ----------------------------------------------------------------------------
def verify() -> None:
    for p in PERSONS:
        check(f"hits {p}", HITS[p] == LOG_HITS[p], f"{HITS[p]}")
    for f in FLAVORS:
        check(f"true-flavor n {f}", FLAVOR_ACC[f][1] == LOG_TRUE_N[f],
              str(FLAVOR_ACC[f][1]))
        check(f"confusion diagonal {f}",
              CONF[FLAVORS.index(f), FLAVORS.index(f)] == LOG_CONF_DIAG[f],
              str(CONF[FLAVORS.index(f), FLAVORS.index(f)]))
    check("confusion matrix total", CONF.sum() == 100, str(CONF.sum()))
    for p in PERSONS:
        check(f"macro-AUC {p}", abs(MACRO_AUC[p] - LOG_MACRO_AUC[p]) < 5e-4,
              f"{MACRO_AUC[p]:.3f}")
    pyauc = pd.read_csv(OUT / "py_auc_by_cell.csv")
    dmax = 0.0
    for _, r in pyauc.iterrows():
        if str(r["flavor"]) in FLAVORS and str(r["person"]) in PERSONS:
            i, j = PERSONS.index(str(r["person"])), FLAVORS.index(str(r["flavor"]))
            dmax = max(dmax, abs(M_auc[i, j] - float(r["auc_py"])))
    check("24 cell AUCs vs py_auc_by_cell.csv", dmax < 1e-9,
          f"max d = {dmax:.2e}")
    for i, p in enumerate(PERSONS):
        dt = np.max(np.abs(REL_T[i] - np.array(LOG_REL_TASTED[p])))
        da = np.max(np.abs(REL_A[i] - np.array(LOG_REL_APRIORI[p])))
        check(f"relationship effects tasted {p}", dt < 6e-3, f"max d = {dt:.3f}")
        check(f"relationship effects a priori {p}", da < 6e-3,
              f"max d = {da:.3f}")
    check("awareness r (24 cells)",
          abs(R_AWARE - LOG_AWARE_R["unweighted"]) < 1e-3, f"{R_AWARE:.3f}")
    for p in PERSONS:
        check(f"awareness r {p}",
              abs(R_AWARE_PERSON[p] - LOG_AWARE_PERSON_R[p]) < 1e-3,
              f"{R_AWARE_PERSON[p]:.3f}")
    r_cons = pearson(ALIKE_FLAVOR, TLIKE_FLAVOR)
    check("consensus mismatch r", abs(r_cons - LOG_CONSENSUS_R) < 1e-3,
          f"{r_cons:.3f}")
    halves = {p: (int(trials[(trials.person == p)
                            & (trials.trial_within_person <= 12)]
                      ["correct"].sum()),
                  int(trials[(trials.person == p)
                             & (trials.trial_within_person > 12)]
                      ["correct"].sum())) for p in PERSONS}
    check("first-half hits Ella / Mitja (PROJECT.md pin)",
          halves["Ella"][0] == 9 and halves["Mitja"][0] == 8,
          f"{halves['Ella'][0]}/12, {halves['Mitja'][0]}/12")
    check("pooled halves 22/48 and 20/52 (log 2.2)",
          sum(h[0] for h in halves.values()) == 22
          and sum(h[1] for h in halves.values()) == 20,
          f"{sum(h[0] for h in halves.values())}/48, "
          f"{sum(h[1] for h in halves.values())}/52")
    for p in PERSONS:
        check(f"predicted accuracy {p}", abs(PRED[p] - LOG_PRED[p]) < 0.06,
              f"{PRED[p]:.1f}")
    for name, M in (("tasted_liking", M_tlike), ("accuracy", M_acc),
                    ("apriori_liking", M_alike)):
        vp = float(np.var(M.mean(axis=1), ddof=1))
        vt = float(np.var(M.mean(axis=0), ddof=1))
        vr = float((dc(M) ** 2).sum() / 15.0)
        want = LOG_VAR[name]["raw"]
        ok = all(abs(a - b) < 5e-4 for a, b in zip((vp, vt, vr), want))
        check(f"raw variance components {name}", ok,
              f"{vp:.4f}/{vt:.4f}/{vr:.4f}")
    for name in ("tasted_liking", "accuracy"):
        v = LOG_VAR[name]
        ok = abs(v["adj"][2] + v["floor"][2] - v["raw"][2]) < 1e-3
        check(f"relationship = adjusted + noise floor ({name})", ok,
              f"{v['adj'][2]:.4f} + {v['floor'][2]:.4f} = {v['raw'][2]:.4f}")
    ex = trials["prior_own_exposures_flavor"].to_numpy()
    bins = [(ex == 0), (ex == 1), (ex == 2), (ex >= 3)]
    got = [(int(trials.loc[b, "correct"].sum()), int(b.sum())) for b in bins]
    check("learning exposure bins", got == LOG_EXPOSURE_BINS, str(got))

    bad = [c for c in CHECKS if not c[1]]
    width = max(len(c[0]) for c in CHECKS)
    for label, ok, detail in CHECKS:
        print(f"  [{'ok ' if ok else 'FAIL'}] {label:<{width}}  {detail}")
    if bad:
        raise SystemExit(f"\n{len(bad)} check(s) failed against "
                         f"analysis/results_{RESULTS_DATE}.md; nothing drawn.")
    print(f"  {len(CHECKS)} checks passed against results_{RESULTS_DATE}.md\n")


# ----------------------------------------------------------------------------
# Shared drawing helpers
# ----------------------------------------------------------------------------
def place_labels(ax, fig, xs, ys, texts, fontsize=7.0, color=MID) -> None:
    """Put a short text label next to each point, trying eight positions and
    keeping the first that collides with no earlier label and no marker."""
    pos = ax.get_position()
    w_pt = pos.width * fig.get_figwidth() * 72.0
    h_pt = pos.height * fig.get_figheight() * 72.0
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    dx_pt, dy_pt = (x1 - x0) / w_pt, (y1 - y0) / h_pt
    boxes: list[tuple[float, float, float, float]] = []
    marker_r = 4.0
    for x, y in zip(xs, ys):                       # markers block label space
        boxes.append((x - marker_r * dx_pt, x + marker_r * dx_pt,
                      y - marker_r * dy_pt, y + marker_r * dy_pt))
    fixed = list(boxes)
    for x, y, t in zip(xs, ys, texts):
        wl = 0.58 * fontsize * len(t) * dx_pt
        hl = 1.0 * fontsize * dy_pt
        gap = 4.6
        cands = [(gap * dx_pt, gap * dy_pt, "left", "bottom"),
                 (-gap * dx_pt, gap * dy_pt, "right", "bottom"),
                 (gap * dx_pt, -gap * dy_pt, "left", "top"),
                 (-gap * dx_pt, -gap * dy_pt, "right", "top"),
                 (0, (gap + 2) * dy_pt, "center", "bottom"),
                 (0, -(gap + 2) * dy_pt, "center", "top"),
                 (2 * gap * dx_pt, 0, "left", "center"),
                 (-2 * gap * dx_pt, 0, "right", "center")]
        chosen = None
        for ox, oy, ha, va in cands:
            lx, ly = x + ox, y + oy
            bx0 = lx if ha == "left" else (lx - wl if ha == "right"
                                           else lx - wl / 2)
            by0 = ly if va == "bottom" else (ly - hl if va == "top"
                                             else ly - hl / 2)
            box = (bx0, bx0 + wl, by0, by0 + hl)
            if box[0] < x0 or box[1] > x1 or box[2] < y0 or box[3] > y1:
                continue
            if any(not (box[1] < b[0] or box[0] > b[1]
                        or box[3] < b[2] or box[2] > b[3])
                   for b in boxes + fixed):
                continue
            chosen = (lx, ly, ha, va, box)
            break
        if chosen is None:
            ox, oy, ha, va = cands[0]
            lx, ly = x + ox, y + oy
            chosen = (lx, ly, ha, va, (lx, lx + wl, ly, ly + hl))
        lx, ly, ha, va, box = chosen
        ax.text(lx, ly, t, fontsize=fontsize, color=color, ha=ha, va=va,
                zorder=5)
        boxes.append(box)


def cell_axis(ax, ylab: str) -> np.ndarray:
    """x-axis grammar for the 24 perceiver x flavor cells: four groups of six,
    each tick labelled with the flavor and the number of trials in the cell."""
    xs = np.array([i * 7.2 + j for i in range(4) for j in range(6)], float)
    labels = [f"{FLAVOR_SHORT[f]}\n{int(M_n[i, j])}"
              for i in range(4) for j, f in enumerate(FLAVORS)]
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=FS_SMALL)
    ax.set_xlim(-1.0, xs[-1] + 1.0)
    ax.set_ylabel(ylab)
    for i in range(1, 4):
        ax.axvline(i * 7.2 - 1.1, color=FAINT, lw=0.8, zorder=0)
    return xs


def flavor_key(fig, y: float = 0.012, fontsize: float = 8.5) -> None:
    """One shared swatch key, drawn as a row at the foot of the figure."""
    x0, span = 0.105, 0.79
    for j, f in enumerate(FLAVORS):
        x = x0 + span * j / (len(FLAVORS) - 1)
        fig.patches.append(Rectangle((x, y), 0.014, 0.020,
                                     facecolor=FLAVOR_COLOR[f], edgecolor=INK,
                                     lw=0.5, transform=fig.transFigure,
                                     zorder=5))
        fig.text(x + 0.021, y + 0.002, FLAVOR_NAME[f], fontsize=fontsize,
                 va="bottom", ha="left")


# ----------------------------------------------------------------------------
# Figure 1: results part a), accurate differentiation
# ----------------------------------------------------------------------------
def figure1() -> None:
    fig = plt.figure(figsize=(W_FULL, 6.3))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[1.0, 0.9],
                  width_ratios=[1.0, 1.1], hspace=0.66, wspace=0.36,
                  left=0.095, right=0.975, top=0.935, bottom=0.115)
    axA = fig.add_subplot(gs[0, 0])
    axB = fig.add_subplot(gs[0, 1])
    axC = fig.add_subplot(gs[1, :])

    # ---- A: per-person accuracy against chance -----------------------------
    x = np.arange(4)
    y = np.array([ACC[p] for p in PERSONS])
    lo, hi = zip(*[binom_ci(HITS[p], 25) for p in PERSONS])
    axA.axhline(CHANCE, color=MID, lw=1.0, ls=(0, (4, 2)), zorder=1)
    axA.text(3.45, CHANCE + 0.02, "chance (1/6)", fontsize=FS_ANNOT, color=MID,
             ha="right", va="bottom")
    axA.errorbar(x, y, yerr=[y - np.array(lo), np.array(hi) - y], fmt="none",
                 ecolor=INK, elinewidth=1.1, capsize=3.2, capthick=1.1,
                 zorder=2)
    axA.scatter(x, y, s=48, color=INK, zorder=3)
    for i, p in enumerate(PERSONS):
        axA.text(i, hi[i] + 0.035, LOG_P_ACC[p], ha="center", va="bottom",
                 fontsize=FS_ANNOT)
        axA.text(i + 0.16, y[i], f"{HITS[p]}/25", ha="left", va="center",
                 fontsize=FS_ANNOT, color=MID)
    axA.set_xticks(x)
    axA.set_xticklabels(PERSONS)
    axA.set_xlim(-0.55, 3.6)
    axA.set_ylim(0, 1.0)
    axA.set_yticks(np.arange(0, 1.01, 0.2))
    axA.set_ylabel("Proportion of trials\nidentified correctly")
    panel(axA, "A", dx=-0.20)

    # ---- B: confusion matrix ----------------------------------------------
    prop = CONF / CONF.sum(axis=1, keepdims=True)
    axB.imshow(prop, cmap="Greys", vmin=0, vmax=0.55, aspect="equal")
    for a in range(6):
        for b in range(6):
            axB.text(b, a, str(CONF[a, b]), ha="center", va="center",
                     fontsize=FS_SMALL,
                     color="white" if prop[a, b] > 0.33 else INK)
        axB.add_patch(Rectangle((a - 0.5, a - 0.5), 1, 1, fill=False,
                                edgecolor=INK, lw=1.1, zorder=4))
        axB.text(6.1, a, f"{LOG_TRUE_N[FLAVORS[a]]}", ha="left", va="center",
                 fontsize=FS_SMALL, color=MID)
    axB.text(6.1, -0.9, "n", ha="left", va="center", fontsize=FS_SMALL,
             color=MID)
    axB.set_xticks(range(6))
    axB.set_yticks(range(6))
    axB.set_xticklabels([FLAVOR_SHORT[f] for f in FLAVORS], fontsize=FS_SMALL)
    axB.set_yticklabels([FLAVOR_SHORT[f] for f in FLAVORS], fontsize=FS_SMALL)
    axB.tick_params(axis="x", pad=13, length=0)
    axB.tick_params(axis="y", pad=13, length=0)
    for j, f in enumerate(FLAVORS):
        axB.scatter([j], [5.95], marker="s", s=42, color=FLAVOR_COLOR[f],
                    edgecolor=INK, linewidth=0.5, clip_on=False, zorder=5)
        axB.scatter([-0.95], [j], marker="s", s=42, color=FLAVOR_COLOR[f],
                    edgecolor=INK, linewidth=0.5, clip_on=False, zorder=5)
    axB.set_xlabel("flavor named")
    axB.set_ylabel("flavor tasted")
    for s in axB.spines.values():
        s.set_visible(False)
    panel(axB, "B", dx=-0.22)

    # ---- C: 24 cell AUCs ---------------------------------------------------
    xs = cell_axis(axC, "Area under the ROC curve")
    axC.axhline(0.5, color=MID, lw=1.0, ls=(0, (4, 2)), zorder=1)
    axC.text(-0.85, 0.52, "chance (AUC = .50)", fontsize=FS_ANNOT, color=MID,
             ha="left", va="bottom")
    k = 0
    for i, p in enumerate(PERSONS):
        for j, f in enumerate(FLAVORS):
            axC.scatter(xs[k], M_auc[i, j], s=24 + 11 * M_n[i, j],
                        color=FLAVOR_COLOR[f], edgecolor=INK, linewidth=0.6,
                        zorder=3)
            k += 1
        axC.plot([i * 7.2 - 0.45, i * 7.2 + 5.45],
                 [MACRO_AUC[p], MACRO_AUC[p]], color=INK, lw=1.8, zorder=2)
        axC.text(i * 7.2 + 2.5, 1.055,
                 f"{p}\nmacro-AUC {MACRO_AUC[p]:.2f}, {LOG_MACRO_AUC_P[p]}",
                 ha="center", va="bottom", fontsize=FS_ANNOT,
                 transform=axC.get_xaxis_transform(), clip_on=False)
    axC.set_ylim(-0.02, 1.16)
    axC.set_yticks(np.arange(0, 1.01, 0.25))
    size_handles = [Line2D([], [], marker="o", color="none",
                           markerfacecolor="white", markeredgecolor=INK,
                           markeredgewidth=0.6,
                           markersize=np.sqrt(24 + 11 * n) * 0.62,
                           label=f"{n}") for n in (1, 4, 8)]
    axC.legend(handles=size_handles, loc="lower right",
               bbox_to_anchor=(1.0, -0.02), ncol=3, fontsize=FS_ANNOT,
               title="trials in cell", title_fontsize=FS_ANNOT,
               handletextpad=0.1, columnspacing=0.9, borderpad=0.2)
    axC.text(0.0, -0.235, "flavor / trials in cell", transform=axC.transAxes,
             fontsize=FS_ANNOT, color=MID, ha="left", va="top")
    panel(axC, "C", dx=-0.075, dy=1.16)

    save(fig, "fig1_accuracy-differentiation")


# ----------------------------------------------------------------------------
# Figure 2: results part b), relative variance components
# ----------------------------------------------------------------------------
def vc_shares(key: str) -> dict[str, float]:
    """Shares of the variance between the 24 perceiver x flavor cells, using
    the log's own convention (component / sum of the three components). The
    relationship component is split into the part that survives the simulated
    noise floor and the floor itself, where the floor is estimable."""
    v = LOG_VAR[key]
    vp, vt, vr = v["raw"]
    tot = vp + vt + vr
    out = {"perceiver": vp / tot, "flavor": vt / tot}
    if v["floor"] is None:
        out["relationship"] = vr / tot
        out["rel_error"] = 0.0
    else:
        out["relationship"] = v["adj"][2] / tot
        out["rel_error"] = v["floor"][2] / tot
    return out


def figure2() -> None:
    order = [("Identification\naccuracy", "accuracy"),
             ("Stated liking\n(before tasting)", "apriori_liking"),
             ("Tasted liking\n(blind trials)", "tasted_liking")]
    fig, ax = plt.subplots(figsize=(5.4, 4.9))
    fig.subplots_adjust(left=0.155, right=0.975, top=0.965, bottom=0.235)
    xb = np.arange(3)
    wbar = 0.56
    segs = ["perceiver", "flavor", "relationship", "rel_error"]
    labels = {"perceiver": "Perceiver", "flavor": "Flavor",
              "relationship": "Relationship (perceiver x flavor)",
              "rel_error": "Relationship: sampling noise"}
    for k, (name, key) in enumerate(order):
        sh = vc_shares(key)
        bottom = 0.0
        for s in segs:
            h = sh[s] * 100
            if h <= 0:
                continue
            ax.bar(xb[k], h, wbar, bottom=bottom, color=VC_COLOR[s],
                   edgecolor=INK, linewidth=0.8,
                   hatch="////" if s == "rel_error" else None, zorder=2)
            txt = f"{h:.0f}%"
            if s == "relationship" and sh["rel_error"] == 0:
                txt = f"{h:.0f}%\n(incl. error)"
            if h >= 8:
                t = ax.text(xb[k], bottom + h / 2, txt, ha="center",
                            va="center", fontsize=FS_ANNOT, linespacing=1.25,
                            color="white" if s == "relationship" else INK,
                            zorder=3)
                if s == "rel_error":       # keep readable over the hatch
                    t.set_path_effects([pe.withStroke(linewidth=2.6,
                                                      foreground="white")])
            else:
                ax.annotate(txt, xy=(xb[k] + wbar / 2, bottom + h / 2),
                            xytext=(xb[k] + wbar / 2 + 0.13, bottom + h / 2),
                            fontsize=FS_ANNOT, va="center", ha="left",
                            arrowprops=dict(arrowstyle="-", color=SOFT,
                                            lw=0.7, shrinkA=1, shrinkB=1))
            bottom += h
    ax.set_xticks(xb)
    ax.set_xticklabels([n for n, _ in order], fontsize=9.5)
    ax.set_xlim(-0.62, 2.62)
    ax.set_ylim(0, 100)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_ylabel("Share of variance between the 24\nperceiver x flavor cells (%)")
    handles = [Rectangle((0, 0), 1, 1, facecolor=VC_COLOR[s], edgecolor=INK,
                         linewidth=0.8,
                         hatch="////" if s == "rel_error" else None,
                         label=labels[s]) for s in segs]
    ax.legend(handles=handles, loc="upper center",
              bbox_to_anchor=(0.5, -0.135), ncol=2, fontsize=8.5,
              handlelength=1.5, handleheight=1.1, labelspacing=0.5,
              columnspacing=1.2)
    save(fig, "fig2_variance-components")


# ----------------------------------------------------------------------------
# Figure 3: results part c), awareness of specific preferences
# ----------------------------------------------------------------------------
def figure3() -> None:
    fig = plt.figure(figsize=(W_FULL, 4.5))
    gs = GridSpec(2, 3, figure=fig, width_ratios=[1.6, 1.0, 1.0],
                  hspace=0.50, wspace=0.34, left=0.085, right=0.985,
                  top=0.905, bottom=0.175)
    axA = fig.add_subplot(gs[:, 0])
    small = [fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2]),
             fig.add_subplot(gs[1, 1]), fig.add_subplot(gs[1, 2])]

    lim = (-2.0, 2.0)
    axA.axhline(0, color=FAINT, lw=0.9, zorder=0)
    axA.axvline(0, color=FAINT, lw=0.9, zorder=0)
    xall, yall = REL_A.ravel(), REL_T.ravel()
    a, b = ols(xall, yall)
    xx = np.array(lim)
    axA.plot(xx, a + b * xx, color=MID, lw=1.2, ls=(0, (5, 2)), zorder=2)
    for i, p in enumerate(PERSONS):
        for j, f in enumerate(FLAVORS):
            axA.scatter(REL_A[i, j], REL_T[i, j], marker=PMARK[p], s=44,
                        color=FLAVOR_COLOR[f], edgecolor=INK, linewidth=0.6,
                        zorder=3)
    axA.set_xlim(-2.15, 2.15)
    axA.set_ylim(-2.15, 2.95)
    axA.set_yticks(np.arange(-2, 2.1, 1))
    axA.set_xticks(np.arange(-2, 2.1, 1))
    axA.set_xlabel("Stated unique liking\n(relationship effect)")
    axA.set_ylabel("Tasted unique liking\n(relationship effect)")
    place_labels(axA, fig, xall, yall,
                 [FLAVOR_SHORT[f] for _ in PERSONS for f in FLAVORS],
                 fontsize=7.0)
    axA.text(0.03, 0.98,
             f"all 24 cells: r = {R_AWARE:.2f}, {LOG_AWARE_P['unweighted']}\n"
             f"weighted by cell n: r = {LOG_AWARE_R['weighted']:.2f}, "
             f"{LOG_AWARE_P['weighted']}",
             transform=axA.transAxes, fontsize=FS_ANNOT, va="top")
    axA.legend(handles=[Line2D([], [], marker=PMARK[p], color="none",
                               markerfacecolor="none", markeredgecolor=INK,
                               markeredgewidth=0.8, markersize=5.5, label=p)
                        for p in PERSONS],
               loc="lower right", fontsize=FS_ANNOT, handletextpad=0.3,
               borderpad=0.2, labelspacing=0.3)
    panel(axA, "A", dx=-0.17)

    for k, (ax, p) in enumerate(zip(small, PERSONS)):
        i = PERSONS.index(p)
        ax.axhline(0, color=FAINT, lw=0.9, zorder=0)
        ax.axvline(0, color=FAINT, lw=0.9, zorder=0)
        aa, bb = ols(REL_A[i], REL_T[i])
        ax.plot(np.array(lim), aa + bb * np.array(lim), color=SOFT, lw=1.1,
                ls=(0, (5, 2)), zorder=2)
        for j, f in enumerate(FLAVORS):
            ax.scatter(REL_A[i, j], REL_T[i, j], marker=PMARK[p], s=32,
                       color=FLAVOR_COLOR[f], edgecolor=INK, linewidth=0.55,
                       zorder=3)
        ax.set_xlim(*lim)
        ax.set_ylim(*lim)
        ax.set_xticks([-2, -1, 0, 1, 2])
        ax.set_yticks([-2, -1, 0, 1, 2])
        ax.tick_params(labelsize=8)
        place_labels(ax, fig, REL_A[i], REL_T[i],
                     [FLAVOR_SHORT[f] for f in FLAVORS], fontsize=6.6)
        ax.set_title(f"{p}: r = {R_AWARE_PERSON[p]:.2f}, "
                     f"{LOG_AWARE_PERSON_P[p]}", fontsize=9, pad=4)
        if k in (0, 1):
            ax.set_xticklabels([])
        if k in (0, 2):
            ax.set_ylabel("Tasted unique liking", fontsize=8.5, labelpad=2)
        panel(ax, "BCDE"[k], dx=-0.34 if k in (0, 2) else -0.16, dy=1.14)
    pD, pE = small[2].get_position(), small[3].get_position()
    fig.text((pD.x0 + pE.x1) / 2, 0.035, "Stated unique liking", fontsize=9,
             ha="center")

    save(fig, "fig3_awareness")


# ----------------------------------------------------------------------------
# Supplementary figures
# ----------------------------------------------------------------------------
def figureS1() -> None:
    """Calibration: predicted against achieved accuracy."""
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    fig.subplots_adjust(left=0.175, right=0.70, top=0.96, bottom=0.30)
    ys = np.arange(4)[::-1]
    ax.axvline(CHANCE, color=SOFT, lw=1.0, ls=(0, (4, 2)), zorder=1)
    ax.text(CHANCE + 0.015, 3.45, "chance", fontsize=FS_ANNOT, color=SOFT,
            ha="left", va="center")
    for k, p in enumerate(PERSONS):
        yv = ys[k]
        pr, ac = PRED[p] / 100.0, ACC[p]
        lo, hi = binom_ci(HITS[p], 25)
        ax.annotate("", xy=(ac, yv), xytext=(pr, yv),
                    arrowprops=dict(arrowstyle="-|>", color=SOFT, lw=1.1,
                                    shrinkA=3.5, shrinkB=3.5))
        ax.plot([lo, hi], [yv, yv], color=INK, lw=1.1, zorder=2)
        ax.scatter([pr], [yv], marker="o", s=44, facecolor="white",
                   edgecolor=INK, linewidth=1.1, zorder=3)
        ax.scatter([ac], [yv], marker="o", s=46, color=INK, zorder=4)
        ax.text(1.03, yv, f"{(ac - pr) * 100:+.0f} pp, {LOG_CALIB_P[p]}",
                fontsize=FS_ANNOT, va="center", ha="left", clip_on=False)
    ax.set_yticks(ys)
    ax.set_yticklabels(PERSONS)
    ax.set_ylim(-0.6, 3.6)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("Proportion correct")
    ax.legend(handles=[
        Line2D([], [], marker="o", color="none", markerfacecolor="white",
               markeredgecolor=INK, markersize=6, label="predicted before"),
        Line2D([], [], marker="o", color="none", markerfacecolor=INK,
               markeredgecolor=INK, markersize=6,
               label="achieved (exact 95% CI)")],
        loc="upper center", bbox_to_anchor=(0.5, -0.20), ncol=2,
        fontsize=FS_ANNOT, handletextpad=0.3, borderpad=0.2,
        columnspacing=1.8)
    save(fig, "figS1_calibration")


def figureS2() -> None:
    """Learning: accuracy by flavor-specific exposure, and by trial half."""
    fig, axes = plt.subplots(1, 2, figsize=(W_FULL * 0.9, 3.3))
    fig.subplots_adjust(left=0.10, right=0.985, top=0.90, bottom=0.20,
                        wspace=0.30)
    ax = axes[0]
    ex = trials["prior_own_exposures_flavor"].to_numpy()
    masks = [(ex == 0), (ex == 1), (ex == 2), (ex >= 3)]
    ax.axhline(CHANCE, color=SOFT, lw=1.0, ls=(0, (4, 2)))
    for i, m in enumerate(masks):
        k, n = int(trials.loc[m, "correct"].sum()), int(m.sum())
        lo, hi = binom_ci(k, n)
        ax.plot([i, i], [lo, hi], color=INK, lw=1.1)
        ax.scatter([i], [k / n], s=46, color=INK, zorder=3)
        ax.text(i, hi + 0.025, f"{k}/{n}", ha="center", va="bottom",
                fontsize=FS_ANNOT, color=MID)
    ax.set_xticks(range(4))
    ax.set_xticklabels(["0", "1", "2", "3+"])
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(0, 0.9)
    ax.set_xlabel("Earlier tastings of the same flavor,\nwith feedback")
    ax.set_ylabel("Proportion correct")
    ax.text(0.03, 0.97, LOG_LEARNING, transform=ax.transAxes,
            fontsize=FS_ANNOT, va="top")
    panel(ax, "A", dx=-0.20)

    ax = axes[1]
    ax.axhline(CHANCE, color=SOFT, lw=1.0, ls=(0, (4, 2)))
    for i, p in enumerate(PERSONS):
        sub = trials[trials.person == p]
        first = sub[sub.trial_within_person <= 12]["correct"]
        second = sub[sub.trial_within_person > 12]["correct"]
        v = [first.mean(), second.mean()]
        ax.plot([i - 0.16, i + 0.16], v, color=SOFT, lw=1.0, zorder=1)
        ax.scatter([i - 0.16], [v[0]], s=44, facecolor="white",
                   edgecolor=INK, linewidth=1.1, zorder=3)
        ax.scatter([i + 0.16], [v[1]], s=46, color=INK, zorder=3)
        ax.text(i - 0.16, v[0] + 0.035, f"{int(first.sum())}/12", ha="center",
                va="bottom", fontsize=FS_ANNOT, color=MID)
        ax.text(i + 0.16, v[1] + 0.035, f"{int(second.sum())}/13", ha="center",
                va="bottom", fontsize=FS_ANNOT, color=MID)
    ax.set_xticks(range(4))
    ax.set_xticklabels(PERSONS)
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(0, 0.9)
    ax.set_ylabel("Proportion correct")
    ax.legend(handles=[
        Line2D([], [], marker="o", color="none", markerfacecolor="white",
               markeredgecolor=INK, markersize=6, label="trials 1 to 12"),
        Line2D([], [], marker="o", color="none", markerfacecolor=INK,
               markeredgecolor=INK, markersize=6, label="trials 13 to 25")],
        loc="upper right", fontsize=FS_ANNOT, handletextpad=0.3,
        borderpad=0.2)
    panel(ax, "B", dx=-0.20)
    save(fig, "figS2_learning")


def roc_points(scores: np.ndarray, labels: np.ndarray):
    order = np.argsort(-scores, kind="mergesort")
    s, y = scores[order], labels[order].astype(float)
    tps, fps = np.cumsum(y), np.cumsum(1 - y)
    idx = np.r_[np.where(np.diff(s))[0], len(s) - 1]
    return (np.r_[0, fps[idx] / max((1 - y).sum(), 1)],
            np.r_[0, tps[idx] / max(y.sum(), 1)])


def figureS3() -> None:
    """One-vs-rest ROC curves for the two discriminating perceivers."""
    fig, axes = plt.subplots(1, 2, figsize=(W_FULL * 0.82, 3.6))
    fig.subplots_adjust(left=0.105, right=0.99, top=0.91, bottom=0.245,
                        wspace=0.24)
    casing = [pe.Stroke(linewidth=2.6, foreground="#6E6E6E"), pe.Normal()]
    for ax, p in zip(axes, ["Ella", "Mitja"]):
        i = PERSONS.index(p)
        S, true_v = SCORES[p]
        ax.plot([0, 1], [0, 1], color=FAINT, lw=1.0)
        for j, f in enumerate(FLAVORS):
            fpr, tpr = roc_points(S[:, j], (true_v == f).astype(int))
            ax.step(fpr, tpr, where="post", color=FLAVOR_COLOR[f], lw=1.5,
                    solid_capstyle="butt", path_effects=casing, zorder=2)
            ax.scatter(fpr[1:], tpr[1:], s=9, color=FLAVOR_COLOR[f],
                       edgecolor=INK, linewidth=0.4, zorder=3)
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_aspect("equal")
        ax.set_xlabel("False-positive rate")
        ax.set_title(f"{p}, macro-AUC {MACRO_AUC[p]:.2f}", fontsize=9.5, pad=5)
        ax.text(0.98, 0.05, "\n".join(
            f"{FLAVOR_SHORT[f]} {M_auc[i, j]:.2f} (n = {int(M_n[i, j])})"
            for j, f in enumerate(FLAVORS)), fontsize=7.4, ha="right",
            va="bottom", transform=ax.transAxes, linespacing=1.35)
    axes[0].set_ylabel("True-positive rate")
    flavor_key(fig, y=0.028, fontsize=8.0)
    save(fig, "figS3_roc-curves")


# ----------------------------------------------------------------------------
# Tables 1 and 2 (main text): descriptives, split per the decision of
# 2026-08-26 (plan/open-points_2026-08-26.md): Table 1 pools the four
# perceivers (portrait, normal font); Table 2 gives the perceiver-specific
# blocks in two stacked panels (Ada and Ella upper, Natalie and Mitja lower).
# Column order in both: predicted accuracy, accuracy, certainty, stated
# liking, tasted liking.
# ----------------------------------------------------------------------------
def msd(v: np.ndarray) -> str:
    v = np.asarray(v, float)
    if len(v) == 0:
        return ""
    if len(v) == 1:
        return f"{v[0]:.2f}"           # single trial: no SD, see table note
    return f"{v.mean():.2f} ({v.std(ddof=1):.2f})"


def acc_cell(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}% ({k}/{n})"


MEASURES = ["predicted accuracy", "accuracy", "certainty", "stated liking",
            "tasted liking"]

TABLE1_CAPTION = ("Predicted accuracy, identification accuracy, certainty, "
                  "and liking for each flavor, aggregated across perceivers")
TABLE2_CAPTION = ("Predicted accuracy, identification accuracy, certainty, "
                  "and liking for each flavor, separately for each perceiver")


def pooled_row(f: str, j: int) -> list[str]:
    k, n = FLAVOR_ACC[f]
    return [FLAVOR_NAME[f], f"{M_pred[:, j].mean():.0f}%", acc_cell(k, n),
            f"{CERT_FLAVOR[j]:.2f}", f"{ALIKE_FLAVOR[j]:.2f}",
            f"{TLIKE_FLAVOR[j]:.2f}"]


def pooled_all_row() -> list[str]:
    return ["*All flavors*", f"{np.mean([PRED[p] for p in PERSONS]):.0f}%",
            acc_cell(int(trials.correct.sum()), 100),
            msd(trials["certainty"].to_numpy()), f"{M_alike.mean():.2f}",
            msd(trials["liking"].to_numpy())]


def person_cells(p: str, i: int, f: str, j: int) -> list[str]:
    cell = trials[(trials.person == p) & (trials.true_flavor == f)]
    return [f"{M_pred[i, j]:.0f}%",
            acc_cell(int(M_hits[i, j]), int(M_n[i, j])),
            f"{cell['certainty'].mean():.2f}", f"{M_alike[i, j]:.2f}",
            f"{cell['liking'].mean():.2f}"]


def person_all_cells(p: str, i: int) -> list[str]:
    sub = trials[trials.person == p]
    return [f"{PRED[p]:.0f}%", acc_cell(HITS[p], 25),
            msd(sub["certainty"].to_numpy()), f"{M_alike[i].mean():.2f}",
            msd(sub["liking"].to_numpy())]


def table1() -> None:
    head = ["Flavor"] + MEASURES
    rows = [pooled_row(f, j) for j, f in enumerate(FLAVORS)]
    rows.append(pooled_all_row())
    lines = ["# Table 1", "",
             f"**{TABLE1_CAPTION}.**", "",
             "Portrait orientation, normal font size. All values pool the "
             "four perceivers; the perceiver-specific blocks are in Table 2.",
             "",
             "| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] * len(head)) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines += ["", TABLE1_NOTE + TABLE_PROV, ""]
    (FIG / "table1_descriptives.md").write_text("\n".join(lines),
                                                encoding="utf-8")
    print("  wrote figures/table1_descriptives.md")


def table2() -> None:
    lines = ["# Table 2", "",
             f"**{TABLE2_CAPTION}.**", "",
             "Landscape orientation, one table of two stacked panels (a "
             "shared table in the typeset version): upper panel Ada and "
             "Ella, lower panel Natalie and Mitja, eleven columns per panel. "
             "Each perceiver keeps an all-flavors row carrying their "
             "marginals.", ""]
    for panel in ([("Ada", 0), ("Ella", 1)], [("Natalie", 2), ("Mitja", 3)]):
        head = ["Flavor"]
        for pname, _ in panel:
            head += [f"**{pname}**", "", "", "", ""]
        lines += ["| " + " | ".join(head) + " |",
                  "|" + "|".join(["---"] * len(head)) + "|",
                  "| " + " | ".join([""] + [m for _ in panel
                                            for m in MEASURES]) + " |"]
        for j, f in enumerate(FLAVORS):
            r = [FLAVOR_NAME[f]]
            for pname, i in panel:
                r += person_cells(pname, i, f, j)
            lines += ["| " + " | ".join(r) + " |"]
        r = ["*All flavors*"]
        for pname, i in panel:
            r += person_all_cells(pname, i)
        lines += ["| " + " | ".join(r) + " |", ""]
    lines += [TABLE2_NOTE + TABLE_PROV, ""]
    (FIG / "table2_descriptives.md").write_text("\n".join(lines),
                                                encoding="utf-8")
    print("  wrote figures/table2_descriptives.md")


TABLE1_NOTE = (
    "*Note.* Four perceivers tasted 25 blind gummy bears each (100 trials, "
    "no missing data). Rows are the six flavors and, in the final row, all "
    "flavors together; every value pools the four perceivers, and Table 2 "
    "gives the same measures separately for each perceiver. Predicted "
    "accuracy is the self-predicted percentage correct for that flavor, "
    "given before any tasting, averaged over perceivers and, in the final "
    "row, also over flavors. Accuracy is the percentage of trials on which "
    "the flavor was named correctly, with hits out of trials tasted in "
    "brackets; chance is 16.7%, because all four perceivers knew the "
    "package contained exactly these six flavors. Certainty and tasted "
    "liking are the per-trial judgments, 1 to 6, averaged within flavor; "
    "the final row adds the standard deviation. Stated liking is the "
    "rating given before any tasting, 1 to 6, one rating per perceiver and "
    "flavor, averaged over perceivers. Exact intervals and binomial tests "
    "for the accuracies are in Table S1; the full certainty and liking "
    "distributions are in Table S4."
)

TABLE2_NOTE = (
    "*Note.* Same design and measures as Table 1: four perceivers, 25 blind "
    "gummy bears each, chance 16.7%. The upper panel gives Ada and Ella, "
    "the lower panel Natalie and Mitja; each perceiver's final row carries "
    "their marginals across all flavors. Accuracy cells give hits out of "
    "trials tasted in brackets, which also shows every cell size. Certainty "
    "and tasted liking are averaged within the perceiver x flavor cell; the "
    "final row adds the standard deviation. One cell rests on a single "
    "trial (Ada, lemon) and carries that trial's value. Exact intervals and "
    "binomial tests for the accuracies are in Table S1; the full certainty "
    "and liking distributions are in Table S4."
)

TABLE_PROV = (
    " Sources: results log sections 1.1, 1.2, 1.5, 4.1, 4.2, 7.1. The "
    "cell-level and flavor-level tasted-liking and certainty means, the "
    "standard deviations, the averaged predictions, and the perceiver-level "
    "and grand stated-liking means are computed from the locked data files "
    "(`gummibear_trials.csv` for the per-trial judgments, "
    "`gummibear_apriori_long.csv` and `gummibear_apriori.csv` for the "
    "measures collected before tasting), and every aggregate they roll up "
    "into reproduces the log."
)


# ----------------------------------------------------------------------------
# Supplement: tables extracted verbatim from the results log
# ----------------------------------------------------------------------------
def log_sections() -> dict[str, tuple[str, str]]:
    """Section key -> (heading text, body), from the session B results log."""
    text = (OUT / f"results_{RESULTS_DATE}.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    idx = []
    for i, ln in enumerate(lines):
        m = re.match(r"^#{2,3} (\d+(?:\.\d+)?)\.?\s*(.*)$", ln)
        if m:
            head = re.sub(r"\s*\[fn [^\]]*\]", "", m.group(2)).strip()
            idx.append((i, m.group(1), head))
    out = {}
    for k, (i, key, head) in enumerate(idx):
        j = idx[k + 1][0] if k + 1 < len(idx) else len(lines)
        out[key] = (head, "\n".join(lines[i + 1:j]).strip("\n"))
    return out


SUPP_TABLES = [
    ("S1", "Identification accuracy against chance, and the test of "
     "perceiver differences",
     ["1.1", "1.2", "1.7", "12.1"],
     "Main text, part a), first paragraph and Tables 1 and 2."),
    ("S2", "Confusion matrices, pooled and per perceiver", ["1.3"],
     "Main text, part a), the paragraph on error structure, and Figure 1B."),
    ("S3", "Response biases, response distributions, and sequential response "
     "indicators", ["1.4"],
     "Main text, preliminaries, the sentence on response distributions."),
    ("S4", "Certainty and liking distributions", ["1.5"],
     "Main text, Tables 1 and 2."),
    ("S5", "Session, package, and country breakdowns", ["1.6"],
     "Main text, Method (design) and preliminaries."),
    ("S6", "Learning checks under trial-by-trial feedback, and stability of "
     "the perceiver differences", ["2", "2.1", "2.2", "2.3", "2.4"],
     "Main text, preliminaries, the no-learning paragraph, and Figure S2."),
    ("S7", "Does certainty discriminate correct from incorrect trials?",
     ["3.0"], "Main text, part a), the precondition sentence for Figure 1C."),
    ("S8", "One-versus-rest AUCs for all 24 cells, sensitivity and "
     "specificity", ["3.1", "3.2"],
     "Main text, part a), and Figures 1C and S3."),
    ("S9", "Awareness of unique preferences: all variants, margin-level "
     "correspondence, the 24 relationship effects, and per-perceiver "
     "correlations", ["4", "4.1", "4.2", "4.3"],
     "Main text, part b), the sentences on concrete unique preferences and "
     "on the consensus mismatch, and part c) with Figure 3."),
    ("S10", "Variance decomposition of liking and accuracy", ["5", "5.1",
     "5.2", "5.3"],
     "Main text, parts a) and b), and Figure 2."),
    ("S11", "Liking and accuracy at perceiver, flavor, and relationship "
     "level", ["6", "6.1", "6.2", "6.3", "6.4"],
     "Main text, part b), the pointer sentence."),
    ("S12", "Calibration of predicted accuracy", ["7", "7.1", "7.2",
     "7.3"],
     "Main text, part a), the closing sentence, and Figure S1."),
    ("S13", "Control analyses in R and their agreement with the Python "
     "pipeline", ["8", "9"],
     "Main text, Method (transparency statement) and wherever a variance "
     "component or an AUC is reported."),
]

SUPP_FIGURES = [
    ("S1", "figS1_calibration",
     "Predicted identification accuracy against achieved "
     "accuracy. Open circles give what each perceiver predicted before any "
     "tasting, averaged over the six flavors; filled circles give the "
     "proportion achieved over 25 trials with Clopper-Pearson exact 95% "
     "intervals; arrows connect the two; p values are exact binomial tests of "
     "the achieved count against that perceiver's own prediction. The two "
     "miscalibrations that reach significance run in opposite directions."),
    ("S2", "figS2_learning",
     "No accuracy gain across the experiment despite feedback on every trial. "
     "(A) Accuracy by the number of times the perceiver had already tasted "
     "that same flavor with feedback, pooled over perceivers, with exact 95% "
     "intervals and the primary stratified permutation test. (B) Accuracy in "
     "the first 12 and the last 13 trials, per perceiver. Feedback was given "
     "after every one of the 100 trials, so this is the check that decides "
     "whether trial order has to enter the later analyses. It does not."),
    ("S3", "figS3_roc-curves",
     "One-versus-rest ROC curves over the certainty-graded score for the two "
     "perceivers whose certainty ratings discriminated correct from incorrect "
     "trials. Each curve rests on 2 to 8 positive trials, so the curves are "
     "short step functions and their shape should not be interpreted; the "
     "areas under them, with the number of positive trials, are given in the "
     "panels. Curves for the other two perceivers are not shown because their "
     "certainty ratings carried no signal (Table S7). Flavor colors are those "
     "of the product, which the perceivers could not see."),
]


TABLE_S14_TITLE = ("Table S14. Where blind identification of gummy bear "
                   "flavors sits between two literatures")

TABLE_S14_BODY = """\
| Benchmark | Source | Raw value | k | Chance | π |
|---|---|---|---|---|---|
| *Single-encounter person perception* | | | | | |
| Extraversion from faces, two-alternative forced choice | Jaeger et al. (2024, Study 2) | .511 correct | 2 | .50 | .51 |
| Deception detection | Bond & DePaulo (2006) | .540 correct | 2 | .50 | .54 |
| Sexual orientation from faces, self-paced viewing | Rule & Ambady (2008) | A′ = .70 | 2 | .50 | .70 |
| *This study* | | | | | |
| Gummy bear flavors, blind, pooled over four perceivers | | .42 correct (perceivers .12 to .68) | 6 | .167 | .78 (.41 to .91) |
| *Emotion and mind reading from standardised cues* | | | | | |
| Reading the Mind in the Eyes | Baron-Cohen et al. (2001) | .728 correct | 4 | .25 | .89 |
| Geneva Emotion Recognition Test | Schlegel et al. (2014) | .67 correct | 14 | .071 | .96 |
| *Cued flavor and odour identification* | | | | | |
| Cued odorant naming, six named alternatives | de Wijk & Cain (1994) | .76 correct | 6 | .167 | .94 |
| Sniffin' Sticks identification, ages 21 to 40 | Oleszkiewicz et al. (2019) | .85 correct | 4 | .25 | .94 |
| Candy Smell Test, retronasal, normosmia cut-off | Renner et al. (2009) | ≥ .70 correct (16 of 23) | 4 | .25 | ≥ .88 |
| Candy Smell Test, retronasal, anosmia cut-off | Renner et al. (2009) | .57 correct (13 of 23) | 4 | .25 | .80 |"""

TABLE_S14_NOTE = (
    "*Note.* π rescales a hit rate P obtained with k equally likely "
    "alternatives onto a scale whose null value is .50 for any k, "
    "π = P(k − 1) / [1 + P(k − 2)] (Rosenthal & Rubin, 1989); the index is "
    "the standard route for comparing forced-choice accuracies in person "
    "perception (Tskhay & Rule, 2013). A′ (Rule & Ambady, 2008) is a "
    "nonparametric discrimination index that already has .50 as its null and "
    "enters the table unconverted. The discrimination-metric conversion "
    "quoted in the main text is d = √2 z(AUC) under equal-variance normal "
    "latent distributions (Rice & Harris, 2005) and r = d / √(d² + 4) for "
    "equal group sizes (Borenstein et al., 2009): the grand macro-AUC of .68 "
    "gives d = 0.66 and r = .31. Because AUC is invariant to monotone "
    "rescaling of the underlying score while d is not, the implied d is an "
    "equal-variance equivalent rather than an effect on the observed "
    "certainty scale (Ruscio, 2008); per-perceiver AUCs are not converted at "
    "all, because the sampling distribution of AUC grows increasingly skewed "
    "away from .50 (Ruscio & Mullen, 2012). Rows are grouped by literature. "
    "Every value in this table except the row for this study comes from the "
    "published sources cited; this is the one deliberate exception to the "
    "rule that every number in the paper derives from the analysis log or "
    "the figure script, and this note records it. Benchmark caveats: the de "
    "Wijk and Cain figure is uncorrected for chance and averaged over age "
    "groups, and their stimuli were odorant simulants rather than foods; the "
    "two Candy Smell Test rows are that instrument's diagnostic cut-offs "
    "rather than sample means, and it is the one benchmark built on the same "
    "physical premise as ours, aromatised sweets identified retronasally. "
    "Both of its cut-offs are listed because the pair shows what π does and "
    "does not correct for: π equalises chance levels across k and leaves task "
    "difficulty untouched, so a value of .80 marks the boundary of clinical "
    "anosmia on a four-alternative retronasal test while a value of .78 is "
    "our pooled six-alternative performance. The comparison across the three "
    "blocks of this table is therefore about where a task sits between "
    "literatures, never about equal difficulty."
)


S13_NOTE = (
    "*Note.* One further control attempted a social-relations estimation "
    "with a round-robin estimator and failed structurally: the design is "
    "one-directional (perceivers judged flavors; flavors judged nothing), "
    "so round-robin estimators do not apply and no estimates were produced. "
    "The decomposition therefore runs as the crossed two-way decomposition "
    "and the crossed random-effects models reported above. The extraction "
    "for this table omits the log lines documenting that attempt."
)


def strip_round_robin_software(body: str) -> str:
    """Remove the round-robin software attempt from extracted log text
    (session E call, 2026-08-18): the package is not part of the reported
    pipeline and produced no estimates, so the supplement carries a one-line
    note (S13_NOTE) instead of the rows. Everything else stays verbatim."""
    body = body.replace(
        "; (d) a TripleR attempt, expected to fail structurally because "
        "TripleR requires round-robin data (perceivers judging each other) "
        "whereas this is a two-mode perceiver x flavor design; the failure "
        "is documented rather than worked around.", ".")
    body = body.replace(
        " (this is a two-mode perceiver x flavor design, not a round robin; "
        "see section 9 on TripleR)",
        " (this is a two-mode perceiver x flavor design, not a round robin)")
    body = body.replace(
        "installed TripleR's compiled dependencies (ggplot2, plyr, reshape2) "
        "as conda binaries because source compilation fails in this sandbox; "
        "TripleR itself is pure R.",
        "installed compiled package dependencies (ggplot2, plyr, reshape2) "
        "as conda binaries because source compilation fails in this "
        "sandbox.")
    return "\n".join(ln for ln in body.splitlines() if "TripleR" not in ln)


def supplement() -> None:
    sec = log_sections()
    L = ["# Supplementary material", "",
         "Gummy bear paper, supplement to the main text. Every table below "
         "except Table S14 is extracted verbatim from "
         "`analysis/results_2026-08-17.md`, the session B results log, by "
         "`analysis/make_figures.py`, so no number here can drift from the "
         "analysis that produced it. Two departures are deliberate: Table "
         "S13 omits the lines documenting a software attempt that produced "
         "no estimates (see its note), and Table S14 is compiled by hand "
         "from published sources (see its note). Each extracted table names "
         "the log sections it came from, and each extracted block keeps its "
         "log heading. Each entry also names where the main text points to "
         "it.", "",
         "Conventions throughout: permutation and exact tests are primary; "
         "permutation p = (1 + #{|T_perm| >= |T_obs|}) / (N + 1) with N = "
         "10,000 draws, two-sided unless marked one-sided; exact enumeration "
         "where the permutation space is small (24 orderings for four "
         "perceivers, so the smallest attainable p is .042; 720 orderings for "
         "six flavors, smallest p = .0014); Clopper-Pearson exact 95% "
         "intervals for proportions; exploratory study, exact p values, no "
         "multiplicity correction. Mixed models are control analyses (Table "
         "S13), not primary inference: with four perceivers and six flavors "
         "every one of them is singular by construction.", "",
         "The extracted material keeps its own internal cross-references to "
         "log sections. They resolve as follows: "
         + "; ".join(f"{', '.join(k for k in keys)} = Table {sid}"
                     for sid, _, keys, _ in SUPP_TABLES)
         + ". Two log sections are not reproduced here and are reported in the "
           "Method instead: section 0, the data verification against the "
           "session A pins, and section 10, the software versions and the "
           "seed.", "",
         "## Supplementary figures", ""]
    for sid, fname, cap in SUPP_FIGURES:
        L += [f"**Figure {sid}.** {cap}", "",
              f"`figures/{fname}.pdf`, `figures/{fname}.png`", ""]
    L += ["## Supplementary tables", ""]
    for sid, title, keys, where in SUPP_TABLES:
        L += [f"### Table {sid}. {title}", "",
              f"*Referenced in:* {where} *Results log:* "
              f"{', '.join(keys)}.", ""]
        for k in keys:
            head, body = sec.get(k, ("", ""))
            body = strip_round_robin_software(body)
            if body.strip():
                L += [f"**Log section {k}. {head}**", "", body.strip(), ""]
        if sid == "S13":
            L += [S13_NOTE, ""]
    L += [f"### {TABLE_S14_TITLE}", "",
          "*Referenced in:* Main text, part a), the between-domains "
          "sentences, and the Discussion. *Results log:* none; compiled by "
          "hand from published sources (session E, 2026-08-18).", "",
          TABLE_S14_BODY, "", TABLE_S14_NOTE, "",
          "References cited only in this table:", "",
          "Baron-Cohen, S., Wheelwright, S., Hill, J., Raste, Y., & Plumb, "
          "I. (2001). The \"Reading the Mind in the Eyes\" test revised "
          "version: A study with normal adults, and adults with Asperger "
          "syndrome or high-functioning autism. *Journal of Child Psychology "
          "and Psychiatry, 42*(2), 241-251. "
          "https://doi.org/10.1111/1469-7610.00715", "",
          "Borenstein, M., Hedges, L. V., Higgins, J. P. T., & Rothstein, "
          "H. R. (2009). Converting among effect sizes. In *Introduction to "
          "meta-analysis* (pp. 45-49). Wiley. "
          "https://doi.org/10.1002/9780470743386.ch7", "",
          "Renner, B., Mueller, C. A., Dreier, J., Faulhaber, S., Rascher, "
          "W., & Kobal, G. (2009). The candy smell test: A new test for "
          "retronasal olfactory performance. *The Laryngoscope, 119*(3), "
          "487-495. https://doi.org/10.1002/lary.20123", "",
          "Rule, N. O., & Ambady, N. (2008). Brief exposures: Male sexual "
          "orientation is accurately perceived at 50 ms. *Journal of "
          "Experimental Social Psychology, 44*(4), 1100-1105. "
          "https://doi.org/10.1016/j.jesp.2007.12.001", "",
          "Ruscio, J. (2008). A probability-based measure of effect size: "
          "Robustness to base rates and other factors. *Psychological "
          "Methods, 13*(1), 19-30. "
          "https://doi.org/10.1037/1082-989X.13.1.19", "",
          "Ruscio, J., & Mullen, T. (2012). Confidence intervals for the "
          "probability of superiority effect size measure and the area under "
          "a receiver operating characteristic curve. *Multivariate "
          "Behavioral Research, 47*(2), 201-223. "
          "https://doi.org/10.1080/00273171.2012.658329", "",
          "Schlegel, K., Grandjean, D., & Scherer, K. R. (2014). Introducing "
          "the Geneva Emotion Recognition Test: An example of Rasch-based "
          "test development. *Psychological Assessment, 26*(2), 666-672. "
          "https://doi.org/10.1037/a0035246", ""]
    (FIG / "supplement.md").write_text("\n".join(L).rstrip() + "\n",
                                       encoding="utf-8")
    print("  wrote figures/supplement.md")


# ----------------------------------------------------------------------------
def main() -> None:
    print(f"Verifying against analysis/results_{RESULTS_DATE}.md ...")
    verify()
    print("Drawing figures ...")
    figure1()
    figure2()
    figure3()
    figureS1()
    figureS2()
    figureS3()
    print("Writing tables ...")
    table1()
    table2()
    supplement()
    print("\nDone. 6 figures (pdf + png at 600 dpi), Tables 1 and 2, and "
          "the assembled supplement in figures/.")


if __name__ == "__main__":
    main()
