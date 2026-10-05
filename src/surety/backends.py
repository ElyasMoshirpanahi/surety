"""Backends: anything that answers POST /v1/systemone-shaped requests.

A backend returns the wire payload: {"model": resolved_model, "answers": {qid: answer}}.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
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


_SHA = re.compile(r"^[0-9a-f]{40}$")


def split_laya_pin(model: str, *, allow_unpinned: bool = False) -> tuple[str, str | None]:
    """ "english@<40-hex commit>" -> ("english", sha). "@reviewed" / "@served" pass through for resolution.

    Laya checkpoint names map to Hugging Face repos whose weights can change, so a
    bare name is a moving alias: it is refused unless `allow_unpinned`.
    """
    name, _, rev = model.partition("@")
    if not name:
        raise ValueError(f"bad Laya model {model!r}; expected <checkpoint>@<commit sha>")
    if not rev:
        if allow_unpinned:
            return name, None
        raise ValueError(
            f"Laya model {model!r} has no revision, so its weights can change under the same name. "
            f"Pin it as {name}@<commit sha>, {name}@reviewed (in-process) or {name}@served (laya-serve)."
        )
    if rev not in ("reviewed", "served") and not _SHA.match(rev):
        raise ValueError(f"Laya revision {rev!r} must be a full 40-character commit SHA")
    return name, rev


def _routed(out: Mapping[str, Any]) -> str | None:
    routing = out.get("routing")
    model = routing.get("model") if isinstance(routing, Mapping) else None
    return str(model) if model is not None else None


def _pinned_payload(out: dict[str, Any], name: str, routed: str | None, rev: str | None, want: str | None) -> None:
    """Check routing and revision, then report the checkpoint pin as the resolved model.

    Laya's own top-level `model` is the same for every checkpoint (e.g. "laya-rl-agent"),
    so it is kept as `server_model` and replaced by "<checkpoint>@<sha>".
    """
    if routed is not None and routed != name:
        raise BackendError(f"laya routed to {routed!r}, not the pinned {name!r}", status=400)
    if want is not None and rev != want:
        raise BackendError(f"laya checkpoint {name!r} is at revision {rev!r}, not the pinned {want!r}", status=400)
    out["server_model"] = out.get("model")
    out["model"] = f"{name}@{rev}" if rev else name


class LayaBackend:
    """In-process Laya (`pip install surety-gate[laya]`).

    Pin the checkpoint and its weights: `LayaBackend("english@<commit sha>")`, or
    `"english@reviewed"` for the SHA Laya publishes in `laya.revisions.PINNED_REVISIONS`.
    `laya.Router` routes per request unless told otherwise, so every response's
    routing and the loaded revision are checked against the pin.
    """

    def __init__(self, model: str, *, router: Any = None, allow_unpinned: bool = False):
        self.name, rev = split_laya_pin(model, allow_unpinned=allow_unpinned)
        if rev == "served":
            raise ValueError("@served is for laya-serve; in-process use a commit SHA or @reviewed")
        if router is None:
            try:
                from laya import Router
            except ImportError:
                raise ImportError("LayaBackend needs Laya: pip install 'surety-gate[laya]'") from None
            if rev == "reviewed":
                rev = _reviewed_revision(self.name)
            router = Router(revisions={self.name: rev} if rev else None, max_loaded=1)
        elif rev == "reviewed":
            rev = _reviewed_revision(self.name)
        self.revision = rev
        self.router = router
        self.model = f"{self.name}@{rev}" if rev else self.name

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.router.predict(state, dict(questions), model=self.name))
        loaded = getattr(self.router, "loaded_revisions", {}) or {}
        _pinned_payload(out, self.name, _routed(out), loaded.get(self.name, self.revision), self.revision)
        return out


def _reviewed_revision(name: str) -> str:
    """The commit SHA Laya itself publishes as reviewed for checkpoint `name`."""
    from laya.revisions import resolve_revision
    from laya.router import DEFAULT_MODELS

    if name not in DEFAULT_MODELS:
        raise ValueError(f"no reviewed revision for Laya checkpoint {name!r}; pin a commit SHA")
    repo = DEFAULT_MODELS[name][0]  # the Router loads every checkpoint from this repo (as subfolders)
    return str(resolve_revision(repo, "reviewed"))


class LayaServeBackend(HttpBackend):
    """laya-serve over HTTP, with the checkpoint and its weights pinned.

    `model="english@<sha>"` checks the routed checkpoint on every response and the
    loaded revision (from `/health`, which is the only place laya-serve reports it)
    at start and every `health_every` seconds. `"english@served"` pins whatever the
    server reports at start, so the certificate records the concrete SHA.
    No API key is sent unless you pass one.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        model: str = "english@served",
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        health_every: float = 60.0,
        allow_unpinned: bool = False,
    ):
        if base_url.rstrip("/") == DEFAULT_BASE_URL:
            raise ValueError("LayaServeBackend is for a self-hosted laya-serve, not the hosted Jev API")
        self.name, rev = split_laya_pin(model, allow_unpinned=allow_unpinned)
        if rev == "reviewed":
            raise ValueError("@reviewed needs the laya package; for laya-serve pin the SHA from /health or use @served")
        super().__init__(base_url, self.name, api_key=api_key, timeout=timeout)
        self.health_every = health_every
        self._checked_at = -math.inf
        if rev == "served":
            rev = self.served_revision()
            if rev is None:
                raise BackendError(f"laya-serve at {self.base_url} reports no revision for {self.name!r}")
            self._checked_at = time.monotonic()
        self.revision = rev
        self.model = f"{self.name}@{rev}" if rev else self.name

    def served_revision(self) -> str | None:
        req = urllib.request.Request(f"{self.base_url}/health", headers={"accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                health = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BackendError(f"cannot read {self.base_url}/health: {e}") from None
        revs = health.get("revisions") or {}
        rev = revs.get(self.name)
        return str(rev) if rev else None

    def predict(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        out = super().predict(state, questions)  # sends model=<checkpoint name>
        rev = self.revision
        if self.revision is not None and time.monotonic() - self._checked_at >= self.health_every:
            rev = self.served_revision()
            self._checked_at = time.monotonic()
        _pinned_payload(out, self.name, _routed(out), rev, self.revision)
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
