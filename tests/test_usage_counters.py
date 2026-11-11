"""The applications' count is recorded by GitHub Actions, never by a machine of ours: a scheduled workflow, the script it
runs, and the arithmetic of the history."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from aihawk.store import SENT_WORDS, SUBMITTED
from fakes import ROOT, load_script

counters = load_script("scripts", "usage_counters")

WORKFLOW = ROOT / ".github" / "workflows" / "counters.yml"


def counted(rows: dict[str, int]):
    """A PostHog that answers with these totals by outcome, and one computer."""

    def query(hogql: str) -> list[list[object]]:
        return [[1]] if "DISTINCT" in hogql else [[outcome, count] for outcome, count in rows.items()]

    return query


def test_the_snapshot_is_a_scheduled_workflow_with_no_personal_secret() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text and "cron:" in text
    assert "scripts/usage_counters.py" in text
    assert "ref: badges" in text and "HEAD:badges" in text
    assert [line.strip() for line in text.splitlines() if "secrets." in line] == ["POSTHOG_API_KEY: ${{ secrets.POSTHOG_API_KEY }}"], "no secret but PostHog's read key"


def test_the_outcomes_counted_are_the_ones_the_app_records_and_the_ones_sent_are_the_submitted() -> None:
    assert set(counters.OUTCOMES) == set(SENT_WORDS)
    assert counters.SENT == tuple(SUBMITTED)


def test_the_totals_are_each_outcome_the_sent_the_applications_and_the_computers() -> None:
    totals = counters.collect(counted({"submitted": 4, "blocked": 2}))
    assert totals == {"submitted": 4, "unconfirmed": 0, "refused": 0, "blocked": 2, "failed": 0, "sent": 4, "applications": 6, "computers": 1}


def test_a_count_says_its_thousands_and_never_rounds_up() -> None:
    assert counters.format_count(4_616_723) == "4.61M"
    assert counters.format_count(2554) == "2.55k"
    assert counters.format_count(999) == "999"


def test_a_gap_between_two_snapshots_is_not_a_day() -> None:
    history = {"2026-11-01": {"sent": 100}, "2026-11-02": {"sent": 160}, "2026-11-04": {"sent": 300}}
    assert counters.per_day(history, ("sent",)) == [("2026-11-01", {"sent": 60})]


def test_a_rerun_the_same_day_keeps_the_first_value(tmp_path: Path) -> None:
    path = tmp_path / "h.csv"
    counters.append_snapshot(path, "2026-11-08", {"computers": 1})
    assert counters.append_snapshot(path, "2026-11-08", {"computers": 2}) == []
    assert counters.read_history(path)["2026-11-08"]["computers"] == 1


def test_a_day_is_the_difference_of_two_snapshots_and_the_badge_shows_the_applications_sent(tmp_path: Path) -> None:
    totals = iter([
        {"submitted": 10, "unconfirmed": 2, "refused": 1, "blocked": 0, "failed": 0},
        {"submitted": 25, "unconfirmed": 3, "refused": 4, "blocked": 1, "failed": 2},
    ])

    def query(hogql: str) -> list[list[object]]:
        return [[1]] if "DISTINCT" in hogql else [[outcome, count] for outcome, count in next(totals).items()]

    history, daily, badges = tmp_path / "history.csv", tmp_path / "daily.csv", tmp_path / "badges"
    counters.run(query, history, daily, badges, dt.date(2026, 11, 9))
    counters.run(query, history, daily, badges, dt.date(2026, 11, 10))
    rows = daily.read_text(encoding="utf-8").splitlines()
    assert rows == ["day_utc,sent,submitted,unconfirmed,refused,blocked,failed", "2026-11-09,16,15,1,3,1,2"]
    assert b"applications sent: 28" in (badges / "applications.svg").read_bytes()
