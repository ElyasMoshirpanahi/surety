"""Backends: anything that answers POST /v1/systemone-shaped requests.

A backend returns the wire payload: {"model": resolved_model, "answers": {qid: answer}}.
"""

from __future__ import annotations

import json
import math
import os
import random
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from .answers import canon, norm, sha
from .certify import is_alias

DEFAULT_BASE_URL = "https://api.typesafe.ai"
API_KEY_ENV = "TYPESAFE_API_KEY"


@runtime_checkable
class Backend(Protocol):
    model: str

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]: ...


class BackendError(RuntimeError):
    """A failed request. `retryable` is True for 429, 5xx, timeouts and connection errors."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None  # HTTP-date form: fall back to our own backoff


class HttpBackend:
    """Tiny client for any POST /v1/systemone server: hosted Jev, laya-serve, compatible local servers.

    The TYPESAFE_API_KEY environment variable is only ever sent to the default
    host. Pass `api_key` explicitly to authenticate anywhere else.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "jev-1.13.0",
        api_key: str | None = None,
        timeout: float = 15.0,
        auth_header: str = "Authorization",
    ):
        self.base_url, self.model, self.timeout, self.auth_header = base_url.rstrip("/"), model, timeout, auth_header
        if api_key is None and self.base_url == DEFAULT_BASE_URL:
            api_key = os.environ.get(API_KEY_ENV)  # never sent to a non-default host implicitly
        self.api_key = api_key

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self.api_key:
            headers[self.auth_header] = f"Bearer {self.api_key}"
        body = canon({"model": self.model, "state": state, "questions": questions}).encode()
        req = urllib.request.Request(f"{self.base_url}/v1/systemone", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out: dict[str, Any] = json.loads(resp.read())
                return out
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise BackendError(
                f"HTTP {e.code} from {self.base_url}: {detail}",
                status=e.code,
                retry_after=_retry_after(e.headers.get("Retry-After") if e.headers else None),
            ) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BackendError(f"cannot reach {self.base_url}: {e}") from None


SystemOne = HttpBackend  # v0.1 name


class SdkBackend:
    """The official typesafe-sdk client (`pip install surety-gate[sdk]`).

    `model` is required: the SDK otherwise defaults to the moving jev-latest alias.
    """

    def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None, client: Any = None):
        if is_alias(model):
            raise ValueError(f"{model!r} is a moving alias; pin a version such as jev-1.13.0")
        self.model = model
        if client is None:
            try:
                from typesafe_sdk import TypeSafeClient
            except ImportError:
                raise ImportError("SdkBackend needs the official SDK: pip install 'surety-gate[sdk]'") from None
            if base_url not in (None, DEFAULT_BASE_URL) and api_key is None:
                raise ValueError("pass api_key explicitly for a non-default base_url")
            # Always pass base_url: the SDK would otherwise honour TYPESAFE_BASE_URL and could
            # send the environment's key to whatever host that variable names.
            client = TypeSafeClient(api_key=api_key, model=model, base_url=base_url or DEFAULT_BASE_URL)
        self.client = client

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        resp = self.client.system_one(state, dict(questions), model=self.model)
        return {
            "model": resp.model,
            "answers": {k: a.model_dump(mode="json") for k, a in resp.answers.items()},
        }


class LayaBackend:
    """In-process Laya (`pip install surety-gate[laya]`).

    `laya.Router` picks a checkpoint per request unless the model is pinned, so
    the model is required and every response's routing is checked against it.
    """

    def __init__(self, model: str, *, router: Any = None):
        self.model = model
        if router is None:
            try:
                from laya import Router
            except ImportError:
                raise ImportError("LayaBackend needs Laya: pip install 'surety-gate[laya]'") from None
            router = Router()
        self.router = router

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.router.predict(state, dict(questions), model=self.model))
        routing = out.get("routing")
        routed = routing.get("model") if isinstance(routing, Mapping) else None
        if routed is not None and routed != self.model:
            raise BackendError(f"laya routed to {routed!r}, not the pinned {self.model!r}", status=400)
        out.setdefault("model", routed or self.model)
        return out


def _options(question: Mapping[str, Any]) -> list[str]:
    qtype, crit = question.get("type"), question.get("criteria")
    if qtype == "noul":
        return ["true", "false"]
    if qtype == "score":
        return [str(i) for i in range(len(crit or []))]
    if isinstance(crit, Mapping):
        return [norm(k) for k in crit]
    return [norm(k) for k in crit or []]


def simulate_answer(
    question: Mapping[str, Any], truth: Any, rng: random.Random, *, skill: float = 0.85, sharpness: float = 1.0
) -> dict[str, Any]:
    """A synthetic answer whose top probability is informative but not calibrated.

    Right with probability `skill`. Right answers get confidence skewed towards 1
    by `sharpness`; wrong ones get it skewed less, so confidence predicts
    correctness without matching it.
    """
    opts = _options(question)
    if not opts:
        raise ValueError("question has no criteria to choose from")
    t = norm(truth) if truth is not None else rng.choice(opts)
    right = rng.random() < skill or len(opts) == 1
    pick = t if right and t in opts else rng.choice([o for o in opts if o != t] or opts)
    floor = 1.0 / len(opts)
    u = rng.random()
    top = floor + (1 - floor) * (u ** (0.35 / sharpness) if right else u ** (1.2 / sharpness))
    top = min(max(top, floor + 1e-6), 0.9995)
    rest = [o for o in opts if o != pick]
    weights = [rng.random() + 1e-9 for _ in rest]
    probs = {pick: top} | {o: (1 - top) * w / sum(weights) for o, w in zip(rest, weights, strict=True)}
    qtype = question.get("type")
    if qtype == "noul":
        return {"type": "noul", "noul": probs["true"]}
    if qtype == "score":
        return {
            "type": "score",
            "score": math.fsum(int(k) * p for k, p in probs.items()),
            "confidence": top,
            "legend": {str(i): c for i, c in enumerate(question.get("criteria") or [])},
            "probabilities": {o: probs[o] for o in opts},
        }
    return {"type": "choice", "choice": pick, "confidence": top, "probabilities": {o: probs[o] for o in opts}}


class FakeBackend:
    """Deterministic offline backend for tests and demos. Never touches the network.

    Pass `fn(state, questions) -> answers` for full control, or `truth(state) ->
    {qid: label}` to get `simulate_answer` noise around the truth.
    """

    def __init__(
        self,
        model: str = "fake-1.0.0",
        *,
        fn: Callable[[Any, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        truth: Callable[[Any], Mapping[str, Any]] | None = None,
        skill: float = 0.85,
        sharpness: float = 1.0,
        seed: int = 0,
    ):
        self.model, self.fn, self.truth = model, fn, truth
        self.skill, self.sharpness, self.seed = skill, sharpness, seed
        self.calls = 0

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.fn is not None:
            return {"model": self.model, "answers": dict(self.fn(state, questions))}
        truth = self.truth(state) if self.truth else {}
        answers = {}
        for qid, q in questions.items():
            rng = random.Random(sha([self.seed, self.model, qid, state]))
            answers[qid] = simulate_answer(q, truth.get(qid), rng, skill=self.skill, sharpness=self.sharpness)
        return {"model": self.model, "answers": answers}
