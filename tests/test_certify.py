from __future__ import annotations

from typing import Any

import pytest

from surety.answers import fingerprint
from surety.certify import Certificate, certify, certify_many, curve, is_alias

from .conftest import CHOICE, NOUL


def noul_rows(n_right: int, n_wrong: int, p: float = 0.99) -> list[dict[str, Any]]:
    return [{"answer": {"noul": p}, "label": True}] * n_right + [{"answer": {"noul": p}, "label": False}] * n_wrong


def cert(rows: list[dict[str, Any]], **kw: Any) -> Certificate:
    args: dict[str, Any] = {"question_id": "refund", "question": NOUL, "model": "m-1", "alpha": 0.05, "delta": 0.05}
    return certify(rows, **(args | kw))


def test_perfect_rows_certify_at_the_lowest_threshold() -> None:
    c = cert(noul_rows(300, 0))
    assert c.threshold == 0.5 and c.coverage == 1.0 and c.n_automated == 300


def test_too_few_rows_is_not_certifiable() -> None:
    c = cert(noul_rows(50, 0))
    assert c.threshold is None and c.coverage == 0.0


def test_high_error_rate_is_not_certifiable() -> None:
    assert cert(noul_rows(500, 100)).threshold is None


def test_smallest_accepted_threshold_is_chosen() -> None:
    # Errors only at low confidence: the threshold must rise above them.
    rows = noul_rows(400, 0, p=0.97) + noul_rows(100, 100, p=0.6)
    c = cert(rows)
    assert c.threshold is not None and 0.6 < c.threshold <= 0.97
    assert c.errors_automated == 0 and c.n_automated == 400


def test_actionable_set_limits_what_counts() -> None:
    rows = noul_rows(300, 0, p=0.99) + [{"answer": {"noul": 0.01}, "label": False}] * 300
    c = cert(rows, actionable=["yes"])
    assert c.actionable == ["true"] and c.n_automated == 300 and c.coverage == 0.5


def test_id_is_content_hash_and_round_trips() -> None:
    c = cert(noul_rows(300, 0))
    again = Certificate(**c.to_dict())
    assert again.id == c.id
    tampered = c.to_dict() | {"id": "", "threshold": 0.9}
    assert Certificate(**tampered).id != c.id


def test_rejects_rows_from_another_question_definition() -> None:
    rows = [{"answer": {"noul": 0.9}, "label": True, "question_fp": "deadbeefdeadbeef"}]
    with pytest.raises(ValueError, match="different question definition"):
        cert(rows)


def test_bad_parameters() -> None:
    with pytest.raises(ValueError):
        cert(noul_rows(10, 0), alpha=0)
    with pytest.raises(ValueError):
        cert(noul_rows(10, 0), grid=())


def test_curve_agrees_with_certify() -> None:
    rows = noul_rows(400, 0, p=0.97) + noul_rows(100, 100, p=0.6)
    c = cert(rows)
    pts = curve(rows, question=NOUL, alpha=0.05, delta=0.05)
    first = next(p for p in pts if p.accepted)
    assert first.threshold == c.threshold
    assert all((p.upper_bound <= 0.05 + 1e-6) == p.accepted for p in pts if p.n)


def calib(qid: str, model: str, n: int, sl: str | None = None, **extra: Any) -> list[dict[str, Any]]:
    q = NOUL if qid == "refund" else CHOICE
    ans = {"noul": 0.99} if qid == "refund" else {"probabilities": {"billing": 0.99, "tech": 0.01, "sales": 0.0}}
    label = True if qid == "refund" else "billing"
    base = {"question_id": qid, "slice": sl, "model": model, "question_fp": fingerprint(q), "answer": ans}
    return [base | {"label": label} | extra for _ in range(n)]


def test_certify_many_groups_and_splits_delta() -> None:
    qs = {"refund": NOUL, "department": CHOICE}
    rows = calib("refund", "m-1", 300, "eu") + calib("refund", "m-1", 300, "us") + calib("department", "m-1", 300)
    certs = certify_many(rows, qs, alpha=0.05, delta=0.06, simultaneous=True)
    assert [(c.question_id, c.slice) for c in certs] == [("department", None), ("refund", "eu"), ("refund", "us")]
    assert all(c.delta == pytest.approx(0.02) for c in certs)
    assert all(c.threshold == 0.5 for c in certs)
    assert certify_many(rows, qs, alpha=0.05, delta=0.06)[0].delta == 0.06


def test_certify_many_refuses_mixed_models_and_aliases() -> None:
    qs = {"refund": NOUL}
    with pytest.raises(ValueError, match="mix models"):
        certify_many(calib("refund", "a-1", 5) + calib("refund", "b-1", 5), qs, alpha=0.1, delta=0.1)
    with pytest.raises(ValueError, match="moving alias"):
        certify_many(calib("refund", "jev-latest", 5), qs, alpha=0.1, delta=0.1)
    assert certify_many(calib("refund", "jev-latest", 5), qs, alpha=0.1, delta=0.1, allow_alias=True)
    with pytest.raises(ValueError, match="answered as"):
        certify_many(calib("refund", "m-1", 5, resolved_model="m-2"), qs, alpha=0.1, delta=0.1)


@pytest.mark.parametrize(
    ("model", "alias"),
    [("jev-latest", True), ("jev-preview", True), ("jev-1.13.0", False), ("laya-base", False), ("latest", True)],
)
def test_is_alias(model: str, alias: bool) -> None:
    assert is_alias(model) is alias
