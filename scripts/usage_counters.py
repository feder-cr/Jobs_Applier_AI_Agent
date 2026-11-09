#!/usr/bin/env python3
"""Daily snapshot of the applications AIHawk has sent, for all its users together, and the README badge drawn from it.

    python scripts/usage_counters.py --history H.csv --daily D.csv --badges DIR
    python scripts/usage_counters.py --selftest

Run once a day by .github/workflows/counters.yml, which keeps the files on the unprotected `badges` branch: no person and
no machine of ours, every input a public GitHub API answer, every output a file on that branch. The way
invisible_playwright records its browser launches.

WHAT IS COUNTED. Every application that ends makes one download of the `usage-counter` release's file named after how it
ended (src/aihawk/counter.py), and GitHub counts each file's downloads as its `download_count`. GitHub keeps only the
running totals, never a history: a day's applications are the difference between two daily totals, and a day nobody
recorded is lost for good.

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

REPO = "feder-cr/Jobs_Applier_AI_Agent"
COUNTER_TAG = "usage-counter"
OUTCOMES = ("submitted", "unconfirmed", "refused", "blocked", "failed")
SENT = ("submitted", "unconfirmed")

Api = Callable[[str], object]


def github_api(token: str | None) -> Api:
    def get(path: str) -> object:
        request = urllib.request.Request("https://api.github.com" + path)
        request.add_header("Accept", "application/vnd.github+json")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    return get


def collect(api: Api) -> dict[str, int]:
    """Each outcome's total, the ones sent, all of them, and the stars."""
    release = api(f"/repos/{REPO}/releases/tags/{COUNTER_TAG}")
    downloads = {asset["name"].removesuffix(".txt"): asset["download_count"] for asset in release["assets"]}  # type: ignore[index]
    out = {outcome: int(downloads.get(outcome, 0)) for outcome in OUTCOMES}
    out["sent"] = sum(out[outcome] for outcome in SENT)
    out["applications"] = sum(out[outcome] for outcome in OUTCOMES)
    out["stars"] = int(api(f"/repos/{REPO}")["stargazers_count"])  # type: ignore[index]
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


def run(api: Api, history: Path, daily: Path, badges: Path, today: dt.date) -> int:
    day = today.isoformat()
    before = read_history(history)
    written = append_snapshot(history, day, collect(api))
    after = read_history(history)
    write_daily(daily, after)
    write_badges(badges, after)
    print(f"[counters] {day}: wrote {len(written)} keys, sent={after[day]['sent']}, applications={after[day]['applications']}")
    previous = sorted(date for date in before if date < day)
    if previous and (today - dt.date.fromisoformat(previous[-1])).days > 1:
        # Recorded anyway, but a hole is a lost measurement: said in the run.
        print(f"::warning::no snapshot between {previous[-1]} and {day}: those days cannot be split")
    return 0


def selftest() -> int:
    import tempfile

    fails = []
    release = {"assets": [{"name": "submitted.txt", "download_count": 7}, {"name": "unconfirmed.txt", "download_count": 2}, {"name": "failed.txt", "download_count": 1}]}
    answers = {f"/repos/{REPO}/releases/tags/{COUNTER_TAG}": release, f"/repos/{REPO}": {"stargazers_count": 5}}
    if collect(answers.__getitem__) != {"submitted": 7, "unconfirmed": 2, "refused": 0, "blocked": 0, "failed": 1, "sent": 9, "applications": 10, "stars": 5}:
        fails.append("collect")
    if format_count(4_616_723) != "4.61M" or format_count(2554) != "2.55k" or format_count(999) != "999":
        fails.append("format")
    history = {"2026-11-01": {"sent": 100}, "2026-11-02": {"sent": 160}, "2026-11-04": {"sent": 300}}
    if per_day(history, ("sent",)) != [("2026-11-01", {"sent": 60})]:
        fails.append("gap")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "h.csv"
        append_snapshot(path, "2026-11-08", {"stars": 1})
        if append_snapshot(path, "2026-11-08", {"stars": 2}) or read_history(path)["2026-11-08"]["stars"] != 1:
            fails.append("idempotent")
    print("selftest:", "OK" if not fails else "FAIL " + ", ".join(fails))
    return 1 if fails else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--history", type=Path)
    parser.add_argument("--daily", type=Path)
    parser.add_argument("--badges", type=Path)
    arguments = parser.parse_args()
    if arguments.selftest:
        return selftest()
    if not (arguments.history and arguments.daily and arguments.badges):
        parser.error("--history, --daily and --badges are required")
    api = github_api(os.environ.get("GITHUB_TOKEN"))
    return run(api, arguments.history, arguments.daily, arguments.badges, dt.datetime.now(dt.UTC).date())


if __name__ == "__main__":
    sys.exit(main())
