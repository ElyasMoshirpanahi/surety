"""Runtime gate: AUTOMATE only under a current, matching certificate; everything else goes to REVIEW."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .answers import fingerprint, norm, read_answer, sha
from .certify import Certificate
from .ledger import Ledger
from .stats import DriftMonitor

AUTOMATE, REVIEW = "automate", "review"


@dataclass
class Decision:
    question_id: str
    slice: str | None
    decision: str
    confidence: float
    action: str
    reason: str
    certificate_id: str | None
    record_hash: str | None = None


class Gate:
    """AUTOMATE only under a current, matching certificate; everything else goes to REVIEW.

    With a ledger, the drift monitors are rebuilt from its audit records when the
    gate is created, so a suspension survives restarts.
    """

    def __init__(self, certificates: Iterable[Certificate], ledger: Ledger | None = None, arl: float = 1000.0):
        self.certs = {(c.question_id, c.slice): c for c in certificates}
        self.ledger = ledger
        self.monitors = {k: DriftMonitor(c.alpha, arl) for k, c in self.certs.items()}
        self.suspended: set[str] = set()
        if ledger is not None:
            self._replay(ledger)

    @classmethod
    def load(cls, path: str | Path, **kw: Any) -> Gate:
        return cls([Certificate(**c) for c in json.loads(Path(path).read_text(encoding="utf-8"))], **kw)

    def _replay(self, ledger: Ledger) -> None:
        by_id = {c.id: k for k, c in self.certs.items()}
        decisions: dict[str, str | None] = {}
        for rec in Ledger.records(ledger.path):
            kind = rec.get("kind")
            if kind == "decision":
                decisions[rec["hash"]] = rec.get("certificate_id")
            elif kind == "audit":
                cid = rec.get("certificate_id") or decisions.get(rec.get("decision_hash") or "")
                key = by_id.get(cid or "")
                if key is not None and self.monitors[key].update(bool(rec["error"])):
                    self.suspended.add(self.certs[key].id)
            elif kind == "suspend" and rec.get("certificate_id") in by_id:
                self.suspended.add(rec["certificate_id"])

    def decide(
        self,
        question_id: str,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        *,
        model: str,
        slice: str | None = None,
        state: Any = None,
    ) -> Decision:
        c = self.certs.get((question_id, slice))
        why: str | None
        try:
            dec, conf = read_answer(question, answer)
            unreadable = None
        except (ValueError, KeyError, TypeError) as e:
            dec, conf, unreadable = "", 0.0, f"unreadable answer: {e}"
        if unreadable is not None:
            why = unreadable
        elif c is None:
            why = "no certificate for this question and slice"
        elif c.question_fp != fingerprint(question):
            why = "question definition changed since certification"
        elif c.model != model:
            why = f"model {model!r} is not the certified {c.model!r}"
        elif c.threshold is None:
            why = "not certifiable at this alpha with the labelled data provided"
        elif c.id in self.suspended:
            why = "certificate suspended by the drift monitor"
        elif c.actionable is not None and dec not in c.actionable:
            why = "decision is outside the actionable set"
        elif conf < c.threshold:
            why = f"confidence {conf:.3f} is below the certified {c.threshold}"
        else:
            why = None
        if why is None and c is not None:
            action, reason = AUTOMATE, f"certified: error <= {c.alpha} with prob >= {1 - c.delta:g}"
        else:
            action, reason = REVIEW, why or "fail closed"
        d = Decision(question_id, slice, dec, conf, action, reason, c.id if c else None)
        if self.ledger:
            body = {k: v for k, v in asdict(d).items() if k != "record_hash"}
            rec = self.ledger.append(
                {
                    "kind": "decision",
                    **body,
                    "model": model,
                    "question_fp": fingerprint(question),
                    "state_sha256": None if state is None else sha(state),
                }
            )
            d.record_hash = rec["hash"]
        return d

    def audit(self, d: Decision, label: Any) -> bool:
        """Record the true label of a randomly sampled AUTOMATED decision.

        Returns True when the drift alarm fires; the certificate is then
        suspended and later decisions on it go to REVIEW until you re-certify.
        """
        if d.action != AUTOMATE:
            raise ValueError("audit a random sample of AUTOMATED decisions only")
        key = (d.question_id, d.slice)
        cert = self.certs.get(key)
        if cert is None or cert.id != d.certificate_id:
            raise ValueError("this decision was made under a certificate the gate no longer holds")
        mon = self.monitors[key]
        error = d.decision != norm(label)
        fired = mon.update(error)
        newly = fired and cert.id not in self.suspended
        if fired:
            self.suspended.add(cert.id)
        if self.ledger:
            self.ledger.append(
                {
                    "kind": "audit",
                    "decision_hash": d.record_hash,
                    "certificate_id": cert.id,
                    "question_id": d.question_id,
                    "slice": d.slice,
                    "error": error,
                    "label": norm(label),
                    "monitor": round(mon.statistic, 4),
                    "alarm": fired,
                }
            )
            if newly:
                self.ledger.append(
                    {
                        "kind": "suspend",
                        "certificate_id": cert.id,
                        "question_id": d.question_id,
                        "slice": d.slice,
                        "audits": mon.audits,
                        "errors": mon.errors,
                        "monitor": round(mon.statistic, 4),
                        "arl": mon.arl,
                    }
                )
        return fired
