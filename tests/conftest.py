"""What every test shares."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aihawk import telemetry


class _Inline:
    """A thread that runs its work when it starts, so a test reads the events the moment they are sent."""

    def __init__(self, target: Any, args: tuple[Any, ...] = (), **_: Any) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture(autouse=True)
def tracked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """AIHawk's events go to PostHog: in a test they go here, as they are sent, and a test that asks for them reads what was sent."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(telemetry, "send", events.append)
    monkeypatch.setattr(telemetry, "threading", SimpleNamespace(Thread=_Inline))
    return events

