from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from surety import AUTOMATE, REVIEW, Certificate, Gate, Ledger, fingerprint

from .conftest import CHOICE, NOUL

MODEL = "jev-1.13.0"


def make_cert(question: dict[str, Any] = NOUL, **kw: Any) -> Certificate:
    base: dict[str, Any] = {
        "question_id": "q",
        "question_fp": fingerprint(question),
        "model": MODEL,
        "slice": None,
        "alpha": 0.05,
        "delta": 0.05,
        "threshold": 0.9,
        "actionable": None,
        "n_calibration": 1000,
        "n_automated": 800,
        "errors_automated": 10,
        "coverage": 0.8,
    }
    return Certificate(**(base | kw))


def decide(gate: Gate, answer: dict[str, Any], **kw: Any) -> Any:
    args: dict[str, Any] = {"model": MODEL, "slice": None}
    q = kw.pop("question", NOUL)
    return gate.decide(kw.pop("question_id", "q"), q, answer, **(args | kw))


def test_automates_under_a_matching_certificate() -> None:
    d = decide(Gate([make_cert()]), {"noul": 0.97})
    assert d.action == AUTOMATE and d.decision == "true" and "certified" in d.reason


# Every fail-closed branch of Gate.decide, one case each.
@pytest.mark.parametrize(
    ("cert_kw", "call_kw", "answer", "reason"),
    [
        ({}, {}, {"noul": float("nan")}, "unreadable answer"),
        ({}, {}, {}, "unreadable answer"),
        ({}, {"question_id": "other"}, {"noul": 0.99}, "no certificate"),
        ({}, {"slice": "eu"}, {"noul": 0.99}, "no certificate"),
        ({}, {"question": NOUL | {"instructions": "changed"}}, {"noul": 0.99}, "question definition changed"),
        ({}, {"model": "jev-latest"}, {"noul": 0.99}, "is not the certified"),
        ({"threshold": None}, {}, {"noul": 0.99}, "not certifiable"),
        ({"actionable": ["false"]}, {}, {"noul": 0.99}, "outside the actionable set"),
        ({}, {}, {"noul": 0.85}, "below the certified"),
    ],
)
def test_fails_closed(cert_kw: dict[str, Any], call_kw: dict[str, Any], answer: dict[str, Any], reason: str) -> None:
    d = decide(Gate([make_cert(**cert_kw)]), answer, **call_kw)
    assert d.action == REVIEW
    assert reason in d.reason


def test_fails_closed_when_suspended() -> None:
    c = make_cert()
    gate = Gate([c])
    gate.suspended.add(c.id)
    d = decide(gate, {"noul": 0.99})
    assert d.action == REVIEW and "suspended" in d.reason


def test_audit_only_automated_and_current() -> None:
    gate = Gate([make_cert()])
    with pytest.raises(ValueError, match="AUTOMATED"):
        gate.audit(decide(gate, {"noul": 0.6}), True)
    d = decide(gate, {"noul": 0.99})
    other = Gate([make_cert(threshold=0.95)])
    with pytest.raises(ValueError, match="no longer holds"):
        other.audit(d, True)


def test_ledger_records_decisions_and_hashes_state(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    d = decide(Gate([make_cert()], ledger=led), {"noul": 0.99}, state={"text": "secret customer text"})
    recs = list(Ledger.records(led.path))
    assert recs[0]["kind"] == "decision" and recs[0]["hash"] == d.record_hash
    assert "secret" not in led.path.read_text(encoding="utf-8")
    assert len(recs[0]["state_sha256"]) == 64


def test_suspension_survives_a_restart(tmp_path: Path) -> None:
    c = make_cert()
    certs = tmp_path / "certs.json"
    certs.write_text(json.dumps([asdict(c)]), encoding="utf-8")
    led_path = tmp_path / "l.jsonl"

    gate = Gate.load(certs, ledger=Ledger(led_path), arl=20)
    fired = False
    while not fired:
        fired = gate.audit(decide(gate, {"noul": 0.99}), False)  # every audited decision is wrong
    assert decide(gate, {"noul": 0.99}).action == REVIEW
    assert [r["kind"] for r in Ledger.records(led_path)].count("suspend") == 1

    reborn = Gate.load(certs, ledger=Ledger(led_path), arl=20)
    assert c.id in reborn.suspended
    assert decide(reborn, {"noul": 0.99}).action == REVIEW
    assert reborn.monitors[("q", None)].audits == gate.monitors[("q", None)].audits

    # The suspend record alone is enough, even if the ARL is later raised.
    assert c.id in Gate.load(certs, ledger=Ledger(led_path), arl=1e12).suspended
    # A fresh certificate starts with a fresh monitor.
    assert not Gate([make_cert(threshold=0.95)], ledger=Ledger(led_path)).suspended


def test_replay_resolves_v01_audit_records(tmp_path: Path) -> None:
    """v0.1 audit records carry only decision_hash; replay finds the certificate through it."""
    c = make_cert(question=CHOICE)
    led = Ledger(tmp_path / "l.jsonl")
    dec = led.append({"kind": "decision", "certificate_id": c.id, "question_id": "q", "slice": None})
    for _ in range(40):
        led.append({"kind": "audit", "decision_hash": dec["hash"], "error": True})
    assert c.id in Gate([c], ledger=Ledger(led.path), arl=20).suspended
