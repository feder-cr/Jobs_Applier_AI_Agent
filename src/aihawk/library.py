"""The job database on this computer: the chunks of one build, downloaded one after the other, the newest postings first,
and the deltas of the builds after it. A search reads what has come so far, so a person who has just come sees the jobs
grow instead of waiting for all of them; nothing is read over the network once a chunk is here.

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
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb

# What each posting row holds, in the chunks and in the deltas, after a delta's `op`.
COLUMNS = ("url", "title", "company", "location", "country", "posted_at", "description")
STATE = "state.json"


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

    def postings_here(self) -> int:
        """How many postings the chunks downloaded hold."""
        have = set(self.state.get("have", []))
        return sum(int(chunk.get("rows", 0)) for chunk in self.state.get("chunks", []) if chunk["name"] in have)

    def complete(self) -> bool:
        return bool(self.state.get("chunks")) and len(self.chunk_files()) == len(self.state["chunks"])

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
        total = len(self.state["chunks"])
        progress(len(self.chunk_files()), total)
        for chunk in self.state["chunks"]:
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
            progress(len(self.chunk_files()), total)
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
                raise LibraryError(f"The disk is full: AIHawk's jobs need about 2 GB free in {path.parent}. Free some space, then start AIHawk again.") from error
            code = f" ({error.code})" if isinstance(error, urllib.error.HTTPError) else ""
            raise LibraryError(f"The jobs cannot be downloaded{code}: check your internet connection, then start AIHawk again. ({error})") from error
        partial.replace(path)
