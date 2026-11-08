"""Publishes work/out as this run's release, and points the release `jobs-latest` at it.

A database much smaller than the one before it is refused: losing a big source must not silently empty everyone's search.
The dated releases keep the files; `jobs-latest` holds only the manifest that names them, so a reader never sees a
half-replaced database. The last KEEP dated releases stay, so a reader mid-search does not lose its files.

Each build also publishes the delta of the index against the build before it: the postings added, the ones whose fields
changed, and the keys gone. A reader that has the index applies them in order and never downloads the whole of it again.
They are kept for ever, a month to a release, because a reader may come back after any time.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

LATEST = "jobs-latest"
DATED = re.compile(r"^jobs-\d{8}-\d{6}$")
# The columns a delta carries: the index's own, without the file and row that point into one build's files.
INDEX_COLUMNS = ("url", "title", "company", "ats", "location", "country", "remote", "salary_min", "salary_max", "salary_currency", "posted_at", "described")
KEEP = 6  # five-hourly builds: about 30 hours of releases, so a reader mid-search does not lose its files
MIN_SHARE_OF_PREVIOUS = 0.7


def gh(*arguments: str) -> str:
    done = subprocess.run(["gh", *arguments], check=False, capture_output=True, text=True)
    if done.returncode:
        raise GhError(f"gh {arguments[0]} {arguments[1]}: {done.stderr.strip()}")
    return done.stdout


class GhError(Exception):
    """A gh command failed; its message says why."""


def still_to_upload(local: dict[str, int], uploaded: dict[str, int]) -> list[str]:
    """The files a release does not hold whole yet."""
    return [name for name, size in local.items() if uploaded.get(name) != size]


def publish_files(repo: str, tag: str, folder: Path, names: list[str], notes: str) -> None:
    """The release of the day. One an earlier attempt left as a draft is finished, not uploaded again: these are gigabytes."""
    local = {name: (folder / name).stat().st_size for name in names}
    try:
        listed = json.loads(gh("release", "view", tag, "--repo", repo, "--json", "assets"))
    except GhError:
        gh("release", "create", tag, *(str(folder / name) for name in names), "--repo", repo, "--title", f"Job database {tag}", "--notes", notes, "--latest=false")
        return
    uploaded = {asset["name"]: asset["size"] for asset in listed["assets"] if asset["state"] == "uploaded"}
    missing = still_to_upload(local, uploaded)
    if missing:
        gh("release", "upload", tag, *(str(folder / name) for name in missing), "--clobber", "--repo", repo)
    gh("release", "edit", tag, "--draft=false", "--latest=false", "--repo", repo)


def previous_manifest(repo: str) -> dict | None:
    """The manifest of the build now published, which this one follows; None for the first build of all."""
    url = f"https://github.com/{repo}/releases/download/{LATEST}/manifest.json"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def build_of(manifest: dict) -> str:
    """A build's tag: written in the manifest since schema 2, the end of its release's address before."""
    return manifest.get("build") or manifest["base_url"].rstrip("/").rsplit("/", 1)[-1]


def deltas_release(tag: str) -> str:
    """The release that keeps the deltas of the month a build belongs to: jobs-20261107-103405 is in jobs-deltas-202611."""
    return f"jobs-deltas-{tag[5:11]}"


def write_delta(previous_index: str, index: Path, out: Path) -> tuple[int, int]:
    """The delta from the previous index to this one: an `add` row for each posting new or changed, a `drop` row for each key
    gone. A changed posting is an add: a reader replaces whatever row it holds under that key. How many adds and drops."""
    columns = ", ".join(INDEX_COLUMNS)
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        # A column added since the previous build is NULL there: every posting is then changed once, and the delta carries it.
        held = {row[0] for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [previous_index]).fetchall()}
        before = ", ".join(column if column in held else "NULL" for column in INDEX_COLUMNS)
        connection.execute(f"CREATE TABLE before AS SELECT key, hash({before}) AS h FROM read_parquet(?)", [previous_index])
        connection.execute(f"CREATE TABLE after AS SELECT key, hash({columns}) AS h FROM read_parquet(?)", [index.as_posix()])
        connection.execute("""
            CREATE TABLE changed AS
            SELECT coalesce(a.key, b.key) AS key, a.key IS NULL AS gone
            FROM after AS a FULL OUTER JOIN before AS b ON a.key = b.key
            WHERE a.key IS NULL OR b.key IS NULL OR a.h <> b.h
        """)
        connection.execute(f"""
            COPY (
                SELECT 'add' AS op, i.key, {", ".join(f"i.{column}" for column in INDEX_COLUMNS)}
                FROM read_parquet(?) AS i SEMI JOIN changed AS c ON i.key = c.key AND NOT c.gone
                UNION ALL
                SELECT 'drop' AS op, c.key, {", ".join(f"NULL AS {column}" for column in INDEX_COLUMNS)}
                FROM changed AS c WHERE c.gone
            ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
        """, [index.as_posix()])
        adds, drops = connection.execute(
            "SELECT count(*) FILTER (WHERE NOT gone), count(*) FILTER (WHERE gone) FROM changed"
        ).fetchone()
        return int(adds), int(drops)
    finally:
        connection.close()


def publish_delta(repo: str, tag: str, path: Path) -> str:
    """The delta into its month's release, made the first time a month needs it; where a reader downloads it."""
    release = deltas_release(tag)
    try:
        gh("release", "view", release, "--repo", repo)
        gh("release", "upload", release, str(path), "--clobber", "--repo", repo)
    except GhError:
        gh("release", "create", release, str(path), "--repo", repo, "--title", f"Job index deltas {tag[5:9]}-{tag[9:11]}",
           "--notes", "The changes to the job index, one file per build. Kept for ever: an AIHawk applies the ones it has not seen.",
           "--latest=false")
    return f"https://github.com/{repo}/releases/download/{release}/{path.name}"


def check(rows: int, previous: int | None) -> None:
    if rows == 0:
        sys.exit("the database is empty: nothing published")
    if previous and rows < previous * MIN_SHARE_OF_PREVIOUS:
        sys.exit(f"{rows} postings against {previous} in the previous build: nothing published. See the manifest's failed sources.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--allow-shrink", action="store_true", help="Publish a database much smaller than the previous build's on purpose.")
    arguments = parser.parse_args()

    manifest_path: Path = arguments.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous = previous_manifest(arguments.repo)
    check(manifest["rows_out"], None if arguments.allow_shrink or previous is None else previous["rows_out"])

    tag = "jobs-" + manifest["built_at"][:19].replace(":", "").replace("-", "").replace("T", "-")
    manifest["build"] = tag
    manifest["deltas"] = []
    if previous is not None:
        delta = arguments.out / f"delta-{tag}.parquet"
        adds, drops = write_delta(f"{previous['base_url']}/{previous['index']['name']}", arguments.out / manifest["index"]["name"], delta)
        url = publish_delta(arguments.repo, tag, delta)
        manifest["deltas"] = [*previous.get("deltas", []), {"build": tag, "after": build_of(previous), "url": url, "adds": adds, "drops": drops}]
        print(f"delta against {build_of(previous)}: {adds} added or changed, {drops} gone")
    names = [manifest["index"]["name"], *(item["name"] for item in manifest["files"])]
    notes = f"{manifest['rows_out']} job postings. See {LATEST} for the current database. Sources and licences: https://github.com/{arguments.repo}/blob/main/docs/data-sources.md"
    publish_files(arguments.repo, tag, arguments.out, names, notes)

    manifest["base_url"] = f"https://github.com/{arguments.repo}/releases/download/{tag}"
    with tempfile.TemporaryDirectory() as folder:
        latest = Path(folder) / "manifest.json"
        latest.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        try:
            gh("release", "view", LATEST, "--repo", arguments.repo)
            gh("release", "upload", LATEST, str(latest), "--clobber", "--repo", arguments.repo)
        except GhError:
            gh("release", "create", LATEST, str(latest), "--repo", arguments.repo, "--title", "Job database: current", "--notes", "The manifest of the current job database.", "--latest=false")

    tags = gh("release", "list", "--repo", arguments.repo, "--limit", "200", "--json", "tagName", "--jq", ".[].tagName").split()
    # The deltas' monthly releases are kept for ever: only the dated builds are cleared.
    dated = sorted(name for name in tags if DATED.match(name))
    for old in dated[:-KEEP]:
        gh("release", "delete", old, "--repo", arguments.repo, "--yes", "--cleanup-tag")
    print(f"published {tag}: {manifest['rows_out']} postings in {len(names)} files")


if __name__ == "__main__":
    main()
