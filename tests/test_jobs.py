"""The job boards, the filters, the store and the settings."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from aihawk.config import ConfigError, Filters, load_config, write_templates
from aihawk.database import DatabaseError, Job, page_text, search, with_description
from aihawk.store import Store

JOB = Job("g:a:1", "Acme", "Senior Python Engineer", "Rome, Italy", "u", "a", "d")


def database(tmp_path: Path) -> str:
    rows = [
        ("k1", "https://jobs.lever.co/a/1", "Senior Python Engineer", "Acme", "Rome, Italy", "Build", "2099-01-01"),
        ("k2", "https://jobs.lever.co/a/2", "Python Intern", "Acme", "Berlin", "Learn", None),
        ("k3", "https://example.com/3", "Accountant &amp; Clerk", "B&amp;A", "Rome, Italy", "Count", "2099-01-01"),
        ("k4", "https://example.com/4", "Python Developer", "Old", "Rome, Italy", "Past", "2001-01-01"),
    ]
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    connection.execute(f"COPY t TO '{(tmp_path / 'jobs_0.parquet').as_posix()}' (FORMAT parquet)")
    manifest = {"rows_out": 4, "built_at": "2026-11-04", "files": [{"name": "jobs_0.parquet"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(tmp_path / "manifest.json")


@pytest.mark.parametrize(
    ("filters", "titles"),
    [
        (Filters(include=("python",)), ["Senior Python Engineer", "Python Intern"]),
        (Filters(include=("PYTHON",), exclude=("senior",)), ["Python Intern"]),
        (Filters(include=("python",), locations=("italy",)), ["Senior Python Engineer"]),
        (Filters(include=("java",)), []),
    ],
)
def test_search_keeps_what_the_filters_say_and_leaves_out_what_is_old(tmp_path: Path, filters: Filters, titles: list[str]) -> None:
    _, jobs = search(database(tmp_path), filters, max_age_days=60)
    assert [job.title for job in jobs] == titles


def test_filters_match_whole_words_and_keep_symbols_literal(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany(
        "INSERT INTO t VALUES (?, 'https://a.example/', ?, 'c', ?, 'd', NULL)",
        [("1", "Java Developer", "Roma, Italy"), ("2", "JavaScript Developer", "Roma"), ("3", "Java Lead", "Bucharest, Romania"), ("4", "C++ Developer", "Roma")],
    )
    connection.execute(f"COPY t TO '{(tmp_path / 'jobs_0.parquet').as_posix()}' (FORMAT parquet)")
    manifest = {"rows_out": 4, "built_at": "x", "files": [{"name": "jobs_0.parquet"}]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    keys = lambda filters: {job.id for job in search(str(path), filters, 60)[1]}  # noqa: E731
    assert keys(Filters(include=("java",))) == {"1", "3"}
    assert keys(Filters(include=("java",), locations=("roma",))) == {"1"}
    assert keys(Filters(include=("c++",))) == {"4"}
    assert keys(Filters(include=("developer",), exclude=("JAVA",))) == {"2", "4"}


def test_names_come_without_html_entities(tmp_path: Path) -> None:
    _, jobs = search(database(tmp_path), Filters(include=("accountant",)), max_age_days=60)
    assert [(job.title, job.company) for job in jobs] == [("Accountant & Clerk", "B&A")]


def test_search_needs_titles_and_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="filters.include"):
        search(database(tmp_path), Filters(), max_age_days=60)


def test_a_database_that_cannot_be_read_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="manifest"):
        search(str(tmp_path / "none.json"), Filters(include=("x",)), max_age_days=60)


def test_page_text_keeps_the_words_and_drops_scripts() -> None:
    text = page_text("<html><head><title>x</title><style>a{}</style></head><body><nav>menu</nav><p>Build &amp; ship</p><script>evil()</script><li>Python</li></body></html>")
    assert text.split() == ["Build", "&", "ship", "Python"]


def test_a_job_with_a_description_reads_no_page() -> None:
    job = Job("a", "c", "t", "l", "http://127.0.0.1:9/never", "u", "Already here")
    assert with_description(job) is job


def test_a_page_that_cannot_be_read_leaves_the_job_as_it_was() -> None:
    job = Job("a", "c", "t", "l", "http://127.0.0.1:9/never", "u", "")
    assert with_description(job, timeout=1) is job


def test_a_job_seen_again_keeps_its_score_and_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    assert store.add(JOB) is True
    store.set_score(JOB.id, 4.5, {"score": 4.5})
    store.set_status(JOB.id, "needs_input", "phone?")
    assert store.add(JOB) is False
    item = store.get(JOB.id)
    assert item is not None and (item.status, item.score, item.report) == ("needs_input", 4.5, {"score": 4.5})


def test_only_what_is_good_enough_and_not_sent_is_applied_to_best_first(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for number, score in enumerate((3.9, 4.0, 4.8, 4.9)):
        job = Job(f"j{number}", "A", "T", "L", "u", "a", "d")
        store.add(job)
        store.set_score(job.id, score, {})
    store.set_status("j3", "submitted")
    assert [item.job.id for item in store.to_apply(4.0, 5)] == ["j2", "j1"]
    assert [item.job.id for item in store.to_apply(4.0, 1)] == ["j2"]


def test_the_template_loads_and_defaults_to_orcarouter_and_the_published_database(tmp_path: Path) -> None:
    write_templates(tmp_path)
    config = load_config(tmp_path)
    assert (config.base_url, config.model, config.api_key_env) == ("https://api.orcarouter.ai/v1", "orcarouter/auto", "ORCAROUTER_API_KEY")
    assert config.database.endswith("/releases/download/jobs-latest/manifest.json")
    assert config.filters.include


def test_templates_keep_a_file_that_exists(tmp_path: Path) -> None:
    (tmp_path / "cv.md").write_text("mine", encoding="utf-8")
    write_templates(tmp_path)
    assert (tmp_path / "cv.md").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize(
    "text",
    ["threshold: 9", "filters: {include: python}", "a: [", "- a"],
)
def test_settings_that_cannot_be_used_are_refused(tmp_path: Path, text: str) -> None:
    (tmp_path / "aihawk.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_no_settings_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="aihawk init"):
        load_config(tmp_path)
