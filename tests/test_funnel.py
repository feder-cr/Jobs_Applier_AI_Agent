"""The funnel: which postings the title's fit and Jev's evidence shortlist for scoring, batch by batch, and the screen that is gone."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from aihawk import documents
from aihawk import judge as judge_module
from aihawk import session as session_module
from aihawk.database import Job
from aihawk.judge import fuse
from aihawk.session import JUDGED, WIDE, Session


@pytest.fixture(autouse=True)
def values(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """The evidence per posting, as the decision model would have said it: none, unless a test gives some."""
    given: dict[str, float] = {}

    async def scripted(client: Any, model: str, cv: str, job: Job, asked: dict[str, Any]) -> float:
        return given.get(job.id, 0.0)

    monkeypatch.setattr(session_module, "evidence", scripted)
    return given


def session_with(tmp_path: Path, count: int) -> Session:
    """`count` postings, the title's fit rising with the number: p000 is the worst, p099 the best."""
    (tmp_path / "cv.md").write_text("# Ada Rossi\n", encoding="utf-8")
    session = Session(tmp_path)
    for number in range(count):
        session.store.add(Job(f"p{number:03d}", "Firm", f"Engineer {number}", "Rome", "u", "a", "", "", number / count))
    # The scripted evidence never calls the client, so a placeholder stands in for it.
    session._client = SimpleNamespace()  # type: ignore[assignment]
    return session


def ids(numbers: range) -> set[str]:
    return {f"p{number:03d}" for number in numbers}


def funnel(session: Session) -> int:
    return asyncio.run(session.funnel(lambda line: None, lambda line: None))


def shortlisted(session: Session) -> set[str]:
    return {item.job.id for item in session.store.with_status("shortlisted")}


def test_the_title_alone_shortlists_the_best_judged_postings(tmp_path: Path) -> None:
    session = session_with(tmp_path, WIDE)
    assert funnel(session) == JUDGED
    assert shortlisted(session) == ids(range(75, 100))
    assert {item.job.id for item in session.store.with_status("passed_over")} == ids(range(75))


def test_evidence_moves_a_posting_into_the_shortlist_and_out_of_it_by_its_sign(tmp_path: Path, values: dict[str, float]) -> None:
    # p000 has the worst title and strong evidence for it; p099 the best title and evidence against it.
    values.update({"p000": 10.0, "p001": 10.0, "p002": 10.0, "p003": 10.0, "p004": 10.0, "p099": -10.0})
    session = session_with(tmp_path, WIDE)
    funnel(session)
    assert shortlisted(session) == ids(range(5)) | ids(range(79, 99))


def test_the_second_batch_takes_the_next_postings_still_new(tmp_path: Path) -> None:
    session = session_with(tmp_path, 150)
    assert funnel(session) == JUDGED
    assert shortlisted(session) == ids(range(125, 150))
    assert funnel(session) == JUDGED
    assert shortlisted(session) == ids(range(125, 150)) | ids(range(25, 50))
    assert len(session.store.with_status("passed_over")) == 100


def test_the_passed_over_postings_are_kept_and_listed_below_the_judged(tmp_path: Path) -> None:
    session = session_with(tmp_path, WIDE)
    funnel(session)
    for item in session.store.with_status("shortlisted"):
        session.store.set_score(item.job.id, 4.0, {})
    listed = [item.job.id for item in session.store.all()]
    assert len(listed) == WIDE
    assert set(listed[:JUDGED]) == ids(range(75, 100))
    assert set(listed[JUDGED:]) == ids(range(75))


def test_a_failed_verdict_is_counted_once_and_the_others_are_as_they_were(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def scripted_endpoint(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "Engineer 7 at" in body["state"]["job_posting"]:
            return httpx.Response(400, json={"error": "the request is wrong"})
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.9} for name in body["questions"]}})

    # The real evidence, over a scripted endpoint: a refused posting is one failed verdict, not a crash.
    monkeypatch.setattr(session_module, "evidence", judge_module.evidence)
    session = session_with(tmp_path, WIDE)
    session._client = AsyncOpenAI(base_url="https://api.orcarouter.ai/v1", api_key="sk-test", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(scripted_endpoint)))
    said: list[str] = []
    asyncio.run(session.funnel(said.append, lambda line: None))
    evidence = {item.job.id: item.evidence for item in session.store.all()}
    assert evidence["p007"] is None
    assert evidence["p008"] == pytest.approx(2 * math.log(9))
    assert said == ["Jev could not read 1 of 100 postings: they are ranked by their title alone"]
    assert len(session.store.with_status("shortlisted")) == JUDGED


def test_the_funnel_says_how_many_descriptions_and_postings_it_has_read(tmp_path: Path) -> None:
    session = session_with(tmp_path, 30)
    heard: list[str] = []
    asyncio.run(session.funnel(lambda line: None, heard.append))
    assert heard[0] == "reading descriptions 1/30" and "reading descriptions 30/30" in heard
    assert heard[-1] == "Jev reads 30/30"


def test_a_scan_that_logs_from_its_thread_says_it_on_the_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []

    def searching(*args: Any) -> tuple[dict[str, Any], list[Job]]:
        args[-1]("from the thread")
        return {"rows_out": 0}, []

    monkeypatch.setattr(session_module, "search", searching)
    session = Session(tmp_path)
    loop_thread = threading.get_ident()
    asyncio.run(session.find(lambda line: seen.append((line, threading.get_ident()))))
    assert seen == [("from the thread", loop_thread), ("0 postings fit your search, of 0 in today's database; 0 new", loop_thread)]


def test_with_no_postings_left_the_funnel_does_nothing(tmp_path: Path) -> None:
    session = session_with(tmp_path, 0)
    assert funnel(session) == 0


def test_fuse_is_the_title_z_score_plus_the_evidence() -> None:
    assert fuse([1.0, 3.0], [0.0, 1.0]) == pytest.approx([-1.0, 2.0])
    assert fuse([2.0, 2.0], [0.5, 0.0]) == pytest.approx([0.5, 0.0])
    assert fuse([], []) == []


def test_the_title_screen_is_gone() -> None:
    assert not any(hasattr(documents, name) for name in ("screen_titles", "SCREEN_SCHEMA", "SCREEN_SYSTEM"))
    assert not any(hasattr(session_module, name) for name in ("BATCH", "SCREEN_BATCH"))
    assert not hasattr(Session, "shortlist") and not hasattr(Session, "next_to_score")
