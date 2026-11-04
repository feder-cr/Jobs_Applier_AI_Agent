"""Publishes work/out as today's release, and points the release `jobs-latest` at it.

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
KEEP = 3
MIN_SHARE_OF_PREVIOUS = 0.7


def gh(*arguments: str) -> str:
    return subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True).stdout


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
        sys.exit(f"{rows} postings against {previous} yesterday: nothing published. See the manifest's failed sources.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    arguments = parser.parse_args()

    manifest_path: Path = arguments.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check(manifest["rows_out"], previous_rows(arguments.repo))

    tag = "jobs-" + manifest["built_at"][:19].replace(":", "").replace("-", "").replace("T", "-")
    files = [str(arguments.out / item["name"]) for item in manifest["files"]]
    notes = f"{manifest['rows_out']} job postings. See {LATEST} for the current database."
    gh("release", "create", tag, *files, "--repo", arguments.repo, "--title", f"Job database {tag}", "--notes", notes, "--latest=false")

    manifest["base_url"] = f"https://github.com/{arguments.repo}/releases/download/{tag}"
    with tempfile.TemporaryDirectory() as folder:
        latest = Path(folder) / "manifest.json"
        latest.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        try:
            gh("release", "view", LATEST, "--repo", arguments.repo)
            gh("release", "upload", LATEST, str(latest), "--clobber", "--repo", arguments.repo)
        except subprocess.CalledProcessError:
            gh("release", "create", LATEST, str(latest), "--repo", arguments.repo, "--title", "Job database: current", "--notes", "The manifest of the current job database.", "--latest=false")

    tags = gh("release", "list", "--repo", arguments.repo, "--limit", "200", "--json", "tagName", "--jq", ".[].tagName").split()
    dated = sorted(name for name in tags if name.startswith("jobs-") and name != LATEST)
    for old in dated[:-KEEP]:
        gh("release", "delete", old, "--repo", arguments.repo, "--yes", "--cleanup-tag")
    print(f"published {tag}: {manifest['rows_out']} postings in {len(files)} files")


if __name__ == "__main__":
    main()
