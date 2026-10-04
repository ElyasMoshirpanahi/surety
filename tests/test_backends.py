from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest

from surety.answers import read_answer
from surety.backends import Backend, BackendError, FakeBackend, HttpBackend, LayaBackend, SdkBackend
from surety.cli import call_with_retry

from .conftest import CHOICE, NOUL, SCORE


class Recorder:
    def __init__(self, responses: list[Any]):
        self.responses, self.requests = responses, []

    def __call__(self, req: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps(r).encode())


@pytest.fixture
def urlopen(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder([{"model": "jev-1.13.0", "answers": {}}] * 3)
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    return rec


def test_env_key_goes_only_to_the_default_host(monkeypatch: pytest.MonkeyPatch, urlopen: Recorder) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-secret")
    HttpBackend().predict("hi", {"q": NOUL})
    assert urlopen.requests[0].get_header("Authorization") == "Bearer sk-secret"
    assert urlopen.requests[0].full_url == "https://api.typesafe.ai/v1/systemone"

    HttpBackend(base_url="http://localhost:8000/").predict("hi", {"q": NOUL})
    assert urlopen.requests[1].get_header("Authorization") is None
    assert urlopen.requests[1].full_url == "http://localhost:8000/v1/systemone"

    HttpBackend(base_url="http://localhost:8000", api_key="local").predict("hi", {"q": NOUL})
    assert urlopen.requests[2].get_header("Authorization") == "Bearer local"


def test_request_body_is_the_wire_protocol(urlopen: Recorder) -> None:
    HttpBackend(model="jev-1.13.0").predict({"text": "x"}, {"q": NOUL})
    body = json.loads(urlopen.requests[0].data)
    assert body == {"model": "jev-1.13.0", "state": {"text": "x"}, "questions": {"q": NOUL}}


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = Message()
    if retry_after:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "x", hdrs, io.BytesIO(b"boom"))


@pytest.mark.parametrize(("code", "retryable"), [(429, True), (500, True), (503, True), (400, False), (401, False)])
def test_http_errors_are_classified(monkeypatch: pytest.MonkeyPatch, code: int, retryable: bool) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", Recorder([http_error(code, "2")]))
    with pytest.raises(BackendError) as e:
        HttpBackend().predict("x", {})
    assert e.value.status == code and e.value.retryable is retryable and e.value.retry_after == 2.0


def test_retry_backs_off_then_succeeds() -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def flaky() -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) < 3:
            raise BackendError("busy", status=429, retry_after=1.5 if len(attempts) == 1 else None)
        return {"ok": True}

    assert call_with_retry(flaky, retries=5, sleep=sleeps.append) == {"ok": True}
    assert len(attempts) == 3 and sleeps[0] == 1.5 and 0 <= sleeps[1] <= 1.0


def test_retry_gives_up_on_client_errors_and_after_budget() -> None:
    def bad() -> dict[str, Any]:
        raise BackendError("nope", status=400)

    with pytest.raises(BackendError):
        call_with_retry(bad, retries=5, sleep=lambda s: pytest.fail("must not retry a 400"))
    calls: list[int] = []

    def down() -> dict[str, Any]:
        calls.append(1)
        raise BackendError("down", status=503)

    with pytest.raises(BackendError):
        call_with_retry(down, retries=2, sleep=lambda s: None)
    assert len(calls) == 3


def test_fake_backend_is_deterministic_and_readable() -> None:
    qs = {"refund": NOUL, "department": CHOICE, "urgency": SCORE}
    a = FakeBackend(truth=lambda s: {"refund": True, "department": "tech", "urgency": 2})
    r1, r2 = a.predict("t", qs), a.predict("t", qs)
    assert r1 == r2 and r1["model"] == "fake-1.0.0"
    for qid, q in qs.items():
        dec, conf = read_answer(q, r1["answers"][qid])
        assert 0 < conf <= 1
    assert FakeBackend(seed=1).predict("t", qs) != FakeBackend(seed=2).predict("t", qs)
    assert isinstance(a, Backend)


def test_fake_backend_confidence_is_informative() -> None:
    q = {"d": CHOICE}
    fb = FakeBackend(truth=lambda s: {"d": "billing"}, skill=0.8)
    pts = [read_answer(CHOICE, fb.predict(i, q)["answers"]["d"]) for i in range(3000)]
    hi = [d == "billing" for d, c in pts if c >= 0.9]
    lo = [d == "billing" for d, c in pts if c < 0.6]
    assert sum(hi) / len(hi) > sum(lo) / len(lo) + 0.2


def test_sdk_backend_returns_wire_dicts() -> None:
    class Ans:
        def __init__(self, d: dict[str, Any]):
            self.d = d

        def model_dump(self, mode: str) -> dict[str, Any]:
            return self.d

    class Client:
        def system_one(self, state: Any, questions: Any, *, model: str) -> Any:
            return SimpleNamespace(model=model, answers={"q": Ans({"type": "noul", "noul": 0.9})})

    b = SdkBackend("jev-1.13.0", client=Client())
    assert b.predict("x", {"q": NOUL}) == {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}}}
    with pytest.raises(ValueError, match="alias"):
        SdkBackend("jev-latest", client=Client())


def test_laya_backend_checks_routing() -> None:
    class Router:
        def __init__(self, routed: str):
            self.routed = routed

        def predict(self, state: Any, questions: Any, model: str | None = None) -> dict[str, Any]:
            return {"answers": {"q": {"noul": 0.7}}, "routing": {"model": self.routed}}

    assert LayaBackend("laya-base", router=Router("laya-base")).predict("x", {})["model"] == "laya-base"
    with pytest.raises(BackendError, match="routed"):
        LayaBackend("laya-base", router=Router("laya-large")).predict("x", {})
