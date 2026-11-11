"""The applications' count is recorded by GitHub Actions, never by a machine of ours: a scheduled workflow, the script it
runs, and the arithmetic of the history."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("usage_counters", ROOT / "scripts" / "usage_counters.py")
counters = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["usage_counters"] = counters
spec.loader.exec_module(counters)  # type: ignore[union-attr]

WORKFLOW = ROOT / ".github" / "workflows" / "counters.yml"


def test_the_snapshot_is_a_scheduled_workflow_with_no_personal_secret() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text and "cron:" in text
    assert "scripts/usage_counters.py" in text
    assert "ref: badges" in text and "HEAD:badges" in text
    assert [line.strip() for line in text.splitlines() if "secrets." in line] == ["POSTHOG_API_KEY: ${{ secrets.POSTHOG_API_KEY }}"], "no secret but PostHog's read key"


def test_the_app_reports_the_outcomes_the_snapshot_counts() -> None:
    from aihawk.apply import Outcome

    assert Outcome.__dataclass_fields__["status"].type == "str"
    assert counters.OUTCOMES == ("submitted", "unconfirmed", "refused", "blocked", "failed")


def test_the_selftest_passes() -> None:
    assert counters.selftest() == 0


def test_a_day_is_the_difference_of_two_snapshots_and_the_badge_shows_the_applications_sent(tmp_path: Path) -> None:
    totals = iter([
        {"submitted": 10, "unconfirmed": 2, "refused": 1, "blocked": 0, "failed": 0},
        {"submitted": 25, "unconfirmed": 3, "refused": 4, "blocked": 1, "failed": 2},
    ])

    def api(path: str) -> object:
        if path.endswith("/releases/tags/usage-counter"):
            return {"assets": [{"name": f"{name}.txt", "download_count": value} for name, value in next(totals).items()]}
        return {"stargazers_count": 3}

    def query(hogql: str) -> list[list[object]]:
        return [[1]] if "DISTINCT" in hogql else []

    history, daily, badges = tmp_path / "history.csv", tmp_path / "daily.csv", tmp_path / "badges"
    counters.run(api, query, history, daily, badges, dt.date(2026, 11, 9))
    counters.run(api, query, history, daily, badges, dt.date(2026, 11, 10))
    rows = daily.read_text(encoding="utf-8").splitlines()
    assert rows == ["day_utc,sent,submitted,unconfirmed,refused,blocked,failed", "2026-11-09,16,15,1,3,1,2"]
    assert b"applications sent: 28" in (badges / "applications.svg").read_bytes()


def test_the_old_versions_new_applications_go_to_posthog_and_the_first_run_sends_none(tmp_path: Path) -> None:
    totals = iter([
        {"submitted": 10, "unconfirmed": 2, "refused": 1, "blocked": 4, "failed": 0},
        {"submitted": 12, "unconfirmed": 2, "refused": 1, "blocked": 5, "failed": 0},
    ])

    def api(path: str) -> object:
        return {"assets": [{"name": f"{name}.txt", "download_count": value} for name, value in next(totals).items()]}

    sent: list[list[dict[str, object]]] = []
    state = tmp_path / "posthog_synced.json"
    assert sum(counters.sync(api, state, "2026-11-11T20:17:00+00:00", sent.append).values()) == 0 and sent == []
    assert counters.sync(api, state, "2026-11-11T21:17:00+00:00", sent.append) == {"submitted": 2, "unconfirmed": 0, "refused": 0, "blocked": 1, "failed": 0}
    (batch,) = sent
    assert sorted(event["properties"]["outcome"] for event in batch) == ["blocked", "submitted", "submitted"]  # type: ignore[index]
    assert all(event["event"] == "application" and event["properties"]["source"] == counters.OLD_VERSION for event in batch)  # type: ignore[index]


def test_the_snapshot_leaves_the_copies_out_of_posthogs_count() -> None:
    asked: list[str] = []

    def query(hogql: str) -> list[list[object]]:
        asked.append(hogql)
        return [[1]] if "DISTINCT" in hogql else []

    counters.collect(lambda path: {"assets": []} if "releases" in path else {"stargazers_count": 0}, query)
    assert any(counters.OLD_VERSION in hogql and "!=" in hogql for hogql in asked)
