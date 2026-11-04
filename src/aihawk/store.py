"""What AIHawk knows about each job, in one SQLite file of the workspace."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aihawk.database import Job

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
    note TEXT,
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL
)
"""

# new -> scored -> needs_input | blocked | declined | failed | submitted | unconfirmed
SUBMITTED = ("submitted", "unconfirmed")


@dataclass(frozen=True)
class Stored:
    job: Job
    status: str
    score: float | None
    report: dict[str, Any] | None
    cv_path: str | None
    note: str | None


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
            "INSERT OR IGNORE INTO jobs (id, company, title, location, url, apply_url, description, first_seen, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.id, job.company, job.title, job.location, job.url, job.apply_url, job.description, now, now),
        )
        self._db.commit()
        return cursor.rowcount == 1

    @staticmethod
    def _stored(row: sqlite3.Row) -> Stored:
        job = Job(row["id"], row["company"], row["title"], row["location"], row["url"], row["apply_url"], row["description"])
        report = json.loads(row["report"]) if row["report"] else None
        return Stored(job, row["status"], row["score"], report, row["cv_path"], row["note"])

    def get(self, job_id: str) -> Stored | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._stored(row) if row else None

    def unscored(self) -> list[Stored]:
        rows = self._db.execute("SELECT * FROM jobs WHERE status = 'new' ORDER BY first_seen, id").fetchall()
        return [self._stored(row) for row in rows]

    def all(self) -> list[Stored]:
        rows = self._db.execute("SELECT * FROM jobs ORDER BY score IS NULL, score DESC, first_seen, id").fetchall()
        return [self._stored(row) for row in rows]

    def to_apply(self, threshold: float, limit: int) -> list[Stored]:
        """The best scored jobs at or above the threshold that were not tried yet or are waiting on an answer."""
        rows = self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('scored', 'needs_input') AND score >= ? ORDER BY score DESC, id LIMIT ?",
            (threshold, limit),
        ).fetchall()
        return [self._stored(row) for row in rows]

    def set_score(self, job_id: str, score: float, report: dict[str, Any]) -> None:
        self._db.execute(
            "UPDATE jobs SET status = 'scored', score = ?, report = ?, updated = ? WHERE id = ?",
            (score, json.dumps(report, ensure_ascii=False), _now(), job_id),
        )
        self._db.commit()

    def set_cv(self, job_id: str, path: Path) -> None:
        self._db.execute("UPDATE jobs SET cv_path = ?, updated = ? WHERE id = ?", (str(path), _now(), job_id))
        self._db.commit()

    def set_status(self, job_id: str, status: str, note: str = "") -> None:
        self._db.execute("UPDATE jobs SET status = ?, note = ?, updated = ? WHERE id = ?", (status, note, _now(), job_id))
        self._db.commit()
