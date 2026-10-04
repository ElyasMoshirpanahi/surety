from __future__ import annotations

import math

import pytest

from surety.stats import DEFAULT_GRID, DriftMonitor, binom_cdf, binom_upper, min_samples


def brute(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(0, min(k, n) + 1))


@pytest.mark.parametrize("p", [0.001, 0.05, 0.1, 0.3, 0.5, 0.9, 0.999])
def test_binom_cdf_matches_brute_force(p: float) -> None:
    for n in range(0, 41):
        for k in range(-1, n + 2):
            assert binom_cdf(k, n, p) == pytest.approx(brute(k, n, p) if k >= 0 else 0.0, rel=1e-9, abs=1e-15)


def test_binom_cdf_large_n_is_stable() -> None:
    for k in (0, 10, 30, 50, 80):
        assert binom_cdf(k, 5000, 0.01) == pytest.approx(brute(k, 5000, 0.01), rel=1e-9, abs=1e-300)


def test_binom_cdf_degenerate_p() -> None:
    assert binom_cdf(0, 10, 0.0) == 1.0
    assert binom_cdf(3, 10, 1.0) == 0.0


def test_default_grid_is_the_documented_25_points() -> None:
    assert len(DEFAULT_GRID) == 25
    assert DEFAULT_GRID[0] == 0.5 and DEFAULT_GRID[-1] == 0.999
    assert list(DEFAULT_GRID) == sorted(set(DEFAULT_GRID))


@pytest.mark.parametrize(("alpha", "delta"), [(0.05, 0.05), (0.1, 0.1), (0.01, 0.05)])
def test_min_samples_is_tight(alpha: float, delta: float) -> None:
    n = min_samples(alpha, delta)
    level = delta / len(DEFAULT_GRID)
    assert binom_cdf(0, n, alpha) <= level < binom_cdf(0, n - 1, alpha)


def test_binom_upper_inverts_cdf() -> None:
    u = binom_upper(3, 200, 0.01)
    assert binom_cdf(3, 200, u) == pytest.approx(0.01, rel=1e-6)
    assert binom_upper(5, 5, 0.01) == 1.0


def test_drift_monitor_validates_and_alarms() -> None:
    with pytest.raises(ValueError):
        DriftMonitor(alpha=0)
    with pytest.raises(ValueError):
        DriftMonitor(alpha=0.1, bets=(1.5,))
    m = DriftMonitor(alpha=0.1, arl=20)
    assert not m.update(False)
    fired = [m.update(True) for _ in range(30)]
    assert any(fired)
    assert m.audits == 31 and m.errors == 30


def test_drift_factors_stay_nonnegative() -> None:
    m = DriftMonitor(alpha=0.01, arl=1e9)
    for _ in range(500):
        m.update(False)
    assert all(r >= 0 for r in m.r)
