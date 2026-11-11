"""The dashboard and the counters read only what AIHawk declares it sends: the events and properties of telemetry.EVENTS."""

from __future__ import annotations

import json
import re

from aihawk.telemetry import EVENTS
from fakes import load_script

dashboard = load_script("scripts", "posthog_dashboard")
counters = load_script("scripts", "usage_counters")


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


def test_the_counters_ask_only_for_declared_events_and_properties() -> None:
    assert set(counters.EVENTS) <= set(EVENTS)
    asked: list[str] = []

    def query(hogql: str) -> list[list[object]]:
        asked.append(hogql)
        return [[1]] if "DISTINCT" in hogql else []

    counters.collect(query)
    properties = {name for hogql in asked for name in re.findall(r"properties\.(\w+)", hogql)}
    assert properties and properties <= set(EVENTS["application"])
