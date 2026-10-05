"""Certify the same labelled set on hosted Jev and on local laya-serve, then compare coverage.

Offline, with two simulated backends (no network, no keys):

    python examples/jev_vs_laya/run.py --fake

Live (needs TYPESAFE_API_KEY for Jev and a running `laya-serve`):

    python examples/jev_vs_laya/run.py --laya-url http://localhost:8000 --laya-model <pinned laya model>

The data is synthetic (see make_data.py). On your own traffic, use human labels only.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from surety import Backend, Certificate, FakeBackend, HttpBackend, certify_many, fingerprint
from surety.cli import call_with_retry

HERE = Path(__file__).parent


def collect(backend: Backend, rows: list[dict[str, Any]], questions: Mapping[str, Any]) -> list[dict[str, Any]]:
    def one(row: dict[str, Any]) -> list[dict[str, Any]]:
        res = call_with_retry(lambda: backend.predict(row["state"], questions), retries=5)
        return [
            {
                "question_id": qid,
                "question_fp": fingerprint(questions[qid]),
                "slice": None,
                "model": backend.model,
                "resolved_model": res.get("model") or backend.model,
                "answer": res["answers"][qid],
                "label": label,
            }
            for qid, label in row["labels"].items()
        ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [rec for recs in pool.map(one, rows) for rec in recs]


def backends(a: argparse.Namespace, rows: list[dict[str, Any]]) -> list[tuple[str, Backend]]:
    if a.fake:
        truth = {json.dumps(r["state"], sort_keys=True): r["labels"] for r in rows}

        def lookup(state: Any) -> Mapping[str, Any]:
            return truth[json.dumps(state, sort_keys=True)]

        # Two simulated models with different skill and calibration, so thresholds differ.
        return [
            ("Jev (simulated)", FakeBackend("sim-jev-1.13.0", truth=lookup, skill=0.93, sharpness=1.4, seed=1)),
            ("Laya (simulated)", FakeBackend("sim-laya-0.3", truth=lookup, skill=0.9, sharpness=0.9, seed=2)),
        ]
    if not a.laya_model:
        raise SystemExit("--laya-model is required for a live run: pin the exact model laya-serve should use")
    return [
        ("Jev", HttpBackend(model=a.jev_model)),
        ("Laya", HttpBackend(base_url=a.laya_url, model=a.laya_model)),
    ]


def fmt(c: Certificate | None) -> str:
    if c is None:
        return "-"
    if c.threshold is None:
        return f"not certifiable (n={c.n_calibration})"
    return f"t={c.threshold:<5} {c.coverage:6.1%} automated"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="simulated backends, fully offline")
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--delta", type=float, default=0.05)
    ap.add_argument("--jev-model", default="jev-1.13.0")
    ap.add_argument("--laya-url", default="http://localhost:8000")
    ap.add_argument("--laya-model")
    ap.add_argument("--data", default=str(HERE / "triage.jsonl"))
    a = ap.parse_args(argv)

    rows = [json.loads(line) for line in Path(a.data).read_text(encoding="utf-8").splitlines() if line.strip()]
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    results: dict[str, dict[str, Certificate]] = {}
    for name, backend in backends(a, rows):
        calib = collect(backend, rows, questions)
        certs = certify_many(calib, questions, alpha=a.alpha, delta=a.delta, simultaneous=True)
        results[name] = {c.question_id: c for c in certs}

    names = list(results)
    print(f"{len(rows)} synthetic tickets, alpha={a.alpha}, delta={a.delta} (split across all certificates)\n")
    header = f"{'question':12}" + "".join(f"{n:>36}" for n in names)
    print(header)
    print("-" * len(header))
    for qid in questions:
        print(f"{qid:12}" + "".join(f"{fmt(results[n].get(qid)):>36}" for n in names))
    print("\nThresholds differ per model: never copy one model's threshold to another.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
