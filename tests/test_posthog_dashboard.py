"""The dashboard reads what AIHawk sends: every event and property a chart asks for is one telemetry.py's callers send."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("posthog_dashboard", ROOT / "scripts" / "posthog_dashboard.py")
dashboard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["posthog_dashboard"] = dashboard
spec.loader.exec_module(dashboard)  # type: ignore[union-attr]


def test_every_chart_reads_an_event_and_properties_aihawk_sends() -> None:
    sent = "".join((ROOT / "src" / "aihawk" / name).read_text(encoding="utf-8") for name in ("session.py", "tui.py"))
    events = set(re.findall(r'track\([^,]+, "(\w+)"', sent))
    assert {"app_started", "search", "jobs_scored", "application"} <= events
    assert "cost" not in json.dumps(dashboard.CHARTS), "no price on the dashboard: tokens only"
    for _, query in dashboard.CHARTS:
        source = query["source"]
        for series in source["series"]:
            assert series["event"] in events
            if "math_property" in series:
                assert f'{series["math_property"]}=' in sent
        if "breakdownFilter" in source:
            assert f'{source["breakdownFilter"]["breakdown"]}=' in sent
        for condition in source.get("properties", []):
            assert f'{condition["key"]}=' in sent
