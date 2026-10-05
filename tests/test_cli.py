from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from surety import Certificate, Gate, Ledger
from surety.cli import main, row_id


@pytest.fixture
def workdir(tmp_path: Path, questions: dict[str, Any]) -> Path:
    (tmp_path / "questions.json").write_text(json.dumps(questions), encoding="utf-8")
    depts = ["billing", "tech", "sales"]
    with (tmp_path / "data.jsonl").open("w", encoding="utf-8") as f:
        for i in range(600):
            labels = {"refund": i % 3 == 0, "department": depts[i % 3], "urgency": i % 4}
            f.write(json.dumps({"state": f"ticket {i}", "labels": labels, "slice": "eu" if i % 2 else "us"}) + "\n")
    return tmp_path


def run(*args: Any) -> int:
    return main([str(a) for a in args])


def test_help_runs_as_a_console_script() -> None:
    out = subprocess.run([sys.executable, "-m", "surety.cli", "--help"], capture_output=True, text=True, check=True)
    assert "certify" in out.stdout and "collect" in out.stdout


def test_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("plan", "--alpha", 0.05) == 0
    assert "122" in capsys.readouterr().out


def test_full_offline_round_trip(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = workdir
    common = ["--questions", w / "questions.json"]
    assert (
        run("collect", w / "data.jsonl", *common, "--backend", "fake", "--model", "fake-1.0.0", "-o", w / "c.jsonl")
        == 0
    )
    rows = [json.loads(x) for x in (w / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1800 and {r["resolved_model"] for r in rows} == {"fake-1.0.0"}

    assert run("certify", w / "c.jsonl", *common, "--alpha", 0.15, "--simultaneous", "-o", w / "certs.json") == 0
    certs = [Certificate(**c) for c in json.loads((w / "certs.json").read_text(encoding="utf-8"))]
    assert len(certs) == 6 and all(c.delta == pytest.approx(0.05 / 6) for c in certs)

    assert run("report", w / "c.jsonl", *common, "--alpha", 0.15, "-o", w / "r.md") == 0
    md = (w / "r.md").read_text(encoding="utf-8")
    assert (
        "Coverage vs threshold" in md
        and "Reliability bins" in md
        and sum(line.startswith("## ") for line in md.splitlines()) == 6
    )

    gate = Gate.load(w / "certs.json", ledger=Ledger(w / "l.jsonl"))
    q = json.loads((w / "questions.json").read_text(encoding="utf-8"))
    gate.decide("department", q["department"], rows[0]["answer"], model="fake-1.0.0", slice=rows[0]["slice"])
    capsys.readouterr()
    assert run("verify", w / "l.jsonl") == 0
    assert run("ledger", "anchor", w / "l.jsonl") == 0
    n, h = capsys.readouterr().out.split()[-2:]
    assert n == "1" and len(h) == 64


def test_collect_resumes_and_repairs_a_torn_line(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = workdir
    args = ["collect", w / "data.jsonl", "--questions", w / "questions.json", "--backend", "fake"]
    args += ["--model", "fake-1.0.0", "-o", w / "c.jsonl"]
    assert run(*args) == 0
    full = (w / "c.jsonl").read_bytes()
    lines = full.splitlines(keepends=True)
    (w / "c.jsonl").write_bytes(b"".join(lines[:300]) + lines[300][:20])  # crash mid-write
    with pytest.raises(SystemExit, match="--resume"):
        run(*args)
    assert run(*args, "--resume") == 0
    assert "already done" in capsys.readouterr().out
    after = (w / "c.jsonl").read_bytes().splitlines()
    assert sorted(after) == sorted(full.splitlines())


def test_collect_refuses_aliases(workdir: Path) -> None:
    w = workdir
    with pytest.raises(SystemExit, match="alias"):
        run("collect", w / "data.jsonl", "--questions", w / "questions.json", "--model", "jev-latest", "-o", w / "x")


def test_certify_reports_mixed_models_cleanly(workdir: Path) -> None:
    w = workdir
    row = {"question_id": "refund", "slice": None, "answer": {"noul": 0.9}, "label": True}
    (w / "mixed.jsonl").write_text(
        json.dumps(row | {"model": "a-1"}) + "\n" + json.dumps(row | {"model": "b-1"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="mix models"):
        run("certify", w / "mixed.jsonl", "--questions", w / "questions.json", "--alpha", 0.1, "-o", w / "c.json")


def test_verify_detects_tampering(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    led.append({"kind": "x", "v": 1})
    led.append({"kind": "x", "v": 2})
    led.path.write_text(led.path.read_text(encoding="utf-8").replace('"v":1', '"v":7'), encoding="utf-8")
    assert run("verify", led.path) == 1
    assert "BROKEN" in capsys.readouterr().out
    assert run("ledger", "anchor", led.path) == 1


def test_row_id_prefers_explicit_id() -> None:
    assert row_id({"id": 7, "state": "a"}) == "7"
    assert row_id({"state": "a", "labels": {}}) == row_id({"labels": {}, "state": "a"})


def test_example_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    import runpy

    root = Path(__file__).resolve().parents[1] / "examples" / "jev_vs_laya"
    mod = runpy.run_path(str(root / "run.py"))
    assert mod["main"](["--fake"]) == 0
    out = capsys.readouterr().out
    assert "Jev (simulated)" in out and "Laya (simulated)" in out and "department" in out


def test_example_dataset_is_marked_synthetic() -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "jev_vs_laya" / "triage.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 400 and all(r["synthetic"] is True for r in rows)
    assert all("@" not in r["state"]["text"] or "@example.com" in r["state"]["text"] for r in rows)
