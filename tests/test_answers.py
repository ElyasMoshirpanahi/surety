"""Answer fixtures copy the wire shapes in typesafe-sdk 0.7.2 `_schemas/models.py`
(ChoiceAnswer, ScoreAnswer, NoulAnswer) and laya 0.3.27 `serve.py`."""

from __future__ import annotations

import math
from typing import Any

import pytest

from surety.answers import canon, fingerprint, norm, read_answer

from .conftest import CHOICE, NOUL, SCORE

JEV_NOUL = {"type": "noul", "noul": 0.98}
JEV_CHOICE = {
    "type": "choice",
    "choice": "tech",
    "confidence": 0.42,  # vendor confidence: must be ignored
    "probabilities": {"billing": 0.1, "tech": 0.7, "sales": 0.2},
}
JEV_SCORE = {
    "type": "score",
    "score": 1.6,  # expected level, NOT the decision
    "confidence": 0.3,
    "legend": {"0": "none", "1": "low", "2": "high", "3": "critical"},
    "probabilities": {"0": 0.05, "1": 0.3, "2": 0.6, "3": 0.05},
}


@pytest.mark.parametrize(("p", "dec", "conf"), [(0.98, "true", 0.98), (0.1, "false", 0.9), (0.5, "true", 0.5)])
def test_noul(p: float, dec: str, conf: float) -> None:
    assert read_answer(NOUL, {"type": "noul", "noul": p}) == (dec, pytest.approx(conf))


def test_choice_ignores_vendor_confidence() -> None:
    assert read_answer(CHOICE, JEV_CHOICE) == ("tech", 0.7)


def test_score_uses_argmax_not_expected_score() -> None:
    assert read_answer(SCORE, JEV_SCORE) == ("2", 0.6)


def test_score_with_int_keys_from_the_sdk() -> None:
    ans = {**JEV_SCORE, "probabilities": {0: 0.05, 1: 0.3, 2: 0.6, 3: 0.05}}
    assert read_answer(SCORE, ans) == ("2", 0.6)


@pytest.mark.parametrize("q", [CHOICE, SCORE])
def test_list_shaped_probabilities(q: dict[str, Any]) -> None:
    assert read_answer(q, {"probabilities": [0.1, 0.2, 0.6, 0.1]}) == ("2", 0.6)


def test_label_normalisation_matches_decisions() -> None:
    assert norm(True) == norm("yes") == norm(" TRUE ") == "true"
    assert norm(False) == norm("No") == "false"
    assert norm(2) == "2"


@pytest.mark.parametrize(
    ("q", "ans"),
    [
        (NOUL, {"type": "noul"}),
        (NOUL, {"noul": math.nan}),
        (NOUL, {"noul": 1.2}),
        (NOUL, {"noul": "high"}),
        (CHOICE, {"probabilities": {}}),
        (CHOICE, {"choice": "tech"}),
        (CHOICE, {"probabilities": {"tech": math.nan, "sales": 0.1}}),
        (SCORE, {"probabilities": "0.5"}),
        (SCORE, "not an object"),
    ],
)
def test_malformed_answers_raise(q: dict[str, Any], ans: Any) -> None:
    with pytest.raises(ValueError):
        read_answer(q, ans)


def test_fingerprint_is_stable_and_order_independent() -> None:
    a = {"type": "choice", "criteria": {"x": "1", "y": "2"}, "instructions": "pick"}
    b = {"instructions": "pick", "criteria": {"y": "2", "x": "1"}, "type": "choice"}
    assert fingerprint(a) == fingerprint(b)
    assert len(fingerprint(a)) == 16
    # Golden value: if this changes, every certificate in the wild is voided.
    assert fingerprint(a) == fingerprint(json_roundtrip(a))
    assert fingerprint({"type": "noul"}) == "10baf7ded3b4cc04"


def test_fingerprint_changes_with_wording() -> None:
    a = {"type": "noul", "instructions": "Is this a refund request?"}
    b = {"type": "noul", "instructions": "Is this a refund request ?"}
    assert fingerprint(a) != fingerprint(b)


def json_roundtrip(x: Any) -> Any:
    import json

    return json.loads(canon(x))
