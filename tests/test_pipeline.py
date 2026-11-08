"""The merge: one row per posting, the richest row kept, and no database published when it lost too much."""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
import uuid
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "pipeline" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = load("build")
publish = load("publish")


def write_source(work: Path, name: str, rows: list[tuple]) -> None:
    connection = duckdb.connect()
    columns = ", ".join(f"{column} {kind}" for column, kind in (
        ("url", "VARCHAR"), ("title", "VARCHAR"), ("company", "VARCHAR"), ("ats", "VARCHAR"), ("location", "VARCHAR"),
        ("country", "VARCHAR"), ("remote", "BOOLEAN"), ("salary_min", "DOUBLE"), ("salary_max", "DOUBLE"),
        ("salary_currency", "VARCHAR"), ("description", "VARCHAR"), ("posted_at", "VARCHAR"),
    ))
    connection.execute(f"CREATE TABLE t ({columns})")
    connection.executemany("INSERT INTO t (url, title, description) VALUES (?, ?, ?)", rows)
    (work / "sources").mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY t TO '{(work / 'sources' / (name + '.parquet')).as_posix()}' (FORMAT parquet)")


def merged(tmp_path: Path) -> tuple[dict, dict[str, tuple]]:
    (tmp_path / "tmp").mkdir()
    connection = build.connect(tmp_path)
    counts = build.merge(connection, tmp_path)
    out = (tmp_path / "out").as_posix()
    rows = duckdb.connect().execute(
        f"SELECT i.url, i.title, d.description, d.sources FROM read_parquet('{out}/index.parquet') AS i"
        f" JOIN read_parquet('{out}/jobs_*.parquet') AS d USING (key)"
    ).fetchall()
    return counts, {row[0]: row for row in rows}


def test_the_same_posting_from_two_sources_is_one_row_with_the_longest_description_and_both_names(tmp_path: Path) -> None:
    write_source(tmp_path, "short", [("https://jobs.lever.co/a/1/apply?utm_source=x", "Engineer", None)])
    write_source(tmp_path, "long", [("https://www.jobs.lever.co/A/1", "Engineer", "A full description")])
    counts, rows = merged(tmp_path)
    assert counts == {"rows_in": 2, "rows_out": 1}
    row = next(iter(rows.values()))
    assert row[2] == "A full description"
    assert sorted(row[3]) == ["long", "short"]


def test_copies_that_tie_go_the_same_way_in_every_build(tmp_path: Path) -> None:
    write_source(tmp_path, "alpha", [("https://jobs.lever.co/a/1", "Engineer (alpha)", None)])
    write_source(tmp_path, "beta", [("https://jobs.lever.co/a/1", "Engineer (beta)", None)])
    _, rows = merged(tmp_path)
    row = next(iter(rows.values()))
    assert row[1] == "Engineer (beta)"
    assert row[3] == ["alpha", "beta"]


def test_workday_and_smartrecruiters_postings_are_one_under_any_of_their_links(tmp_path: Path) -> None:
    write_source(tmp_path, "a", [
        ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Ankeny-IA/Sales-Rep_R-113371", "Sales Rep", "With the city"),
        ("https://jobs.smartrecruiters.com/Acme/743999872941471-test-job?oga=true", "Test job", None),
    ])
    write_source(tmp_path, "b", [
        ("https://acme.wd5.myworkdayjobs.com/External/job/Sales-Rep_R-113371/apply", "Sales Rep", "Without the city, a longer text"),
        ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Sales-Rep_R-113371", "Sales Rep", None),
        ("https://api.smartrecruiters.com/v1/companies/acme/postings/743999872941471", "Test job", None),
    ])
    counts, rows = merged(tmp_path)
    assert counts == {"rows_in": 5, "rows_out": 2}
    assert "Without the city, a longer text" in [row[2] for row in rows.values()], "the copy with the longest description wins"
    keys = [row[0] for row in duckdb.connect().execute(f"SELECT key FROM read_parquet('{(tmp_path / 'out').as_posix()}/index.parquet') ORDER BY key").fetchall()]
    assert keys == ["smartrecruiters:743999872941471", "workday:acme:r-113371"]


def test_greenhouse_serves_one_posting_from_several_hosts(tmp_path: Path) -> None:
    write_source(tmp_path, "s", [
        ("https://boards.greenhouse.io/acme/jobs/1", "One", None),
        ("https://job-boards.greenhouse.io/acme/jobs/1", "One", None),
        ("https://job-boards.eu.greenhouse.io/acme/jobs/1", "One", None),
    ])
    assert merged(tmp_path)[0]["rows_out"] == 1


def test_a_query_that_names_the_posting_keeps_it_apart(tmp_path: Path) -> None:
    write_source(tmp_path, "s", [
        ("https://stripe.com/jobs/search?gh_jid=1", "One", None),
        ("https://stripe.com/jobs/search?gh_jid=2", "Two", None),
    ])
    assert merged(tmp_path)[0]["rows_out"] == 2


def test_the_index_points_at_each_description_without_holding_it(tmp_path: Path) -> None:
    write_source(tmp_path, "s", [("https://a.example/1", "One", "First text"), ("https://a.example/2", "Two", "Second text")])
    merged(tmp_path)
    out = (tmp_path / "out").as_posix()
    connection = duckdb.connect()
    columns = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{out}/index.parquet')").fetchall()]
    assert "description" not in columns and "described" in columns and "part" not in columns
    held = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{out}/jobs_*.parquet')").fetchall()]
    assert held == list(build.DESCRIPTION_COLUMNS), "a description file holds what a lookup by key needs, nothing the index has"
    for key, title in connection.execute(f"SELECT key, title FROM read_parquet('{out}/index.parquet')").fetchall():
        text = connection.execute(f"SELECT description FROM read_parquet('{out}/jobs_*.parquet') WHERE key = ?", [key]).fetchone()[0]
        assert text == {"One": "First text", "Two": "Second text"}[title]


def test_row_groups_stay_at_the_measured_size_and_the_files_stay_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "ROWS_PER_FILE", 1000)
    write_source(tmp_path, "s", [(f"https://a.example/{number}", f"Job {number}", uuid.uuid4().hex + uuid.uuid4().hex) for number in range(20000)])
    merged(tmp_path)
    files = sorted((tmp_path / "out").glob("jobs_*.parquet"))
    assert len(files) > 1
    for file in files:
        largest = duckdb.connect().execute("SELECT max(row_group_num_rows) FROM parquet_metadata(?)", [file.as_posix()]).fetchone()[0]
        assert largest <= build.DESCRIPTION_GROUP
        stats = duckdb.connect().execute("SELECT path_in_schema, bool_or(stats_min_value IS NOT NULL) FROM parquet_metadata(?) GROUP BY 1", [file.as_posix()]).fetchall()
        assert {column for column, kept in stats if kept} == {"key"}, "statistics on the key alone keep the footer small"


def test_rows_without_a_link_or_a_title_are_left_out(tmp_path: Path) -> None:
    write_source(tmp_path, "s", [("not a link", "A", None), ("https://a.example/1", "", None), ("https://a.example/2", "B", None)])
    assert merged(tmp_path)[0]["rows_out"] == 1


def test_a_source_that_fails_is_named_and_the_others_still_run(tmp_path: Path) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "sources").mkdir()
    connection = build.connect(tmp_path)
    broken = build.Source("broken", "home", "licence", lambda _: "SELECT * FROM read_parquet('/no/such/file.parquet')")
    report = build.read_source(connection, broken, tmp_path)
    assert report["name"] == "broken"
    assert "error" in report
    assert not (tmp_path / "sources" / "broken.parquet").exists()


@pytest.mark.parametrize(("rows", "previous"), [(0, None), (69, 100)])
def test_an_empty_or_much_smaller_database_is_not_published(rows: int, previous: int | None) -> None:
    with pytest.raises(SystemExit):
        publish.check(rows, previous)


@pytest.mark.parametrize(("rows", "previous"), [(1, None), (70, 100), (200, 100)])
def test_a_healthy_database_is_published(rows: int, previous: int | None) -> None:
    publish.check(rows, previous)


def test_one_commit_adds_the_build_its_delta_and_the_manifest_and_removes_all_but_the_last_builds(tmp_path: Path) -> None:
    manifest = {"build": "jobs-20261108-100000", "index": {"name": "index.parquet"}, "files": [{"name": "jobs_0.parquet"}, {"name": "jobs_1.parquet"}]}
    built = [f"jobs-2026110{day}-000000" for day in range(1, 9)]
    adds, removed = publish.commit_plan(tmp_path, manifest, tmp_path / "delta-jobs-20261108-100000.parquet", built)
    assert [path for path, _ in adds] == [
        "builds/jobs-20261108-100000/index.parquet", "builds/jobs-20261108-100000/jobs_0.parquet", "builds/jobs-20261108-100000/jobs_1.parquet",
        "deltas/delta-jobs-20261108-100000.parquet", "manifest.json",
    ]
    assert adds[-1][0] == "manifest.json", "the manifest names files the same commit adds"
    assert removed == [f"builds/jobs-2026110{day}-000000" for day in range(1, 4)]
    adds, removed = publish.commit_plan(tmp_path, manifest, None, [])
    assert "deltas/" not in " ".join(path for path, _ in adds) and removed == []


def test_every_source_names_its_home_and_licence() -> None:
    names = [source.name for source in build.SOURCES]
    assert len(names) == len(set(names))
    assert all(source.home.startswith("https://") and source.license for source in build.SOURCES)
    assert json.dumps(names)


def test_descriptions_are_written_in_key_order_and_each_file_says_its_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A reader asks for a posting by key and reads the one file, and the one row group, that can hold it.
    monkeypatch.setattr(build, "ROWS_PER_FILE", 1000)
    write_source(tmp_path, "s", [(f"https://a.example/{uuid.uuid4().hex}", "Job", uuid.uuid4().hex * 4) for _ in range(6000)])
    merged(tmp_path)
    connection = duckdb.connect()
    entries = [build.file_entry(connection, path) for path in sorted((tmp_path / "out").glob("jobs_*.parquet"))]
    assert len(entries) > 1
    for path, entry in zip(sorted((tmp_path / "out").glob("jobs_*.parquet")), entries, strict=True):
        keys = [row[0] for row in connection.execute("SELECT key FROM read_parquet(?)", [path.as_posix()]).fetchall()]
        assert keys == sorted(keys) and (entry["first_key"], entry["last_key"]) == (keys[0], keys[-1])
    ranges = sorted((entry["first_key"], entry["last_key"]) for entry in entries)
    assert all(earlier[1] < later[0] for earlier, later in itertools.pairwise(ranges))


def write_index(path: Path, rows: list[tuple[str, str]]) -> None:
    connection = duckdb.connect()
    connection.execute(f"CREATE TABLE i (key VARCHAR, {', '.join(f'{column} VARCHAR' for column in publish.INDEX_COLUMNS)})")
    connection.executemany("INSERT INTO i (key, url, title) VALUES (?, ?, ?)", [(key, f"https://{key}", title) for key, title in rows])
    connection.execute(f"COPY i TO '{path.as_posix()}' (FORMAT parquet)")


def test_a_delta_holds_the_postings_new_or_changed_and_the_keys_gone(tmp_path: Path) -> None:
    write_index(tmp_path / "before.parquet", [("a", "Gone"), ("b", "Old title"), ("c", "Same")])
    write_index(tmp_path / "after.parquet", [("b", "New title"), ("c", "Same"), ("d", "New")])
    adds, drops = publish.write_delta((tmp_path / "before.parquet").as_posix(), tmp_path / "after.parquet", tmp_path / "delta.parquet")
    assert (adds, drops) == (2, 1)
    rows = duckdb.connect().execute(f"SELECT op, key, title FROM read_parquet('{(tmp_path / 'delta.parquet').as_posix()}') ORDER BY key").fetchall()
    assert rows == [("drop", "a", None), ("add", "b", "New title"), ("add", "d", "New")]


def test_a_column_the_previous_index_lacks_changes_every_posting_once(tmp_path: Path) -> None:
    write_index(tmp_path / "index.parquet", [("a", "Same"), ("b", "Same too")])
    connection = duckdb.connect()
    index = (tmp_path / "index.parquet").as_posix()
    connection.execute(f"COPY (SELECT * REPLACE ('true' AS described) FROM read_parquet('{index}')) TO '{(tmp_path / 'after.parquet').as_posix()}' (FORMAT parquet)")
    connection.execute(f"COPY (SELECT * EXCLUDE (described) FROM read_parquet('{index}')) TO '{(tmp_path / 'before.parquet').as_posix()}' (FORMAT parquet)")
    adds, drops = publish.write_delta((tmp_path / "before.parquet").as_posix(), tmp_path / "after.parquet", tmp_path / "delta.parquet")
    assert (adds, drops) == (2, 0)


def test_a_build_is_named_by_its_manifest_or_by_the_end_of_its_address() -> None:
    assert publish.build_of({"base_url": "https://github.com/o/r/releases/download/jobs-20261107-103405"}) == "jobs-20261107-103405"
    assert publish.build_of({"build": "jobs-20261107-153405", "base_url": "x"}) == "jobs-20261107-153405"


def test_a_source_is_versioned_by_its_homes_commit_or_by_its_files_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(build, "_git_head", lambda repo: asked.append(f"git {repo}") or "c1")
    monkeypatch.setattr(build, "_hf_revision", lambda dataset: asked.append(f"hf {dataset}") or "h1")
    monkeypatch.setattr(build, "VERSIONS", {**build.VERSIONS, "jobhive": lambda: asked.append("file") or "e1"})
    by_name = {source.name: source for source in build.SOURCES}
    assert build.version_of(by_name["rolebase"]) == "c1" and asked[-1] == "git chandranshB/roleBase"
    assert build.version_of(by_name["scholarweave"]) == "h1" and asked[-1] == "hf scholarweave/Jobs-Dataset-v2"
    assert build.version_of(by_name["jobhive"]) == "e1" and asked[-1] == "file"
    assert build.version_of(by_name["riley"]) is None


def test_a_version_that_cannot_be_had_means_the_source_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(_: str) -> str:
        raise OSError("no answer")

    monkeypatch.setattr(build, "_git_head", down)
    assert build.version_of(next(source for source in build.SOURCES if source.name == "rolebase")) is None


class _Cache:
    def __init__(self, versions: dict[str, str]) -> None:
        self.versions, self.kept = versions, []

    def take(self, name: str, version: str, path: Path) -> dict | None:
        if self.versions.get(name) != version:
            return None
        write_source(path.parent.parent, name, [("https://a.example/kept", "Kept", None)])
        return {"name": name, "rows": 1}

    def keep(self, fresh: list, work: Path) -> None:
        self.kept = [name for name, _, _ in fresh]


def test_a_source_unchanged_since_the_last_build_is_taken_from_the_cache_and_a_changed_one_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [build.Source(name, f"https://github.com/o/{name}", "MIT", lambda _: "SELECT 'https://a.example/read' AS url, 'Read' AS title") for name in ("same", "changed", "unknown")]
    versions = {"same": "v1", "changed": "v2", "unknown": None}
    monkeypatch.setattr(build, "version_of", lambda source: versions[source.name])
    read: list[str] = []
    monkeypatch.setattr(build, "read_source", lambda connection, source, work: read.append(source.name) or {"name": source.name, "rows": 1})
    cache = _Cache({"same": "v1", "changed": "v1"})
    (tmp_path / "sources").mkdir()
    reports = build.read_all(duckdb.connect(), sources, tmp_path, cache)  # type: ignore[arg-type]
    assert read == ["changed", "unknown"]
    assert [report.get("reused", False) for report in reports] == [True, False, False]
    assert cache.kept == ["changed"], "a source with no version is not kept: it is read every build"
