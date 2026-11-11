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
import threading
import time
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
# A download is read this many bytes at a time, and the person is told how far it is no more often than every half second.
READ_BYTES = 1 << 18
TELL_EVERY = 0.5


class LibraryError(Exception):
    """The database cannot be brought to this computer. `status` is the server's answer when it gave one, such as 404."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class Stopped(LibraryError):
    """The download was stopped because AIHawk is closing."""


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
        # Set when AIHawk closes: a download in its thread stops at its next part, instead of going on for minutes after the
        # screen is gone.
        self.stopping = threading.Event()
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

    def has_postings(self) -> bool:
        """Whether anything is here to search: the first chunk or the first delta is enough for a search to run, and each one
        that comes after makes it find more."""
        return bool(self.chunk_files() or self.delta_files())

    def bytes_of_needed(self) -> tuple[int, int]:
        """The bytes of the chunks a search over `days` reads that are here, and of all of them. 0 for both when the manifest
        gives no sizes."""
        have = set(self.state.get("have", []))
        needed = self.needed()
        return sum(int(chunk.get("bytes", 0)) for chunk in needed if chunk["name"] in have), sum(int(chunk.get("bytes", 0)) for chunk in needed)

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

    def sync(
        self, manifest: dict[str, Any], location: str, progress: Callable[[int, int], None] = lambda done, size: None, arrived: Callable[[], None] = lambda: None,
        on_bytes: Callable[[int, int], None] = lambda done, total: None,
    ) -> None:
        """This computer's database brought to the manifest's build: the deltas it has not seen, then the chunks it does not
        have, one at a time, `arrived` told after each that changes what a search finds, `progress` the chunks here of how
        many, and `on_bytes` the bytes here of the bytes the chunks come to, at least every half second while one downloads.
        A delta chain that cannot be followed, or a build the dataset no longer has, starts over from the newest."""
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
        here, size = self.bytes_of_needed()
        on_bytes(here, size)
        for chunk in needed:
            if chunk["name"] in self.state["have"]:
                continue
            told = 0.0
            partial = 0

            def read(count: int, here: int = here, size: int = size) -> None:
                nonlocal partial, told
                if self.stopping.is_set():
                    raise Stopped("The download was stopped.")
                partial += count
                if time.monotonic() - told >= TELL_EVERY:
                    told = time.monotonic()
                    on_bytes(here + partial, size)

            try:
                self._fetch(f"{self.state['base_url']}/{chunk['name']}", self.chunks / chunk["name"], read)
            except LibraryError as error:
                gone = error.status in (401, 404)
                if not gone or self.state["build"] == manifest["build"]:
                    raise
                # The build this computer started from is gone from the dataset: the newest one, from the start.
                self._start(manifest, base)
                self.sync(manifest, location, progress, arrived, on_bytes)
                return
            self.state["have"].append(chunk["name"])
            self._save()
            here, size = self.bytes_of_needed()
            on_bytes(here, size)
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
    def _fetch(source: str, path: Path, read: Callable[[int], None] = lambda count: None) -> None:
        """A file into `path`, in one rename at the end: a search never reads half of one. `read` is told how many bytes
        each part of it was."""
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        try:
            if source.startswith(("http://", "https://")):
                opened = urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "AIHawk"}), timeout=120)
            else:
                opened = open(source, "rb")  # noqa: SIM115  Closed by the with below.
            with opened as response, partial.open("wb") as out:
                while part := response.read(READ_BYTES):
                    out.write(part)
                    read(len(part))
        except Stopped:
            partial.unlink(missing_ok=True)
            raise
        except OSError as error:
            partial.unlink(missing_ok=True)
            if error.errno == errno.ENOSPC:
                raise LibraryError("The disk is full. AIHawk needs about 2 GB free for the jobs. Free some space, then start AIHawk again.") from error
            status = error.code if isinstance(error, urllib.error.HTTPError) else 0
            raise LibraryError("Cannot download the jobs. Check your internet connection, then start AIHawk again.", status) from error
        partial.replace(path)
