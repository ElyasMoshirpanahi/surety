"""The `surety` command line."""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .answers import canon, fingerprint, sha
from .backends import DEFAULT_BASE_URL, Backend, BackendError, FakeBackend, HttpBackend, LayaBackend, SdkBackend
from .certify import Certificate, certify_many, is_alias
from .ledger import Ledger
from .report import render
from .stats import DEFAULT_GRID, min_samples


def _jsonl(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _grid(spec: str | None) -> tuple[float, ...]:
    if not spec:
        return DEFAULT_GRID
    grid = tuple(sorted({float(x) for x in spec.split(",") if x.strip()}))
    if not grid or any(not 0 < t <= 1 for t in grid):
        raise SystemExit("--grid needs comma-separated thresholds in (0, 1]")
    return grid


def _actionable(specs: Sequence[str]) -> dict[str, list[str]]:
    out = {}
    for spec in specs:
        qid, sep, labels = spec.partition("=")
        if not sep or not labels:
            raise SystemExit(f"--actionable expects QID=LABEL[,LABEL...], got {spec!r}")
        out[qid] = labels.split(",")
    return out


# ------------------------------------------------------------------ collect
def row_id(row: Mapping[str, Any]) -> str:
    """Stable id for resume: the row's own `id`, else a hash of its state, labels and slice."""
    if row.get("id") is not None:
        return str(row["id"])
    return sha({"state": row.get("state"), "labels": row.get("labels"), "slice": row.get("slice")})[:16]


def _done_ids(out: Path) -> set[str]:
    """Rows already collected. A torn last line from a crash is cut off so appends stay valid JSONL."""
    if not out.exists():
        return set()
    data = out.read_bytes()
    if data and not data.endswith(b"\n"):
        cut = data.rfind(b"\n") + 1
        with out.open("r+b") as f:
            f.truncate(cut)
        data = data[:cut]
    return {json.loads(line)["row_id"] for line in data.splitlines() if line.strip()}


def call_with_retry(
    fn: Callable[[], dict[str, Any]],
    *,
    retries: int,
    base: float = 0.5,
    cap: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Exponential backoff with full jitter on retryable BackendErrors, honouring Retry-After."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except BackendError as e:
            if not e.retryable or attempt == retries:
                raise
            wait = e.retry_after if e.retry_after is not None else random.uniform(0, min(cap, base * 2**attempt))
            sleep(min(wait, cap))
    raise AssertionError("unreachable")


def make_backend(a: argparse.Namespace, rows: Sequence[Mapping[str, Any]]) -> Backend:
    if a.backend == "http":
        return HttpBackend(a.base_url, a.model, timeout=a.timeout)
    if a.backend == "sdk":
        return SdkBackend(a.model, base_url=None if a.base_url == DEFAULT_BASE_URL else a.base_url)
    if a.backend == "laya":
        return LayaBackend(a.model)
    truth = {sha(r["state"]): r.get("labels", {}) for r in rows}
    return FakeBackend(a.model, truth=lambda s: truth.get(sha(s), {}), seed=a.seed)


def collect(a: argparse.Namespace) -> int:
    if is_alias(a.model) and not a.allow_alias:
        raise SystemExit(f"{a.model!r} is a moving alias; pin a version such as jev-1.13.0 (or --allow-alias)")
    questions = _json(a.questions)
    fps = {qid: fingerprint(q) for qid, q in questions.items()}
    rows = _jsonl(a.data)
    out = Path(a.out)
    done = _done_ids(out) if a.resume else set()
    if not a.resume and out.exists() and out.stat().st_size:
        raise SystemExit(f"{out} exists; pass --resume to continue it or choose another --out")
    todo = [r for r in rows if row_id(r) not in done]
    backend = make_backend(a, rows)
    lock = threading.Lock()
    failures: list[str] = []

    def one(row: dict[str, Any]) -> None:
        res = call_with_retry(lambda: backend.predict(row["state"], questions), retries=a.max_retries)
        resolved = res.get("model") or a.model
        if resolved != a.model and not a.allow_alias:
            raise BackendError(f"asked for {a.model!r} but the server answered as {resolved!r}", status=400)
        lines = []
        for qid, label in row["labels"].items():
            if qid not in questions:
                raise BackendError(f"row labels question {qid!r}, which is not in {a.questions}", status=400)
            rec = {
                "row_id": row_id(row),
                "question_id": qid,
                "question_fp": fps[qid],
                "slice": row.get("slice"),
                "model": a.model,
                "resolved_model": resolved,
                "answer": res["answers"][qid],
                "label": label,
            }
            lines.append(canon(rec) + "\n")
        with lock, out.open("a", encoding="utf-8", newline="\n") as f:
            f.write("".join(lines))  # one write per row keeps resume all-or-nothing per row

    with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as pool:
        futures = {pool.submit(one, r): row_id(r) for r in todo}
        for fut in as_completed(futures):
            err = fut.exception()
            if err is not None:
                failures.append(f"{futures[fut]}: {err}")
    print(f"collected {len(todo) - len(failures)} rows ({len(done)} already done, {len(failures)} failed) -> {out}")
    for f in failures[:10]:
        print(f"  failed {f}", file=sys.stderr)
    if failures:
        print("re-run with --resume to retry the failed rows", file=sys.stderr)
    return 1 if failures else 0


# ------------------------------------------------------------------ certify
def _certs(a: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any], list[Certificate]]:
    questions = _json(a.questions)
    rows = _jsonl(a.calib)
    try:
        certs = certify_many(
            rows,
            questions,
            alpha=a.alpha,
            delta=a.delta,
            simultaneous=a.simultaneous,
            actionable=_actionable(a.actionable),
            grid=_grid(a.grid),
            allow_alias=a.allow_alias,
        )
    except ValueError as e:
        raise SystemExit(str(e)) from None
    return rows, questions, certs


def certify_cmd(a: argparse.Namespace) -> int:
    _, _, certs = _certs(a)
    for c in certs:
        t = "NOT CERTIFIABLE" if c.threshold is None else f"t={c.threshold}"
        print(
            f"{c.question_id:24} {c.slice!s:10} {t:16} automates {c.coverage:6.1%} "
            f"({c.errors_automated}/{c.n_automated} errors in calibration)"
        )
    Path(a.out).write_text(json.dumps([asdict(c) for c in certs], indent=2), encoding="utf-8")
    return 0


def report_cmd(a: argparse.Namespace) -> int:
    rows, questions, certs = _certs(a)
    md = render(rows, questions, certs, grid=_grid(a.grid), bins=a.bins)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(md)
    return 0


# ---------------------------------------------------------------------- main
def _cert_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("calib", help="JSONL written by `surety collect`")
    p.add_argument("--questions", required=True, help="JSON file: the questions dict you send in production")
    p.add_argument("--alpha", type=float, required=True, help="maximum error rate among automated decisions")
    p.add_argument("--delta", type=float, default=0.05, help="allowed failure probability of the guarantee")
    p.add_argument("--actionable", action="append", default=[], metavar="QID=LABEL[,LABEL...]")
    p.add_argument("--simultaneous", action="store_true", help="split delta so all certificates hold at once")
    p.add_argument("--grid", help="comma-separated thresholds; fix them BEFORE looking at the data")
    p.add_argument("--allow-alias", action="store_true", help="certify a moving alias such as jev-latest (unsafe)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="surety", description="Statistical guarantees for System One decisions.")
    ap.add_argument("--version", action="version", version=f"surety {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="how many labelled rows a certificate needs")
    p.add_argument("--alpha", type=float, required=True)
    p.add_argument("--delta", type=float, default=0.05)
    p.add_argument("--grid-size", type=int, default=len(DEFAULT_GRID))

    c = sub.add_parser("collect", help="answer labelled states with a /v1/systemone backend")
    c.add_argument("data", help='JSONL rows: {"state": ..., "labels": {"qid": label}, "slice": optional, "id": optional}')
    c.add_argument("--questions", required=True, help="JSON file: the questions dict you send in production")
    c.add_argument("--model", required=True, help="pin a version such as jev-1.13.0, never an alias")
    c.add_argument("--backend", choices=["http", "sdk", "laya", "fake"], default="http")
    c.add_argument("--base-url", default=DEFAULT_BASE_URL, help="e.g. http://localhost:8000 for laya-serve")
    c.add_argument("--concurrency", type=int, default=4)
    c.add_argument("--max-retries", type=int, default=5, help="retries on 429, 5xx and network errors")
    c.add_argument("--timeout", type=float, default=15.0)
    c.add_argument("--resume", action="store_true", help="skip rows already in --out")
    c.add_argument("--seed", type=int, default=0, help="seed for --backend fake")
    c.add_argument("--allow-alias", action="store_true")
    c.add_argument("-o", "--out", required=True)

    k = sub.add_parser("certify", help="fit certificates from collected answers")
    _cert_args(k)
    k.add_argument("-o", "--out", required=True)

    r = sub.add_parser("report", help="markdown: coverage vs threshold and reliability bins per slice")
    _cert_args(r)
    r.add_argument("--bins", type=int, default=10)
    r.add_argument("-o", "--out", help="write here instead of stdout")

    v = sub.add_parser("verify", help="check a ledger's hash chain")
    v.add_argument("ledger")

    lg = sub.add_parser("ledger", help="ledger utilities")
    lsub = lg.add_subparsers(dest="ledger_cmd", required=True)
    an = lsub.add_parser("anchor", help="verify the chain, then print the record count and tail hash to publish")
    an.add_argument("ledger")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "plan":
        n = min_samples(a.alpha, a.delta, tuple(range(a.grid_size)))
        print(
            f"Need >= {n} automated, human-labelled rows per question and slice even with zero errors; "
            f"budget 2-3x that in practice."
        )
        return 0
    if a.cmd == "collect":
        return collect(a)
    if a.cmd == "certify":
        return certify_cmd(a)
    if a.cmd == "report":
        return report_cmd(a)
    if a.cmd == "verify":
        ok, n = Ledger.verify(a.ledger)
        print(f"{'intact' if ok else 'BROKEN'} after {n} records")
        return 0 if ok else 1
    if a.cmd == "ledger" and a.ledger_cmd == "anchor":
        try:
            n, h = Ledger.anchor(a.ledger)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{n} {h}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
