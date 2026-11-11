"""Jev's round: the next postings the search ranked best, each scored by how sure Jev is that the job is in line, and the
ones it gave no answer for set aside."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from aihawk import scoring as scoring_module
from aihawk import session as session_module
from aihawk.config import Config, Filters
from aihawk.database import Job
from aihawk.scoring import NOT_JUDGED, NotAnswering
from aihawk.session import WIDE, Session, _places
from aihawk.store import Store


@pytest.fixture(autouse=True)
def values(monkeypatch: pytest.MonkeyPatch) -> dict[str, float | None]:
    """How sure Jev is of each posting: half, unless a test says otherwise; None is no answer."""
    given: dict[str, float | None] = {}

    async def scripted(client: Any, model: str, cv: str, jobs: list[Job], asked: str, why: Any) -> list[float | None]:
        return [given.get(job.id, 0.5) for job in jobs]

    monkeypatch.setattr(session_module, "fits", scripted)
    return given


def session_with(tmp_path: Path, count: int) -> Session:
    """`count` postings, the title's fit rising with the number: p000 is the worst."""
    (tmp_path / "cv.md").write_text("# Ada Rossi\n", encoding="utf-8")
    session = Session(tmp_path)
    for number in range(count):
        session.store.add(Job(f"p{number:03d}", "Firm", f"Engineer {number}", "Rome", "u", "a", "", number / count))
    # The scripted verdict never calls the client, so a placeholder stands in for it.
    session._client = SimpleNamespace()  # type: ignore[assignment]
    return session


def ids(numbers: range) -> set[str]:
    return {f"p{number:03d}" for number in numbers}


def read(session: Session) -> int:
    return asyncio.run(session.read(lambda line: None))


def scored(session: Session) -> dict[str, float | None]:
    return {item.job.id: item.score for item in session.store.with_status("scored")}


def test_a_round_reads_the_best_titles_one_full_request_and_scores_every_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "WIDE", 20)
    session = session_with(tmp_path, 30)
    assert read(session) == 20
    assert set(scored(session)) == ids(range(10, 30))
    assert {item.job.id for item in session.store.with_status("new")} == ids(range(10))
    assert WIDE == 200


def test_the_score_is_jevs_certainty_from_1_to_5(tmp_path: Path, values: dict[str, float | None]) -> None:
    values.update({"p000": 1.0, "p001": 0.0, "p002": 0.85})
    session = session_with(tmp_path, 3)
    read(session)
    assert scored(session) == {"p000": 5.0, "p001": 1.0, "p002": 4.4}
    assert [item.job.id for item in session.store.all()] == ["p000", "p002", "p001"]


def test_a_posting_jev_gave_no_answer_for_is_set_aside_and_said_once(tmp_path: Path, values: dict[str, float | None]) -> None:
    values.update({"p001": None})
    session = session_with(tmp_path, 3)
    said: list[str] = []
    assert asyncio.run(session.read(said.append)) == 2
    assert [item.job.id for item in session.store.with_status("passed_over")] == ["p001"]
    assert said == ["1 of 3 jobs could not be read."]
    # Not asked again: the next round has nothing left.
    assert read(session) == 0


def test_a_failed_request_is_said_with_its_reason_and_the_others_are_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def scripted_endpoint(request: httpx.Request) -> httpx.Response:
        if b"Engineer 7 at" in request.content:
            return httpx.Response(400, json={"error": {"message": "maximum context length exceeded", "code": "context_length_exceeded"}})
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.75} for name in body["questions"]}})

    # The real verdict, over a scripted endpoint: a posting too large to read alone is one failed verdict, not a crash.
    monkeypatch.setattr(session_module, "fits", scoring_module.fits)
    session = session_with(tmp_path, 20)
    session._client = AsyncOpenAI(base_url="https://api.orcarouter.ai/v1", api_key="sk-test", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(scripted_endpoint)))
    said: list[str] = []
    assert asyncio.run(session.read(said.append)) == 19
    assert scored(session)["p008"] == 4.0 and "p007" not in scored(session)
    assert len(said) == 1 and said[0] == "1 of 20 jobs could not be read. The AI service did not accept the request."


def test_the_jobs_an_older_store_left_waiting_with_no_score_are_read_again(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name in ("waiting", "passed", "done"):
        store.add(Job(name, "Firm", "Engineer", "Rome", name, "a", ""))
    store.set_statuses(["waiting"], "shortlisted")
    store.set_statuses(["passed"], "passed_over")
    store.set_score("done", 4.0)
    store._db.execute("PRAGMA user_version = 2")
    store._db.commit()
    store.close()
    store = Store(tmp_path / "jobs.db")
    assert {item.job.id for item in store.with_status("new")} == {"waiting", "passed"}
    assert [item.job.id for item in store.with_status("scored")] == ["done"]


def test_with_no_postings_left_jev_reads_nothing(tmp_path: Path) -> None:
    session = session_with(tmp_path, 0)
    assert read(session) == 0


@pytest.mark.parametrize(
    ("cities", "locations", "places"),
    [(["Lyon"], ["France"], "Lyon, France"), ([], ["Italy"], "Italy"), (["Lyon"], [], "Lyon"), ([], [], "")],
)
def test_the_places_a_posting_names_have_no_empty_part(cities: list[str], locations: list[str], places: str) -> None:
    assert _places(Config(workspace=Path("."), filters=Filters(cities=tuple(cities), locations=tuple(locations)))) == places


def test_a_posting_whose_request_got_no_answer_stays_new_and_the_round_says_so_once_the_others_are_kept(tmp_path: Path, values: dict[str, float | None]) -> None:
    values.update({"p001": NOT_JUDGED})  # type: ignore[dict-item]
    session = session_with(tmp_path, 3)
    said: list[str] = []
    with pytest.raises(NotAnswering):
        asyncio.run(session.read(said.append))
    assert [item.job.id for item in session.store.with_status("new")] == ["p001"]
    assert set(scored(session)) == {"p000", "p002"}
    assert not session.store.with_status("passed_over") and said == []


def test_a_round_nobody_answered_sets_nothing_aside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(client: Any, model: str, cv: str, jobs: list[Job], asked: str, why: Any) -> list[Any]:
        return [NOT_JUDGED] * len(jobs)

    monkeypatch.setattr(session_module, "fits", unreachable)
    session = session_with(tmp_path, 3)
    with pytest.raises(NotAnswering):
        read(session)
    assert len(session.store.with_status("new")) == 3 and not session.store.with_status("passed_over")


def test_the_jobs_a_round_set_aside_for_want_of_an_answer_are_read_again(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name in ("lost", "kept"):
        store.add(Job(name, "Firm", "Engineer", "Rome", name, "a", ""))
    store.set_statuses(["lost", "kept"], "passed_over")
    store.set_score("kept", 3.0)
    store._db.execute("PRAGMA user_version = 4")
    store._db.commit()
    store.close()
    store = Store(tmp_path / "jobs.db")
    assert [item.job.id for item in store.with_status("new")] == ["lost"]
    assert [item.job.id for item in store.with_status("scored")] == ["kept"]
    # Once: a job set aside again after the migration stays set aside.
    store.set_statuses(["lost"], "passed_over")
    store.close()
    assert [item.job.id for item in Store(tmp_path / "jobs.db").with_status("passed_over")] == ["lost"]


def test_forgetting_the_untried_keeps_every_job_that_was_tried(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name in ("new", "scored", "passed", "sent", "refused", "failed"):
        store.add(Job(name, "Firm", "Engineer", "Rome", name, "a", ""))
    store.set_score("scored", 4.0)
    store.set_statuses(["passed"], "passed_over")
    for name in ("sent", "refused", "failed"):
        store.set_status(name, {"sent": "submitted"}.get(name, name), "")
    store.forget_untried()
    assert sorted(item.job.id for item in store.all()) == ["failed", "refused", "sent"]
