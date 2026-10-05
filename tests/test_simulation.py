"""Monte Carlo checks of the two statistical promises. Run with `pytest -m slow`.

Never loosen these to make them pass: a failure means the guarantee is broken.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from typing import Any

import pytest

from surety import DriftMonitor, certify

from .conftest import NOUL

pytestmark = pytest.mark.slow

RUNS = 2000
N_CALIB = 1000  # small samples are where a false certificate is most likely
N_POWER = 3000  # enough rows for the overconfident model to certify most of the time
ALPHA, DELTA = 0.1, 0.1
# Monte Carlo slack on a share estimated from RUNS runs: 3 standard errors at p = DELTA.
SLACK = 3 * math.sqrt(DELTA * (1 - DELTA) / RUNS)


def simulate(
    err_given_conf: Callable[[float], float], true_risk: Callable[[float], float], seed: int, n: int = N_CALIB
) -> tuple[float, float | None, float]:
    """Certify on a fresh sample. Returns (true error rate above the chosen t, t, coverage)."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for _ in range(n):
        c = 0.5 + 0.5 * rng.random()  # stated confidence, uniform on [0.5, 1)
        wrong = rng.random() < err_given_conf(c)
        rows.append({"answer": {"noul": c}, "label": not wrong})
    cert = certify(rows, question_id="q", question=NOUL, model="sim", alpha=ALPHA, delta=DELTA)
    if cert.threshold is None:
        return 0.0, None, 0.0  # everything goes to review: no automated errors at all
    return true_risk(cert.threshold), cert.threshold, cert.coverage


def test_guarantee_holds_for_an_overconfident_model() -> None:
    """Stated confidence c, true error 1.5·(1−c): the model claims more accuracy than it has.

    With c uniform on [0.5, 1), the true error rate above t is R(t) = 0.75·(1−t),
    so R(t) <= α only for t >= 0.8667. Grid points 0.825 and 0.85 are tempting
    but invalid; the test has to resist them.
    """
    kappa = 1.5
    results = [simulate(lambda c: kappa * (1 - c), lambda t: kappa * (1 - t) / 2, s, N_POWER) for s in range(RUNS)]
    violations = sum(r > ALPHA for r, _, _ in results) / RUNS
    certified = [cov for _, t, cov in results if t is not None]
    assert violations <= DELTA + SLACK, f"guarantee violated in {violations:.1%} of runs"
    assert len(certified) / RUNS >= 0.9, "should usually find a valid threshold"
    assert statistics.mean(certified) >= 0.1, "coverage should be non-trivial"


def test_guarantee_holds_when_every_threshold_is_slightly_bad() -> None:
    """Error rate α + 0.01 at every confidence: any certificate at all is a violation."""
    results = [simulate(lambda c: ALPHA + 0.01, lambda t: ALPHA + 0.01, 10_000 + s) for s in range(RUNS)]
    violations = sum(r > ALPHA for r, _, _ in results) / RUNS
    assert violations <= DELTA + SLACK, f"guarantee violated in {violations:.1%} of runs"


def run_length(alpha: float, arl: float, rate: float, cap: int, rng: random.Random) -> int:
    m = DriftMonitor(alpha, arl)
    for t in range(1, cap + 1):
        if m.update(rng.random() < rate):
            return t
    return cap  # censored: biases the mean down, which is the safe direction here


@pytest.mark.parametrize("alpha", [0.05, 0.1])
def test_drift_null_mean_run_length_is_at_least_arl(alpha: float) -> None:
    arl = 50
    rng = random.Random(7)
    lengths = [run_length(alpha, arl, alpha, 100 * arl, rng) for _ in range(RUNS)]
    assert statistics.mean(lengths) >= arl


def test_drift_detects_a_shift_to_five_alpha() -> None:
    alpha, arl = 0.05, 1000
    rng = random.Random(11)
    delays = sorted(run_length(alpha, arl, 5 * alpha, 5000, rng) for _ in range(500))
    assert statistics.median(delays) <= 60
    assert delays[int(0.95 * len(delays))] <= 150
