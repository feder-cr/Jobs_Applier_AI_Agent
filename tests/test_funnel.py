"""The funnel: which postings the title's fit and jevos's evidence shortlist for scoring, batch by batch, and the screen that is gone."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import pytest

from aihawk import documents
from aihawk import judge as judge_module
from aihawk import session as session_module
from aihawk.config import Config
from aihawk.database import Job
from aihawk.judge import Judge, fuse
from aihawk.session import JUDGED, WIDE, Session


class Scripted(Judge):
    """A judge whose evidence per posting is given, as jevos would have said it."""

    def __init__(self, config: Config, values: dict[str, float]) -> None:
        super().__init__(config)
        self.values = values

    async def read(self, cv: str, job: Job, asked: dict[str, Any]) -> float:
        return self.values.get(job.id, 0.0)


def session_with(tmp_path: Path, count: int, values: dict[str, float] | None = None) -> Session:
    """`count` postings, the title's fit rising with the number: p000 is the worst, p099 the best."""
    (tmp_path / "cv.md").write_text("# Ada Rossi\n", encoding="utf-8")
    session = Session(tmp_path)
    for number in range(count):
        session.store.add(Job(f"p{number:03d}", "Firm", f"Engineer {number}", "Rome", "u", "a", "", "", number / count))
    session._judge = Scripted(session.config, values or {})
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


def test_evidence_moves_a_posting_into_the_shortlist_and_out_of_it_by_its_sign(tmp_path: Path) -> None:
    # p000 has the worst title and strong evidence for it; p099 the best title and evidence against it.
    session = session_with(tmp_path, WIDE, {"p000": 10.0, "p001": 10.0, "p002": 10.0, "p003": 10.0, "p004": 10.0, "p099": -10.0})
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


def test_a_failed_verdict_leaves_the_others_as_they_were(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        if "Engineer 7 at" in body["state"]["job_posting"]:
            raise OSError("HTTP 500")
        return {"answers": {name: {"type": "noul", "noul": 0.9} for name in body["questions"]}}

    monkeypatch.setattr(judge_module, "_post", fake_post)
    session = session_with(tmp_path, WIDE)
    live = Judge(session.config)
    live.url, live.model = "http://127.0.0.1:1/v1/systemone", "jev-latest"
    session._judge = live
    funnel(session)
    evidence = {item.job.id: item.evidence for item in session.store.all()}
    assert evidence["p007"] == 0.0
    assert evidence["p008"] == pytest.approx(2 * math.log(9))


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
