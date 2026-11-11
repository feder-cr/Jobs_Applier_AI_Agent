"""The job database on this computer: the chunks of one build, downloaded one after the other, the newest postings first,
and the deltas of the builds after it: of a build, only the chunks a search over the person's days reads, all of them before
it runs. Nothing is read over the network once a chunk is here.

A posting is its newest version: the one in the latest delta that names its address, else the one in the chunks; a
delta's `drop` takes it away. The chunks can come before or after the deltas that change them, and the answer is the same,
so the deltas are fetched first, being small, and the chunks after, one by one.
"""

from __future__ import annotations

import errno
import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb

# What each posting row holds, in the chunks and in the deltas, after a delta's `op`.
COLUMNS = ("url", "title", "company", "location", "country", "posted_at", "description")
STATE = "state.json"
# Deltas kept apart before they are merged into one: a search reads them all, and a computer used every day gets five.
DELTAS_APART = 8


class LibraryError(Exception):
    """The database cannot be brought to this computer."""


def _literal(paths: list[Path]) -> str:
    """A list of files as SQL: a view holds no parameters."""
    return "[" + ", ".join("'" + path.as_posix().replace("'", "''") + "'" for path in paths) + "]"


class Library:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.chunks = folder / "chunks"
        self.deltas = folder / "deltas"
        self.state: dict[str, Any] = {}
        # The days a search looks back: only the chunks holding postings that new are downloaded, all of them before a
        # search runs. None: every chunk. The owner's decision of 2026-11-09, with 15 days by default.
        self.days: int | None = None
        if (folder / STATE).is_file():
            try:
                self.state = json.loads((folder / STATE).read_text(encoding="utf-8"))
            except ValueError:
                self.state = {}

    # What is here.

    def chunk_files(self) -> list[Path]:
        """The chunks downloaded so far, in the order they came."""
        have = set(self.state.get("have", []))
        return [self.chunks / chunk["name"] for chunk in self.state.get("chunks", []) if chunk["name"] in have and (self.chunks / chunk["name"]).is_file()]

    def delta_files(self) -> list[Path]:
        """The deltas downloaded, the oldest first: their names follow their builds' times."""
        return sorted(self.deltas.glob("delta-*.parquet")) if self.deltas.is_dir() else []

    def needed(self) -> list[dict[str, Any]]:
        """The chunks a search over `days` reads: those holding a posting that new. The chunks go newest first, so these
        are the first ones of each kind."""
        chunks = self.state.get("chunks", [])
        if self.days is None:
            return chunks
        since = (datetime.now(UTC) - timedelta(days=self.days)).replace(tzinfo=None).isoformat()
        return [chunk for chunk in chunks if not chunk.get("newest") or chunk["newest"] >= since]

    def complete(self) -> bool:
        """Whether every chunk a search over `days` reads is here."""
        have = set(self.state.get("have", []))
        return bool(self.state.get("chunks")) and all(chunk["name"] in have for chunk in self.needed())

    def readiness(self) -> float:
        """How much of what a search over `days` reads is here, from 0 to 1: a search runs at 1."""
        if not self.state.get("chunks"):
            return 0.0
        have = set(self.state.get("have", []))
        needed = self.needed()
        total = sum(int(chunk.get("rows", 0)) or 1 for chunk in needed)
        return sum(int(chunk.get("rows", 0)) or 1 for chunk in needed if chunk["name"] in have) / total if total else 1.0

    def ready(self) -> bool:
        return self.complete()

    def view(self, connection: duckdb.DuckDBPyConnection) -> bool:
        """`postings`, in this connection: every posting here at its newest, with `described` saying whether it has a text.
        False when nothing has come yet."""
        chunks, deltas = self.chunk_files(), self.delta_files()
        if not chunks and not deltas:
            return False
        columns = ", ".join(COLUMNS)
        parts = []
        if deltas:
            connection.execute(f"""
                CREATE OR REPLACE TEMP TABLE changes AS
                SELECT * EXCLUDE (filename, newest) FROM (
                    SELECT *, row_number() OVER (PARTITION BY url ORDER BY filename DESC) AS newest
                    FROM read_parquet({_literal(deltas)}, filename = true, union_by_name = true)
                ) WHERE newest = 1
            """)
        if chunks:
            # A chunk of postings with no text says so by its name: the description column is not read to know it.
            kept = " WHERE url NOT IN (SELECT url FROM changes)" if deltas else ""
            parts.append(f"SELECT {columns}, NOT contains(filename, 'titles_') AS described FROM read_parquet({_literal(chunks)}, filename = true){kept}")
        if deltas:
            parts.append(f"SELECT {columns}, description IS NOT NULL AS described FROM changes WHERE op = 'add'")
        connection.execute("CREATE OR REPLACE TEMP VIEW postings AS " + " UNION ALL ".join(parts))
        return True

    def descriptions(self, urls: list[str]) -> list[str]:
        """The descriptions of these postings, in order; empty where there is none, or the posting is not here."""
        connection = duckdb.connect()
        try:
            if not urls or not self.view(connection):
                return [""] * len(urls)
            marks = ", ".join("?" for _ in urls)
            found = dict(connection.execute(f"SELECT url, description FROM postings WHERE url IN ({marks})", urls).fetchall())
        finally:
            connection.close()
        return [found.get(url) or "" for url in urls]

    # Bringing it here.

    def sync(self, manifest: dict[str, Any], location: str, progress: Callable[[int, int], None] = lambda done, size: None, arrived: Callable[[], None] = lambda: None) -> None:
        """This computer's database brought to the manifest's build: the deltas it has not seen, then the chunks it does not
        have, one at a time, `arrived` told after each that changes what a search finds, and `progress` the chunks here of
        how many. A delta chain that cannot be followed, or a build the dataset no longer has, starts over from the newest."""
        base = manifest.get("base_url") or location.replace("\\", "/").rsplit("/", 1)[0]
        if not self.state or self._chain(manifest) is None:
            self._start(manifest, base)
        for delta in self._chain(manifest) or []:
            self._fetch(delta["url"], self.deltas / delta["url"].rsplit("/", 1)[-1])
            self.state["at"] = delta["build"]
            self._save()
            arrived()
        self.state["at"] = manifest["build"]
        self._save()
        self._merge_deltas()
        needed = self.needed()
        total = len(needed)
        progress(sum(1 for chunk in needed if chunk["name"] in self.state["have"]), total)
        for chunk in needed:
            if chunk["name"] in self.state["have"]:
                continue
            try:
                self._fetch(f"{self.state['base_url']}/{chunk['name']}", self.chunks / chunk["name"])
            except LibraryError as error:
                gone = "(404)" in str(error) or "(401)" in str(error)
                if not gone or self.state["build"] == manifest["build"]:
                    raise
                # The build this computer started from is gone from the dataset: the newest one, from the start.
                self._start(manifest, base)
                self.sync(manifest, location, progress, arrived)
                return
            self.state["have"].append(chunk["name"])
            self._save()
            progress(sum(1 for each in needed if each["name"] in self.state["have"]), total)
            arrived()

    def _chain(self, manifest: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The deltas from the build this computer is at to the manifest's, in order; None when they cannot be followed."""
        at = self.state.get("at")
        if at == manifest["build"]:
            return []
        deltas = manifest.get("deltas", [])
        start = next((index for index, delta in enumerate(deltas) if delta["after"] == at), None)
        if start is None:
            return None
        chain = deltas[start:]
        if chain[-1]["build"] != manifest["build"] or any(later["after"] != earlier["build"] for earlier, later in pairwise(chain)):
            return None
        return chain

    def _merge_deltas(self) -> None:
        """Past DELTAS_APART, the deltas become one, each address at its newest action, under the newest one's name: what a
        search reads is the same, and it reads one file."""
        deltas = self.delta_files()
        if len(deltas) <= DELTAS_APART:
            return
        merged = deltas[-1].with_suffix(".merging")
        connection = duckdb.connect()
        try:
            connection.execute(f"""
                COPY (
                    SELECT * EXCLUDE (filename, newest) FROM (
                        SELECT *, row_number() OVER (PARTITION BY url ORDER BY filename DESC) AS newest
                        FROM read_parquet({_literal(deltas)}, filename = true, union_by_name = true)
                    ) WHERE newest = 1
                ) TO '{merged.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
            """)
        finally:
            connection.close()
        for delta in deltas:
            delta.unlink()
        merged.replace(deltas[-1])

    def _start(self, manifest: dict[str, Any], base: str) -> None:
        """Everything here forgotten, and the manifest's build the one to download."""
        shutil.rmtree(self.chunks, ignore_errors=True)
        shutil.rmtree(self.deltas, ignore_errors=True)
        self.state = {"build": manifest["build"], "at": manifest["build"], "base_url": base, "chunks": manifest["chunks"], "have": []}
        self._save()

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.folder / (STATE + ".part")
        partial.write_text(json.dumps(self.state), encoding="utf-8")
        partial.replace(self.folder / STATE)

    @staticmethod
    def _fetch(source: str, path: Path) -> None:
        """A file into `path`, in one rename at the end: a search never reads half of one."""
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        try:
            if source.startswith(("http://", "https://")):
                with urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "AIHawk"}), timeout=120) as response, partial.open("wb") as out:
                    shutil.copyfileobj(response, out, 1 << 20)
            else:
                shutil.copyfile(source, partial)
        except OSError as error:
            partial.unlink(missing_ok=True)
            if error.errno == errno.ENOSPC:
                raise LibraryError("The disk is full. AIHawk needs about 2 GB free for the jobs. Free some space, then start AIHawk again.") from error
            raise LibraryError("Cannot download the jobs. Check your internet connection, then start AIHawk again.") from error
        partial.replace(path)
