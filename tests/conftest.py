from __future__ import annotations

import socket
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default test run must never touch the network. Only `laya`-marked live tests may."""
    if request.node.get_closest_marker("laya"):
        return

    def guard(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


NOUL = {"type": "noul", "instructions": "Is this a refund request?"}
CHOICE = {"type": "choice", "criteria": {"billing": "Payments", "tech": "Bugs", "sales": "Buying"}}
SCORE = {"type": "score", "criteria": ["none", "low", "high", "critical"]}


@pytest.fixture
def questions() -> dict[str, dict[str, Any]]:
    return {"refund": NOUL, "department": CHOICE, "urgency": SCORE}
