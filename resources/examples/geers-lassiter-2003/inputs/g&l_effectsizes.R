

# Effect sizes from G&L AEM studies ---------------------------------------
es <- NULL

## Geers & Lassiter, 2002, Study 1
# the affective reactions of the positive-expectation participants became more positive
# as level ofoptimism increased and more negative as level of pessimism increased, 
# as compared to the no-expectation participants, F(2, 117) = 5.16, p< .05 (β = .97)
es <- rbind(es, 
            unlist(esc::esc_f(f = 5.16, totaln = 122, es.type = "r")[c(1, 5, 6)])
)

# the affective reactions of the negative-expectation participants tended to 
# be less positive than those in the no-expectation condition, regardless of 
# their level of optimism–pessimism, F(2, 117) = 3.45, p = .06 (β = –.17)
es <- rbind(es, 
            unlist(esc::esc_f(f = 3.45, totaln = 122, es.type = "r")[c(1, 5, 6)])
)

## Geers & Lassiter, 2003, Study 1
# "positiv exp.: higher affective reaction with increasing NFC: 
# F(1, 154) = 6.40, MSE = 3.21, p = .01, r2 = .04
es <- rbind(es, 
            unlist(esc::esc_f(f = 6.4, totaln = 160, es.type = "r") [c(1, 5, 6)])
)

# negativ exp: less positive than no exp. regardless of NFC:
# F(1, 156) = 12.09, MSE = 3.30, p = .001, r2 = .07"

es <- rbind(es, 
            unlist(esc::esc_f(f = 12.09, totaln = 160, es.type = "r") [c(1, 5, 6)])
)

es <- as.data.frame(es)
es$study <- c("2002, S1, A"
              , "2002, S1, B"
              , "2003, S1, A"
              , "2003, S1, B")


library(ggplot2)
ggplot(es, aes(x = es, y = study)) + geom_point() +
  geom_errorbar(aes(xmin = ci.lo, xmax = ci.hi)) + 
  geom_abline(xintercept = 0, slope = 0) +
  ggtitle("Effect Sizes from four Geers & Lassiter studies\non Affective Expectations") +
  xlab("Effect Size converted to r") + ylab("Study") +
  geom_vline(xintercept = 0, lty = 2)
