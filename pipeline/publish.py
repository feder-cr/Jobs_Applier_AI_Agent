"""Publishes work/out to the Hugging Face dataset AIHawk reads, in one commit: the build's files, its delta and the
manifest that names them, so a reader never sees a half-replaced database.

A database much smaller than the one before it is refused: losing a big source must not silently empty everyone's search.
The last KEEP builds stay, so a reader mid-search does not lose its files. Each build also publishes the delta of the
index against the build before it: the postings added, the ones whose fields changed, and the keys gone. A reader that
has the index applies them in order and never downloads the whole of it again; they are kept for ever, because a reader
may come back after any time. After the commit the history is squashed: the files old commits hold would otherwise grow
the dataset by three gigabytes a build.

The database moved from GitHub's releases on 2026-11-08, when github.com answered 504 for its downloads for hours and its
API allows sixty calls an hour with no account; Hugging Face allows three thousand every five minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

DATASET = "feder-cr/jobs"
RESOLVE = f"https://huggingface.co/datasets/{DATASET}/resolve/main"
# The columns a delta carries: the index's own, without the file and row that point into one build's files.
INDEX_COLUMNS = ("url", "title", "company", "ats", "location", "country", "remote", "salary_min", "salary_max", "salary_currency", "posted_at", "described")
KEEP = 6  # five-hourly builds: about 30 hours of builds, so a reader mid-search does not lose its files
MIN_SHARE_OF_PREVIOUS = 0.7


def previous_manifest() -> dict | None:
    """The manifest of the build now published, which this one follows; None for the first build of all."""
    try:
        with urllib.request.urlopen(f"{RESOLVE}/manifest.json", timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 404):  # Hugging Face answers 401 for a file a dataset does not have yet
            return None
        raise


def build_of(manifest: dict) -> str:
    """A build's tag: written in the manifest since schema 2, the end of its release's address before."""
    return manifest.get("build") or manifest["base_url"].rstrip("/").rsplit("/", 1)[-1]


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


def check(rows: int, previous: int | None) -> None:
    if rows == 0:
        sys.exit("the database is empty: nothing published")
    if previous and rows < previous * MIN_SHARE_OF_PREVIOUS:
        sys.exit(f"{rows} postings against {previous} in the previous build: nothing published. See the manifest's failed sources.")


def commit_plan(out: Path, manifest: dict, delta: Path | None, built: list[str]) -> tuple[list[tuple[str, Path]], list[str]]:
    """What the commit adds, as (path in the dataset, file here), and the build folders it removes: all but the last KEEP."""
    tag = manifest["build"]
    names = [manifest["index"]["name"], *(item["name"] for item in manifest["files"])]
    adds = [(f"builds/{tag}/{name}", out / name) for name in names]
    if delta is not None:
        adds.append((f"deltas/{delta.name}", delta))
    adds.append(("manifest.json", out / "manifest.json"))
    kept = sorted({*built, tag})[-KEEP:]
    return adds, [f"builds/{old}" for old in sorted(built) if old not in kept]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-shrink", action="store_true", help="Publish a database much smaller than the previous build's on purpose.")
    arguments = parser.parse_args()
    # Imported here: the tests of the rest of this file do not need it, and the app never does.
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

    api = HfApi()  # the token is HF_TOKEN's
    api.create_repo(DATASET, repo_type="dataset", exist_ok=True)
    manifest_path: Path = arguments.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous = previous_manifest()
    check(manifest["rows_out"], None if arguments.allow_shrink or previous is None else previous["rows_out"])

    tag = "jobs-" + manifest["built_at"][:19].replace(":", "").replace("-", "").replace("T", "-")
    manifest["build"] = tag
    manifest["base_url"] = f"{RESOLVE}/builds/{tag}"
    manifest["deltas"] = []
    delta = None
    if previous is not None:
        delta = arguments.out / f"delta-{tag}.parquet"
        adds, drops = write_delta(f"{previous['base_url']}/{previous['index']['name']}", arguments.out / manifest["index"]["name"], delta)
        manifest["deltas"] = [*previous.get("deltas", []), {"build": tag, "after": build_of(previous), "url": f"{RESOLVE}/deltas/{delta.name}", "adds": adds, "drops": drops}]
        print(f"delta against {build_of(previous)}: {adds} added or changed, {drops} gone")
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    try:
        built = [item.path.removeprefix("builds/") for item in api.list_repo_tree(DATASET, path_in_repo="builds", repo_type="dataset")]
    except Exception:  # noqa: BLE001  The first build: there is no builds folder yet.
        built = []
    adds, removed = commit_plan(arguments.out, manifest, delta, built)
    operations = [CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(local)) for path, local in adds]
    operations += [CommitOperationDelete(path_in_repo=f"{folder}/", is_folder=True) for folder in removed]
    api.create_commit(DATASET, repo_type="dataset", operations=operations, commit_message=f"{tag}: {manifest['rows_out']} postings")
    api.super_squash_history(DATASET, repo_type="dataset", commit_message=f"The job database as of {tag}")
    print(f"published {tag}: {manifest['rows_out']} postings, {len(adds)} files; removed {len(removed)} old builds")


if __name__ == "__main__":
    main()
