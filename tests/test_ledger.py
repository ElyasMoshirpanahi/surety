from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from surety.ledger import GENESIS, Ledger


def fill(path: Path, n: int = 5) -> Ledger:
    led = Ledger(path)
    for i in range(n):
        led.append({"kind": "decision", "i": i, "text": f"row {i} ünïcode"})
    return led


def test_round_trip_and_chain(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    led = fill(p)
    assert Ledger.verify(p) == (True, 5)
    recs = list(Ledger.records(p))
    assert recs[0]["prev"] == GENESIS
    assert all(b["prev"] == a["hash"] for a, b in zip(recs, recs[1:], strict=False))
    assert Ledger(p).prev == led.prev == recs[-1]["hash"]
    assert Ledger.anchor(p) == (5, recs[-1]["hash"])


def test_empty_ledger(tmp_path: Path) -> None:
    p = tmp_path / "none.jsonl"
    assert Ledger(p).prev == GENESIS
    p.write_text("")
    assert Ledger.verify(p) == (True, 0)
    assert Ledger.anchor(p) == (0, GENESIS)


def test_every_single_byte_edit_is_detected(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    fill(p, 3)
    original = p.read_bytes()
    for i in range(len(original)):
        data = bytearray(original)
        data[i] = ord("0") if data[i] != ord("0") else ord("1")
        p.write_bytes(bytes(data))
        ok, _ = Ledger.verify(p)
        assert not ok, f"edit at byte {i} ({chr(original[i])!r}) went undetected"


def test_deleting_or_reordering_records_is_detected(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    fill(p, 4)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text("".join(lines[:1] + lines[2:]), encoding="utf-8")
    assert Ledger.verify(p) == (False, 1)
    p.write_text("".join([lines[1], lines[0], *lines[2:]]), encoding="utf-8")
    assert not Ledger.verify(p)[0]


def test_anchor_refuses_a_broken_ledger(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    fill(p, 2)
    p.write_text(p.read_text(encoding="utf-8").replace('"i":1', '"i":2'), encoding="utf-8")
    with pytest.raises(ValueError, match="broken"):
        Ledger.anchor(p)


def test_stale_writer_does_not_fork_the_chain(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    a, b = Ledger(p), Ledger(p)  # both start at GENESIS
    a.append({"who": "a"})
    b.append({"who": "b"})  # b's cached prev is stale; the lock + re-read must fix it
    assert Ledger.verify(p) == (True, 2)


def _writer(path: str, who: int, n: int) -> None:
    led = Ledger(path, fsync=False)
    for i in range(n):
        led.append({"who": who, "i": i})


def test_concurrent_processes_keep_the_chain_intact(tmp_path: Path) -> None:
    p = tmp_path / "l.jsonl"
    procs = [mp.get_context("spawn").Process(target=_writer, args=(str(p), w, 40)) for w in range(4)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(60)
        assert pr.exitcode == 0
    assert Ledger.verify(p) == (True, 160)
    assert sorted((r["who"], r["i"]) for r in Ledger.records(p)) == [(w, i) for w in range(4) for i in range(40)]
    assert all(json.loads(line) for line in p.read_text(encoding="utf-8").splitlines())
