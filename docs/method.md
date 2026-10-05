# Method

This page states exactly what `surety` computes and why the guarantee holds.

## Setup

Fix a question q, a pinned model m and a slice s. Each calibration row i has the
model's answer and a human label y_i. From the answer we take:

- the **decision** d_i: the argmax option. For `noul`, d_i = true iff P(true) ≥ 0.5.
- the **confidence** c_i: the probability the model gave that decision. For
  `noul`, c_i = max(p, 1 − p). For `choice` and `score`, c_i = max_k p_k.

Vendor `confidence` fields are never used. Jev defines its confidence as
(n·p_max − 1)/(n − 1) and Laya as 1 − normalized entropy. Neither is the
probability of the decision, and they aren't comparable to each other.

Row i is an **error** if d_i ≠ y_i, after normalising booleans and integers. For
`score` questions this is exact match on the argmax level. The expected `score`
field is ignored.

An optional **actionable set** A restricts automation to some decisions. Rows
whose decision is outside A are never automated, and they don't count toward
n_t or k_t.

## Risk above a threshold

For a threshold t, the gate automates a decision when c ≥ t and d ∈ A. Define the
true selective risk

  R(t) = P(d ≠ y | c ≥ t, d ∈ A)

over the traffic distribution. The goal is to pick t with R(t) ≤ α, with
probability at least 1 − δ over the calibration sample.

## The test

Fix a grid T = {t_1 < … < t_m} **before looking at the data**. The default is the
25 points 0.50, 0.525, …, 0.95, 0.96, 0.97, 0.98, 0.99, 0.995, 0.999. For each
t ∈ T:

- n_t = number of eligible calibration rows with c ≥ t,
- k_t = number of errors among them,
- p_t = P(Binomial(n_t, α) ≤ k_t), computed exactly in log space.

Reject the null H_t: R(t) > α when p_t ≤ δ/m. Then **choose the smallest
rejected t**, which is the one that automates the most. If none is rejected, the
question is *not certifiable* and every decision goes to review.

### Why it is valid

Assume the calibration rows and future traffic are i.i.d., or more generally
exchangeable. Conditional on n_t, the rows above t are an i.i.d. sample from the
distribution above t, so k_t ~ Binomial(n_t, R(t)). If H_t is true, so R(t) > α,
then because the binomial CDF decreases in its success probability

  P(p_t ≤ δ/m | n_t) ≤ P(Binomial(n_t, R(t)) ≤ k*) ≤ δ/m,

where k* is the largest k with BinomCDF(k; n_t, α) ≤ δ/m. So each p_t is a valid
(conservative) p-value. By the union bound over the m fixed thresholds, the
probability that *any* true null is rejected is at most m · δ/m = δ. On the
complementary event, which has probability ≥ 1 − δ, every rejected t has
R(t) ≤ α. In particular that holds for the one we choose. This is the
Learn-then-Test framework [1] with Bonferroni as the family-wise error rate
procedure. It is also the guaranteed-risk selective classifier of [2], with an
exact binomial bound in place of their bound and a fixed grid in place of a
data-dependent search.

The grid must not depend on the calibration data. Choosing it after looking
would void the union bound.

### Several certificates at once

Each certificate holds with probability 1 − δ on its own. With `--simultaneous`, δ
is divided by the number of (question, model, slice) groups G. By another union
bound, all G certificates then hold together with probability ≥ 1 − δ.

### How many rows

Even with zero errors, certifying needs (1 − α)^n ≤ δ/m, so
n ≥ log(δ/m) / log(1 − α). `surety plan` prints this number. Errors and a
large share of low-confidence rows push the real requirement higher.

## Drift: Shiryaev–Roberts e-detector

The certificate is only as good as the exchangeability assumption. After
deployment, audit a **uniformly random sample of automated decisions**, and let
x_j = 1 if audited decision j was wrong. Under the null "the automated error rate is
still ≤ α", each factor 1 + λ(x_j − α) has expectation ≤ 1 for λ ∈ [0, 1/α].
The Shiryaev–Roberts recursion

  R_j = (R_{j−1} + 1) · (1 + λ(x_j − α)),  R_0 = 0,

is then an *e-detector* in the sense of Shin, Ramdas and Rinaldo [3]: R_j − j
is a supermartingale under the null. By optional stopping, the first time
R_j ≥ ARL has expected value at least ARL. `surety` runs four detectors with
λ = b/α for b ∈ {0.05, 0.1, 0.2, 0.5} and averages them. An average of
e-detectors is an e-detector, so the ARL bound still holds while detection
adapts to the size of the shift. The bound is anytime-valid: you can check the
statistic after every audit without inflating false alarms.

When the alarm fires, the gate suspends the certificate. The ledger gets a `suspend`
record, and every later decision under it goes to review. The suspension
survives restarts, because `Gate.load(..., ledger=...)` replays the audit and
suspend records. The fix is to collect fresh labels and re-certify.

## Simulation checks

`pytest -m slow` checks both promises by Monte Carlo:

- **Guarantee.** For a deliberately overconfident synthetic model, where stated
  confidence c has true error 1.5·(1 − c), R(t) is known in closed form.
  Across 2,000 independent calibration samples, the share of runs whose chosen
  threshold has R(t) > α must be ≤ δ plus 3 Monte Carlo standard errors. Coverage
  must also be non-trivial. A second scenario sets the error to α + 0.01 at every
  confidence, so that any certificate at all is a violation.
- **Drift.** With error rate exactly α and ARL = 50, the mean run length must be
  at least ARL. After a shift to 5α, the alarm must fire within a bounded
  number of audits.

## References

1. A. N. Angelopoulos, S. Bates, E. J. Candès, M. I. Jordan, L. Lei.
   *Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control.*
   arXiv:2110.01052, 2021.
2. Y. Geifman, R. El-Yaniv. *Selective Classification for Deep Neural Networks.*
   NeurIPS 2017 (selection with guaranteed risk, SGR).
3. J. Shin, A. Ramdas, A. Rinaldo. *E-detectors: a nonparametric framework for
   sequential change detection.* New England Journal of Statistics in Data
   Science, 2023. arXiv:2203.03532.
4. C. J. Clopper, E. S. Pearson. *The use of confidence or fiducial limits
   illustrated in the case of the binomial.* Biometrika 26(4), 1934. Used for the
   upper bounds in `surety report`.
