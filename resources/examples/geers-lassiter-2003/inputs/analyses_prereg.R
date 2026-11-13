# Replication of G&L, 2003, S1 --------------------------------------------


# load packages
library(esc) # for effect size calculation
library(psych) # for reliability analysis and descriptives
library(ggplot2) # for plotting


## values from the original study
# central test
# negative exp: less positive than no exp. regardless of NFC: F(1, 154) = 6.40, MSE = 3.21, p = .01, r2 = .04; Negative Expectation: less positive than no expectation, regardless of NFC: 
# F(1, 156) = 12.09, MSE = 3.30, p = .001, r2 = .07
esc::esc_f(f = 12.09, totaln = 160, es.type = "f")$es
# "positive exp.: higher affective reaction with increasing NFC: F(1, 154) = 6.40, MSE = 3.21, p = .01, r2 = .04
esc::esc_f(f = 6.4, totaln = 160, es.type = "f")$es

pwr::pwr.anova.test(3, n = NULL, f = 0.2, power = .95)$n*3 # 390 Participants per group will provide 95% power for the smaller of the two central effects
# small telescopes approach
ntarget <- 2.5*160 # our target sample size is 400 complete cases

## Open data
datalink <- "https://www.soscisurvey.de/replikationSeminar/?act=ZEXHquL7onzOIEi9ExwKVwCv" # only works temporarily, use OSF data if this doesn't work (https://osf.io/rge23/)

ds <- read.csv(datalink, sep = "\t")

write.csv(ds, file = paste("gl2003-replication-raw-", Sys.Date(), ".csv", sep = ""))

# Data processing ---------------------------------------------------------

# ds <- ds[ds$MODE == "interview", ] # comment out for code testing, comment in for actual data analysis

## replace "-9" with NAs
ds <- dplyr::na_if(ds, -9)

## exclude piping page
ds <- ds[ds$QUESTNNR != "random", ]

## compute affect index 
ds$ai <- rowMeans(cbind(ds$BK01_01, ds$BK01_02, ds$BK01_03))


## compute NFC scores (sums)
ds$nfc <- rowMeans(cbind( ds$NC01_01
                          , ds$NC01_02
                          , ds$NC01_03
                          , ds$NC01_09
                          , ds$NC01_12
                          , ds$NC01_14
                          , ds$NC01_15
                          , ds$NC01_16
                          , ds$NC01_17
                          , ds$NC01_19
                          , ds$NC01_22
                          , ds$NC01_23
                          , ds$NC01_28
                          , ds$NC01_29
                          , ds$NC01_30
                          , ds$NC01_31
), na.rm = FALSE)


# Experimental condition (affective expectation induction)
ds$expectation <- dplyr::recode(ds$RD01
                          , "1" = "positive"
                          , "2" = "negative"
                          , "3" = "none")
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
nrow(ds) # target: 400
"Power"; pwr::pwr.anova.test(3, n = nrow(ds)/3, f = 0.2, power = NULL)$power # power for achieved sample size

## demographics
psych::describe(as.numeric(ds$SD01s)) # age
table(ds$SD02, useNA = "always") # gender: m, w, d, I don't want to tell, NA
table(ds$SD03, useNA = "always") # occupancy: student, undergraduate, graduate, trainee, employee, unemployed, retired, NA
# original study demographics: "40 men and 120 women) Ohio University undergraduates" (p. 316)

## internal consistency of affect index (Klaaren et al. (1994), Cronbachs alpha: .93 (p. 316)) 
psych::alpha(cbind(ds$BK01_01, ds$BK01_02, ds$BK01_03))

## internal consistency of NFC scale; The mean score in this sample was 89.96, with scores ranging from 39 to 120. (p. 317) 
psych::alpha(cbind(cbind( ds$NC01_01, ds$NC01_02, ds$NC01_03, ds$NC01_09, ds$NC01_12, ds$NC01_14, ds$NC01_15, ds$NC01_16, ds$NC01_17, ds$NC01_19, ds$NC01_22, ds$NC01_23, ds$NC01_28, ds$NC01_29, ds$NC01_30, ds$NC01_31)))

## "manipulation check" [beware when interpreting: people may succumb to hindsight bias when remembering their expectation]

# oneway anova
mc <- aov(BK03_07 ~ expectation, data = ds)
summary(mc) # BK03_07: how much did you expect to like the movie after having seen the explanation film but not the film itself?

# post hoc tests
TukeyHSD(mc)
# expected pattern: expectations in the positive condition should be more positive than those in the negative condition
# absence of this pattern does not affect interpretability of the main test below (potential hindsight bias might be at play here)

ggplot(ds, aes(y = BK03_07, x = expectation)) +
  geom_violin(draw_quantiles = .5) + geom_jitter(width = .1) +
  ylab("Remembered positivity of expectation") +
  xlab("Induced Expectation") #+ ylim(c(1, 9))
ggsave("glrep_rememberedexpecation.jpg", scale = 1.5)

## MAIN TEST: ANOVA
summary(lm(ai ~ expectation * nfc, data = ds)) # central hypothesis: interaction effect

# slopes for the single groups
summary(lm(ai ~ nfc, data = ds[ds$expectation == "positive", ])) # "b = –.03 , SE = .02 , t(154) = 1.91, p = .05" (p. 318)
summary(lm(ai ~ nfc, data = ds[ds$expectation == "negative", ])) # "b = –.008, SE = .019, t(154) = 0.42, p = .67"
summary(lm(ai ~ nfc, data = ds[ds$expectation == "none", ])) # "b =  .03 , SE = .02 , t(154) = 1.62, p = .10"

## Plot results (this was created to correspond to the original study's main results plot; see Figure 1 on p. 318 from Geers, A. L., & Lassiter, G. D. (2003). Need for Cognition and Expectations as Determinants of Affective Experience. Basic and Applied Social Psychology, 25(4), 313–325. https://doi.org/10.1207/S15324834BASP2504_5)
ggplot(ds, aes(x = nfc, y = ai, col = expectation)) + 
  geom_point() + geom_smooth(method = "lm", se = FALSE) +
  xlab("Need for Cognition") + ylab("Affect Index")
ggsave("glrep_interaction.jpg", scale = 1.5)



# Exploratory Analyses ----------------------------------------------------

### How long did participants stay on the film pages?
# expectation induction films: both 28s            ; NFC 1st: P. 3, NFC last: P. 2
# Don't Shove film (target stimulus): 6:02 -> 362s; NFC 1st: P. 4, NFC last: P. 3


# use only positive and negative groups
ds_pn <- ds[ds$expectation != "none", ]

# determine affective expectation induction (aei) film watching time
ds_pn$time_aei <- ifelse(ds_pn$nfc_pos == "before", ds_pn$TIME003, ds_pn$TIME002)
hist(ds_pn$time_aei)
abline(v = 28, col = "red")
table(ds_pn$time_aei >= 28, useNA = "always") # only true cases will be retained for data analysis

# determine target film watching time
ds_pn$time_target <- ifelse(ds_pn$nfc_pos == "before", ds_pn$TIME004, ds_pn$TIME003)
hist(ds_pn$time_target)
abline(v = 362, col = "red")
table(ds_pn$time_target >= 362, useNA = "always") # only true cases will be retained for data analysis



### Is there a difference between NFC-scores for people who were asked before vs. after the film?


### "Attention Checks" for the target film

## Did participants know the correct answers to the questions about the film? 

## Does filtering for attentive participants affect the results?


### Mediation analysis



# Save processed data -----------------------------------------------------
write.csv(ds, file = paste("gl2003-replication-processed-", Sys.Date(), ".csv", sep = ""))
