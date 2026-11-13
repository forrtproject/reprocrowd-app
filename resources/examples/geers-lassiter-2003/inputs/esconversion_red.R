## This script has been used to compute the effect sizes entered into the Replication Database (t1p.de/ReD)

# Original effect sizes
esc::esc_t(t = 1.91, totaln = 53, es.type = "r")$es
esc::esc_t(t = 0.42, totaln = 53, es.type = "r")$es
esc::esc_t(t = 1.62, totaln = 54, es.type = "r")$es

# Replication effect sizes
esc::esc_t(t = 0.94, totaln = 75, es.type = "r")$es
esc::esc_t(t = 1.53, totaln = 94, es.type = "r")$es
esc::esc_t(t = -.42, totaln = 83, es.type = "r")$es

# Test statistics
# F(1, 154) = 6.40, MSE = 3.21, p = .01, r²= .04	F(2, 246) = 0.92, p = .398, η² < 0.01, 95% CI [0, 0.04]
# b = -0.03, SE = .02, t(154) = 1.91, p = .05	b = 0.288, se = 0.306, t(1, 73) = 0.94, p = .349
# b = -.008, SE = .019, t(154) = 0.42, p = .67	b = 0.420, se = 0.275, t(1, 92) = 1.53, p = .130
# b = .03, SE = .02, t(154) = 1.62, p = .10	b = -0.131, se = 0.312, t(1, 81) = -0.420, p = .675
#
# Sample sizes
# 53	75
# 53	94
# 54	83

