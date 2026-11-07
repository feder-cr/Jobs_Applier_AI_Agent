"""Publishes work/out as this run's release, and points the release `jobs-latest` at it.

A database much smaller than the one before it is refused: losing a big source must not silently empty everyone's search.
The dated releases keep the files; `jobs-latest` holds only the manifest that names them, so a reader never sees a
half-replaced database. The last KEEP dated releases stay, so a reader mid-search does not lose its files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

LATEST = "jobs-latest"
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


def previous_rows(repo: str) -> int | None:
    url = f"https://github.com/{repo}/releases/download/{LATEST}/manifest.json"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["rows_out"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


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
    check(manifest["rows_out"], None if arguments.allow_shrink else previous_rows(arguments.repo))

    tag = "jobs-" + manifest["built_at"][:19].replace(":", "").replace("-", "").replace("T", "-")
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
    dated = sorted(name for name in tags if name.startswith("jobs-") and name != LATEST)
    for old in dated[:-KEEP]:
        gh("release", "delete", old, "--repo", arguments.repo, "--yes", "--cleanup-tag")
    print(f"published {tag}: {manifest['rows_out']} postings in {len(names)} files")


if __name__ == "__main__":
    main()
