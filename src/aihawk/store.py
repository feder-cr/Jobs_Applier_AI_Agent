"""What AIHawk knows about each job, in one SQLite file of the workspace."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aihawk.database import Job
from aihawk.judge import fuse

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    score REAL,
    report TEXT,
    cv_path TEXT,
    ref TEXT,
    lexical REAL NOT NULL DEFAULT 0,
    evidence REAL,
    note TEXT,
    sent_at TEXT,
    final_url TEXT,
    verdict TEXT,
    verdict_evidence TEXT,
    page_text TEXT,
    screenshot TEXT,
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL
)
"""

# new -> shortlisted | passed_over; shortlisted -> scored -> needs_input | refused | blocked | failed | submitted | unconfirmed
SUBMITTED = ("submitted", "unconfirmed")
# The jobs the person sees as tried, and what each status says in plain words.
SENT_WORDS = {
    "submitted": "sent and confirmed",
    "unconfirmed": "sent, not confirmed",
    "refused": "refused by the form",
    "blocked": "blocked",
    "failed": "failed",
    "needs_input": "needs an answer",
}
LEXICAL_SHARE = 0.5
# A spreadsheet runs a cell that starts like a formula; such a cell is written as text.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
COLUMNS = ("When", "Company", "Role", "Status", "What the page said", "Posting", "Final address", "Screenshot", "What was filled", "Page text")


@dataclass(frozen=True)
class Proof:
    """What the page said after the click. Blank for a job that was not clicked."""

    at: str = ""
    url: str = ""
    verdict: str = ""
    evidence: str = ""
    text: str = ""
    screenshot: str = ""


@dataclass(frozen=True)
class Sent:
    """A job that was tried: when, its status and reason, and what the page said."""

    job: Job
    status: str
    note: str
    when: str
    proof: Proof


@dataclass(frozen=True)
class Stored:
    job: Job
    status: str
    score: float | None
    report: dict[str, Any] | None
    cv_path: str | None
    note: str | None
    evidence: float | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_name(text: str) -> str:
    """A job's id as a file name."""
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:120]


def sent_word(item: Sent) -> str:
    return SENT_WORDS.get(item.status, item.status)


def said(item: Sent) -> str:
    """What the page said, or the reason the job was not sent."""
    return item.proof.evidence or item.note


def inert(cell: str) -> str:
    return "'" + cell if cell.startswith(FORMULA_START) else cell


def write_applied(path: Path, sent: list[Sent]) -> None:
    """applied.csv, written whole beside itself and renamed over the old one, so it is never half written."""
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as out:
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        for item in sent:
            cells = (item.when, item.job.company, item.job.title, sent_word(item), said(item), item.job.url, item.proof.url, item.proof.screenshot, item.note, item.proof.text)
            writer.writerow(inert(cell) for cell in cells)
    os.replace(temp, path)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def add(self, job: Job) -> bool:
        """Record a job seen in a scan; True when it is new. A known job keeps its status and score."""
        now = _now()
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO jobs (id, company, title, location, url, apply_url, description, ref, lexical, first_seen, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.id, job.company, job.title, job.location, job.url, job.apply_url, job.description, job.ref, job.lexical, now, now),
        )
        self._db.commit()
        return cursor.rowcount == 1

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(row["id"], row["company"], row["title"], row["location"], row["url"], row["apply_url"], row["description"], row["ref"] or "", row["lexical"])

    @classmethod
    def _stored(cls, row: sqlite3.Row) -> Stored:
        report = json.loads(row["report"]) if row["report"] else None
        return Stored(cls._job(row), row["status"], row["score"], report, row["cv_path"], row["note"], row["evidence"])

    def sent(self) -> list[Sent]:
        """Every job tried, the newest first."""
        marks = ",".join("?" * len(SENT_WORDS))
        rows = self._db.execute(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY COALESCE(sent_at, updated) DESC", tuple(SENT_WORDS)).fetchall()
        return [Sent(self._job(row), row["status"], row["note"] or "", row["sent_at"] or row["updated"], self._proof(row)) for row in rows]

    @staticmethod
    def _proof(row: sqlite3.Row) -> Proof:
        return Proof(row["sent_at"] or "", row["final_url"] or "", row["verdict"] or "", row["verdict_evidence"] or "", row["page_text"] or "", row["screenshot"] or "")

    def get(self, job_id: str) -> Stored | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._stored(row) if row else None

    def with_status(self, status: str) -> list[Stored]:
        """The postings in this status, the best fit by title first."""
        rows = self._db.execute("SELECT * FROM jobs WHERE status = ? ORDER BY lexical DESC, rowid", (status,)).fetchall()
        return [self._stored(row) for row in rows]

    def all(self) -> list[Stored]:
        """What the person sees, the best first: the judged by the judgment and the title's fit together, then the rest by the title's fit and Jev's evidence."""
        items = [self._stored(row) for row in self._db.execute("SELECT * FROM jobs").fetchall()]
        judged = sorted(
            (item for item in items if item.score is not None),
            key=lambda item: (-(item.score + LEXICAL_SHARE * item.job.lexical), -item.job.lexical, item.job.id),
        )
        rest = [item for item in items if item.score is None]
        order = fuse([item.job.lexical for item in rest], [item.evidence or 0.0 for item in rest])
        rest = [item for _, item in sorted(zip(order, rest, strict=True), key=lambda pair: (-pair[0], pair[1].job.id))]
        return judged + rest

    def to_apply(self, threshold: float, limit: int) -> list[Stored]:
        """The best scored jobs at or above the threshold that were not tried yet or are waiting on an answer."""
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('scored', 'needs_input') AND score >= ? ORDER BY score DESC, id LIMIT ?",
            (threshold, limit),
        ).fetchall()
        return [self._stored(row) for row in rows]

    def set_evidence(self, job_id: str, evidence: float) -> None:
        self._db.execute("UPDATE jobs SET evidence = ?, updated = ? WHERE id = ?", (evidence, _now(), job_id))
        self._db.commit()

    def set_score(self, job_id: str, score: float, report: dict[str, Any]) -> None:
        self._db.execute(
            "UPDATE jobs SET status = 'scored', score = ?, report = ?, updated = ? WHERE id = ?",
            (score, json.dumps(report, ensure_ascii=False), _now(), job_id),
        )
        self._db.commit()

    def set_statuses(self, job_ids: list[str], status: str) -> None:
        self._db.executemany("UPDATE jobs SET status = ?, updated = ? WHERE id = ?", [(status, _now(), job_id) for job_id in job_ids])
        self._db.commit()

    def set_description(self, job_id: str, description: str) -> None:
        self._db.execute("UPDATE jobs SET description = ?, updated = ? WHERE id = ?", (description, _now(), job_id))
        self._db.commit()

    def set_cv(self, job_id: str, path: Path) -> None:
        self._db.execute("UPDATE jobs SET cv_path = ?, updated = ? WHERE id = ?", (str(path), _now(), job_id))
        self._db.commit()

    def set_status(self, job_id: str, status: str, note: str = "") -> None:
        self._db.execute("UPDATE jobs SET status = ?, note = ?, updated = ? WHERE id = ?", (status, note, _now(), job_id))
        self._db.commit()

    def set_proof(self, job_id: str, proof: Proof) -> None:
        self._db.execute(
            "UPDATE jobs SET sent_at = ?, final_url = ?, verdict = ?, verdict_evidence = ?, page_text = ?, screenshot = ? WHERE id = ?",
            (proof.at or None, proof.url, proof.verdict, proof.evidence, proof.text, proof.screenshot, job_id),
        )
        self._db.commit()
