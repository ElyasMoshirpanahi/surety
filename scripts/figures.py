"""Regenerate the README figures in assets/.

    python scripts/figures.py     # ~1 min: simulation + coverage chart

The coverage chart uses examples/jev_vs_laya/laya-answers.jsonl: real answers from
laya 0.3.27 (`english@55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, CPU) to the 400
synthetic tickets, collected with `surety collect --backend laya-serve --model english@served`.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from surety import DEFAULT_GRID, certify, curve
from surety.answers import norm, read_answer

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MUTED, GREEN, ORANGE = "#8b949e", "#2da44e", "#d9822b"
NOUL = {"type": "noul"}
ALPHA, DELTA, RUNS, N = 0.1, 0.1, 2000, 1000


# ------------------------------------------------------------------ simulation
def sample(err: Callable[[float], float], rng: random.Random) -> list[dict[str, Any]]:
    rows = []
    for _ in range(N):
        c = 0.5 + 0.5 * rng.random()
        rows.append({"answer": {"noul": c}, "label": not (rng.random() < err(c))})
    return rows


def naive_threshold(rows: list[dict[str, Any]]) -> float | None:
    """What people do by hand: the lowest threshold whose observed error is <= alpha."""
    pts = [(*read_answer(NOUL, r["answer"]), r["label"]) for r in rows]
    for t in DEFAULT_GRID:
        sel = [d != norm(y) for d, c, y in pts if c >= t]
        if sel and sum(sel) / len(sel) <= ALPHA:
            return t
    return None


def violation_rates(err: Callable[[float], float], risk: Callable[[float], float], seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    naive = ours = 0
    for _ in range(RUNS):
        rows = sample(err, rng)
        t_naive = naive_threshold(rows)
        t_ours = certify(rows, question_id="q", question=NOUL, model="sim", alpha=ALPHA, delta=DELTA).threshold
        naive += t_naive is not None and risk(t_naive) > ALPHA
        ours += t_ours is not None and risk(t_ours) > ALPHA
    return naive / RUNS, ours / RUNS


def guarantee_svg(results: list[tuple[str, str, float, float]]) -> str:
    w, h, left, right, top = 860, 300, 250, 800, 70
    row_h, bar_h = 92, 26

    def x(p: float) -> float:
        return left + (right - left) * p

    out = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">',
        "  <title>How often the promised error rate is broken: hand-picked threshold vs surety</title>",
        f'  <text x="{w / 2}" y="24" font-size="15" font-weight="600" fill="{MUTED}" text-anchor="middle">'
        f"How often the promise “error ≤ {ALPHA:g}” is broken ({RUNS:,} calibration samples of {N:,} rows)</text>",
        f'  <rect x="292" y="38" width="12" height="12" rx="2" fill="{ORANGE}"/>'
        f'<text x="309" y="48" font-size="12" fill="{MUTED}">lowest threshold with observed error ≤ α</text>',
        f'  <rect x="560" y="38" width="12" height="12" rx="2" fill="{GREEN}"/>'
        f'<text x="577" y="48" font-size="12" fill="{MUTED}">surety certificate</text>',
    ]
    for p in (0, 0.25, 0.5, 0.75, 1.0):
        out.append(
            f'  <line x1="{x(p):.1f}" y1="{top}" x2="{x(p):.1f}" y2="{top + 2 * row_h - 8}" '
            f'stroke="{MUTED}" stroke-opacity="0.16"/>'
            f'<text x="{x(p):.1f}" y="{top + 2 * row_h + 8}" font-size="11" fill="{MUTED}" '
            f'text-anchor="middle">{p:.0%}</text>'
        )
    xd = x(DELTA)
    out.append(
        f'  <line x1="{xd:.1f}" y1="{top - 6}" x2="{xd:.1f}" y2="{top + 2 * row_h - 8}" stroke="{MUTED}" '
        f'stroke-opacity="0.8" stroke-dasharray="4 4"/>'
        f'<text x="{xd + 6:.1f}" y="{top + 2 * row_h + 26}" font-size="11" fill="{MUTED}">'
        f"allowed: δ = {DELTA:.0%}</text>"
    )
    for i, (title, sub, naive, ours) in enumerate(results):
        y0 = top + i * row_h
        out.append(
            f'  <text x="{left - 14}" y="{y0 + 24}" font-size="13" fill="{MUTED}" text-anchor="end">{title}</text>'
        )
        out.append(
            f'  <text x="{left - 14}" y="{y0 + 41}" font-size="10" fill="{MUTED}" opacity="0.8" '
            f'text-anchor="end">{sub}</text>'
        )
        for j, (val, color) in enumerate(((naive, ORANGE), (ours, GREEN))):
            yb = y0 + j * (bar_h + 6)
            wbar = max(x(val) - left, 2)
            weight = ' font-weight="600"' if color == GREEN else ""
            out.append(
                f'  <rect x="{left}" y="{yb}" width="{wbar:.1f}" height="{bar_h}" rx="3" fill="{color}"/>'
                f'<text x="{left + wbar + 8:.1f}" y="{yb + 18}" font-size="12"{weight} fill="{color}">{val:.1%}</text>'
            )
    out.append("</svg>\n")
    return "\n".join(out)


# ---------------------------------------------------------------- coverage
def coverage_svg(rows: list[dict[str, Any]], question: dict[str, Any], label: str) -> str:
    cert = certify(rows, question_id="q", question=question, model="m", alpha=ALPHA, delta=0.05)
    pts = curve(rows, question=question, alpha=ALPHA, delta=0.05)
    w, h, left, right, base, top = 860, 380, 85, 830, 300, 80
    step = (right - left) / len(pts)
    bw = step * 0.62

    def y(p: float) -> float:
        return base - (base - top) * p

    out = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">',
        f"  <title>Share of traffic automated at each confidence threshold, {label}</title>",
        f'  <text x="{w / 2}" y="24" font-size="15" font-weight="600" fill="{MUTED}" text-anchor="middle">'
        f"Share of tickets automated at each threshold: {label}</text>",
        f'  <rect x="250" y="40" width="12" height="12" rx="2" fill="{MUTED}" fill-opacity="0.45"/>'
        f'<text x="267" y="50" font-size="12" fill="{MUTED}">error ≤ {ALPHA:g} not provable</text>',
        f'  <rect x="430" y="40" width="12" height="12" rx="2" fill="{GREEN}"/>'
        f'<text x="447" y="50" font-size="12" fill="{MUTED}">certified (error ≤ {ALPHA:g}, 95% confidence)</text>',
        f'  <text x="30" y="{(base + top) / 2}" font-size="12" fill="{MUTED}" text-anchor="middle" '
        f'transform="rotate(-90 30 {(base + top) / 2})">automated</text>',
    ]
    for p in (0, 0.25, 0.5, 0.75, 1.0):
        out.append(
            f'  <line x1="{left}" y1="{y(p):.1f}" x2="{right}" y2="{y(p):.1f}" stroke="{MUTED}" '
            f'stroke-opacity="{0.55 if p == 0 else 0.16}"/>'
            f'<text x="{left - 8}" y="{y(p) + 4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{p:.0%}</text>'
        )
    for i, g in enumerate(pts):
        cx = left + step * (i + 0.5)
        chosen = g.threshold == cert.threshold
        fill = f'fill="{GREEN}"' if g.accepted else f'fill="{MUTED}" fill-opacity="0.45"'
        hgt = base - y(g.coverage)
        out.append(
            f'  <rect x="{cx - bw / 2:.1f}" y="{y(g.coverage):.1f}" width="{bw:.1f}" height="{hgt:.1f}" rx="2" {fill}/>'
        )
        if i % 2 == 0 or chosen:
            out.append(
                f'  <text x="{cx:.1f}" y="{base + 16}" font-size="10" fill="{MUTED}" '
                f'text-anchor="middle">{g.threshold:g}</text>'
            )
        if chosen:
            out.append(
                f'  <text x="{cx:.1f}" y="{y(g.coverage) - 34:.1f}" font-size="12" font-weight="600" fill="{GREEN}" '
                f'text-anchor="middle">t = {g.threshold:g}</text>'
                f'<text x="{cx:.1f}" y="{y(g.coverage) - 20:.1f}" font-size="11" fill="{GREEN}" '
                f'text-anchor="middle">{g.coverage:.0%} automated</text>'
            )
    out.append(
        f'  <text x="{(left + right) / 2}" y="{base + 40}" font-size="12" fill="{MUTED}" text-anchor="middle">'
        f"confidence threshold t (fixed grid)</text>"
    )
    out.append(
        f'  <text x="{(left + right) / 2}" y="{base + 62}" font-size="11" fill="{MUTED}" opacity="0.8" '
        f'text-anchor="middle">{cert.n_calibration} labelled tickets · {cert.errors_automated} errors among the '
        f"{cert.n_automated} automated at t = {cert.threshold}</text>"
    )
    out.append("</svg>\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--calib",
        default=str(ROOT / "examples/jev_vs_laya/laya-answers.jsonl"),
        help="JSONL from `surety collect` for the coverage chart",
    )
    ap.add_argument("--question", default="department")
    a = ap.parse_args()
    ASSETS.mkdir(exist_ok=True)

    flat = violation_rates(lambda c: ALPHA + 0.01, lambda t: ALPHA + 0.01, seed=1)
    over = violation_rates(lambda c: 1.5 * (1 - c), lambda t: 0.75 * (1 - t), seed=2)
    print(f"flat:          naive {flat[0]:.1%}  surety {flat[1]:.1%}")
    print(f"overconfident: naive {over[0]:.1%}  surety {over[1]:.1%}")
    results = [
        ("Slightly too wrong", f"true error {ALPHA + 0.01:g} at every confidence", *flat),
        ("Overconfident model", "true error 1.5 × (1 − confidence)", *over),
    ]
    (ASSETS / "guarantee.svg").write_text(guarantee_svg(results), encoding="utf-8", newline="\n")

    if a.calib:
        questions = json.loads((ROOT / "examples/jev_vs_laya/questions.json").read_text(encoding="utf-8"))
        rows = [json.loads(x) for x in Path(a.calib).read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if r["question_id"] == a.question]
        label = f"Laya english, “{a.question}” (4 teams)"
        (ASSETS / "coverage.svg").write_text(
            coverage_svg(rows, questions[a.question], label), encoding="utf-8", newline="\n"
        )


if __name__ == "__main__":
    main()
