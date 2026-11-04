"""The job database AIHawk publishes every day: read in place, so a search downloads only what it needs."""

from __future__ import annotations

import html
import json
import re
import urllib.request
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import duckdb

from aihawk.config import Filters

# Where each form can be filled without an account. Other postings are found and listed, and not applied to.
FILLABLE_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com")


class DatabaseError(Exception):
    """The database cannot be read."""


@dataclass(frozen=True)
class Job:
    id: str
    company: str
    title: str
    location: str
    url: str
    apply_url: str
    description: str

    @property
    def fillable(self) -> bool:
        return any(host in self.apply_url.lower() for host in FILLABLE_HOSTS)


class _PageText(HTMLParser):
    SKIPPED = frozenset({"script", "style", "noscript", "svg", "head", "nav", "footer"})
    BLOCKS = frozenset({"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "ul", "ol", "section"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def page_text(markup: str) -> str:
    parser = _PageText()
    parser.feed(html.unescape(markup))
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


def with_description(job: Job, timeout: float = 30.0) -> Job:
    """The job with the text of its page, when its source published none. One page, for one job the person may want."""
    if job.description.strip():
        return job
    request = urllib.request.Request(job.url, headers={"User-Agent": "AIHawk (+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return replace(job, description=page_text(response.read().decode("utf-8", "replace")))
    except OSError:
        return job


def load_manifest(location: str) -> dict[str, Any]:
    try:
        if location.startswith(("http://", "https://")):
            with urllib.request.urlopen(location, timeout=60) as response:
                return json.load(response)
        return json.loads(Path(location).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DatabaseError(f"the job database's manifest at {location} cannot be read: {error}") from error


def file_locations(manifest: dict[str, Any], location: str) -> list[str]:
    """Where the database's files are: the release the manifest names, or beside the manifest."""
    base = manifest.get("base_url") or location.rsplit("/", 1)[0].rsplit("\\", 1)[0]
    return [f"{base}/{item['name']}" for item in manifest["files"]]


def _any_like(column: str, words: tuple[str, ...]) -> tuple[str, list[str]]:
    return "(" + " OR ".join(f"lower({column}) LIKE ?" for _ in words) + ")", [f"%{word.lower()}%" for word in words]


def search(location: str, filters: Filters, max_age_days: int) -> tuple[dict[str, Any], list[Job]]:
    """The postings the filters keep, the newest first, with the manifest they came from."""
    if not filters.include:
        raise DatabaseError("filters.include is empty: say which job titles you want")
    manifest = load_manifest(location)
    files = file_locations(manifest, location)

    clauses, parameters = [], []
    clause, words = _any_like("title", filters.include)
    clauses.append(clause)
    parameters += words
    if filters.exclude:
        clause, words = _any_like("title", filters.exclude)
        clauses.append(f"NOT {clause}")
        parameters += words
    if filters.locations:
        clause, words = _any_like("coalesce(location, '')", filters.locations)
        clauses.append(clause)
        parameters += words
    # A posting with no date is kept: many sources have none.
    clauses.append(f"(TRY_CAST(posted_at AS TIMESTAMP) IS NULL OR TRY_CAST(posted_at AS TIMESTAMP) >= now()::TIMESTAMP - INTERVAL {int(max_age_days)} DAY)")

    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        rows = connection.execute(
            f"""SELECT key, coalesce(company, ''), title, coalesce(location, ''), url, coalesce(description, '')
                FROM read_parquet(?) WHERE {' AND '.join(clauses)}
                ORDER BY TRY_CAST(posted_at AS TIMESTAMP) DESC NULLS LAST""",
            [files, *parameters],
        ).fetchall()
    except duckdb.Error as error:
        raise DatabaseError(f"the job database cannot be read: {error}") from error
    finally:
        connection.close()
    return manifest, [Job(key, company, title, place, url, url, text) for key, company, title, place, url, text in rows]
