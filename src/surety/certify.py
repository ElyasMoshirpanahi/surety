"""Certificates: the threshold per (question, model, slice) with a finite-sample guarantee."""

from __future__ import annotations

import bisect
import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .answers import fingerprint, norm, read_answer
from .stats import DEFAULT_GRID, binom_cdf, binom_upper

METHOD = "exact binomial tail + Bonferroni over a fixed grid"
_ALIAS = re.compile(r"(^|[-_.])(latest|preview|stable|current)$", re.IGNORECASE)


def is_alias(model: str) -> bool:
    """True for moving aliases such as jev-latest or jev-preview, which must never be certified."""
    return bool(_ALIAS.search(model.strip()))


@dataclass
class Certificate:
    question_id: str
    question_fp: str
    model: str
    slice: str | None
    alpha: float
    delta: float
    threshold: float | None  # None: not certifiable, everything goes to review
    actionable: list[str] | None  # None: any decision may be automated
    n_calibration: int
    n_automated: int
    errors_automated: int
    coverage: float  # share of calibration rows that would be automated
    method: str = METHOD
    created_at: float = field(default_factory=time.time)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            body = asdict(self)
            body.pop("id")
            self.id = fingerprint(body)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GridPoint:
    """The test at one threshold, for reports. `accepted` uses the Bonferroni level."""

    threshold: float
    n: int
    errors: int
    p_value: float
    accepted: bool
    coverage: float
    upper_bound: float  # Clopper-Pearson upper bound on R(t) at the per-threshold level


@dataclass
class _Scored:
    """Calibration rows reduced to what the test needs, sorted by confidence."""

    confs: list[float]  # ascending, eligible rows only
    suffix_errors: list[int]  # suffix_errors[i] = errors among confs[i:]
    n_total: int


def _score(
    rows: Iterable[Mapping[str, Any]], question: Mapping[str, Any], act: set[str] | None
) -> tuple[_Scored, list[tuple[float, bool]]]:
    fp = fingerprint(question)
    pts: list[tuple[float, bool]] = []
    eligible: list[tuple[float, bool]] = []
    for i, r in enumerate(rows):
        row_fp = r.get("question_fp")
        if row_fp is not None and row_fp != fp:
            raise ValueError(
                f"calibration row {i} was answered under a different question definition ({row_fp} != {fp})"
            )
        dec, conf = read_answer(question, r["answer"])
        err = dec != norm(r["label"])
        pts.append((conf, err))
        if act is None or dec in act:
            eligible.append((conf, err))
    eligible.sort(key=lambda ce: ce[0])
    suffix = [0] * (len(eligible) + 1)
    for i in range(len(eligible) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + int(eligible[i][1])
    return _Scored([c for c, _ in eligible], suffix, len(pts)), pts


def _at(s: _Scored, t: float) -> tuple[int, int]:
    i = bisect.bisect_left(s.confs, t)
    return len(s.confs) - i, s.suffix_errors[i]


def _check(alpha: float, delta: float, grid: Sequence[float]) -> None:
    if not (0 < alpha < 1 and 0 < delta < 1):
        raise ValueError("alpha and delta must be in (0, 1)")
    if not grid:
        raise ValueError("the threshold grid must not be empty")


def certify(
    rows: Iterable[Mapping[str, Any]],
    *,
    question_id: str,
    question: Mapping[str, Any],
    model: str,
    alpha: float,
    delta: float,
    slice: str | None = None,
    actionable: Iterable[Any] | None = None,
    grid: Sequence[float] = DEFAULT_GRID,
) -> Certificate:
    """Pick the loosest threshold whose automated error rate is provably <= alpha.

    Each grid threshold t tests "error rate among rows with confidence >= t
    exceeds alpha" with the exact binomial tail, conditional on how many rows
    clear t. Bonferroni over the fixed grid keeps every accepted threshold
    valid at once with probability >= 1 - delta; we keep the one that
    automates the most. Rows: {"answer": {...}, "label": ...}.
    """
    _check(alpha, delta, grid)
    act = None if actionable is None else {norm(a) for a in actionable}
    s, _ = _score(rows, question, act)
    level = delta / len(grid)
    best = None
    for t in sorted(grid):  # ascending: first accepted automates the most
        n, k = _at(s, t)
        if n and binom_cdf(k, n, alpha) <= level:
            best = (t, n, k)
            break
    t_best, n, k = best or (None, 0, 0)
    return Certificate(
        question_id,
        fingerprint(question),
        model,
        slice,
        alpha,
        delta,
        t_best,
        sorted(act) if act is not None else None,
        s.n_total,
        n,
        k,
        n / s.n_total if s.n_total else 0.0,
    )


def curve(
    rows: Iterable[Mapping[str, Any]],
    *,
    question: Mapping[str, Any],
    alpha: float,
    delta: float,
    actionable: Iterable[Any] | None = None,
    grid: Sequence[float] = DEFAULT_GRID,
) -> list[GridPoint]:
    """The full test at every grid threshold. For reporting only; `certify` decides."""
    _check(alpha, delta, grid)
    act = None if actionable is None else {norm(a) for a in actionable}
    s, _ = _score(rows, question, act)
    level = delta / len(grid)
    out = []
    for t in sorted(grid):
        n, k = _at(s, t)
        p = binom_cdf(k, n, alpha) if n else 1.0
        out.append(
            GridPoint(t, n, k, p, bool(n) and p <= level, n / s.n_total if s.n_total else 0.0, binom_upper(k, n, level))
        )
    return out


def reliability(
    rows: Iterable[Mapping[str, Any]], *, question: Mapping[str, Any], bins: int = 10
) -> list[tuple[float, float, int, float, float]]:
    """(lo, hi, n, mean confidence, accuracy) per equal-width confidence bin over [0, 1].

    Descriptive only. Calibration error says nothing about the error rate above a threshold.
    """
    _, pts = _score(rows, question, None)
    acc: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, err in pts:
        acc[min(int(conf * bins), bins - 1)].append((conf, err))
    out = []
    for b, cell in enumerate(acc):
        if cell:
            mean = sum(c for c, _ in cell) / len(cell)
            accuracy = 1 - sum(e for _, e in cell) / len(cell)
            out.append((b / bins, (b + 1) / bins, len(cell), mean, accuracy))
    return out


Group = tuple[str, str | None]


def group_rows(rows: Iterable[Mapping[str, Any]]) -> dict[Group, list[Mapping[str, Any]]]:
    """Calibration rows keyed by (question_id, slice), in a stable order."""
    groups: dict[Group, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["question_id"], r.get("slice"))].append(r)
    return dict(sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))))


def group_model(key: Group, rows: Sequence[Mapping[str, Any]], *, allow_alias: bool = False) -> str:
    """The single pinned model a group was answered by, or ValueError."""
    qid, sl = key
    models = {r["model"] for r in rows}
    if len(models) != 1:
        raise ValueError(f"{qid}/{sl}: rows mix models {sorted(models)}; certify each model separately")
    model = str(next(iter(models)))
    resolved = {r.get("resolved_model") or model for r in rows}
    if resolved != {model}:
        raise ValueError(f"{qid}/{sl}: requested {model!r} but the server answered as {sorted(resolved)}")
    if is_alias(model) and not allow_alias:
        raise ValueError(f"{qid}/{sl}: {model!r} is a moving alias; pin a version such as jev-1.13.0")
    return model


def certify_many(
    rows: Iterable[Mapping[str, Any]],
    questions: Mapping[str, Mapping[str, Any]],
    *,
    alpha: float,
    delta: float,
    simultaneous: bool = False,
    actionable: Mapping[str, Iterable[Any]] | None = None,
    grid: Sequence[float] = DEFAULT_GRID,
    allow_alias: bool = False,
) -> list[Certificate]:
    """One certificate per (question, model, slice).

    With `simultaneous`, delta is split evenly across groups (Bonferroni again),
    so every certificate holds at once with probability >= 1 - delta.
    """
    groups = group_rows(rows)
    d = delta / len(groups) if simultaneous and groups else delta
    certs = []
    for key, grp in groups.items():
        qid, sl = key
        if qid not in questions:
            raise ValueError(f"calibration rows mention question {qid!r}, which is not in the questions file")
        certs.append(
            certify(
                grp,
                question_id=qid,
                question=questions[qid],
                model=group_model(key, grp, allow_alias=allow_alias),
                alpha=alpha,
                delta=d,
                slice=sl,
                actionable=(actionable or {}).get(qid),
                grid=grid,
            )
        )
    return certs
