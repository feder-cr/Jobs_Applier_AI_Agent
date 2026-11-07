"""What AIHawk knows about each job, and the person's answers, in one SQLite file of the workspace."""

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
    updated TEXT NOT NULL,
    cost REAL
)
"""

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    host TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    made_at TEXT NOT NULL
)
"""

ANSWERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    updated TEXT NOT NULL
)
"""

# new -> shortlisted | passed_over; shortlisted -> scored -> refused | blocked | failed | submitted | unconfirmed
SUBMITTED = ("submitted", "unconfirmed")
# The jobs the person sees as tried, and what each status says in plain words.
SENT_WORDS = {
    "submitted": "sent and confirmed",
    "unconfirmed": "sent, not confirmed",
    "refused": "refused by the form",
    "blocked": "blocked",
    "failed": "failed",
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
    cost: float | None = None


@dataclass(frozen=True)
class Stored:
    job: Job
    status: str
    score: float | None
    report: dict[str, Any] | None
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
        self._db.execute(ANSWERS_SCHEMA)
        self._db.execute(ACCOUNTS_SCHEMA)
        # A store from before the cost was kept gets the column; the rows keep their nulls.
        if "cost" not in {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}:
            self._db.execute("ALTER TABLE jobs ADD COLUMN cost REAL")
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    def add(self, job: Job) -> bool:
        """Record a job seen in a search; True when it is new. A known job keeps its status and score."""
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
        return Stored(cls._job(row), row["status"], row["score"], report, row["note"], row["evidence"])

    def sent(self) -> list[Sent]:
        """Every job tried, the newest first."""
        marks = ",".join("?" * len(SENT_WORDS))
        rows = self._db.execute(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY COALESCE(sent_at, updated) DESC", tuple(SENT_WORDS)).fetchall()
        return [Sent(self._job(row), row["status"], row["note"] or "", row["sent_at"] or row["updated"], self._proof(row), row["cost"]) for row in rows]

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

    # The accounts the applications made, one per site: next time the model signs in instead of registering again.
    def account(self, host: str) -> tuple[str, str] | None:
        """The email an account was made with on this host, and when; None where there is none."""
        row = self._db.execute("SELECT email, made_at FROM accounts WHERE host = ?", (host.lower(),)).fetchone()
        return (row["email"], row["made_at"]) if row else None

    def set_account(self, host: str, email: str) -> None:
        self._db.execute(
            "INSERT INTO accounts (host, email, made_at) VALUES (?, ?, ?) ON CONFLICT(host) DO UPDATE SET email = excluded.email",
            (host.lower(), email, _now()),
        )
        self._db.commit()

    def accounts(self) -> dict[str, str]:
        return {row["host"]: row["email"] for row in self._db.execute("SELECT host, email FROM accounts ORDER BY host")}

    # The answers: what the person said about themselves, given to the model with the CV when a form is filled.
    def answers(self) -> dict[str, str]:
        return {row["name"]: row["value"] for row in self._db.execute("SELECT name, value FROM answers ORDER BY rowid")}

    def answers_text(self) -> str:
        """The answers as the model reads them: one line each, name and value."""
        return "\n".join(f"{name}: {value}" for name, value in self.answers().items())

    def add_answers(self, answers: dict[str, str], source: str) -> None:
        """Answers that are new. One already there is kept: what the person said wins over what is read again from a CV."""
        rows = [(name, value.strip(), source, _now()) for name, value in answers.items() if value and value.strip()]
        self._db.executemany("INSERT OR IGNORE INTO answers (name, value, source, updated) VALUES (?, ?, ?, ?)", rows)
        self._db.commit()

    def set_answer(self, name: str, value: str, source: str = "person") -> None:
        """One answer as the person changes it; an empty value removes it."""
        if value.strip():
            self._db.execute(
                "INSERT INTO answers (name, value, source, updated) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value, source = excluded.source, updated = excluded.updated",
                (name, value.strip(), source, _now()),
            )
        else:
            self._db.execute("DELETE FROM answers WHERE name = ?", (name,))
        self._db.commit()

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

    def set_status(self, job_id: str, status: str, note: str = "") -> None:
        self._db.execute("UPDATE jobs SET status = ?, note = ?, updated = ? WHERE id = ?", (status, note, _now(), job_id))
        self._db.commit()

    def set_cost(self, job_id: str, cost: float) -> None:
        self._db.execute("UPDATE jobs SET cost = ? WHERE id = ?", (cost, job_id))
        self._db.commit()

    def set_proof(self, job_id: str, proof: Proof) -> None:
        self._db.execute(
            "UPDATE jobs SET sent_at = ?, final_url = ?, verdict = ?, verdict_evidence = ?, page_text = ?, screenshot = ? WHERE id = ?",
            (proof.at or None, proof.url, proof.verdict, proof.evidence, proof.text, proof.screenshot, job_id),
        )
        self._db.commit()
