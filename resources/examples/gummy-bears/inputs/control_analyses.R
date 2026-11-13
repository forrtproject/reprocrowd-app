# control_analyses.R -- Session B control analyses, gummi bear paper.
#
# Replicates, as controls to the primary permutation/exact pipeline in
# analysis/gummibear_analyses.py: (a) crossed mixed models (lmer for liking,
# glmer logistic for accuracy) with the 4-perceiver identifiability problem
# reported, not hidden; (b) fixed-slope mirrors of the learning, awareness,
# certainty-signal, and calibration tests; (c) pROC AUCs for all 24 person x
# flavor cells; (d) a TripleR attempt, expected to fail structurally (TripleR
# fits round-robin designs; this is a two-mode perceiver x flavor design).
#
# Run from the project root after analysis/setup-r-sandbox.sh:
#   MAMBA_ROOT_PREFIX=/tmp/mamba /tmp/bin/micromamba run -n renv Rscript analysis/control_analyses.R
#
# Writes: analysis/r_controls_summary.csv   (analysis, quantity, value)
#         analysis/r_auc_by_cell.csv        (person, flavor, auc_r)
#         analysis/r_sessioninfo.txt
# Deterministic: no stochastic step.

suppressMessages({
  library(lme4)
  library(pROC)
})

trials <- read.csv("data/gummibear_trials.csv", stringsAsFactors = FALSE)
along  <- read.csv("data/gummibear_apriori_long.csv", stringsAsFactors = FALSE)

FLAVORS <- c("E", "O", "Ap", "H", "Z", "An")
PERSONS <- c("Ada", "Ella", "Natalie", "Mitja")
trials$person      <- factor(trials$person, levels = PERSONS)
trials$true_flavor <- factor(trials$true_flavor, levels = FLAVORS)

rows <- list()
add <- function(analysis, quantity, value) {
  rows[[length(rows) + 1]] <<- data.frame(
    analysis = analysis, quantity = quantity,
    value = as.character(value), stringsAsFactors = FALSE)
}
num <- function(x, d = 4) formatC(x, digits = d, format = "f")

vc_rows <- function(m, label) {
  vc <- as.data.frame(VarCorr(m))
  for (i in seq_len(nrow(vc))) {
    add(label, paste0("var(", vc$grp[i], ")"), num(vc$vcov[i]))
  }
  add(label, "singular_fit", isSingular(m))
}
fe_rows <- function(m, label, term) {
  co <- summary(m)$coefficients
  est <- co[term, 1]; se <- co[term, 2]; stat <- co[term, 3]
  add(label, paste0("beta(", term, ")"), num(est))
  add(label, "SE", num(se))
  add(label, "z_or_t", num(stat))
  p <- if (ncol(co) >= 4) co[term, 4] else 2 * pnorm(-abs(stat))
  add(label, "p_Wald", num(p))
}

# ---------------------------------------------------------------- null models
m_lik <- lmer(liking ~ 1 + (1 | person) + (1 | true_flavor) +
                (1 | person:true_flavor), data = trials, REML = TRUE)
vc_rows(m_lik, "lmer liking null (REML)")

m_acc <- glmer(correct ~ 1 + (1 | person) + (1 | true_flavor) +
                 (1 | person:true_flavor), data = trials, family = binomial)
vc_rows(m_acc, "glmer accuracy null (logit scale)")

# ---------------------------------------------------------------- learning
fit_glmer <- function(formula_full, formula_reduced, label, term) {
  m <- tryCatch(
    glmer(formula_full, data = trials, family = binomial,
          control = glmerControl(optimizer = "bobyqa")),
    warning = function(w) w, error = function(e) e)
  if (inherits(m, "merMod")) {
    add(label, "RE_structure", "person + flavor + person:flavor")
  } else {
    m <- glmer(formula_reduced, data = trials, family = binomial,
               control = glmerControl(optimizer = "bobyqa"))
    add(label, "RE_structure", "person + flavor (interaction dropped: full model did not converge cleanly)")
  }
  add(label, "singular_fit", isSingular(m))
  fe_rows(m, label, term)
  m
}

invisible(fit_glmer(
  correct ~ prior_own_exposures_flavor + (1 | person) + (1 | true_flavor) +
    (1 | person:true_flavor),
  correct ~ prior_own_exposures_flavor + (1 | person) + (1 | true_flavor),
  "glmer learning: flavor-specific exposure", "prior_own_exposures_flavor"))

trials$time_z <- as.numeric(scale(trials$feedback_prior_own))
invisible(fit_glmer(
  correct ~ time_z + (1 | person) + (1 | true_flavor) +
    (1 | person:true_flavor),
  correct ~ time_z + (1 | person) + (1 | true_flavor),
  "glmer learning: global trial position (z)", "time_z"))

# ------------------------------------------------------- certainty signal
invisible(fit_glmer(
  correct ~ certainty_c + (1 | person) + (1 | true_flavor) +
    (1 | person:true_flavor),
  correct ~ certainty_c + (1 | person) + (1 | true_flavor),
  "glmer certainty-signal (person-centered certainty)", "certainty_c"))

# ------------------------------------------------------------- awareness
# Relationship effect of the a priori liking (double-centered 4 x 6 matrix),
# merged to trials by person x true flavor; slope = relationship-level
# awareness control mirroring section 4 of the Python log.
A <- matrix(NA_real_, 4, 6, dimnames = list(PERSONS, FLAVORS))
for (i in seq_len(nrow(along))) {
  A[along$person[i], along$flavor[i]] <- along$apriori_like[i]
}
Arel <- sweep(sweep(A, 1, rowMeans(A)), 2, colMeans(A)) + mean(A)
trials$apriori_like_rel <- Arel[cbind(as.character(trials$person),
                                      as.character(trials$true_flavor))]
m_aw <- lmer(liking ~ apriori_like_rel + (1 | person) + (1 | true_flavor) +
               (1 | person:true_flavor), data = trials, REML = TRUE)
add("lmer awareness (a priori rel -> tasted liking)", "singular_fit",
    isSingular(m_aw))
fe_rows(m_aw, "lmer awareness (a priori rel -> tasted liking)",
        "apriori_like_rel")

# ------------------------------------------------------------ calibration
trials$pred_prop <- trials$apriori_pred_true_flavor / 100
invisible(fit_glmer(
  correct ~ pred_prop + (1 | person) + (1 | true_flavor) +
    (1 | person:true_flavor),
  correct ~ pred_prop + (1 | person) + (1 | true_flavor),
  "glmer calibration: raw a priori prediction", "pred_prop"))

P <- matrix(NA_real_, 4, 6, dimnames = list(PERSONS, FLAVORS))
for (i in seq_len(nrow(along))) {
  P[along$person[i], along$flavor[i]] <- along$apriori_pred_pct[i] / 100
}
Prel <- sweep(sweep(P, 1, rowMeans(P)), 2, colMeans(P)) + mean(P)
trials$pred_rel <- Prel[cbind(as.character(trials$person),
                              as.character(trials$true_flavor))]
invisible(fit_glmer(
  correct ~ pred_rel + (1 | person) + (1 | true_flavor) +
    (1 | person:true_flavor),
  correct ~ pred_rel + (1 | person) + (1 | true_flavor),
  "glmer calibration: profile (relationship-effect) prediction", "pred_rel"))

# ------------------------------------------------------------------- pROC
auc_out <- data.frame()
for (p in PERSONS) {
  sub <- trials[trials$person == p, ]
  for (f in FLAVORS) {
    score <- ifelse(sub$judged_flavor == f, sub$certainty, -sub$certainty)
    resp <- as.integer(sub$true_flavor == f)
    r <- roc(response = resp, predictor = score, direction = "<",
             quiet = TRUE)
    auc_out <- rbind(auc_out,
                     data.frame(person = p, flavor = f,
                                auc_r = as.numeric(auc(r))))
  }
}
write.csv(auc_out, "analysis/r_auc_by_cell.csv", row.names = FALSE)
add("pROC", "n_cell_aucs_written", nrow(auc_out))

# ---------------------------------------------------------------- TripleR
tripler_msg <- tryCatch({
  library(TripleR)
  long <- data.frame(actor = as.character(trials$person),
                     partner = as.character(trials$true_flavor),
                     liking = trials$liking)
  fit <- RR(liking ~ actor * partner, data = long)
  "UNEXPECTED: TripleR accepted a two-mode design"
}, error = function(e) paste("failed as expected:", conditionMessage(e)),
   warning = function(w) paste("warning:", conditionMessage(w)))
add("TripleR attempt (round-robin package, two-mode data)", "outcome",
    tripler_msg)

# ---------------------------------------------------------------- versions
add("versions", "R", R.version.string)
add("versions", "lme4", as.character(packageVersion("lme4")))
add("versions", "pROC", as.character(packageVersion("pROC")))
add("versions", "TripleR",
    tryCatch(as.character(packageVersion("TripleR")), error = function(e) "n/a"))

summary_df <- do.call(rbind, rows)
write.csv(summary_df, "analysis/r_controls_summary.csv", row.names = FALSE)
writeLines(capture.output(sessionInfo()), "analysis/r_sessioninfo.txt")
cat("wrote analysis/r_controls_summary.csv,",
    "analysis/r_auc_by_cell.csv, analysis/r_sessioninfo.txt\n")
