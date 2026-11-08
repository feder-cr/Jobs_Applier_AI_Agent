"""Builds AIHawk's job database: every public job database we know of, in one schema, one row per posting.

    python pipeline/build.py --out work                 # everything
    python pipeline/build.py --out work --only riley    # only these sources

Scheduled every five hours in .github/workflows/jobs-database.yml, which publishes work/out to the Hugging Face dataset feder-cr/jobs. Each source is read into
work/sources/<name>.parquet in the columns of COMMON. A source that fails is left out of that build's database and named in
the manifest; publish.py refuses a database that lost too much of itself. Postings are the same posting when their links
are the same after `KEY`, and of those the row with the longest description is kept, the other rows adding their source's
name. The output is cut in files of ROWS_PER_FILE postings, with index.parquet beside them: every posting without its
description, and where to find that description. A search reads the index; only the jobs being judged read descriptions.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

HF = "https://huggingface.co/datasets"
HF_API = "https://huggingface.co/api/datasets"
RAW = "https://raw.githubusercontent.com"
# Files of about 128 MB and row groups of 2048 rows: small row groups let a search read only the rows it needs over HTTP.
# A group is a whole description column chunk, which DuckDB cannot write below 2048 rows.
# Postings to a description file: about 120 MB at the measured 590 bytes a posting, under GitHub's 2 GiB a file, and small
# enough that one file is sorted by key in memory.
ROWS_PER_FILE = 200_000
ROW_GROUP = 2048
INDEX = "index.parquet"
# A description shorter than this says nothing a title does not: the index marks the posting as having none, and a search
# puts it below one that has.
DESCRIBED = 200

COMMON = (
    "url", "title", "company", "ats", "location", "country", "remote",
    "salary_min", "salary_max", "salary_currency", "description", "posted_at",
)  # fmt: skip

# The posting's identity in its link: no scheme, no www, no tracking, no /apply step, in lower case.
KEY = r"""lower(regexp_replace(regexp_replace(regexp_replace(
    url, '^https?://(www\.)?', ''),
    '[?&](utm_[a-z]+|gh_src|source|src|ref|lever-[a-z-]+|trk|jobPipeline)=[^&#]*', '', 'g'),
    '(/apply|/application|/)?([?#].*)?$', '\2'))"""
# Greenhouse serves one posting from several hosts.
KEY = rf"""regexp_replace({KEY}, '^(job-boards|boards)(\.eu)?\.greenhouse\.io/', 'greenhouse.io/')"""


def _text(value: str | None) -> str:
    return f"CAST({value} AS VARCHAR)" if value else "CAST(NULL AS VARCHAR)"


def _number(value: str | None) -> str:
    return f"TRY_CAST({value} AS DOUBLE)" if value else "CAST(NULL AS DOUBLE)"


def select(
    source: str,
    url: str,
    title: str = "title",
    company: str | None = "company",
    ats: str | None = None,
    location: str | None = "location",
    country: str | None = None,
    remote: str | None = None,
    salary_min: str | None = None,
    salary_max: str | None = None,
    salary_currency: str | None = None,
    description: str | None = None,
    posted_at: str | None = None,
    where: str = "TRUE",
) -> str:
    """The SELECT that turns one source's rows into COMMON."""
    return f"""SELECT
        {_text(url)} AS url, {_text(title)} AS title, {_text(company)} AS company, lower({_text(ats)}) AS ats,
        {_text(location)} AS location, upper({_text(country)}) AS country, TRY_CAST({remote or 'NULL'} AS BOOLEAN) AS remote,
        {_number(salary_min)} AS salary_min, {_number(salary_max)} AS salary_max, {_text(salary_currency)} AS salary_currency,
        {_text(description)} AS description, {_text(posted_at)} AS posted_at
        FROM {source} WHERE ({where})"""


def _hf_files(dataset: str, prefix: str) -> list[str]:
    with urllib.request.urlopen(f"{HF_API}/{dataset}", timeout=60) as response:
        names = [item["rfilename"] for item in json.load(response)["siblings"]]
    return [f"{HF}/{dataset}/resolve/main/{name}" for name in names if name.startswith(prefix) and name.endswith(".parquet")]


def _unnested(url: str, field: str) -> str:
    """The rows of a JSON file that holds them under one field, each as `u`."""
    return f"(SELECT unnest({field}) AS u FROM read_json('{url}', maximum_object_size=200000000))"


def _github_release_asset(repo: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/latest/download/{name}"


@dataclass(frozen=True)
class Source:
    name: str
    home: str
    license: str
    sql: Callable[[Path], str]


def _simplify(repo: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_json('{RAW}/{repo}/HEAD/.github/scripts/listings.json', maximum_object_size=200000000, format='array')",
        url="url", company="company_name", location="array_to_string(locations, '; ')",
        posted_at="make_timestamp(date_posted * 1000000)", where="active AND is_visible",
    )  # fmt: skip


def _open_jobs_data(repo: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_json('{RAW}/{repo}/HEAD/data/jobs.json', maximum_object_size=200000000, format='array')",
        url="applyUrl", ats="platform", location="array_to_string(locations, '; ')", remote="isRemote", posted_at="postedAt",
    )  # fmt: skip


def _headcount(name: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_parquet('{HF}/HeadcountLabs/{name}/resolve/main/data/part-0.parquet')",
        url="coalesce(application_url, posting_url)", company="employer_name",
        location="concat_ws(', ', location_city, location_region, location_country)", country="location_country",
        remote="work_arrangement = 'remote'", salary_min="pay_min", salary_max="pay_max", salary_currency="pay_currency",
        posted_at="posted_at", where="currentness = 'current'",
    )  # fmt: skip


def _scholarweave(_: Path) -> str:
    files = _hf_files("scholarweave/Jobs-Dataset-v2", "data/active/full/current/")
    companies = max(_hf_files("scholarweave/Jobs-Dataset-v2", "data/companies/"))
    source = (
        f"read_parquet({files!r}) AS job LEFT JOIN read_parquet('{companies}') AS firm ON job.company_id = firm.id"
    )
    return select(
        source, url="job.apply_url", title="job.title", company="firm.name", ats="firm.ats",
        location="job_model_json->>'$.location.city'", country="job.country", remote="job.is_remote",
        description="job_model_json->>'$.description_plain'", posted_at="job.posted_at",
    )  # fmt: skip


def _eu_tech(_: Path) -> str:
    latest = sorted(_hf_files("Aramente/eu-tech-jobs", "snapshots/"))
    jobs = [name for name in latest if name.endswith("/jobs.parquet")][-1]
    return select(
        f"read_parquet('{jobs}')", url="url", company="company_slug", ats="source", location="location",
        remote="remote_policy = 'remote'", salary_min="salary_min", salary_max="salary_max",
        salary_currency="salary_currency", description="description_md", posted_at="posted_at",
    )  # fmt: skip


def _rolebase(work: Path) -> str:
    # Thousands of small files in the repository: a shallow clone is the one way to read them in a reasonable time.
    clones = work / "clones"
    clones.mkdir(parents=True, exist_ok=True)
    clone = Path(tempfile.mkdtemp(dir=clones)) / "roleBase"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", "https://github.com/chandranshB/roleBase", str(clone)],
        check=True, timeout=3600,
    )  # fmt: skip
    pattern = (clone / "data" / "jobs" / "*.jsonl").as_posix()
    source = (
        f"read_json('{pattern}', format='newline_delimited', union_by_name=true, ignore_errors=true, "
        "columns={id: 'VARCHAR', company: 'VARCHAR', title: 'VARCHAR', location: 'VARCHAR', remote: 'BOOLEAN', "
        "url: 'VARCHAR', posted: 'VARCHAR', closed_at: 'VARCHAR'})"
    )
    # A link is left out of the file when it follows the board's standard pattern: for these the pattern is known.
    url = r"""coalesce(url, CASE split_part(id, ':', 1)
        WHEN 'greenhouse' THEN 'https://job-boards.greenhouse.io/' || split_part(id, ':', 2) || '/jobs/' || split_part(id, ':', 3)
        WHEN 'lever' THEN 'https://jobs.lever.co/' || split_part(id, ':', 2) || '/' || split_part(id, ':', 3)
        WHEN 'ashby' THEN 'https://jobs.ashbyhq.com/' || split_part(id, ':', 2) || '/' || split_part(id, ':', 3)
        END)"""
    # Links ending in a 12-character hex code are not the board's own ids, and many of them answer 404 or 403 when checked.
    # A posting with no working link is of no use to anyone.
    usable = r"closed_at IS NULL AND NOT regexp_matches(coalesce(url, ''), '/job/[0-9a-f]{12}$')"
    return select(source, url=url, ats="split_part(id, ':', 1)", remote="remote", posted_at="posted", where=usable)


def _clean_up(work: Path) -> None:
    """The clone is read: its room is for the merge."""
    shutil.rmtree(work / "clones", ignore_errors=True)


SOURCES = (
    Source("jobhive", "https://github.com/kalil0321/ats-scrapers", "not stated", lambda _: select(
        "read_parquet('https://storage.stapply.ai/jobhive/v1/all.parquet')",
        url="coalesce(apply_url, url)", ats="ats_type", country="country_iso", remote="is_remote",
        salary_min="salary_min", salary_max="salary_max", salary_currency="salary_currency",
        description="description", posted_at="posted_at",
    )),
    Source("riley", "https://github.com/Riley-Dorrington/job-board-aggregator", "MIT (code)", lambda _: select(
        f"read_json('{_github_release_asset('Riley-Dorrington/job-board-aggregator', 'all_jobs.json.gz')}', "
        "format='array', compression='gzip', maximum_object_size=100000000)",
        url="url", ats="ats", remote="remote",
    )),
    Source("rolebase", "https://github.com/chandranshB/roleBase", "AGPL-3.0", _rolebase),
    Source("scholarweave", "https://huggingface.co/datasets/scholarweave/Jobs-Dataset-v2", "Apache-2.0", _scholarweave),
    Source("headcount-pay", "https://huggingface.co/datasets/HeadcountLabs/pay-transparency-jobs", "ODbL", _headcount("pay-transparency-jobs")),
    Source("headcount-tech", "https://huggingface.co/datasets/HeadcountLabs/tech-jobs", "ODbL", _headcount("tech-jobs")),
    Source("headcount-remote", "https://huggingface.co/datasets/HeadcountLabs/remote-jobs", "ODbL", _headcount("remote-jobs")),
    Source("headcount-internships", "https://huggingface.co/datasets/HeadcountLabs/internships", "ODbL", _headcount("internships")),
    Source("headcount-healthcare", "https://huggingface.co/datasets/HeadcountLabs/healthcare-jobs", "ODbL", _headcount("healthcare-jobs")),
    Source("eu-tech-jobs", "https://huggingface.co/datasets/Aramente/eu-tech-jobs", "CC-BY-4.0", _eu_tech),
    Source("minmaxxxer", "https://huggingface.co/datasets/minmaxxxer/job-postings-with-salaries", "research use, no republishing as listings", lambda _: select(
        f"read_parquet('{HF}/minmaxxxer/job-postings-with-salaries/resolve/main/job_postings_with_salaries.parquet')",
        url="coalesce(apply_url, url)", ats="ats", country="country_code", remote="workplace_type = 'remote'",
        salary_min="salary_min", salary_max="salary_max", salary_currency="salary_currency",
        description="description", posted_at="posted_at",
    )),
    Source("lookin4jobs", "https://github.com/lookin4jobs/open-jobs-data", "MIT (code)", _open_jobs_data("lookin4jobs/open-jobs-data")),
    Source("conorscode", "https://github.com/ConorsCode/open-jobs-data", "MIT (code)", _open_jobs_data("ConorsCode/open-jobs-data")),
    Source("fecklesspleb", "https://github.com/fecklesspleb/ats-new-jobs-feed", "MIT (code)", lambda _: select(
        f"read_csv('{RAW}/fecklesspleb/ats-new-jobs-feed/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="url", ats="ats", remote="workplace = 'remote'", posted_at="posted_at",
    )),
    Source("simplify-new-grad", "https://github.com/SimplifyJobs/New-Grad-Positions", "not stated", _simplify("SimplifyJobs/New-Grad-Positions")),
    Source("simplify-internships", "https://github.com/SimplifyJobs/Summer2027-Internships", "not stated", _simplify("SimplifyJobs/Summer2027-Internships")),
    Source("vanshb03-new-grad", "https://github.com/vanshb03/New-Grad-2027", "MIT", _simplify("vanshb03/New-Grad-2027")),
    Source("vanshb03-internships", "https://github.com/vanshb03/Summer2026-Internships", "MIT", _simplify("vanshb03/Summer2026-Internships")),
    Source("simplify-trabaho", "https://github.com/yanicells/SimplifyTrabaho", "CC0-1.0 (data), MIT (code)", lambda _: select(
        _unnested(f"{RAW}/yanicells/SimplifyTrabaho/HEAD/data/listings.json", "listings"),
        url="u.url", title="u.title", company="u.company", ats="u.source", location="array_to_string(u.locations, '; ')",
        remote="u.workSetup = 'remote'",
    )),
    Source("headcount-engineering-interns", "https://github.com/HeadcountLabs/engineering-internships", "ODbL", lambda _: select(
        f"read_csv('{RAW}/HeadcountLabs/engineering-internships/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="apply_url", location="locations", remote="remote = 'true'", posted_at="posted",
    )),
    Source("headcount-software-interns", "https://github.com/HeadcountLabs/software-engineering-internships", "ODbL", lambda _: select(
        f"read_csv('{RAW}/HeadcountLabs/software-engineering-internships/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="apply_url", location="locations", remote="remote = 'true'", posted_at="posted",
    )),
    Source("applyguy", "https://github.com/ApplyGuy/2027-New-Grad-Jobs", "not stated", lambda _: select(
        _unnested(f"{RAW}/ApplyGuy/2027-New-Grad-Jobs/HEAD/data/new-grad-jobs.json", "jobs"),
        url="u.url", title="u.title", company="u.company", location="u.location", posted_at="u.posted",
    )),
    Source("fde-jobs", "https://github.com/stephanelx88/forward-deployed-engineer-jobs", "CC-BY-4.0", lambda _: select(
        f"read_csv('{RAW}/stephanelx88/forward-deployed-engineer-jobs/HEAD/data/fde-jobs.csv', header=true, all_varchar=true)",
        url="fdetimes_url", country="country", salary_min="pay_low_usd", salary_max="pay_high_usd", posted_at="first_seen",
    )),
    Source("remote-india", "https://github.com/raghavendra81422-boop/remote-jobs-open-to-india", "MIT", lambda _: select(
        _unnested(f"{RAW}/raghavendra81422-boop/remote-jobs-open-to-india/HEAD/data.json", "jobs"),
        url="u.roliva_url", title="u.title", company="u.company", location="u.location", remote="TRUE", posted_at="u.posted_date",
    )),
)  # fmt: skip


def connect(work: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute(f"SET temp_directory = '{(work / 'tmp').as_posix()}'")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET max_temp_directory_size = '40GB'")
    # Below the runner's 16 GB, so DuckDB spills to the temp directory before the system runs out: a build that sorted
    # with the default limit used 12.4 GiB of 12.4 and stopped.
    connection.execute("SET memory_limit = '9GB'")
    return connection


def read_source(connection: duckdb.DuckDBPyConnection, source: Source, work: Path) -> dict[str, object]:
    """One source into work/sources/<name>.parquet. A failure is recorded, never raised: the others still run."""
    path = work / "sources" / f"{source.name}.parquet"
    started = time.monotonic()
    report: dict[str, object] = {"name": source.name, "home": source.home, "license": source.license}
    try:
        connection.execute(
            f"COPY ({source.sql(work)}) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"
        )
        report["rows"] = connection.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]
        report["seconds"] = round(time.monotonic() - started)
        print(f"{source.name}: {report['rows']} rows in {report['seconds']} s", flush=True)
    except Exception as error:  # noqa: BLE001  One source's failure must not lose the other sources.
        path.unlink(missing_ok=True)
        report["error"] = str(error)[:500]
        print(f"{source.name}: FAILED: {report['error']}", file=sys.stderr, flush=True)
    return report


def merge(connection: duckdb.DuckDBPyConnection, work: Path) -> dict[str, int]:
    """One row per posting, cut in files. Returns how many rows went in and came out.

    Which row wins is decided on the keys alone, and only then are the winners' rows, descriptions included, read and
    written: sorting 20 million descriptions would take more disk than a runner has.
    """
    inputs = sorted((work / "sources").glob("*.parquet"))
    if not inputs:
        raise SystemExit("no source could be read")
    files = [path.as_posix() for path in inputs]
    every_row = f"read_parquet({files!r}, filename = true, file_row_number = true)"
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE best AS
        SELECT key, count(*) AS copies, list_sort(list(DISTINCT source)) AS sources,
            -- The longest description wins. Copies that tie, most often two with none, are settled by the source's name
            -- and then the row: left to DuckDB, a tie went a different way in each build, and a third of the postings
            -- came out changed in a delta though nothing about them had.
            arg_max(
                struct_pack(filename := filename, row := file_row_number),
                struct_pack(length := length, source := source, row := -file_row_number)
            ) AS winner
        FROM (
            SELECT {KEY} AS key, regexp_extract(filename, '([^/\\\\]+)\\.parquet$', 1) AS source, filename, file_row_number,
                length(coalesce(description, '')) AS length
            FROM {every_row}
            WHERE url LIKE 'http%' AND coalesce(title, '') <> ''
        ) GROUP BY key
    """)
    rows_in, rows_out_expected = connection.execute("SELECT sum(copies), count(*) FROM best").fetchone()
    out = work / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    columns = ", ".join(f"row.{column}" for column in COMMON)
    # The files are in key order, so a reader that asks for a posting by its key reads the one row group that can hold it,
    # whatever build it came from. Sorting ten million rows with their descriptions at once ran a runner out of memory,
    # so only the keys are sorted, into ranges of ROWS_PER_FILE; the rows go to their range's part in one pass; and each
    # part, a file's worth, is sorted alone.
    connection.execute(f"CREATE OR REPLACE TEMP TABLE ranges AS SELECT key, (row_number() OVER (ORDER BY key) - 1) // {ROWS_PER_FILE} AS part FROM best")
    parts = work / "tmp" / "parts"
    shutil.rmtree(parts, ignore_errors=True)
    connection.execute(f"""
        COPY (
            SELECT best.key, {columns}, best.sources, ranges.part
            FROM {every_row} AS row
            JOIN best ON row.filename = best.winner.filename AND row.file_row_number = best.winner.row
            JOIN ranges ON ranges.key = best.key
        ) TO '{parts.as_posix()}' (
            -- One open file a range, each holding a row group in memory: small row groups keep fifty of them light.
            FORMAT parquet, COMPRESSION zstd, PARTITION_BY (part), ROW_GROUP_SIZE {ROW_GROUP}
        )
    """)
    for part in sorted(parts.glob("part=*"), key=lambda folder: int(folder.name.split("=")[1])):
        connection.execute(f"""
            COPY (SELECT * FROM read_parquet('{part.as_posix()}/*.parquet', hive_partitioning = false) ORDER BY key)
            TO '{(out / f"jobs_{part.name.split('=')[1]}.parquet").as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})
        """)
        shutil.rmtree(part)
    rows_out = connection.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}/jobs_*.parquet')").fetchone()[0]
    if rows_out != rows_out_expected:
        raise SystemExit(f"{rows_out_expected} postings chosen and {rows_out} written")
    connection.execute(f"""
        COPY (
            SELECT key, url, title, company, ats, location, country, remote, salary_min, salary_max, salary_currency, posted_at,
                length(coalesce(description, '')) >= {DESCRIBED} AS described,
                regexp_extract(filename, '([^/\\\\\\\\]+)$', 1) AS part, file_row_number AS row_number
            FROM read_parquet('{out.as_posix()}/jobs_*.parquet', filename = true, file_row_number = true)
        ) TO '{(out / INDEX).as_posix()}' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 9)
    """)
    return {"rows_in": int(rows_in), "rows_out": rows_out}


def file_entry(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, object]:
    """A description file as the manifest names it: its size, and the first and last key it holds."""
    first, last = connection.execute("SELECT min(key), max(key) FROM read_parquet(?)", [path.as_posix()]).fetchone()
    return {"name": path.name, "bytes": path.stat().st_size, "first_key": first, "last_key": last}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("work"))
    parser.add_argument("--only", nargs="*", help="Read only these sources.")
    parser.add_argument("--reuse", action="store_true", help="Keep a source already read in this work folder.")
    arguments = parser.parse_args()

    work: Path = arguments.out.resolve()
    (work / "sources").mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    connection = connect(work)
    started = time.monotonic()
    chosen = [source for source in SOURCES if not arguments.only or source.name in arguments.only]
    reports = []
    for source in chosen:
        if arguments.reuse and (work / "sources" / f"{source.name}.parquet").exists():
            reports.append({"name": source.name, "home": source.home, "license": source.license, "reused": True})
            continue
        reports.append(read_source(connection, source, work))
    _clean_up(work)
    counts = merge(connection, work)
    out = work / "out"
    manifest = {
        # 2: the description files are in key order and say their first and last key, and the dataset carries a
        # delta of the index for each build. Everything schema 1 had is still here, for an AIHawk not yet updated.
        "schema": 2,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **counts,
        "seconds": round(time.monotonic() - started),
        "columns": ["key", *COMMON, "sources"],
        "index": {"name": INDEX, "bytes": (out / INDEX).stat().st_size},
        "files": [file_entry(connection, path) for path in sorted(out.glob("jobs_*.parquet"))],
        "sources": reports,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{counts['rows_in']} rows in, {counts['rows_out']} postings out, {len(manifest['files'])} files, {manifest['seconds']} s")


if __name__ == "__main__":
    main()
