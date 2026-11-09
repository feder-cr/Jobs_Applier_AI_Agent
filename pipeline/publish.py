"""Publishes work/out to the Hugging Face dataset AIHawk reads, in one commit: the build's chunks, its delta and the
manifest that names them, so a reader never sees a half-replaced database.

Everything is under `library/`: the chunks of a build in `library/builds/<tag>/`, the deltas in `library/deltas/`, the
manifest at `library/manifest.json`. The layout before it, an index and description files read in place at the dataset's
root, is removed by the first commit that finds it: AIHawk brings itself up to date at every start, and nothing reads it.

A database much smaller than the one before it is refused: losing a big source must not silently empty everyone's search.
The last KEEP builds stay, so a reader still downloading one does not lose its chunks. Each build also publishes the delta
against the build before it: every posting added or changed, whole, with its description, and the addresses gone. A
reader that has a build applies the deltas after it in order and never downloads the whole database again; the last
KEEP_DELTAS are kept, and a reader further behind downloads the newest build instead. After the commit the history is
squashed: the files old commits hold would otherwise grow the dataset by two gigabytes a build.

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
FOLDER = "library"
RESOLVE = f"https://huggingface.co/datasets/{DATASET}/resolve/main/{FOLDER}"
# The layout before 2026-11-09, at the dataset's root: removed. The owner's decision of 2026-11-09.
OLD_LAYOUT = ("builds", "deltas", "manifest.json")
# The columns of a chunk, which a delta carries too, after its `op`: the address is the key.
COLUMNS = ("url", "title", "company", "location", "country", "posted_at", "description")
KEEP = 6  # five-hourly builds: about 30 hours, more than a slow line takes to download one
KEEP_DELTAS = 60  # about twelve days of builds
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


def write_delta(previous: list[str], chunks: list[Path], out: Path) -> tuple[int, int]:
    """The delta from the previous build's chunks to these: an `add` row for each posting new or changed, whole, and a
    `drop` row for each address gone. A changed posting is an add: a reader replaces whatever row it holds under that
    address. How many adds and drops."""
    fields = ", ".join(column for column in COLUMNS if column != "url")
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        connection.execute(f"CREATE TABLE before AS SELECT url, hash({fields}) AS h FROM read_parquet(?)", [previous])
        now = [path.as_posix() for path in chunks]
        connection.execute(f"CREATE TABLE after AS SELECT url, hash({fields}) AS h FROM read_parquet(?)", [now])
        connection.execute("""
            CREATE TABLE changed AS
            SELECT coalesce(a.url, b.url) AS url, a.url IS NULL AS gone
            FROM after AS a FULL OUTER JOIN before AS b ON a.url = b.url
            WHERE a.url IS NULL OR b.url IS NULL OR a.h <> b.h
        """)
        connection.execute(f"""
            COPY (
                SELECT 'add' AS op, {", ".join(f"c.{column}" for column in COLUMNS)}
                FROM read_parquet(?) AS c SEMI JOIN changed AS d ON c.url = d.url AND NOT d.gone
                UNION ALL
                SELECT 'drop' AS op, d.url, {", ".join(f"NULL AS {column}" for column in COLUMNS if column != "url")}
                FROM changed AS d WHERE d.gone
            ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 12)
        """, [now])
        adds, drops = connection.execute("SELECT count(*) FILTER (WHERE NOT gone), count(*) FILTER (WHERE gone) FROM changed").fetchone()
        return int(adds), int(drops)
    finally:
        connection.close()


def check(rows: int, previous: int | None) -> None:
    if rows == 0:
        sys.exit("the database is empty: nothing published")
    if previous and rows < previous * MIN_SHARE_OF_PREVIOUS:
        sys.exit(f"{rows} postings against {previous} in the previous build: nothing published. See the manifest's failed sources.")


def commit_plan(out: Path, manifest: dict, delta: Path | None, built: list[str], stored_deltas: list[str]) -> tuple[list[tuple[str, Path]], list[str], list[str]]:
    """What the commit adds, as (path in the dataset, file here), the build folders it removes, all but the last KEEP,
    and the delta files it removes, those the manifest no longer names."""
    tag = manifest["build"]
    adds = [(f"{FOLDER}/builds/{tag}/{chunk['name']}", out / chunk["name"]) for chunk in manifest["chunks"]]
    if delta is not None:
        adds.append((f"{FOLDER}/deltas/{delta.name}", delta))
    adds.append((f"{FOLDER}/manifest.json", out / "manifest.json"))
    kept = sorted({*built, tag})[-KEEP:]
    named = {entry["url"].rsplit("/", 1)[-1] for entry in manifest["deltas"]}
    return adds, [f"{FOLDER}/builds/{old}" for old in sorted(built) if old not in kept], [f"{FOLDER}/deltas/{name}" for name in sorted(stored_deltas) if name not in named]


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
        chunks = [arguments.out / chunk["name"] for chunk in manifest["chunks"]]
        adds, drops = write_delta([f"{previous['base_url']}/{chunk['name']}" for chunk in previous["chunks"]], chunks, delta)
        entry = {"build": tag, "after": previous["build"], "url": f"{RESOLVE}/deltas/{delta.name}", "bytes": delta.stat().st_size, "adds": adds, "drops": drops}
        manifest["deltas"] = [*previous.get("deltas", []), entry][-KEEP_DELTAS:]
        print(f"delta against {previous['build']}: {adds} added or changed, {drops} gone, {delta.stat().st_size // 1_000_000} MB")
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    def listed(folder: str) -> list[str]:
        try:
            return [item.path.rsplit("/", 1)[-1] for item in api.list_repo_tree(DATASET, path_in_repo=f"{FOLDER}/{folder}", repo_type="dataset")]
        except Exception:  # noqa: BLE001  The first build: there is no such folder yet.
            return []

    adds, removed, old_deltas = commit_plan(arguments.out, manifest, delta, listed("builds"), listed("deltas"))
    operations = [CommitOperationAdd(path_in_repo=path, path_or_fileobj=str(local)) for path, local in adds]
    operations += [CommitOperationDelete(path_in_repo=f"{folder}/", is_folder=True) for folder in removed]
    operations += [CommitOperationDelete(path_in_repo=path) for path in old_deltas]
    root = {item.path for item in api.list_repo_tree(DATASET, repo_type="dataset")}
    operations += [CommitOperationDelete(path_in_repo=f"{path}/" if "." not in path else path, is_folder="." not in path) for path in OLD_LAYOUT if path in root]
    api.create_commit(DATASET, repo_type="dataset", operations=operations, commit_message=f"{tag}: {manifest['rows_out']} postings")
    api.super_squash_history(DATASET, repo_type="dataset", commit_message=f"The job database as of {tag}")
    print(f"published {tag}: {manifest['rows_out']} postings, {len(adds)} files; removed {len(removed)} old builds and {len(old_deltas)} old deltas")


if __name__ == "__main__":
    main()
