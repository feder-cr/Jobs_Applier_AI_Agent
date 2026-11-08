"""The job boards, the filters, the store and the settings."""

from __future__ import annotations

import csv
import errno
import io
import json
import socket
import textwrap
import urllib.error
from pathlib import Path
from typing import Any

import duckdb
import pytest

from aihawk.config import Arc, ConfigError, Filters, Form, Node, Plan, load_config
from aihawk.database import (
    INDEX_COLUMNS,
    UNDESCRIBED,
    DatabaseError,
    Job,
    _apply,
    _chain,
    description_files,
    load_manifest,
    local_index,
    rarity,
    search,
    stored_descriptions,
)
from aihawk.store import COLUMNS, Proof, Store, said, sent_word, write_applied
from fakes import publish

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
    return publish(connection, tmp_path, 4)


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
    _, jobs = search(database(tmp_path), filters, 60, tmp_path)
    assert [job.title for job in jobs] == titles


def catalogue(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> str:
    """Postings (key, title, company, location), all posted today, published as the pipeline does."""
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany("INSERT INTO t VALUES (?, 'https://a.example/', ?, ?, ?, 'd', NULL)", rows)
    return publish(connection, tmp_path, 4)


def test_a_title_holding_the_rare_node_comes_before_one_holding_only_the_common_one(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    rows = [(str(n), "Engineer") for n in range(200)] + [("rare", "Fortran Engineer"), ("both", "Senior Fortran Engineer Fortran")]
    connection.executemany("INSERT INTO t VALUES (?, 'https://a.example/', ?, 'c', 'Rome', 'd', NULL)", rows)
    path = publish(connection, tmp_path, 4)
    plan = Plan((Node("role", 1.0, (Form("Engineer"),)), Node("skill", 1.5, (Form("Fortran"),))))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path, limit=3)
    assert jobs[0].id in {"rare", "both"} and jobs[1].id in {"rare", "both"}
    # A node counts once, however many times a title says it: the two score the same.
    assert jobs[0].lexical == jobs[1].lexical == 1.0
    assert jobs[2].lexical < 0.2


def test_a_node_holds_its_best_wording_and_wordings_never_add_up(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("both", "Engineer Developer", "A", "Rome"), ("dev", "Developer", "B", "Rome"), ("eng", "Engineer", "C", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer"), Form("Developer"))),))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path)
    # Each title holds one wording of the node as rare as any other: all three score the same, and "both" is no higher.
    assert {job.id: job.lexical for job in jobs} == {"both": 1.0, "dev": 1.0, "eng": 1.0}


@pytest.mark.parametrize(("weights", "order"), [((2.0, 0.5), ["Engineer", "Developer"]), ((0.5, 2.0), ["Developer", "Engineer"])])
def test_a_wording_weight_changes_the_order(tmp_path: Path, weights: tuple[float, float], order: list[str]) -> None:
    path = catalogue(tmp_path, [("e", "Engineer", "A", "Rome"), ("d", "Developer", "B", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer", weights[0]), Form("Developer", weights[1]))),))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path)
    assert [job.title for job in jobs] == order


def test_a_rare_wording_beats_a_common_one(tmp_path: Path) -> None:
    rows = [(f"common-{number}", "Engineer", f"Firm {number}", "Rome") for number in range(50)] + [("rare", "Fortran", "Lab", "Rome")]
    path = catalogue(tmp_path, rows)
    plan = Plan((Node("role", 1.0, (Form("Engineer"),)), Node("skill", 1.0, (Form("Fortran"),))))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path, limit=1)
    assert jobs[0].id == "rare"


def test_a_when_arc_on_a_phrase_scales_the_whole_title_down(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("human", "Nurse", "Clinic", "Rome"), ("vet", "Veterinary Nurse", "Vets", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Nurse"),)),), (Arc((("*", 0.2),), when="Veterinary"),))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path)
    assert [job.id for job in jobs] == ["human", "vet"]
    assert jobs[1].lexical == pytest.approx(0.2)


def test_an_unless_arc_scales_the_titles_that_lack_the_phrase(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("plain", "Engineer", "A", "Rome"), ("senior", "Senior Engineer", "B", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer"),)),), (Arc((("*", 0.3),), unless="Senior"),))
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path)
    assert [job.id for job in jobs] == ["senior", "plain"]
    assert jobs[1].lexical == pytest.approx(0.3)


def test_a_node_of_weight_zero_scores_nothing_but_still_fires_an_arc(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("remote", "Remote Engineer", "A", "Rome"), ("plain", "Engineer", "B", "Rome"), ("only", "Remote Designer", "C", "Rome")])
    plan = Plan(
        (Node("role", 1.0, (Form("Engineer"),)), Node("remote", 0.0, (Form("Remote"),))),
        (Arc((("role", 2.0),), when="remote"),),
    )
    _, jobs = search(path, Filters(plan=plan), 60, tmp_path)
    # The remote title's engineer is boosted by the arc; a title holding only the weight-0 node is not brought in.
    assert [job.id for job in jobs] == ["remote", "plain"]


def test_an_arc_multiplier_is_clamped_to_three_at_most_and_a_twentieth_at_least(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("senior", "Senior Nurse", "A", "Rome"), ("plain", "Nurse", "B", "Rome")])
    role = Node("role", 1.0, (Form("Nurse"),))
    _, boosted = search(path, Filters(plan=Plan((role,), (Arc((("*", 100.0),), when="Senior"),))), 60, tmp_path)
    assert {job.id: job.lexical for job in boosted}["plain"] == pytest.approx(1 / 3)
    _, damped = search(path, Filters(plan=Plan((role,), (Arc((("*", 0.0),), when="Senior"),))), 60, tmp_path)
    assert {job.id: job.lexical for job in damped}["senior"] == pytest.approx(0.05)


def test_a_plan_is_read_from_the_settings_with_unused_conditions_left_empty(tmp_path: Path) -> None:
    (tmp_path / "aihawk.yaml").write_text(
        textwrap.dedent("""\
            filters:
              plan:
                nodes:
                  - {id: role, weight: 1.0, forms: [{text: Nurse, weight: 1}]}
                  - {id: zone, weight: 0, forms: [{text: ITU, weight: 0.8}]}
                arcs:
                  - {when: Veterinary, unless: "", then: [{target: "*", multiplier: 0.2}]}
                  - {when: "", unless: zone, then: [{target: role, multiplier: 1.5}]}
            """),
        encoding="utf-8",
    )
    assert load_config(tmp_path).filters.plan == Plan(
        (Node("role", 1.0, (Form("Nurse", 1.0),)), Node("zone", 0.0, (Form("ITU", 0.8),))),
        (Arc((("*", 0.2),), when="Veterinary"), Arc((("role", 1.5),), unless="zone")),
    )


@pytest.mark.parametrize(
    "text",
    [
        "filters:\n  plan: [1]\n",
        "filters:\n  plan:\n    nodes: [{id: role, weight: -1, forms: []}]\n",
        "filters:\n  plan:\n    nodes: [{id: role, forms: [{weight: 1}]}]\n",
        "filters:\n  plan:\n    nodes: [{id: role, forms: []}]\n    arcs: [{when: a, unless: b, then: []}]\n",
        "filters:\n  plan:\n    nodes: [{id: role, forms: []}]\n    arcs: [{then: []}]\n",
        "filters:\n  plan:\n    nodes: [{id: role, forms: []}]\n    arcs: [{when: a, then: [{target: role}]}]\n",
        "filters:\n  plan:\n    nodes: [{id: role, forms: []}]\n    arcs: [{when: a, then: [{target: nurse, multiplier: 0.5}]}]\n",
    ],
)
def test_a_plan_that_cannot_be_used_is_refused(tmp_path: Path, text: str) -> None:
    (tmp_path / "aihawk.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_a_place_is_found_by_its_word_by_its_country_code_or_by_a_remote_posting_naming_no_country(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, country VARCHAR, remote BOOLEAN, description VARCHAR, posted_at VARCHAR)")
    rows = [
        ("word", "Milano, Lombardia", None, False),
        ("code", "IT (ITC4C)", "IT", False),
        ("spelled", "Turin", "ITALY", False),
        ("open-remote", "Anywhere", None, True),
        ("us-remote", "Anywhere", "US", True),
        ("named-remote", "Remote, Czechia", None, True),
        ("elsewhere", "Paris", "FR", False),
        ("same-name-elsewhere", "Milano, Indiana", "US", False),
    ]
    connection.executemany("INSERT INTO t VALUES (?, 'https://a.example/', 'Engineer', ?, ?, ?, ?, 'd', NULL)", [(row[0], *row) for row in rows])
    path = publish(connection, tmp_path, 4)
    _, jobs = search(path, Filters(include=("Engineer",), locations=("Milano", "Italy"), countries=("IT",)), 60, tmp_path)
    assert {job.id for job in jobs} == {"word", "code", "spelled", "open-remote"}


def test_the_titles_a_person_reads_are_the_first_wordings_not_the_ids() -> None:
    plan = Plan((Node("role", 1.0, (Form("Electrician"), Form("Approved Electrician"))), Node("testing", 1.5, (Form("EICR Tester"),))))
    assert Filters(include=("role", "testing"), plan=plan).titles() == "Electrician, EICR Tester"
    # Without a plan the ids are the words searched, and they are what is shown.
    assert Filters(include=("python", "java")).titles() == "python, java"


def test_a_place_word_is_a_whole_part_of_the_location_not_a_word_inside_a_longer_name(tmp_path: Path) -> None:
    # The Australian posting has no country, so only its location can keep it out of a search for Wales.
    path = catalogue(tmp_path, [("au", "Engineer", "Firm", "Moorebank, New South Wales, Australia"), ("uk", "Engineer", "Firm", "Cardiff, Wales, GB")])
    _, jobs = search(path, Filters(include=("Engineer",), locations=("Wales",)), 60, tmp_path)
    assert [job.id for job in jobs] == ["uk"]


def test_a_place_starts_the_location_or_follows_a_separator_a_dash_or_a_number_never_a_word(tmp_path: Path) -> None:
    kept = ["London", "UK - London", "IT-Milan", "ITA-Lombardy-Milan", "Dallas TX", "TX-Dallas", "Milano Via Turati 25-27", "Berlin Office", "Italy - Rome", "Remote - Italy", "Dallas-Fort Worth", "10117 Berlin"]
    refused = ["New London", "New Berlin", "New South Wales"]
    # Each posting has its own company: one job per title and company would keep only one of them.
    path = catalogue(tmp_path, [(location, "Engineer", location, location) for location in kept + refused])
    places = ("London", "Milan", "Milano", "Dallas", "Berlin", "Rome", "Italy", "Wales")
    _, jobs = search(path, Filters(include=("Engineer",), locations=places), 60, tmp_path)
    assert {job.id for job in jobs} == set(kept)


def test_the_own_city_comes_first_and_a_job_posted_once_per_place_is_one_job(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    rows = [
        ("rome", "Engineer", "Same", "Rome, Italy"),
        ("milan", "engineer", "Same", "Milano, Italy"),
        ("other", "Engineer", "Other", "Rome, Italy"),
        ("no-name-1", "Engineer", "", "Rome, Italy"),
        ("no-name-2", "Engineer", "", "Rome, Italy"),
    ]
    connection.executemany("INSERT INTO t VALUES (?, 'https://a.example/', ?, ?, ?, 'd', NULL)", rows)
    path = publish(connection, tmp_path, 4)
    _, jobs = search(path, Filters(include=("Engineer",), locations=("Italy",), cities=("Milano",)), 60, tmp_path)
    assert jobs[0].id == "milan"
    assert {job.id for job in jobs} == {"milan", "other", "no-name-1", "no-name-2"}


def test_rarity_runs_from_a_floor_to_one() -> None:
    assert rarity(1000, 100_000) == 0.05
    assert rarity(10_000_000, 10) == 1.0
    assert 0.05 < rarity(1000, 100) < 1.0


def test_filters_match_whole_words_and_keep_symbols_literal(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany(
        "INSERT INTO t VALUES (?, 'https://a.example/', ?, 'c', ?, 'd', NULL)",
        [("1", "Java Developer", "Roma, Italy"), ("2", "JavaScript Developer", "Roma"), ("3", "Java Lead", "Bucharest, Romania"), ("4", "C++ Developer", "Roma")],
    )
    path = publish(connection, tmp_path, 4)

    def keys(filters: Filters) -> set[str]:
        return {job.id for job in search(path, filters, 60, tmp_path)[1]}

    assert keys(Filters(include=("java",))) == {"1", "3"}
    assert keys(Filters(include=("java",), locations=("roma",))) == {"1"}
    assert keys(Filters(include=("c++",))) == {"4"}
    assert keys(Filters(include=("developer",), exclude=("JAVA",))) == {"2", "4"}


def test_names_come_without_html_entities(tmp_path: Path) -> None:
    _, jobs = search(database(tmp_path), Filters(include=("accountant",)), 60, tmp_path)
    assert [(job.title, job.company) for job in jobs] == [("Accountant & Clerk", "B&A")]


def test_search_needs_titles_and_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="no job titles"):
        search(database(tmp_path), Filters(), 60, tmp_path)


def test_a_database_that_cannot_be_read_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="manifest"):
        search(str(tmp_path / "none.json"), Filters(include=("x",)), 60, tmp_path)


def test_a_database_that_cannot_be_reached_says_so_in_plain_words_and_keeps_the_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    monkeypatch.setattr("aihawk.database.urllib.request.urlopen", no_network)
    with pytest.raises(DatabaseError, match="cannot be reached: check your internet connection") as error:
        search("https://example.test/manifest.json", Filters(include=("x",)), 60, tmp_path)
    assert "<urlopen error [Errno 11001] getaddrinfo failed>" in str(error.value)


def test_a_posting_with_no_description_needs_a_title_twice_as_fitting(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany("INSERT INTO t VALUES (?, ?, 'Python Engineer', ?, 'Rome', ?, NULL)", [
        ("bare", "https://a.example/1", "Bare Co", ""),
        ("told", "https://a.example/2", "Told Co", "Builds Python services. " * 20),
    ])
    _, jobs = search(publish(connection, tmp_path, 2), Filters(include=("python",)), 60, tmp_path)
    assert [job.id for job in jobs] == ["told", "bare"]
    assert jobs[1].lexical == pytest.approx(UNDESCRIBED)


def files_of(location: str) -> tuple[tuple[str, str, str], ...]:
    return description_files(json.loads(Path(location).read_text(encoding="utf-8")), location)


def test_many_descriptions_come_in_order_and_empty_where_there_is_no_key(tmp_path: Path) -> None:
    location = database(tmp_path)
    _, jobs = search(location, Filters(include=("python",)), 60, tmp_path)
    keys = [job.id for job in jobs] + ["", "no-such-key"]
    counts: list[int] = []
    texts = stored_descriptions(keys, files_of(location), done=counts.append)
    assert texts == [stored_descriptions([key], files_of(location))[0] for key in keys[:-2]] + ["", ""]
    assert sorted(texts[:-2]) == ["Build", "Learn"] and counts[-1] == len(keys)


def test_a_database_that_cannot_be_read_is_reported_once_and_the_search_has_no_descriptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    location = database(tmp_path)
    _, jobs = search(location, Filters(include=("python",)), 60, tmp_path)
    keys = [job.id for job in jobs]
    files = files_of(location)

    class Unreadable:
        def execute(self, *args: object) -> None:
            raise duckdb.Error("the extension cannot be downloaded")

        def close(self) -> None:
            pass

    monkeypatch.setattr(duckdb, "connect", lambda: Unreadable())
    reported: list[str] = []
    assert stored_descriptions(keys, files, problem=reported.append) == [""] * len(keys)
    assert len(reported) == 1 and "job database" in reported[0]


def test_what_was_tried_is_listed_newest_first_in_plain_words_with_what_the_page_said(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name in ("sent", "unsure", "refused", "blocked", "failed", "untried"):
        store.add(Job(name, "Co", name, "L", "u", "a", "d"))
    store.set_proof("sent", Proof("2026-11-03T09:00:00", "https://x/thanks", "submitted", "Thank you", "the page", "/shots/sent.png"))
    store.set_status("sent", "submitted", "filled")
    store.set_proof("unsure", Proof("2026-11-04T08:00:00", "https://x/", "unknown", "no words"))
    store.set_status("unsure", "unconfirmed", "filled")
    store.set_proof("refused", Proof("2026-11-02T10:00:00", "https://x/form", "rejected", "phone is required"))
    store.set_status("refused", "refused", "filled")
    store.set_status("blocked", "blocked", "a captcha")
    store.set_status("failed", "failed", "timeout")
    # Set after the statuses, which stamp the time of the change: these two are ordered by it.
    store._db.execute("UPDATE jobs SET updated = ? WHERE id = ?", ("2026-11-01T12:00:00", "blocked"))
    store._db.execute("UPDATE jobs SET updated = ? WHERE id = ?", ("2026-11-05T07:00:00", "failed"))
    listed = store.sent()
    assert [(item.job.id, sent_word(item)) for item in listed] == [
        ("failed", "failed"),
        ("unsure", "sent, not confirmed"),
        ("sent", "sent and confirmed"),
        ("refused", "refused by the form"),
        ("blocked", "blocked"),
    ]
    assert [said(item) for item in listed] == ["timeout", "no words", "Thank you", "phone is required", "a captcha"]
    assert listed[2].proof.screenshot == "/shots/sent.png" and listed[2].proof.text == "the page" and listed[2].proof.url == "https://x/thanks"


def test_a_later_attempt_without_a_click_clears_the_proof_of_an_earlier_one(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    store.add(JOB)
    store.set_status(JOB.id, "refused", "the form refused it")
    store.set_proof(JOB.id, Proof("2026-11-04T08:00:00", "u", "rejected", "old"))
    store.set_status(JOB.id, "blocked", "a captcha")
    store.set_proof(JOB.id, Proof())
    assert store.sent()[0].proof == Proof()


def test_applied_csv_has_the_columns_and_a_page_cannot_make_a_formula_of_a_cell(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    store.add(Job("h", "=HYPERLINK(\"http://x\")", "+1 Engineer", "L", "https://u", "a", "d"))
    store.set_status("h", "submitted", "filled")
    store.set_proof("h", Proof("2026-11-04T08:00:00", "https://u/thanks", "submitted", "@cmd evidence", "page text\nline two", "/shots/h.png"))
    path = tmp_path / "applied.csv"
    write_applied(path, store.sent())
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.reader(file))
    assert rows[0] == list(COLUMNS)
    assert rows[1] == [
        "2026-11-04T08:00:00",
        "'=HYPERLINK(\"http://x\")",
        "'+1 Engineer",
        "sent and confirmed",
        "'@cmd evidence",
        "https://u",
        "https://u/thanks",
        "/shots/h.png",
        "filled",
        "page text\nline two",
    ]
    assert not path.with_name("applied.csv.tmp").exists()


def test_the_list_weighs_the_judgment_and_the_title_together_and_keeps_what_jev_passed_over_below(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name, lexical in (("judged-fit", 0.2), ("same-score-better-title", 1.0), ("unjudged", 0.9), ("passed", 1.0)):
        store.add(Job(name, "A", "T", "L", "u", "a", "d", lexical))
    store.set_score("judged-fit", 4.0, {})
    store.set_score("same-score-better-title", 4.0, {})
    store.set_statuses(["passed"], "passed_over")
    store.set_evidence("passed", 2.0)
    assert [item.job.id for item in store.all()] == ["same-score-better-title", "judged-fit", "passed", "unjudged"]


@pytest.mark.parametrize(
    "text",
    ["filters: {include: python}", "a: [", "- a"],
)
def test_settings_that_cannot_be_used_are_refused(tmp_path: Path, text: str) -> None:
    (tmp_path / "aihawk.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("text", "key"),
    [("max_age_days: sixty", "max_age_days"), ("max_age_days: [1]", "max_age_days"), ("filters: [python]", "filters")],
)
def test_a_setting_of_the_wrong_type_is_a_config_error_that_names_the_key(tmp_path: Path, text: str, key: str) -> None:
    (tmp_path / "aihawk.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        load_config(tmp_path)


def test_a_posting_whose_address_is_not_the_webs_is_left_out(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany(
        "INSERT INTO t VALUES (?, ?, 'Java Developer', ?, 'Rome', 'd', NULL)",
        [("web", "HTTPS://a.example/1", "a"), ("disk", "file:///home/me/.aihawk/key", "b"), ("app", "javascript:alert(1)", "c")],
    )
    path = publish(connection, tmp_path, 4)
    assert [job.id for job in search(path, Filters(include=("java",)), 60, tmp_path)[1]] == ["web"]


def test_a_full_disk_while_the_index_downloads_says_the_disk_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Unreadable(io.BytesIO):
        def read(self, *_: object) -> bytes:
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_, **__: Unreadable())
    manifest = {"index": {"name": "index.parquet", "bytes": 740_000_000}, "built_at": "2026-11-05T00:00:00+00:00", "base_url": "https://a.example"}
    with pytest.raises(DatabaseError, match="The disk is full: the job index needs 740 MB"):
        local_index(manifest, "https://a.example/manifest.json", tmp_path)
    assert not list(tmp_path.glob("*.part"))


def test_the_index_download_says_how_far_it_has_come(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *_, **__: io.BytesIO(b"x" * (4 << 20)))
    manifest = {"index": {"name": "index.parquet", "bytes": 4 << 20}, "built_at": "2026-11-05T00:00:00+00:00", "base_url": "https://a.example"}
    lines: list[str] = []
    assert local_index(manifest, "https://a.example/manifest.json", tmp_path, lines.append).read_bytes() == b"x" * (4 << 20)
    assert [line.strip() for line in lines[1:]] == ["25%", "50%", "75%"]


def test_a_job_seen_again_keeps_its_score_and_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    job = Job("x", "Co", "T", "L", "https://a.example/", "https://a.example/", "d")
    assert store.add(job) is True
    store.set_score(job.id, 4.5, {"score": 4.5})
    store.set_status(job.id, "blocked", "a puzzle")
    assert store.add(job) is False
    item = store.get(job.id)
    assert item is not None and (item.status, item.score, item.report) == ("blocked", 4.5, {"score": 4.5})


def test_a_new_folder_defaults_to_the_published_database(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.model == "deepseek/deepseek-v4.1-flash" and config.database.endswith("/jobs-latest/manifest.json")



def _index_rows(path: Path, rows: list[tuple[str, str]]) -> None:
    connection = duckdb.connect()
    connection.execute(f"CREATE TABLE i ({', '.join(f'{column} VARCHAR' for column in INDEX_COLUMNS)})")
    connection.executemany("INSERT INTO i (key, url, title) VALUES (?, ?, ?)", [(key, f"https://{key}", title) for key, title in rows])
    connection.execute(f"COPY i TO '{path.as_posix()}' (FORMAT parquet)")


def _delta_rows(path: Path, rows: list[tuple[str, str, str | None]]) -> None:
    connection = duckdb.connect()
    columns = ", ".join(f"{column} VARCHAR" for column in INDEX_COLUMNS)
    connection.execute(f"CREATE TABLE d (op VARCHAR, {columns})")
    connection.executemany("INSERT INTO d (op, key, url, title) VALUES (?, ?, ?, ?)",
                           [(op, key, f"https://{key}" if title else None, title) for op, key, title in rows])
    connection.execute(f"COPY d TO '{path.as_posix()}' (FORMAT parquet)")


def test_the_chain_of_deltas_is_followed_from_the_build_this_computer_has() -> None:
    deltas = [{"build": "b2", "after": "b1"}, {"build": "b3", "after": "b2"}, {"build": "b4", "after": "b3"}]
    assert _chain(deltas, "b2") == deltas[1:]
    assert _chain(deltas, "b4") == []
    assert _chain(deltas, "b0") is None
    assert _chain([{"build": "b2", "after": "b1"}, {"build": "b4", "after": "b3"}], "b1") is None


def test_deltas_are_applied_in_one_rewrite_and_the_newest_action_on_a_key_wins(tmp_path: Path) -> None:
    index = tmp_path / "index.parquet"
    _index_rows(index, [("a", "Old A"), ("b", "B"), ("c", "C")])
    # First delta: a changes, d arrives. Second: d goes again, e arrives, b goes.
    _delta_rows(tmp_path / "delta-jobs-20261107-010000.parquet", [("add", "a", "New A"), ("add", "d", "D")])
    _delta_rows(tmp_path / "delta-jobs-20261107-060000.parquet", [("drop", "d", None), ("add", "e", "E"), ("drop", "b", None)])
    chain = [{"url": (tmp_path / "delta-jobs-20261107-010000.parquet").as_posix()}, {"url": (tmp_path / "delta-jobs-20261107-060000.parquet").as_posix()}]
    _apply(index, chain)
    rows = duckdb.connect().execute(f"SELECT key, title FROM read_parquet('{index.as_posix()}') ORDER BY key").fetchall()
    assert rows == [("a", "New A"), ("c", "C"), ("e", "E")]


def test_an_index_kept_from_before_a_column_was_added_takes_the_delta_that_brings_it(tmp_path: Path) -> None:
    path = tmp_path / "index.parquet"
    _index_rows(path, [("a", "A"), ("b", "B")])
    connection = duckdb.connect()
    connection.execute(f"COPY (SELECT * EXCLUDE (described) FROM read_parquet('{path.as_posix()}')) TO '{(tmp_path / 'old.parquet').as_posix()}' (FORMAT parquet)")
    (tmp_path / "old.parquet").replace(path)
    _delta_rows(tmp_path / "delta.parquet", [("add", "b", "B"), ("add", "c", "C")])
    _apply(path, [{"url": (tmp_path / "delta.parquet").as_posix()}])
    rows = connection.execute(f"SELECT key FROM read_parquet('{path.as_posix()}') ORDER BY key").fetchall()
    assert rows == [("a",), ("b",), ("c",)]
    assert "described" in {row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()}


class _Answer(io.BytesIO):
    def __init__(self, body: bytes, url: str = "") -> None:
        super().__init__(body)
        self.url = url


def test_when_github_com_does_not_serve_the_release_its_files_come_through_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.database as database_module

    monkeypatch.setattr(database_module, "RELEASES", database_module._Releases())
    latest = "https://github.com/o/r/releases/download/jobs-latest/manifest.json"
    asked: list[str] = []

    def served(request: Any, *_: Any, **__: Any) -> _Answer:
        url = request if isinstance(request, str) else request.full_url
        asked.append(url)
        if url.startswith("https://github.com/"):
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)  # type: ignore[arg-type]
        if url == "https://api.github.com/repos/o/r/releases/tags/jobs-latest":
            return _Answer(json.dumps({"assets": [{"name": "manifest.json", "url": "https://api.github.com/repos/o/r/releases/assets/1"}]}).encode())
        if url == "https://api.github.com/repos/o/r/releases/assets/1":
            assert request.headers["Accept"] == "application/octet-stream"
            return _Answer(b"", url="https://release-assets.example/signed-1")
        if url == "https://release-assets.example/signed-1":
            return _Answer(b'{"build": "b1"}')
        raise AssertionError(url)

    monkeypatch.setattr("urllib.request.urlopen", served)
    assert load_manifest(latest) == {"build": "b1"}
    assert database_module.RELEASES.where(latest) == "https://release-assets.example/signed-1"
    assert asked.count("https://api.github.com/repos/o/r/releases/tags/jobs-latest") == 1


def test_a_file_github_com_does_not_have_is_not_taken_for_its_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.database as database_module

    monkeypatch.setattr(database_module, "RELEASES", database_module._Releases())

    def missing(url: str, *_: Any, **__: Any) -> None:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", missing)
    with pytest.raises(DatabaseError):
        load_manifest("https://github.com/o/r/releases/download/jobs-latest/manifest.json")
    assert not database_module.RELEASES.via_api


def test_a_description_is_found_by_its_key_in_the_file_whose_range_holds_it(tmp_path: Path) -> None:
    connection = duckdb.connect()
    for name, rows in (("jobs_0.parquet", [("a/1", "First"), ("a/2", "Second")]), ("jobs_1.parquet", [("b/1", "Third")])):
        connection.execute("CREATE OR REPLACE TABLE t (key VARCHAR, description VARCHAR)")
        connection.executemany("INSERT INTO t VALUES (?, ?)", rows)
        connection.execute(f"COPY (SELECT * FROM t ORDER BY key) TO '{(tmp_path / name).as_posix()}' (FORMAT parquet)")
    manifest = {"base_url": tmp_path.as_posix(), "files": [
        {"name": "jobs_1.parquet", "first_key": "b/1", "last_key": "b/1"},
        {"name": "jobs_0.parquet", "first_key": "a/1", "last_key": "a/2"},
    ]}
    files = description_files(manifest, "unused")
    assert [first for first, _, _ in files] == ["a/1", "b/1"]
    assert stored_descriptions(["b/1", "a/2", "zz/9", ""], files) == ["Third", "Second", "", ""]


def test_an_index_kept_here_is_downloaded_once_then_brought_up_to_date_by_its_deltas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = tmp_path / "release"
    release.mkdir()
    snapshot = release / "index.parquet"
    _index_rows(snapshot, [("a", "A"), ("b", "B")])
    downloads: list[str] = []

    def served(url: str, *_: object, **__: object) -> io.BytesIO:
        downloads.append(url)
        return io.BytesIO(snapshot.read_bytes())

    monkeypatch.setattr("urllib.request.urlopen", served)
    cache = tmp_path / "cache"
    first = {"build": "b1", "deltas": [], "index": {"name": "index.parquet", "bytes": snapshot.stat().st_size},
             "built_at": "2026-11-07T01:00:00+00:00", "base_url": "https://a.example/b1"}
    path = local_index(first, "https://a.example/manifest.json", cache)
    assert duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0] == 2
    assert len(downloads) == 1
    _delta_rows(release / "delta-jobs-b2.parquet", [("add", "c", "C"), ("drop", "a", None)])
    second = {**first, "build": "b2", "base_url": "https://a.example/b2",
              "deltas": [{"build": "b2", "after": "b1", "url": (release / "delta-jobs-b2.parquet").as_posix(), "adds": 1, "drops": 1}]}
    lines: list[str] = []
    path = local_index(second, "https://a.example/manifest.json", cache, lines.append)
    assert len(downloads) == 1, "the whole index is never downloaded again"
    assert lines == ["updating the job index: 1 postings new or changed, 1 gone"]
    keys = [row[0] for row in duckdb.connect().execute(f"SELECT key FROM read_parquet('{path.as_posix()}') ORDER BY key").fetchall()]
    assert keys == ["b", "c"]
    assert local_index(second, "https://a.example/manifest.json", cache) == path and len(downloads) == 1
