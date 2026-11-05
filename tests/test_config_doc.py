"""The configuration doc lists exactly the keys the config reads: no more, no fewer."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from aihawk.config import Config, Filters

ROOT = Path(__file__).resolve().parents[1]


def read_keys() -> set[str]:
    """The keys aihawk.yaml feeds, from the code: each Config field, and each Filters field under `filters.`. The folder is not a key."""
    keys = {field.name for field in fields(Config) if field.name not in ("workspace", "filters")}
    return keys | {f"filters.{field.name}" for field in fields(Filters)}


def documented_keys() -> set[str]:
    text = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| `([^`]+)` \|", text, flags=re.MULTILINE))


def test_every_key_the_config_reads_is_documented() -> None:
    assert read_keys() - documented_keys() == set()


def test_every_documented_key_is_one_the_config_reads() -> None:
    assert documented_keys() - read_keys() == set()
    assert read_keys() and documented_keys()
