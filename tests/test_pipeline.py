"""The merge: one row per posting, the richest row kept, and no database published when it lost too much."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
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
    rows = duckdb.connect().execute(f"SELECT url, title, description FROM read_parquet('{(tmp_path / 'out').as_posix()}/*.parquet')").fetchall()
    return counts, {row[0]: row for row in rows}


def urls_out(tmp_path: Path) -> list[str]:
    """The postings published, by their address, which is their key."""
    return sorted(row[0] for row in duckdb.connect().execute(f"SELECT url FROM read_parquet('{(tmp_path / 'out').as_posix()}/*.parquet')").fetchall())


def text(words: str) -> str:
    """A description long enough to be published: DESCRIBED characters or more."""
    return (words + " ") * (build.DESCRIBED // len(words) + 1)


def test_the_same_posting_from_two_sources_is_one_row_with_the_longest_description(tmp_path: Path) -> None:
    write_source(tmp_path, "short", [("https://jobs.lever.co/a/1/apply?utm_source=x", "Engineer", None)])
    write_source(tmp_path, "long", [("https://www.jobs.lever.co/A/1", "Engineer", text("A full description"))])
    counts, rows = merged(tmp_path)
    assert counts == {"rows_in": 2, "rows_out": 1}
    assert rows == {"https://www.jobs.lever.co/A/1": ("https://www.jobs.lever.co/A/1", "Engineer", text("A full description"))}


def test_copies_that_tie_go_the_same_way_in_every_build(tmp_path: Path) -> None:
    write_source(tmp_path, "alpha", [("https://jobs.lever.co/a/1", "Engineer (alpha)", None)])
    write_source(tmp_path, "beta", [("https://jobs.lever.co/a/1", "Engineer (beta)", None)])
    _, rows = merged(tmp_path)
    row = next(iter(rows.values()))
    assert row[1] == "Engineer (beta)"


def test_workday_and_smartrecruiters_postings_are_one_under_any_of_their_links(tmp_path: Path) -> None:
    write_source(tmp_path, "a", [
        ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Ankeny-IA/Sales-Rep_R-113371", "Sales Rep", text("With the city")),
        ("https://jobs.smartrecruiters.com/Acme/743999872941471-test-job?oga=true", "Test job", None),
    ])
    write_source(tmp_path, "b", [
        ("https://acme.wd5.myworkdayjobs.com/External/job/Sales-Rep_R-113371/apply", "Sales Rep", text("Without the city, a longer text") * 2),
        ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Sales-Rep_R-113371", "Sales Rep", None),
        ("https://api.smartrecruiters.com/v1/companies/acme/postings/743999872941471", "Test job", None),
    ])
    counts, _ = merged(tmp_path)
    assert counts == {"rows_in": 5, "rows_out": 2}
    # The copy with the longest description wins; copies with none tie, and go the same way in every build.
    assert urls_out(tmp_path) == [
        "https://acme.wd5.myworkdayjobs.com/External/job/Sales-Rep_R-113371/apply",
        "https://api.smartrecruiters.com/v1/companies/acme/postings/743999872941471",
    ]


def write_full_source(work: Path, name: str, rows: list[tuple]) -> None:
    """A source whose rows give link, title, company, location, vendor and description."""
    connection = duckdb.connect()
    columns = ", ".join(f"{column} {kind}" for column, kind in (
        ("url", "VARCHAR"), ("title", "VARCHAR"), ("company", "VARCHAR"), ("ats", "VARCHAR"), ("location", "VARCHAR"),
        ("country", "VARCHAR"), ("remote", "BOOLEAN"), ("salary_min", "DOUBLE"), ("salary_max", "DOUBLE"),
        ("salary_currency", "VARCHAR"), ("description", "VARCHAR"), ("posted_at", "VARCHAR"),
    ))
    connection.execute(f"CREATE TABLE t ({columns})")
    connection.executemany("INSERT INTO t (url, title, company, location, ats, description) VALUES (?, ?, ?, ?, ?, ?)", rows)
    (work / "sources").mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY t TO '{(work / 'sources' / (name + '.parquet')).as_posix()}' (FORMAT parquet)")


def test_one_job_on_a_companys_form_and_on_a_board_is_one_posting_kept_on_the_form(tmp_path: Path) -> None:
    write_source_rows = [("https://boards.greenhouse.io/acme/jobs/1", "Engineer", "Acme Inc.", "Rome, Italy", None, "Short")]
    write_full_source(tmp_path, "company", write_source_rows)
    write_full_source(tmp_path, "board", [
        ("https://www.welcometothejungle.com/en/companies/acme/jobs/99", "engineer", "ACME", "Rome", None, "A much longer description of the job"),
        ("https://www.welcometothejungle.com/en/companies/acme/jobs/100", "Engineer", "Acme", "Milan", None, "The same role in another city"),
    ])
    counts, _ = merged(tmp_path)
    assert counts == {"rows_in": 3, "rows_out": 2}, "the role in Milan is another job"
    # The company's own link wins over the board's copy, even when the copy's description is longer.
    assert urls_out(tmp_path) == ["https://boards.greenhouse.io/acme/jobs/1", "https://www.welcometothejungle.com/en/companies/acme/jobs/100"]


def test_one_role_posted_twice_on_one_site_is_two_openings(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://boards.greenhouse.io/acme/jobs/1", "Engineer", "Acme", "Rome", None, None),
        ("https://boards.greenhouse.io/acme/jobs/2", "Engineer", "Acme", "Rome", None, None),
        ("https://www.welcometothejungle.com/en/companies/acme/jobs/3", "Engineer", "Acme", "Rome", None, None),
    ])
    counts, _ = merged(tmp_path)
    assert counts["rows_out"] == 2, "both Greenhouse openings stay; the board's copy goes into one of them"


def test_a_role_with_no_city_is_never_merged_across_a_country(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://acme.example/jobs/1", "Sales Associate", "Acme", "", "workday", None),
        ("https://www.welcometothejungle.com/en/companies/acme/jobs/2", "Sales Associate", "Acme", "Not specified", None, None),
    ])
    counts, _ = merged(tmp_path)
    assert counts["rows_out"] == 2


def test_no_linkedin_posting_is_kept_whatever_source_carries_it(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://www.linkedin.com/jobs/view/1", "Engineer", "Acme", "Rome", None, None),
        ("https://boards.greenhouse.io/beta/jobs/2", "Engineer", "Beta", "Rome", None, None),
    ])
    counts, _ = merged(tmp_path)
    assert counts["rows_out"] == 1


def test_a_boards_posting_that_sends_on_to_a_companys_form_goes(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://www.arbeitnow.com/jobs/a", "Engineer", "Acme", "Berlin", "arbeitnow", None),
        ("https://www.arbeitnow.com/jobs/b", "Designer", "Beta", "Berlin", "arbeitnow", None),
    ])
    (tmp_path / "tmp").mkdir()
    asked: list[list[tuple[str, str]]] = []

    def sent_on(postings: list[tuple[str, str]]) -> set[str]:
        asked.append(postings)
        return {key for key, url in postings if url.endswith("/a")}

    counts = build.merge(build.connect(tmp_path), tmp_path, sent_on)
    assert counts["rows_out"] == 1 and len(asked[0]) == 2


def test_a_board_page_is_opened_once_and_its_answer_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(build, "sends_on", lambda url: opened.append(url) or url.endswith("/a"))

    class Cache:
        def __init__(self) -> None:
            self.kept: dict[str, bool] = {"old": True}

        def checks(self) -> dict[str, bool]:
            return dict(self.kept)

        def keep_checks(self, checks: dict[str, bool], work: Path) -> None:
            self.kept = checks

    cache = Cache()
    sent = build.board_check(cache, tmp_path)([("old", "https://x/old"), ("k1", "https://x/a"), ("k2", "https://x/b")])  # type: ignore[arg-type]
    assert sent == {"old", "k1"} and opened == ["https://x/a", "https://x/b"]
    assert cache.kept == {"old": True, "k1": True, "k2": False}


def test_a_site_mostly_made_of_copies_of_companies_forms_goes_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "FRONT_LEAST", 3)
    write_full_source(tmp_path, "forms", [
        ("https://boards.greenhouse.io/acme/jobs/1", "Engineer", "Acme", "Rome", None, None),
        ("https://boards.greenhouse.io/beta/jobs/2", "Designer", "Beta", "Rome", None, None),
    ])
    write_full_source(tmp_path, "front", [
        ("https://careers.front.example/1", "Engineer", "Acme", "Rome", None, None),
        ("https://careers.front.example/2", "Designer", "Beta", "Rome", None, None),
        ("https://careers.front.example/3", "Analyst", "Gamma", "Rome", None, None),
        ("https://other.example/4", "Analyst", "Delta", "Rome", None, None),
    ])
    merged(tmp_path)
    assert urls_out(tmp_path) == ["https://boards.greenhouse.io/acme/jobs/1", "https://boards.greenhouse.io/beta/jobs/2", "https://other.example/4"], "the front's third posting goes too"


def test_a_posting_with_no_company_is_never_merged_by_its_title(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://a.example/1", "Engineer", None, "Rome", None, None),
        ("https://b.example/2", "Engineer", "", "Rome", None, None),
    ])
    counts, _ = merged(tmp_path)
    assert counts["rows_out"] == 2


def test_a_vendor_has_one_name_and_is_read_from_the_link_when_a_source_does_not_say(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://jobs.ashbyhq.com/acme/1", "One", "A", "X", "ashbyhq", None),
        ("https://recruiting.paylocity.com/Recruiting/Jobs/Details/2", "Two", "B", "Y", None, None),
        ("https://nobody.example/3", "Three", "C", "Z", None, None),
    ])
    (tmp_path / "tmp").mkdir()
    vendors = build.connect(tmp_path).execute(
        f"SELECT vendor(ats, url) FROM read_parquet('{(tmp_path / 'sources' / 's.parquet').as_posix()}') ORDER BY url"
    ).fetchall()
    assert [vendor for (vendor,) in vendors] == ["ashby", None, "paylocity"]


def test_the_links_host_names_the_vendor_before_a_sources_label(tmp_path: Path) -> None:
    write_full_source(tmp_path, "s", [
        ("https://www.linkedin.com/jobs/view/1", "One", "A", "X", "datastax", None),
        ("https://boards.greenhouse.io/acme/jobs/2", "Two", "B", "Y", "workday", None),
        ("https://nobody.example/3", "Three", "C", "Z", "datastax", None),
        ("https://sjobs.brassring.com/TGnewUI/4", "Four", "D", "W", None, None),
    ])
    (tmp_path / "tmp").mkdir()
    connection = build.connect(tmp_path)
    vendors = connection.execute(f"SELECT vendor(ats, url) FROM read_parquet('{(tmp_path / 'sources' / 's.parquet').as_posix()}') ORDER BY url").fetchall()
    # LinkedIn's posting labelled "datastax" is LinkedIn's, and goes; "datastax" names no vendor.
    assert [vendor for (vendor,) in vendors] == ["greenhouse", None, "brassring", "linkedin"]
    assert build.merge(connection, tmp_path)["rows_out"] == 3


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


def write_dated(work: Path, name: str, rows: list[tuple]) -> None:
    """A source whose rows give link, title, description and the date posted."""
    connection = duckdb.connect()
    columns = ", ".join(f"{column} {'BOOLEAN' if column == 'remote' else 'DOUBLE' if column.startswith('salary_m') else 'VARCHAR'}" for column in build.COMMON)
    connection.execute(f"CREATE TABLE t ({columns})")
    given = ("url", "title", "description", "posted_at", "location", "country", "remote")
    connection.executemany(f"INSERT INTO t ({', '.join(given)}) VALUES ({', '.join('?' for _ in given)})", [(*row, *([None] * (len(given) - len(row)))) for row in rows])
    (work / "sources").mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY t TO '{(work / 'sources' / (name + '.parquet')).as_posix()}' (FORMAT parquet)")


def days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def test_a_chunk_holds_what_aihawk_reads_and_a_short_text_goes_to_the_chunks_without_one(tmp_path: Path) -> None:
    write_source(tmp_path, "s", [("https://a.example/1", "One", text("First text")), ("https://a.example/2", "Two", "Too short to say anything")])
    merged(tmp_path)
    out = (tmp_path / "out").as_posix()
    connection = duckdb.connect()
    assert sorted(path.name for path in (tmp_path / "out").glob("*.parquet")) == ["jobs_0.parquet", "titles_0.parquet"]
    held = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{out}/jobs_0.parquet')").fetchall()]
    assert held == list(build.CHUNK_COLUMNS), "a chunk holds what AIHawk reads, the address being the key"
    assert connection.execute(f"SELECT url, description FROM read_parquet('{out}/jobs_0.parquet')").fetchall() == [("https://a.example/1", text("First text"))]
    assert connection.execute(f"SELECT url, description FROM read_parquet('{out}/titles_0.parquet')").fetchall() == [("https://a.example/2", None)]


def test_chunks_go_newest_first_and_a_posting_too_old_is_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CHUNK_ROWS", 3)
    monkeypatch.setattr(build, "CHUNK_GROUP", 2)
    rows = [(f"https://a.example/{age}", f"Job {age}", text("Work"), days_ago(age)) for age in (1, 2, 5, 9, 40, 100)]
    future = ("https://a.example/future", "Future", text("Work"), days_ago(-30))
    write_dated(tmp_path, "s", [*rows, future, ("https://a.example/old", "Old", text("Work"), days_ago(build.MAX_AGE_DAYS + 5))])
    counts, _ = merged(tmp_path)
    assert counts["rows_out"] == 7
    dated = dict(duckdb.connect().execute(f"SELECT title, posted_at FROM read_parquet('{(tmp_path / 'out').as_posix()}/*.parquet')").fetchall())
    assert dated["Future"] <= datetime.now() + timedelta(minutes=1), "a date after the build is the build's"
    connection = duckdb.connect()
    chunks = sorted((tmp_path / "out").glob("*.parquet"), key=build.chunk_order)
    assert [path.name for path in chunks] == ["jobs_0.parquet", "jobs_1.parquet", "jobs_2.parquet"]
    titles = [[row[0] for row in connection.execute("SELECT title FROM read_parquet(?)", [path.as_posix()]).fetchall()] for path in chunks]
    assert titles == [["Future", "Job 1", "Job 2"], ["Job 5", "Job 9", "Job 40"], ["Job 100"]]
    first = build.chunk_entry(connection, chunks[0])
    assert first["rows"] == 3 and not first["titles_only"] and first["newest"] > first["oldest"]
    groups = connection.execute("SELECT max(row_group_num_rows) FROM parquet_metadata(?)", [chunks[0].as_posix()]).fetchone()[0]
    assert groups == 2
    stats = connection.execute("SELECT path_in_schema, bool_or(stats_min_value IS NOT NULL) FROM parquet_metadata(?) GROUP BY 1", [chunks[0].as_posix()]).fetchall()
    assert {column for column, kept in stats if kept} == {"posted_at"}, "statistics on the date alone keep the footer small"


def test_the_chunks_with_no_text_come_after_all_the_others() -> None:
    names = ["titles_0.parquet", "jobs_10.parquet", "jobs_2.parquet", "titles_1.parquet", "jobs_0.parquet"]
    assert sorted((Path(name) for name in names), key=build.chunk_order) == [Path(name) for name in ("jobs_0.parquet", "jobs_2.parquet", "jobs_10.parquet", "titles_0.parquet", "titles_1.parquet")]


def test_a_remote_posting_that_names_no_place_says_remote(tmp_path: Path) -> None:
    write_dated(tmp_path, "s", [
        ("https://a.example/1", "Anywhere", None, None, "", None, True),
        ("https://a.example/2", "In Rome", None, None, "Rome", None, True),
        ("https://a.example/3", "Nowhere said", None, None, "", None, None),
    ])
    merged(tmp_path)
    places = duckdb.connect().execute(f"SELECT title, location FROM read_parquet('{(tmp_path / 'out').as_posix()}/*.parquet') ORDER BY title").fetchall()
    assert places == [("Anywhere", "Remote"), ("In Rome", "Rome"), ("Nowhere said", "")]


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


def test_one_commit_adds_the_build_its_delta_and_the_manifest_and_removes_old_builds_and_deltas(tmp_path: Path) -> None:
    manifest = {"build": "jobs-20261108-100000", "chunks": [{"name": "jobs_0.parquet"}, {"name": "titles_0.parquet"}],
                "deltas": [{"url": f"https://x/library/deltas/delta-jobs-2026110{day}-000000.parquet"} for day in (5, 6, 7)]}
    built = [f"jobs-2026110{day}-000000" for day in range(1, 9)]
    stored = [f"delta-jobs-2026110{day}-000000.parquet" for day in range(1, 8)]
    adds, removed, old_deltas = publish.commit_plan(tmp_path, manifest, tmp_path / "delta-jobs-20261108-100000.parquet", built, stored)
    assert [path for path, _ in adds] == [
        "library/builds/jobs-20261108-100000/jobs_0.parquet", "library/builds/jobs-20261108-100000/titles_0.parquet",
        "library/deltas/delta-jobs-20261108-100000.parquet", "library/manifest.json",
    ]
    assert adds[-1][0] == "library/manifest.json", "the manifest names files the same commit adds"
    assert removed == [f"library/builds/jobs-2026110{day}-000000" for day in range(1, 4)]
    assert old_deltas == [f"library/deltas/delta-jobs-2026110{day}-000000.parquet" for day in range(1, 5)], "a delta the manifest no longer names goes"
    adds, removed, old_deltas = publish.commit_plan(tmp_path, {**manifest, "deltas": []}, None, [], [])
    assert "deltas/" not in " ".join(path for path, _ in adds) and removed == [] and old_deltas == []


def test_every_source_names_its_home_and_licence() -> None:
    names = [source.name for source in build.SOURCES]
    assert len(names) == len(set(names))
    assert all(source.home.startswith("https://") and source.license for source in build.SOURCES)
    assert json.dumps(names)


def write_chunk(path: Path, rows: list[tuple[str, str, str | None]]) -> None:
    """A chunk of (address, title, description)."""
    connection = duckdb.connect()
    connection.execute(f"CREATE TABLE c ({', '.join(f'{column} VARCHAR' for column in publish.COLUMNS)})")
    connection.executemany("INSERT INTO c (url, title, description) VALUES (?, ?, ?)", rows)
    connection.execute(f"COPY c TO '{path.as_posix()}' (FORMAT parquet)")


def test_a_delta_holds_the_postings_new_or_changed_whole_and_the_addresses_gone(tmp_path: Path) -> None:
    write_chunk(tmp_path / "before_0.parquet", [("https://a", "Gone", "x"), ("https://b", "Same title", "Old text")])
    write_chunk(tmp_path / "before_1.parquet", [("https://c", "Same", "Same text")])
    write_chunk(tmp_path / "after_0.parquet", [("https://d", "New", "New text"), ("https://b", "Same title", "New text")])
    write_chunk(tmp_path / "after_1.parquet", [("https://c", "Same", "Same text")])
    before = [(tmp_path / f"before_{n}.parquet").as_posix() for n in (0, 1)]
    adds, drops = publish.write_delta(before, [tmp_path / f"after_{n}.parquet" for n in (0, 1)], tmp_path / "delta.parquet")
    assert (adds, drops) == (2, 1)
    rows = duckdb.connect().execute(f"SELECT op, url, title, description FROM read_parquet('{(tmp_path / 'delta.parquet').as_posix()}') ORDER BY url").fetchall()
    # A posting whose description changed is changed: the reader gets it whole, its text included.
    assert rows == [("drop", "https://a", None, None), ("add", "https://b", "Same title", "New text"), ("add", "https://d", "New", "New text")]


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
