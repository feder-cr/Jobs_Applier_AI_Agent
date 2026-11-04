"""What AIHawk knows about each job, in one SQLite file of the workspace."""

from __future__ import annotations

import json
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
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL
)
"""

# new -> shortlisted | passed_over; shortlisted -> scored -> needs_input | blocked | declined | failed | submitted | unconfirmed
SUBMITTED = ("submitted", "unconfirmed")
LEXICAL_SHARE = 0.5


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
    def _stored(row: sqlite3.Row) -> Stored:
        job = Job(row["id"], row["company"], row["title"], row["location"], row["url"], row["apply_url"], row["description"], row["ref"] or "", row["lexical"])
        report = json.loads(row["report"]) if row["report"] else None
        return Stored(job, row["status"], row["score"], report, row["cv_path"], row["note"], row["evidence"])

    def get(self, job_id: str) -> Stored | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._stored(row) if row else None

    def with_status(self, status: str) -> list[Stored]:
        """The postings in this status, the best fit by title first."""
        rows = self._db.execute("SELECT * FROM jobs WHERE status = ? ORDER BY lexical DESC, rowid", (status,)).fetchall()
        return [self._stored(row) for row in rows]

    def all(self) -> list[Stored]:
        """What the person sees, the best first: the judged by the judgment and the title's fit together, then the rest by the title's fit and jevos's evidence."""
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
