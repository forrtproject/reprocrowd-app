# Replication of G&L, 2003, S1 --------------------------------------------

# load packages
library(esc) # for effect size calculation
library(psych) # for reliability analysis and descriptives
library(ggplot2) # for plotting
library(dplyr) # ADDED: for na_if()
library(effectsize) # ADDED: for effectsizes
library(pwr) # ADDED: for power analysis
library(emmeans) # ADDED: for post hoc tests
library(MBESS) # ADDED: for effect size computation
library(metafor) # ADDED: for effect size comparison

## values from the original study
# central test
# negative exp: less positive than no exp. regardless of NFC:
#  F(1, 154) = 6.40, MSE = 3.21, p = .01, r2 = .04;
esc::esc_f(f = 6.40, totaln = 160, es.type = "f")$es # ADDED (for Table 1)
# ADDED: confidence intervals
MBESS::ci.pvaf(
  F.value = 6.4,
  df.1 = 1,
  df.2 = 154,
  N = 160,
  conf.level = .90
)

# Negative Expectation: less positive than no expectation, regardless of NFC:
#  F(1, 156) = 12.09, MSE = 3.30, p = .001, r2 = .07
esc::esc_f(f = 12.09, totaln = 160, es.type = "f")$es


# ADDED: confidence intervals
MBESS::ci.pvaf(
  F.value = 12.09,
  df.1 = 1,
  df.2 = 156,
  N = 160,
  conf.level = .90
)


# "positive exp.: higher affective reaction with increasing NFC: F(1, 154) = 6.40, MSE = 3.21, p = .01, r2 = .04
esc::esc_f(f = 6.4, totaln = 160, es.type = "f") # $es # ADDED: confidence intervals for F

pwr::pwr.anova.test(3, n = NULL, f = 0.2, power = .95)$n * 3 # 390 Participants per group will provide 95% power for the smaller of the two central effects
# small telescopes approach
ntarget <- 2.5 * 160 # our target sample size is 400 complete cases

## Open data
ds <- read.csv("https://osf.io/2s3tv/download/", sep = ";", dec = ",") # CHANGED: downloading directly from the OSF repository

# write.csv(ds, file = paste("gl2003-replication-raw-", Sys.Date(), ".csv", sep = "")) # commented out (raw data is now static)

# Data processing ---------------------------------------------------------

ds <- ds[ds$MODE == "interview", ] # comment out for code testing, comment in for actual data analysis

## replace "-9" with NAs
# ds <- dplyr::na_if(ds, -9) # CHANGED: removed due to errors
ds <- ds[ds$NC01_01 != -9, ] # ADDED instead of dplyr::na_if()

## exclude piping page
# ds <- ds[ds$QUESTNNR != "random", ]

## compute affect index
ds$ai <- rowMeans(cbind(ds$BK01_01, ds$BK01_02, ds$BK01_03))


## compute NFC scores (sums)
ds$nfc <- rowMeans(
  cbind(
    ds$NC01_01,
    ds$NC01_02,
    ds$NC01_03,
    ds$NC01_09,
    ds$NC01_12,
    ds$NC01_14,
    ds$NC01_15,
    ds$NC01_16,
    ds$NC01_17,
    ds$NC01_19,
    ds$NC01_22,
    ds$NC01_23,
    ds$NC01_28,
    ds$NC01_29,
    ds$NC01_30,
    ds$NC01_31
  ),
  na.rm = FALSE
)


# Experimental condition (affective expectation induction)
ds$expectation <- dplyr::recode(
  ds$RD01,
  "1" = "positive",
  "2" = "negative",
  "3" = "none"
)
table(ds$expectation, useNA = "always") # should be uniformly distributed along the 3 conditions with low number of NAs

# Experimental condition (position of NFC in the questionnaire)
ds$nfc_pos <- ifelse(ds$QUESTNNR == "nfc_01", "before", "after") # says whether NFC scale was completed before or after watching the expectation induction and target films
table(ds$nfc_pos, useNA = "always")

# Application of Exclusion Criteria ---------------------------------------

### participants must have completed all of the following variables:
## NFC, affect index, experimental group (positive - neutral - negative)
ds <- ds[!is.na(ds$nfc), ]
ds <- ds[!is.na(ds$ai), ]
ds <- ds[!is.na(ds$expectation), ]


# Hypothesis Tests --------------------------------------------------------

### Data quality checks

## achieved sample size and power
nrow(ds) # target: 400 # ADDED: new target: 244 for 80% power
nrow(ds) / 160 # ADDED: relative sample size (Noriginal = 160)
pwr::pwr.anova.test(3, n = nrow(ds) / 3, f = NULL, power = .95)$f # ADDED: sensitivity
"Power"
pwr::pwr.anova.test(3, n = nrow(ds) / 3, f = 0.2, power = NULL)$power # power for achieved sample size

## demographics
psych::describe(as.numeric(ds$SD01s)) # age
table(ds$SD02, useNA = "always") # gender: m, w, d, I don't want to tell, NA
table(ds$SD03, useNA = "always") # occupancy: student, undergraduate, graduate, trainee, employee, unemployed, retired, NA
# original study demographics: "40 men and 120 women) Ohio University undergraduates" (p. 316)

## internal consistency of affect index (Klaaren et al. (1994), Cronbachs alpha: .93 (p. 316))
psych::alpha(cbind(ds$BK01_01, ds$BK01_02, ds$BK01_03))

## internal consistency of NFC scale; The mean score in this sample was 89.96, with scores ranging from 39 to 120. (p. 317)
psych::alpha(cbind(cbind(
  ds$NC01_01,
  ds$NC01_02,
  ds$NC01_03,
  ds$NC01_09,
  ds$NC01_12,
  ds$NC01_14,
  ds$NC01_15,
  ds$NC01_16,
  ds$NC01_17,
  ds$NC01_19,
  ds$NC01_22,
  ds$NC01_23,
  ds$NC01_28,
  ds$NC01_29,
  ds$NC01_30,
  ds$NC01_31
)))

## "manipulation check" [beware when interpreting: people may succumb to hindsight bias when remembering their expectation]

# oneway anova
mc <- aov(BK03_07 ~ expectation, data = ds)
summary(mc) # BK03_07: how much did you expect to like the movie after having seen the explanation film but not the film itself?

# post hoc tests
TukeyHSD(mc)
psych::describeBy(BK03_07 ~ expectation, data = ds) # ADDED
# expected pattern: expectations in the positive condition should be more positive than those in the negative condition
# absence of this pattern does not affect interpretability of the main test below (potential hindsight bias might be at play here)

# ADDED during revision 1: planned contrast - since they are more like the original and better since there are hypotheses
model <- lm(ai ~ expectation * nfc, data = ds)

emtrends(model, ~expectation, var = "nfc") |>
  summary(infer = TRUE)


set.seed(1) # ADDED for reproducibility
ggplot(ds, aes(y = BK03_07, x = expectation)) + # CHANGED: the look of the plot
  geom_violin(draw_quantiles = .5) +
  geom_jitter(width = .1, height = 0) + # ADDED: set height-jittering to 0
  scale_x_discrete(
    name = "Remembered Positivity of Expectation",
    limits = c("negative", "none", "positive")
  ) +
  scale_y_continuous(
    name = "Induced Expectation",
    limits = c(1, 9),
    breaks = seq(1, 9, 1)
  ) +
  theme_bw() +
  theme(
    panel.border = element_blank(),
    axis.line = element_line(colour = "black")
  )
ggsave(
  "glrep_rememberedexpecation.jpg",
  scale = 2,
  height = 800,
  width = 800,
  units = "px"
)

## MAIN TEST: ANOVA
summary(lm(ai ~ expectation * nfc, data = ds)) # central hypothesis: interaction effect
anova(lm(ai ~ expectation * nfc, data = ds)) # ADDED: omnibus test for the interaction
effectsize::eta_squared(
  anova(lm(ai ~ expectation * nfc, data = ds)),
  partial = FALSE,
  alternative = "less",
  ci = .90
) # ADDED: for reporting

# slopes for the single groups
summary(lm(ai ~ nfc, data = ds[ds$expectation == "positive", ])) # "b = –.03 , SE = .02 , t(154) = 1.91, p = .05" (p. 318)
summary(lm(ai ~ nfc, data = ds[ds$expectation == "negative", ])) # "b = –.008, SE = .019, t(154) = 0.42, p = .67"
summary(lm(ai ~ nfc, data = ds[ds$expectation == "none", ])) # "b =  .03 , SE = .02 , t(154) = 1.62, p = .10"

## Plot results (this was created to correspond to the original study's main results plot; see Figure 1 on p. 318 from Geers, A. L., & Lassiter, G. D. (2003). Need for Cognition and Expectations as Determinants of Affective Experience. Basic and Applied Social Psychology, 25(4), 313–325. https://doi.org/10.1207/S15324834BASP2504_5)
ggplot(ds, aes(x = nfc, y = ai, col = expectation)) +
  geom_point() +
  geom_smooth(method = "lm", se = TRUE) + # CHANGED during revision (before: se = FALSE)
  scale_x_continuous(
    name = "Need for Cognition",
    limits = c(1, 7),
    breaks = seq(1, 7, 1)
  ) +
  scale_y_continuous(
    name = "Affect Index",
    limits = c(1, 9),
    breaks = seq(1, 9, 1)
  ) +
  theme_bw() +
  theme(panel.border = element_blank()) +
  # scale_fill_discrete(name = "Expectation", values = c("negative" = "red", "none" = "yellow", "positive" = "green"), labels = c("a", "B", "c")) +
  scale_color_manual(values = c("#d95f02", "#7570b3", "#1b9e77")) +
  theme(axis.line = element_line(colour = "black"))
ggsave(
  "glrep_interaction.jpg",
  scale = 1.5,
  height = 900,
  width = 1200,
  unit = "px"
)


# Exploratory Analyses ----------------------------------------------------

### distribution of Affect Index and NFC
psych::describe(ds$ai)
hist(ds$ai)
ggplot(ds, aes(x = ai)) +
  geom_histogram(col = "black", fill = "grey", binwidth = 0.5) +
  theme_bw() +
  theme(
    panel.border = element_blank(),
    axis.line = element_line(colour = "black")
  ) +
  scale_x_continuous(
    name = "Affect Index",
    limits = c(1, 9),
    breaks = seq(1, 9, 1)
  ) +
  ylab("Count")
ggsave("glrep_ai_dist.jpg", scale = 2, height = 800, width = 800, units = "px")
sum(!is.na(ds$ai))

ggplot(ds, aes(x = nfc)) +
  geom_histogram(col = "black", fill = "grey") +
  theme_bw() +
  theme(
    panel.border = element_blank(),
    axis.line = element_line(colour = "black")
  ) +
  scale_x_continuous(
    name = "Need for Cognition",
    limits = c(1, 9),
    breaks = seq(1, 9, 1)
  ) +
  ylab("Count")
ggsave("glrep_nfc_dist.jpg", scale = 2, height = 800, width = 800, units = "px")
sum(!is.na(ds$nfc))

### Testing for main effect of expectation on Affect Index only (omnibus test in contrast to dummy effects in lm)
aimain <- aov(ai ~ expectation, data = ds)
summary(aimain)
# post hoc tests
TukeyHSD(aimain)

set.seed(1)
ggplot(ds, aes(y = ai, x = expectation)) +
  geom_violin(draw_quantiles = .5) +
  geom_jitter(width = .1, height = 0) + # ADDED: set height-jittering to 0
  scale_x_discrete(
    name = "Induced Expectation",
    limits = c("negative", "none", "positive")
  ) +
  scale_y_continuous(
    name = "Affect Index",
    limits = c(1, 9),
    breaks = seq(1, 9, 1)
  ) +
  theme_bw() +
  theme(
    panel.border = element_blank(),
    axis.line = element_line(colour = "black")
  )
ggsave(
  "glrep_ai_by_expecation.jpg",
  scale = 2,
  height = 800,
  width = 800,
  units = "px"
)


### Remembered positivity of expectation and affect index
set.seed(1) # ADDED for reproducibility
ggplot(ds, aes(y = ai, x = BK03_07)) +
  geom_jitter(width = .2, height = 0) + # ADDED: set height-jittering to 0
  geom_smooth(method = "lm") +
  ylab("Affect Index") +
  xlab("Remembered Expectation") #+ ylim(c(1, 9))
ggsave("glrep_ai_by_rememberedexpectation.jpg", scale = 1.5)
cor.test(ds$ai, ds$BK03_07)

### How long did participants stay on the film pages?
# expectation induction films: both 28s            ; NFC 1st: P. 3, NFC last: P. 2
# Don't Shove film (target stimulus): 6:02 -> 362s; NFC 1st: P. 4, NFC last: P. 3

# use only positive and negative groups
ds_pn <- ds[ds$expectation != "none", ]

# determine affective expectation induction (aei) film watching time
ds_pn$time_aei <- ifelse(
  ds_pn$nfc_pos == "before",
  ds_pn$TIME003,
  ds_pn$TIME002
)
hist((ds_pn$time_aei))
abline(v = 28, col = "red")
table(ds_pn$time_aei >= 28, useNA = "always") # only true cases will be retained for data analysis

# determine target film watching time
ds_pn$time_target <- ifelse(
  ds_pn$nfc_pos == "before",
  ds_pn$TIME004,
  ds_pn$TIME003
)
hist(ds_pn$time_target)
abline(v = 362, col = "red")
table(ds_pn$time_target >= 362, useNA = "always") # only true cases will be retained for data analysis


### Is there a difference between NFC-scores for people who were asked before vs. after the film?

### "Attention Checks" for the target film

ds$ac1 <- ifelse(ds$IK01_01 == 1, TRUE, FALSE)
ds$ac2 <- ifelse(ds$IK01_02 == 2, TRUE, FALSE)
ds$ac3 <- ifelse(ds$IK01_03 == 1, TRUE, FALSE)
ds$ac4 <- ifelse(ds$IK01_04 == 2, TRUE, FALSE)
ds$ac <- apply(cbind(ds$ac1, ds$ac2, ds$ac3, ds$ac4), 1, FUN = "sum")

## Did participants know the correct answers to the questions about the film?
table(ds$ac, useNA = "always")
round((table(ds$ac, useNA = "always") / nrow(ds)) * 100, 2)

## Does filtering for attentive participants affect the results?
ds_ac <- ds[ds$ac == 4, ]
summary(lm(ai ~ expectation * nfc, data = ds_ac)) # central hypothesis: interaction effect
anova(lm(ai ~ expectation * nfc, data = ds_ac)) # ADDED: omnibus test for the interaction
effectsize::eta_squared(
  anova(lm(ai ~ expectation * nfc, data = ds_ac)),
  partial = FALSE,
  alternative = "less",
  ci = .90
) # ADDED: for reporting

### Mediation analysis

### When did how many participants participate? [recruitment tracking]
plot(
  y = 1:nrow(ds),
  x = lubridate::ymd_hms(ds$STARTED),
  type = "b",
  xlab = "Date",
  ylab = "N",
  pch = 16,
  col = rgb(0, 0, 0, .5),
  # , ylim = c(0, 410)
  ylim = c(0, 300),
  xlim = c(
    lubridate::ymd_hms("2023-01-18 18:31:05 UTC"),
    lubridate::ymd_hms("2023-04-01 18:31:05 UTC")
  ),
  main = paste("Stand:", Sys.Date(), "\nN =", nrow(ds))
)
abline(h = nrow(ds), lty = 2, col = "grey")
abline(h = 400, lty = 2, col = "red")


### Replication success based on effect sizes converted to SMDs and according to Lakens' approach https://lakens.github.io/statistical_inferences/17-replication.html
# Original: F(1, 154) = 6.40, eta2 = .04
# Replication: F(2, 246) = 0.92, eta2 < .01
# Convert eta2 to r for metafor (r = sqrt(eta2))

d_original <- escalc(
  measure = "COR",
  ri = sqrt(0.04),
  ni = 160
)

d_replication <- escalc(
  measure = "COR",
  ri = sqrt(0.00),
  ni = 252
)

metadata <- data.frame(
  yi = c(d_original$yi, d_replication$yi),
  vi = c(d_original$vi, d_replication$vi),
  study = c("original", "replication")
)

# replication success test via moderator/heterogeneity
res_h <- rma(yi, vi, mods = ~study, method = "FE", data = metadata, digits = 3)
res_h

# Save processed data -----------------------------------------------------
# write.csv(ds, file = paste("gl2003-replication-processed-", Sys.Date(), ".csv", sep = "")) # CHANGED: commented out, processed dataset is available here:
