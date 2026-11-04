"""The job database AIHawk publishes every day: read in place, so a search downloads only what it needs."""

from __future__ import annotations

import html
import json
import re
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import duckdb

from aihawk.config import Filters


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
    # Where the database holds the description: "<file>#<row>".
    ref: str = ""


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


def stored_description(ref: str) -> str:
    """The description the database holds for a posting; empty when its sources published none."""
    if "#" not in ref:
        return ""
    file, number = ref.rsplit("#", 1)
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        row = connection.execute(
            "SELECT description FROM read_parquet(?, file_row_number = true) WHERE file_row_number = ?", [file, int(number)]
        ).fetchone()
    except duckdb.Error:
        return ""
    finally:
        connection.close()
    return row[0] or "" if row else ""


def with_description(job: Job, timeout: float = 30.0) -> Job:
    """The job with its description: the database's, or else the text of its page. One job, the one being judged."""
    if job.description.strip():
        return job
    stored = stored_description(job.ref)
    if stored.strip():
        return replace(job, description=stored)
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


def _base(manifest: dict[str, Any], location: str) -> str:
    """Where the database's files are: the release the manifest names, or beside the manifest."""
    return manifest.get("base_url") or location.rsplit("/", 1)[0].rsplit("\\", 1)[0]


def local_index(manifest: dict[str, Any], location: str, cache: Path, log: Callable[[str], None] = lambda _: None) -> Path:
    """Today's index on this computer: downloaded once per database, in one rename, and the older ones removed."""
    source = f"{_base(manifest, location)}/{manifest['index']['name']}"
    if not source.startswith(("http://", "https://")):
        return Path(source)
    cache.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", manifest["built_at"])
    path = cache / f"index-{stamp}.parquet"
    if path.is_file():
        return path
    log(f"downloading today's job index ({manifest['index']['bytes'] // 1_000_000} MB), once a day")
    partial = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(source, timeout=120) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise DatabaseError(f"the job index at {source} cannot be downloaded: {error}") from error
    partial.replace(path)
    for old in cache.glob("index-*.parquet"):
        if old != path:
            old.unlink()
    return path


def _any_word(column: str, words: tuple[str, ...]) -> tuple[str, list[str]]:
    """Whole words, in any case: `java` is not `javascript`, and `roma` is not `Romania`."""
    # Not \\b: it finds no end after a symbol, as in `c++`, and takes accented letters for edges.
    pattern = "(?i)(?:^|[^\\pL\\pN])(?:" + "|".join(re.escape(word) for word in words) + ")(?:$|[^\\pL\\pN])"
    return f"regexp_matches({column}, ?)", [pattern]


def search(
    location: str, filters: Filters, max_age_days: int, cache: Path, log: Callable[[str], None] = lambda _: None
) -> tuple[dict[str, Any], list[Job]]:
    """The postings the filters keep, the newest first, with the manifest they came from. Their descriptions stay in the database."""
    if not filters.include:
        raise DatabaseError("filters.include is empty: say which job titles you want")
    manifest = load_manifest(location)
    index = local_index(manifest, location, cache, log)
    base = _base(manifest, location)

    clauses, parameters = [], []
    clause, words = _any_word("title", filters.include)
    clauses.append(clause)
    parameters += words
    if filters.exclude:
        clause, words = _any_word("title", filters.exclude)
        clauses.append(f"NOT {clause}")
        parameters += words
    if filters.locations:
        clause, words = _any_word("coalesce(location, '')", filters.locations)
        clauses.append(clause)
        parameters += words
    # A posting with no date is kept: many sources have none.
    clauses.append(f"(TRY_CAST(posted_at AS TIMESTAMP) IS NULL OR TRY_CAST(posted_at AS TIMESTAMP) >= now()::TIMESTAMP - INTERVAL {int(max_age_days)} DAY)")

    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"""SELECT key, coalesce(company, ''), title, coalesce(location, ''), url, part, row_number
                FROM read_parquet(?) WHERE {' AND '.join(clauses)}
                ORDER BY TRY_CAST(posted_at AS TIMESTAMP) DESC NULLS LAST""",
            [str(index), *parameters],
        ).fetchall()
    except duckdb.Error as error:
        raise DatabaseError(f"the job index cannot be read: {error}") from error
    finally:
        connection.close()
    # Some sources publish their names with HTML entities still in them.
    return manifest, [
        Job(key, html.unescape(company), html.unescape(title), html.unescape(place), url, url, "", f"{base}/{part}#{number}")
        for key, company, title, place, url, part, number in rows
    ]
