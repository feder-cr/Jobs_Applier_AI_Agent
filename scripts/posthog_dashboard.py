#!/usr/bin/env python3
"""Makes, or makes again, the "AIHawk" dashboard in PostHog: the charts that say how AIHawk is used and where it stops.

    POSTHOG_DASHBOARD_KEY=... python scripts/posthog_dashboard.py
    python scripts/posthog_dashboard.py --dry-run     # prints what it would send

Run by hand from .github/workflows/posthog-dashboard.yml, with a personal key that can write dashboards and insights,
kept as the repository's secret POSTHOG_DASHBOARD_KEY: nobody but GitHub sees it. A dashboard of the same name is
replaced, so running it again after a change here leaves one dashboard, as this file says. The events are the ones
src/aihawk/telemetry.py sends.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Any

API = "https://us.posthog.com/api/projects/728626"
NAME = "AIHawk"


def trends(event: str, *, math: str = "total", math_property: str | None = None, breakdown: str | None = None,
           display: str = "ActionsLineGraph", date_from: str = "-30d", where: dict[str, Any] | None = None,
           sums: tuple[str, ...] = ()) -> dict[str, Any]:
    """A trends query over one event, as PostHog's insight API takes it: one series, or with `sums` one series summing
    each of those properties."""
    series: dict[str, Any] = {"kind": "EventsNode", "event": event, "name": event, "math": math}
    if math_property:
        series["math_property"] = math_property
    every = [{"kind": "EventsNode", "event": event, "name": name, "math": "sum", "math_property": name} for name in sums] or [series]
    source: dict[str, Any] = {
        "kind": "TrendsQuery",
        "series": every,
        "interval": "day",
        "dateRange": {"date_from": date_from},
        "trendsFilter": {"display": display},
    }
    if breakdown:
        source["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "event"}
    if where:
        source["properties"] = [{"key": key, "value": [value], "operator": "exact", "type": "event"} for key, value in where.items()]
    return {"kind": "InsightVizNode", "source": source}


CHARTS = [
    ("Applications a day, by how they ended", trends("application", breakdown="outcome", display="ActionsBar")),
    ("Why applications are blocked", trends("application", breakdown="reason", display="ActionsPie", date_from="-7d", where={"outcome": "blocked"})),
    ("Applications by site and outcome", trends("application", breakdown="site", display="ActionsTable", date_from="-7d")),
    ("Tokens a day, read and written, by forms model", trends("application", sums=("input_tokens", "output_tokens"), breakdown="forms_model", display="ActionsBar")),
    ("Computers that opened AIHawk", trends("app_started", math="dau")),
]


def call(key: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read()
        return json.loads(text) if text else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    if parser.parse_args().dry_run:
        print(json.dumps([{"name": name, "query": query} for name, query in CHARTS], indent=1))
        return 0
    key = os.environ.get("POSTHOG_DASHBOARD_KEY")
    if not key:
        parser.error("POSTHOG_DASHBOARD_KEY is not set")
    # The dashboard of this name, if one was made before, goes: what this file says is the one dashboard.
    for old in call(key, "GET", "/dashboards/?limit=200")["results"]:
        if old.get("name") == NAME and not old.get("deleted"):
            call(key, "PATCH", f"/dashboards/{old['id']}/", {"deleted": True})
            print(f"replaced dashboard {old['id']}")
    dashboard = call(key, "POST", "/dashboards/", {"name": NAME, "description": "How AIHawk is used, and where it stops: the events of src/aihawk/telemetry.py."})
    for name, query in CHARTS:
        call(key, "POST", "/insights/", {"name": name, "query": query, "dashboards": [dashboard["id"]]})
        print(f"chart: {name}")
    print(f"dashboard: https://us.posthog.com/project/728626/dashboard/{dashboard['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
