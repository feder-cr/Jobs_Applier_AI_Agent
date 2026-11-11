"""What every test shares."""

from __future__ import annotations

from typing import Any

import pytest

from aihawk import telemetry


@pytest.fixture(autouse=True)
def tracked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """AIHawk's events go to PostHog: in a test they go here, and a test that asks for them reads what was sent."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(telemetry, "send", events.append)
    return events
