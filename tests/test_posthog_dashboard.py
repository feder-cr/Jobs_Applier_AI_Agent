"""The dashboard reads only what AIHawk declares it sends: the events and properties of telemetry.EVENTS."""

from __future__ import annotations

import json

from aihawk.telemetry import EVENTS
from fakes import load_script

dashboard = load_script("scripts", "posthog_dashboard")


def test_every_chart_reads_a_declared_event_and_its_declared_properties() -> None:
    assert "cost" not in json.dumps(dashboard.CHARTS), "no price on the dashboard: tokens only"
    for name, query in dashboard.CHARTS:
        source = query["source"]
        for series in source["series"]:
            assert series["event"] in EVENTS, name
            if "math_property" in series:
                assert series["math_property"] in EVENTS[series["event"]], name
            if "breakdownFilter" in source:
                assert source["breakdownFilter"]["breakdown"] in EVENTS[series["event"]], name
            for condition in source.get("properties", []):
                assert condition["key"] in EVENTS[series["event"]], name
