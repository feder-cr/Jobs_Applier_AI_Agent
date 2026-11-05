"""The data sources doc lists exactly the sources the pipeline reads: no more, no fewer."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_sources() -> set[str]:
    spec = importlib.util.spec_from_file_location("build", ROOT / "pipeline" / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build"] = module
    spec.loader.exec_module(module)
    return {source.name for source in module.SOURCES}


def documented_sources() -> set[str]:
    text = (ROOT / "docs" / "data-sources.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| `([^`]+)` \|", text, flags=re.MULTILINE))


def test_every_source_the_build_reads_is_documented() -> None:
    assert build_sources() - documented_sources() == set()


def test_every_documented_source_is_one_the_build_reads() -> None:
    assert documented_sources() - build_sources() == set()
    assert build_sources() and documented_sources()
