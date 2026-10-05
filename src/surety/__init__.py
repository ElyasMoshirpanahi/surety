"""surety: statistical guarantees for System One decisions.

Turns a typed answer from Jev, Laya, or any POST /v1/systemone server into
AUTOMATE or REVIEW, with a finite-sample guarantee:

    with probability >= 1 - delta over the calibration sample,
    the error rate among AUTOMATED decisions is at most alpha.

Read before trusting a certificate:
  * Calibration rows must be a random sample of real traffic, labelled by
    people, never by the model being certified.
  * The guarantee assumes future traffic looks like that sample. The drift
    monitor exists to tell you when it stops looking like it.
  * Pin model versions. An alias such as jev-latest can move under you, so
    the gate refuses any model string other than the certified one.
"""

from .answers import fingerprint, read_answer
from .backends import (
    Backend,
    BackendError,
    FakeBackend,
    HttpBackend,
    LayaBackend,
    LayaServeBackend,
    SdkBackend,
    SystemOne,
)
from .certify import Certificate, certify, certify_many, curve, is_alias
from .gate import AUTOMATE, REVIEW, Decision, Gate
from .ledger import GENESIS, Ledger
from .stats import DEFAULT_GRID, DriftMonitor, binom_cdf, min_samples

__version__ = "0.2.0"

__all__ = [
    "AUTOMATE",
    "DEFAULT_GRID",
    "GENESIS",
    "REVIEW",
    "Backend",
    "BackendError",
    "Certificate",
    "Decision",
    "DriftMonitor",
    "FakeBackend",
    "Gate",
    "HttpBackend",
    "LayaBackend",
    "LayaServeBackend",
    "Ledger",
    "SdkBackend",
    "SystemOne",
    "__version__",
    "binom_cdf",
    "certify",
    "certify_many",
    "curve",
    "fingerprint",
    "is_alias",
    "min_samples",
    "read_answer",
]
