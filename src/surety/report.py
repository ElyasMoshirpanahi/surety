"""Markdown report: the test at every threshold, plus reliability bins, per slice."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .certify import Certificate, curve, group_rows, reliability
from .stats import DEFAULT_GRID


def _cell(x: float | None, fmt: str) -> str:
    return "-" if x is None else format(x, fmt)


def render(
    rows: Iterable[Mapping[str, Any]],
    questions: Mapping[str, Mapping[str, Any]],
    certs: Sequence[Certificate],
    *,
    grid: Sequence[float] = DEFAULT_GRID,
    bins: int = 10,
) -> str:
    """Markdown for every certificate in `certs`, recomputed from the same calibration rows."""
    groups = group_rows(rows)
    out = ["# surety calibration report", ""]
    for c in certs:
        grp = groups.get((c.question_id, c.slice), [])
        q = questions[c.question_id]
        level = c.delta / len(grid)
        t = "**not certifiable**" if c.threshold is None else f"**t = {c.threshold}**"
        out += [
            f"## {c.question_id} / slice `{c.slice}` / model `{c.model}`",
            "",
            f"- certificate `{c.id}`, question fingerprint `{c.question_fp}`",
            f"- α = {c.alpha}, δ = {c.delta:g}, per-threshold level δ/|grid| = {level:.3g}",
            f"- {t}: automates {c.coverage:.1%} of {c.n_calibration} calibration rows "
            f"({c.errors_automated}/{c.n_automated} errors)",
            "",
            "### Coverage vs threshold",
            "",
            "| t | automated | coverage | errors | error rate | upper bound | p-value | accepted |",
            "|---:|---:|---:|---:|---:|---:|---:|:---:|",
        ]
        for g in curve(grp, question=q, alpha=c.alpha, delta=c.delta, actionable=c.actionable, grid=grid):
            mark = "✅" if g.accepted else ""
            if g.threshold == c.threshold:
                mark += " ◀ chosen"
            rate = g.errors / g.n if g.n else None
            out.append(
                f"| {g.threshold:g} | {g.n} | {g.coverage:.1%} | {g.errors} | {_cell(rate, '.3f')} "
                f"| {g.upper_bound:.3f} | {g.p_value:.2e} | {mark} |"
            )
        out += [
            "",
            "Upper bound: one-sided Clopper-Pearson bound on the error rate above t at level δ/|grid|.",
            "A threshold is accepted when its p-value is at most δ/|grid|, which is when the bound is at most α.",
            "",
            "### Reliability bins (descriptive only)",
            "",
            "| confidence bin | rows | mean confidence | accuracy | gap |",
            "|---|---:|---:|---:|---:|",
        ]
        for lo, hi, n, mean, acc in reliability(grp, question=q, bins=bins):
            out.append(f"| [{lo:.1f}, {hi:.1f}) | {n} | {mean:.3f} | {acc:.3f} | {mean - acc:+.3f} |")
        out.append("")
    return "\n".join(out)
