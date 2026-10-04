"""Exact binomial tails, sample-size planning, and the drift e-detector."""

from __future__ import annotations

import math
from collections.abc import Sequence

# Fixed in advance: a grid chosen by looking at the data would void the guarantee.
DEFAULT_GRID: tuple[float, ...] = tuple(round(0.5 + 0.025 * i, 3) for i in range(19)) + (
    0.96,
    0.97,
    0.98,
    0.99,
    0.995,
    0.999,
)
DEFAULT_BETS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.5)


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(Binomial(n, p) <= k), summed in log space so large n stays stable."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    logs = [
        math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq for i in range(k + 1)
    ]
    top = max(logs)
    return min(1.0, math.exp(top) * math.fsum(math.exp(t - top) for t in logs))


def binom_upper(k: int, n: int, level: float) -> float:
    """One-sided Clopper-Pearson upper bound: the largest p with P(Bin(n, p) <= k) >= level."""
    if n <= 0 or k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > level:
            lo = mid
        else:
            hi = mid
    return hi


def min_samples(alpha: float, delta: float, grid: Sequence[float] = DEFAULT_GRID) -> int:
    """Fewest automated calibration rows that could certify, even with zero errors."""
    return math.ceil(math.log(delta / len(grid)) / math.log1p(-alpha))


class DriftMonitor:
    """Anytime-valid alarm for "the automated error rate has risen above alpha".

    Feed it a uniformly random sample of AUTOMATED decisions with true labels.
    It is a Shiryaev-Roberts e-detector averaged over a few bet sizes: while the
    true error rate stays <= alpha, the expected number of audits before a false
    alarm is at least `arl`, however often you look.
    """

    def __init__(self, alpha: float, arl: float = 1000.0, bets: Sequence[float] = DEFAULT_BETS):
        if not 0 < alpha < 1:
            raise ValueError("alpha must be in (0, 1)")
        if arl <= 1:
            raise ValueError("arl must be > 1")
        if any(not 0 < b <= 1 for b in bets) or not bets:
            raise ValueError("bets must be in (0, 1]")
        self.alpha, self.arl = alpha, arl
        self.lams = [b / alpha for b in bets]  # each <= 1/alpha keeps every factor >= 0
        self.r = [0.0] * len(self.lams)
        self.audits = self.errors = 0

    def update(self, is_error: bool) -> bool:
        x = 1.0 if is_error else 0.0
        self.audits += 1
        self.errors += int(is_error)
        self.r = [(r + 1.0) * (1.0 + lam * (x - self.alpha)) for r, lam in zip(self.r, self.lams, strict=True)]
        return self.alarm

    @property
    def statistic(self) -> float:
        return sum(self.r) / len(self.r)

    @property
    def alarm(self) -> bool:
        return self.statistic >= self.arl
