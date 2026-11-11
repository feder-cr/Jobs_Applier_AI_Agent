#!/usr/bin/env python3
"""Daily snapshot of the applications AIHawk has sent, for all its users together, and the README badge drawn from it.

    python scripts/usage_counters.py --history H.csv --daily D.csv --badges DIR

Run once a day by .github/workflows/counters.yml, which keeps the files on the unprotected `badges` branch: no person and
no machine of ours, every output a file on that branch. The way invisible_playwright records its browser launches.

WHAT IS COUNTED. AIHawk sends an `application` event to PostHog for each application (src/aihawk/telemetry.py), read
here with the project's read key, POSTHOG_API_KEY, a secret of the repository. PostHog alone counts. A day's applications
are the difference between two daily totals, and a day nobody recorded is lost for good.

`sent` is what the badge shows: the applications whose send was clicked, confirmed by the page or not.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

OUTCOMES = ("submitted", "unconfirmed", "refused", "blocked", "failed")
SENT = ("submitted", "unconfirmed")

POSTHOG_QUERY = "https://us.posthog.com/api/projects/728626/query/"
# AIHawk's own events: the project also holds a check made when it was set up, which is no computer of anyone's.
EVENTS = ("app_started", "search", "jobs_scored", "application")

# A HogQL query's rows.
Query = Callable[[str], list[list[object]]]


def posthog_query(key: str) -> Query:
    def query(hogql: str) -> list[list[object]]:
        body = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode("utf-8")
        request = urllib.request.Request(POSTHOG_QUERY, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)["results"]

    return query


def collect(query: Query) -> dict[str, int]:
    """Each outcome's total in PostHog, the ones sent, all of them, and the computers AIHawk has run on."""
    events = {str(outcome): int(count) for outcome, count in query("SELECT properties.outcome, count() FROM events WHERE event = 'application' GROUP BY 1")}  # type: ignore[call-overload]
    out = {outcome: events.get(outcome, 0) for outcome in OUTCOMES}
    out["sent"] = sum(out[outcome] for outcome in SENT)
    out["applications"] = sum(out[outcome] for outcome in OUTCOMES)
    out["computers"] = int(query(f"SELECT count(DISTINCT distinct_id) FROM events WHERE event IN {EVENTS!r}")[0][0])  # type: ignore[call-overload]
    return out


# ── history ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def read_history(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                out.setdefault(row["date"], {})[row["key"]] = int(float(row["value"]))
    return out


def append_snapshot(path: Path, day: str, data: dict[str, int]) -> list[str]:
    """Append today's rows. Idempotent per (date, key): a rerun the same day writes only keys that are not there yet, and
    never a second value."""
    seen = set(read_history(path).get(day, {}))
    fresh = {key: value for key, value in data.items() if key not in seen}
    if not fresh:
        return []
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        if new:
            writer.writerow(["date", "key", "value"])
        for key in sorted(fresh):
            writer.writerow([day, key, fresh[key]])
    return list(fresh)


def per_day(history: dict[str, dict[str, int]], keys: tuple[str, ...]) -> list[tuple[str, dict[str, int]]]:
    """(day counted, each key's count that day) for each pair of snapshots one day apart. The snapshot dated D is taken just
    after midnight UTC, so its difference from D-1's is calendar day D-1. A gap between snapshots is not a day, and is never
    spread over the missing days."""
    dates = sorted(day for day in history if all(key in history[day] for key in keys))
    out = []
    for first, second in pairwise(dates):
        if (dt.date.fromisoformat(second) - dt.date.fromisoformat(first)).days != 1:
            continue
        out.append((first, {key: max(0, history[second][key] - history[first][key]) for key in keys}))
    return out


def write_daily(path: Path, history: dict[str, dict[str, int]]) -> None:
    keys = ("sent", *OUTCOMES)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["day_utc", *keys])
        writer.writerows([day, *(counts[key] for key in keys)] for day, counts in per_day(history, keys))


# ── badge ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

VALUE_PAD = 6
COLOUR_LABEL = "#555"
CHAR_W = {".": 4.0, "M": 10.0, "k": 6.0}  # Verdana 11px advances
DIGIT_W = 7.0
SAFE_VALUE = re.compile(r"^[0-9]+(\.[0-9]+)?[kM]?$")


@dataclass(frozen=True)
class Badge:
    label: str
    label_box: int  # the label's width in Verdana 11px, with its padding
    colour: str
    file_name: str
    key: str


BADGES = [Badge("applications sent", 113, "#2ea44f", "applications.svg", "sent")]


def format_count(number: int) -> str:
    """Two decimals, truncated: an adoption figure is never rounded up."""
    if number < 1000:
        return str(number)
    scaled, suffix = (number / 1000, "k") if number < 1_000_000 else (number / 1_000_000, "M")
    return f"{int(scaled * 100) / 100:.2f}".rstrip("0").rstrip(".") + suffix


def render(value: str, badge: Badge) -> bytes:
    if not SAFE_VALUE.match(value):
        raise ValueError(f"refusing to draw an unexpected value: {value!r}")
    box = int(sum(CHAR_W.get(char, DIGIT_W) for char in value) + VALUE_PAD)
    total = badge.label_box + box
    label_x, value_x = badge.label_box / 2, badge.label_box + box / 2
    aria = f"{badge.label}: {value}"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{aria}">',
        f"<title>{aria}</title>",
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>',
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>',
        '<g clip-path="url(#r)">',
        f'<rect width="{badge.label_box}" height="20" fill="{COLOUR_LABEL}"/>',
        f'<rect x="{badge.label_box}" width="{box}" height="20" fill="{badge.colour}"/>',
        f'<rect width="{total}" height="20" fill="url(#s)"/>',
        "</g>",
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">',
        f'<text x="{label_x:.1f}" y="15" fill="#010101" fill-opacity=".3">{badge.label}</text>',
        f'<text x="{label_x:.1f}" y="14">{badge.label}</text>',
        f'<text x="{value_x:.1f}" y="15" fill="#010101" fill-opacity=".3">{value}</text>',
        f'<text x="{value_x:.1f}" y="14">{value}</text>',
        "</g>",
        "</svg>",
    ]
    return "\n".join(lines).encode("utf-8")


def write_badges(directory: Path, history: dict[str, dict[str, int]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for badge in BADGES:
        days = [day for day in history if badge.key in history[day]]
        if not days:
            raise SystemExit(f"no {badge.key} in the history")
        (directory / badge.file_name).write_bytes(render(format_count(history[max(days)][badge.key]), badge))


# ── entry point ───────────────────────────────────────────────────────────────────────────────────────────────────────


def run(query: Query, history: Path, daily: Path, badges: Path, today: dt.date) -> int:
    day = today.isoformat()
    before = read_history(history)
    written = append_snapshot(history, day, collect(query))
    after = read_history(history)
    write_daily(daily, after)
    write_badges(badges, after)
    print(f"[counters] {day}: wrote {len(written)} keys, sent={after[day]['sent']}, applications={after[day]['applications']}")
    previous = sorted(date for date in before if date < day)
    if previous and (today - dt.date.fromisoformat(previous[-1])).days > 1:
        # Recorded anyway, but a hole is a lost measurement: said in the run.
        print(f"::warning::no snapshot between {previous[-1]} and {day}: those days cannot be split")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path)
    parser.add_argument("--daily", type=Path)
    parser.add_argument("--badges", type=Path)
    arguments = parser.parse_args()
    if not (arguments.history and arguments.daily and arguments.badges):
        parser.error("--history, --daily and --badges are required")
    key = os.environ.get("POSTHOG_API_KEY")
    if not key:
        parser.error("POSTHOG_API_KEY is not set: the applications are counted in PostHog")
    return run(posthog_query(key), arguments.history, arguments.daily, arguments.badges, dt.datetime.now(dt.UTC).date())


if __name__ == "__main__":
    sys.exit(main())
