# Individual differences in the perception of abiotic individuals: The case of gummy bears

Supplement, data, and analysis code for the paper of the same name.

The paper itself is not part of this archive; it is deposited separately and linked from the record.

Förster, N., Back, E., Förster, A. M., & Back, M. D. (2026).

Correspondence: natalie.foerster@uni-wuppertal.de and mitja.back@uni-muenster.de

---

## What this study is, and what it is not

Four members of one household each tasted 25 Haribo Goldbears drawn blind, 100 trials in total, and on every trial named the flavor among six known alternatives, rated their certainty, and rated how much they liked the bear. Liking for each of the six flavors had also been stated before any tasting.

**This study is exploratory and was not preregistered.** Every analysis was specified after the data had been collected and inspected. There were no exclusions and no manipulations; all 100 trials from all four participants are included. The sample is four people who live together, and the number of them was set by the number of people in the household rather than by a power analysis. Inference rests on permutation tests, exact binomial tests and exact intervals rather than on asymptotic approximations, because random-effect variances estimated from four perceivers and six flavors are unstable by construction. The variance decompositions are descriptive, and only the ordering of the components is interpreted.

Two of the four participants designed the study and ran the analyses. All four are authors.

## Contents

```
supplement/
  supplement.pdf               Tables S1 to S14 and Figures S1 to S3, 23 pp
  supplement.docx              the same supplement, editable
data/
  Gummibear_data.xlsx          source workbook, two sheets, never edited
  info_gummibear_data.txt      the source codebook as originally written
  gummibear_trials.csv         100 trials, 35 columns          (built by step 1)
  gummibear_apriori.csv        4 perceivers, wide              (built by step 1)
  gummibear_apriori_long.csv   24 perceiver x flavor cells     (built by step 1)
  codebook.md                  every variable, its coding, provenance, and
                               derivation formula, plus the verification log
analysis/
  build_dataset.py             step 1, with 31 integrity checks
  gummibear_analyses.py        step 2, fixed seed, writes the results log
  make_figures.py              step 3, 48 pre-draw checks, figures and tables
  make_supplement.sh           step 4, typesets the supplement
  control_analyses.R           independent R controls (lme4, pROC)
  setup-r-sandbox.sh           R bootstrap for a machine without R
  results_2026-08-17.md        the numbered results log, the source of every
                               statistic in the paper
  r_controls_summary.csv       R control output
  r_auc_by_cell.csv            24 cell AUCs from pROC
  py_auc_by_cell.csv           the same 24 from Python, for the comparison
  r_sessioninfo.txt            R session info for the archived control run
figures/
  fig1_accuracy-differentiation.{pdf,png}   main text
  fig2_variance-components.{pdf,png}        main text
  fig3_awareness.{pdf,png}                  main text
  figS1_calibration.{pdf,png}               supplement
  figS2_learning.{pdf,png}                  supplement
  figS3_roc-curves.{pdf,png}                supplement
  source/                      the Haribo product images the palette came from
  supplement.md                the supplement as the pipeline writes it; step 4
                               typesets it into supplement/supplement.{pdf,docx}
  supplement.{pdf,docx}        the typeset copies, same files as in supplement/
  table1_descriptives.md       main-text Table 1, as the pipeline writes it
  table2_descriptives.md       main-text Table 2, as the pipeline writes it
  figure-notes.md              stand-alone figure captions, the design
                               rationale for each display, and the closed list
                               of quantities that come from the figure script
```

Everything under `figures/` and `analysis/results_2026-08-17.md` is regenerated in place by the scripts, so re-running the pipeline overwrites those files rather than leaving a second copy somewhere else.

## Reproducing everything in the paper and the supplement

Run the scripts from the root of this archive; they resolve their own paths from the layout above. Each step reads only what the step before it wrote, and the source workbook is never edited.

1. `python3 analysis/build_dataset.py` reads `data/Gummibear_data.xlsx` and writes the three analysis-ready files into `data/`. It runs 31 integrity checks against the source and stops if any of them fails. Both sheets of the workbook carry a scale-legend row above the header row, so header positions are hard-coded; reading either sheet with a default header setting produces unusable column names.
2. `python3 analysis/gummibear_analyses.py` runs every analysis end to end and writes the numbered results log `analysis/results_2026-08-17.md`. Every number in the paper appears there with the analysis that produced it.
3. `python3 analysis/make_figures.py` recomputes 48 quantities and checks them against the results log before drawing anything, then writes the six figures as PDF and 600 dpi PNG, the two main-text tables, and the assembled supplement.
4. `bash analysis/make_supplement.sh` typesets `figures/supplement.md` as `supplement/supplement.pdf` and `supplement/supplement.docx`. Needs pandoc, and xelatex for the PDF. Two settings are not incidental: the PDF font, because the LaTeX default has no pi glyph and Table S14 is built entirely on pi, and the page orientation, because the widest table runs to nine columns, so both outputs are landscape.

`analysis/control_analyses.R` is independent of the chain and can run any time after step 1. It replicates the mixed models and the ROC curves in lme4 and pROC. Step 2 picks its output up if present and notes its absence if not, so the Python chain reproduces with or without R. `analysis/setup-r-sandbox.sh` bootstraps R on a machine that has none; it takes about eight minutes and has to run in the foreground.

The manuscript itself was assembled by hand from these outputs, is not built by a script, and is not included here.

## Seed and reproducibility

`analysis/gummibear_analyses.py` fixes the seed at **20240805** and draws from a single random-number generator in fixed program order, so the results log reproduces byte for byte. 10,000 permutation draws, 10,000 bootstrap draws, and 10,000 noise-floor simulations throughout. Inserting a resampling test before an existing one moves every later p-value in the log, which is why deterministic additions sit at their topic and anything that draws from the generator goes after the last existing draw.

Monte Carlo p-values apply the standard correction, p = (1 + the number of draws at least as extreme as the observed value) / (B + 1), so with 10,000 draws the smallest attainable value is 1/10001 and a p of exactly zero cannot occur. Values at that floor are reported as p < .001. Two tests enumerate the permutation space exhaustively instead (24 orderings for four perceivers, 720 for six flavors) and correctly apply no correction, because the observed ordering is inside the enumeration; their floors are 1/24 = .042 and 1/720 = .0014. The exact conditional test of perceiver differences sums hypergeometric probabilities over 10,314 enumerated tables and is not a resampling test at all.

## Software versions

The versions of the run that produced the files in this archive, confirmed by a clean end-to-end reproduction from the source workbook on 29 August 2026.

- Python 3.10.12, pandas 2.3.3, numpy 2.2.6, scipy 1.15.3, matplotlib for the figures.
- R 4.5.3 (2026-03-11), lme4 2.0-6, pROC 1.19.0.1, Matrix 1.7-5. Full session info in `analysis/r_sessioninfo.txt`.

That reproduction rebuilt the three data files from the workbook, re-ran the analyses, and redrew everything; the results log, all six figures, both main-text tables, and the supplement came out byte-identical to the versions archived here.

## Where the numbers in the paper come from

Every number in the manuscript comes from `analysis/results_2026-08-17.md` or from `analysis/make_figures.py` running on the locked data files, with two documented exceptions. Table S14 is compiled by hand from published sources and says so in its own note, and benchmark values quoted from the literature are attributed to the papers they come from. `figures/figure-notes.md` section 8 enumerates the closed list of quantities that legitimately come from the figure script rather than the log.

## Materials

Commercially available Haribo Goldbears (Goldbären), two 175 g packages of the standard German product as sold in the Italian market, bought in Italy in August 2024. All of them were eaten in the course of the study. No financial or material support was received from Haribo.

## Ethics

No ethics approval was sought; the study began as a holiday activity and was written up afterwards. The two adults consented to participation and to publication. The two minors gave assent, with parental consent from the two adults, and both later read and approved the manuscript. The gummy bears did not consent.

## License

Data and code released for reuse with attribution. Please cite the paper.
