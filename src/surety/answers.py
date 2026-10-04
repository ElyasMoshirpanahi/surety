"""Reading typed answers, and the hashing every other module agrees on.

Answer shapes, verified against typesafe-sdk 0.7.2 (`_schemas/models.py`) and
laya 0.3.27 (`serve.py`), which emit the same wire format:

    noul    {"type": "noul", "noul": P(true)}
    choice  {"type": "choice", "choice": name, "confidence": c, "probabilities": {name: p}}
    score   {"type": "score", "score": E[level], "confidence": c,
             "legend": {"0": criterion, ...}, "probabilities": {"0": p, ...}}

For `score`, the `score` field is the probability-weighted mean level and can
fall between levels. It is never the decision. The decision is the argmax level.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def canon(obj: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace. Every hash in surety is over this."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def fingerprint(obj: Any) -> str:
    """Short stable hash of a question definition: any wording change voids its certificate."""
    return sha(obj)[:16]


_BOOLS = {"true": "true", "yes": "true", "false": "false", "no": "false"}


def norm(x: Any) -> str:
    """Normalize a decision or label so `True`, "yes" and "true" compare equal, and 2 == "2"."""
    if isinstance(x, bool):
        return "true" if x else "false"
    s = str(x).strip()
    return _BOOLS.get(s.lower(), s)


def _prob(value: Any, where: str) -> float:
    try:
        p = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where} is not a number: {value!r}") from None
    if not (math.isfinite(p) and 0.0 <= p <= 1.0):
        # NaN would otherwise compare False against every threshold and slip through the gate.
        raise ValueError(f"{where} must be a probability in [0, 1], got {value!r}")
    return p


def read_answer(question: Mapping[str, Any], answer: Mapping[str, Any]) -> tuple[str, float]:
    """(decision, confidence) from a Jev- or Laya-shaped answer.

    The decision is the argmax option (exact match is what gets certified).
    Confidence is the probability of that decision. Jev and Laya define their
    own `confidence` fields differently, so neither is used here.

    Raises ValueError on any answer it cannot read.
    """
    if not isinstance(answer, Mapping):
        raise ValueError(f"answer must be an object, got {type(answer).__name__}")
    if question.get("type") == "noul":
        if "noul" not in answer:
            raise ValueError("noul answers need a 'noul' field holding P(true)")
        p = _prob(answer["noul"], "noul")
        return ("true" if p >= 0.5 else "false"), max(p, 1.0 - p)
    probs: Any = answer.get("probabilities")
    if isinstance(probs, list):
        probs = {str(i): p for i, p in enumerate(probs)}
    if not isinstance(probs, Mapping) or not probs:
        raise ValueError(f"{question.get('type', 'choice')} answers need a non-empty 'probabilities' map")
    clean = {norm(k): _prob(v, f"probabilities[{k!r}]") for k, v in probs.items()}
    top = max(clean, key=lambda k: clean[k])
    return top, clean[top]
