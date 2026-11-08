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
    rows = duckdb.connect().execute(
        f"SELECT url, title, description, sources FROM read_parquet('{(tmp_path / 'out').as_posix()}/jobs_*.parquet')"
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
    assert "description" not in columns and "described" in columns
    for title, part, row in connection.execute(f"SELECT title, part, row_number FROM read_parquet('{out}/index.parquet')").fetchall():
        text = connection.execute(f"SELECT description FROM read_parquet('{out}/{part}', file_row_number = true) WHERE file_row_number = {row}").fetchone()[0]
        assert text == {"One": "First text", "Two": "Second text"}[title]


def test_row_groups_stay_at_the_measured_size_and_the_files_stay_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "ROWS_PER_FILE", 1000)
    write_source(tmp_path, "s", [(f"https://a.example/{number}", f"Job {number}", uuid.uuid4().hex + uuid.uuid4().hex) for number in range(20000)])
    merged(tmp_path)
    files = sorted((tmp_path / "out").glob("jobs_*.parquet"))
    assert len(files) > 1
    for file in files:
        largest = duckdb.connect().execute("SELECT max(row_group_num_rows) FROM parquet_metadata(?)", [file.as_posix()]).fetchone()[0]
        assert largest <= build.ROW_GROUP


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


def test_a_release_interrupted_uploads_only_what_it_does_not_hold_whole() -> None:
    local = {"a": 10, "b": 20, "c": 30}
    assert publish.still_to_upload(local, {"a": 10, "b": 5}) == ["b", "c"]
    assert publish.still_to_upload(local, local) == []


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


def test_deltas_go_to_their_month_and_only_the_dated_builds_are_ever_cleared() -> None:
    assert publish.deltas_release("jobs-20261107-103405") == "jobs-deltas-202611"
    assert publish.build_of({"base_url": "https://github.com/o/r/releases/download/jobs-20261107-103405"}) == "jobs-20261107-103405"
    assert publish.build_of({"build": "jobs-20261107-153405", "base_url": "x"}) == "jobs-20261107-153405"
    tags = ["jobs-latest", "jobs-deltas-202611", "golden-abc", "jobs-20261107-103405"]
    assert [tag for tag in tags if publish.DATED.match(tag)] == ["jobs-20261107-103405"]
