"""What every test shares."""

from __future__ import annotations

import pytest

from aihawk import counter


@pytest.fixture(autouse=True)
def counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The applications' count goes to GitHub: in a test it goes here, and a test that asks for it reads what was counted."""
    downloads: list[str] = []
    monkeypatch.setattr(counter, "fetch", downloads.append)
    return downloads
