"""Live tests against a real Laya. Skipped unless pointed at one, so the default run stays offline.

    LAYA_REVISION=reviewed LAYA_MODELS=english laya-serve &         # pip install "laya[serve]"
    SURETY_LAYA_URL=http://127.0.0.1:8000 pytest -m laya
    SURETY_LAYA_INPROCESS=1 ...                                      # also load laya.Router here

CI runs these weekly in the `laya` job of .github/workflows/ci.yml.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from surety import Gate, LayaBackend, LayaServeBackend, Ledger, read_answer
from surety.cli import main

pytestmark = pytest.mark.laya

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "jev_vs_laya"
URL = os.environ.get("SURETY_LAYA_URL")
needs_serve = pytest.mark.skipif(not URL, reason="set SURETY_LAYA_URL to a running laya-serve")


@pytest.fixture(scope="module")
def questions() -> dict[str, Any]:
    q: dict[str, Any] = json.loads((EXAMPLE / "questions.json").read_text(encoding="utf-8"))
    return q


@needs_serve
def test_serve_payload_is_readable_and_pinned(questions: dict[str, Any]) -> None:
    b = LayaServeBackend(str(URL), "english@served")
    name, _, sha = b.model.partition("@")
    assert name == "english" and len(sha) == 40
    out = b.predict({"text": "I was charged twice. Please refund the charge."}, questions)
    assert out["model"] == b.model
    assert out["routing"]["model"] == "english"
    for qid, q in questions.items():
        decision, conf = read_answer(q, out["answers"][qid])
        assert 0.0 < conf <= 1.0 and decision


@needs_serve
def test_cli_round_trip_against_serve(tmp_path: Path, questions: dict[str, Any]) -> None:
    rows = (EXAMPLE / "triage.jsonl").read_text(encoding="utf-8").splitlines()[:30]
    (tmp_path / "d.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    q = str(EXAMPLE / "questions.json")
    base = ["--questions", q]
    collect = ["collect", str(tmp_path / "d.jsonl"), *base, "--backend", "laya-serve", "--base-url", str(URL)]
    assert main([*collect, "--model", "english@served", "-o", str(tmp_path / "c.jsonl")]) == 0
    calib = [json.loads(x) for x in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(calib) == 90 and len({r["model"] for r in calib}) == 1
    assert main(["certify", str(tmp_path / "c.jsonl"), *base, "--alpha", "0.3", "-o", str(tmp_path / "k.json")]) == 0

    gate = Gate.load(tmp_path / "k.json", ledger=Ledger(tmp_path / "l.jsonl"))
    r = calib[0]
    d = gate.decide(r["question_id"], questions[r["question_id"]], r["answer"], model=r["model"])
    assert d.action in ("automate", "review")
    assert main(["verify", str(tmp_path / "l.jsonl")]) == 0


@pytest.mark.skipif(os.environ.get("SURETY_LAYA_INPROCESS") != "1", reason="set SURETY_LAYA_INPROCESS=1")
def test_inprocess_matches_serve_revision(questions: dict[str, Any]) -> None:
    pytest.importorskip("laya")
    b = LayaBackend("english@reviewed")
    out = b.predict({"text": "The app crashes on start, production is down!"}, questions)
    assert out["model"] == b.model and out["server_model"]
    if URL:
        assert LayaServeBackend(URL, "english@served").model == b.model
