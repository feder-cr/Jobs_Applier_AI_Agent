"""What every test shares."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from aihawk import telemetry


class _Inline:
    """A thread that runs its work when it starts, so a test reads the events the moment they are sent."""

    def __init__(self, target: Any, args: tuple[Any, ...] = (), **_: Any) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture(autouse=True)
def no_engine_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test starts the browser's download: the engine is here, unless a test says it is not."""
    from aihawk import session

    monkeypatch.setattr(session.Session, "engine_here", staticmethod(lambda: True))


@pytest.fixture(autouse=True)
def a_computer_the_browser_can_open_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test's browser is a scripted one: on a Linux machine with no desktop, as the ones that run these tests are, the
    check for one would stop every application before it. A test of that check names its own system."""
    from aihawk import apply

    monkeypatch.setattr(apply, "PLATFORM", "a test")


@pytest.fixture(autouse=True)
def no_key_check_over_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key typed in is tried against the service: in a test the service cannot be reached, which never blocks a key; its
    account's credit is read from the billing page, which a test answers with some."""
    from openai import APIConnectionError

    from aihawk import session

    async def unreachable(path: str, cast_to: type) -> dict[str, Any]:
        if path == "/dashboard/billing/subscription":
            # The billing page says what credit is left: by default the account has some, so no test is asked about it.
            return {"object": "billing_subscription", "has_payment_method": False, "soft_limit_usd": 5.0, "hard_limit_usd": 5.0}
        raise APIConnectionError(request=httpx.Request("GET", "https://api.orcarouter.ai/v1" + path))

    monkeypatch.setattr(session.Session, "key_client", lambda self, key: SimpleNamespace(get=unreachable))


@pytest.fixture(autouse=True)
def tracked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """AIHawk's events go to PostHog: in a test they go here, as they are sent, and a test that asks for them reads what was sent."""
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(telemetry, "send", events.append)
    monkeypatch.setattr(telemetry, "threading", SimpleNamespace(Thread=_Inline))
    return events

