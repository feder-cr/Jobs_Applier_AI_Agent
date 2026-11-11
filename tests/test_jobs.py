"""The job boards, the filters, the store and the settings."""

from __future__ import annotations

import csv
import errno
import io
import json
import socket
import textwrap
import urllib.error
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from aihawk.config import Arc, ConfigError, Filters, Form, Node, Plan, load_config
from aihawk.database import RECALL, UNDESCRIBED, DatabaseError, Job, load_manifest, rarity, search
from aihawk.library import Library, LibraryError
from aihawk.store import COLUMNS, Proof, Store, said, sent_word, write_applied
from fakes import publish

JOB = Job("g:a:1", "Acme", "Senior Python Engineer", "Rome, Italy", "u", "a", "d")


def found(location: str, filters: Filters, max_age_days: int, cache: Path, limit: int = RECALL) -> list[Job]:
    """The search over the database at `location`, brought to this computer first, each job named by its row's key."""
    library = Library(cache / "library")
    library.sync(load_manifest(location), location)
    keys = json.loads(Path(location).with_name("keys.json").read_text(encoding="utf-8"))
    return [replace(job, id=keys.get(job.url, job.id)) for job in search(library, filters, max_age_days, limit)]


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
    jobs = found(database(tmp_path), filters, 60, tmp_path)
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
    jobs = found(path, Filters(plan=plan), 60, tmp_path, limit=3)
    assert jobs[0].id in {"rare", "both"} and jobs[1].id in {"rare", "both"}
    # A node counts once, however many times a title says it: the two score the same.
    assert jobs[0].lexical == jobs[1].lexical == 1.0
    assert jobs[2].lexical < 0.2


def test_a_node_holds_its_best_wording_and_wordings_never_add_up(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("both", "Engineer Developer", "A", "Rome"), ("dev", "Developer", "B", "Rome"), ("eng", "Engineer", "C", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer"), Form("Developer"))),))
    jobs = found(path, Filters(plan=plan), 60, tmp_path)
    # Each title holds one wording of the node as rare as any other: all three score the same, and "both" is no higher.
    assert {job.id: job.lexical for job in jobs} == {"both": 1.0, "dev": 1.0, "eng": 1.0}


@pytest.mark.parametrize(("weights", "order"), [((2.0, 0.5), ["Engineer", "Developer"]), ((0.5, 2.0), ["Developer", "Engineer"])])
def test_a_wording_weight_changes_the_order(tmp_path: Path, weights: tuple[float, float], order: list[str]) -> None:
    path = catalogue(tmp_path, [("e", "Engineer", "A", "Rome"), ("d", "Developer", "B", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer", weights[0]), Form("Developer", weights[1]))),))
    jobs = found(path, Filters(plan=plan), 60, tmp_path)
    assert [job.title for job in jobs] == order


def test_a_rare_wording_beats_a_common_one(tmp_path: Path) -> None:
    rows = [(f"common-{number}", "Engineer", f"Firm {number}", "Rome") for number in range(50)] + [("rare", "Fortran", "Lab", "Rome")]
    path = catalogue(tmp_path, rows)
    plan = Plan((Node("role", 1.0, (Form("Engineer"),)), Node("skill", 1.0, (Form("Fortran"),))))
    jobs = found(path, Filters(plan=plan), 60, tmp_path, limit=1)
    assert jobs[0].id == "rare"


def test_a_when_arc_on_a_phrase_scales_the_whole_title_down(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("human", "Nurse", "Clinic", "Rome"), ("vet", "Veterinary Nurse", "Vets", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Nurse"),)),), (Arc((("*", 0.2),), when="Veterinary"),))
    jobs = found(path, Filters(plan=plan), 60, tmp_path)
    assert [job.id for job in jobs] == ["human", "vet"]
    assert jobs[1].lexical == pytest.approx(0.2)


def test_an_unless_arc_scales_the_titles_that_lack_the_phrase(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("plain", "Engineer", "A", "Rome"), ("senior", "Senior Engineer", "B", "Rome")])
    plan = Plan((Node("role", 1.0, (Form("Engineer"),)),), (Arc((("*", 0.3),), unless="Senior"),))
    jobs = found(path, Filters(plan=plan), 60, tmp_path)
    assert [job.id for job in jobs] == ["senior", "plain"]
    assert jobs[1].lexical == pytest.approx(0.3)


def test_a_node_of_weight_zero_scores_nothing_but_still_fires_an_arc(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("remote", "Remote Engineer", "A", "Rome"), ("plain", "Engineer", "B", "Rome"), ("only", "Remote Designer", "C", "Rome")])
    plan = Plan(
        (Node("role", 1.0, (Form("Engineer"),)), Node("remote", 0.0, (Form("Remote"),))),
        (Arc((("role", 2.0),), when="remote"),),
    )
    jobs = found(path, Filters(plan=plan), 60, tmp_path)
    # The remote title's engineer is boosted by the arc; a title holding only the weight-0 node is not brought in.
    assert [job.id for job in jobs] == ["remote", "plain"]


def test_an_arc_multiplier_is_clamped_to_three_at_most_and_a_twentieth_at_least(tmp_path: Path) -> None:
    path = catalogue(tmp_path, [("senior", "Senior Nurse", "A", "Rome"), ("plain", "Nurse", "B", "Rome")])
    role = Node("role", 1.0, (Form("Nurse"),))
    boosted = found(path, Filters(plan=Plan((role,), (Arc((("*", 100.0),), when="Senior"),))), 60, tmp_path)
    assert {job.id: job.lexical for job in boosted}["plain"] == pytest.approx(1 / 3)
    damped = found(path, Filters(plan=Plan((role,), (Arc((("*", 0.0),), when="Senior"),))), 60, tmp_path)
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
    jobs = found(path, Filters(include=("Engineer",), locations=("Milano", "Italy"), countries=("IT",)), 60, tmp_path)
    assert {job.id for job in jobs} == {"word", "code", "spelled", "open-remote"}


def test_the_titles_a_person_reads_are_the_first_wordings_not_the_ids() -> None:
    plan = Plan((Node("role", 1.0, (Form("Electrician"), Form("Approved Electrician"))), Node("testing", 1.5, (Form("EICR Tester"),))))
    assert Filters(include=("role", "testing"), plan=plan).titles() == "Electrician, EICR Tester"
    # Without a plan the ids are the words searched, and they are what is shown.
    assert Filters(include=("python", "java")).titles() == "python, java"


def test_a_place_word_is_a_whole_part_of_the_location_not_a_word_inside_a_longer_name(tmp_path: Path) -> None:
    # The Australian posting has no country, so only its location can keep it out of a search for Wales.
    path = catalogue(tmp_path, [("au", "Engineer", "Firm", "Moorebank, New South Wales, Australia"), ("uk", "Engineer", "Firm", "Cardiff, Wales, GB")])
    jobs = found(path, Filters(include=("Engineer",), locations=("Wales",)), 60, tmp_path)
    assert [job.id for job in jobs] == ["uk"]


def test_a_place_starts_the_location_or_follows_a_separator_a_dash_or_a_number_never_a_word(tmp_path: Path) -> None:
    kept = ["London", "UK - London", "IT-Milan", "ITA-Lombardy-Milan", "Dallas TX", "TX-Dallas", "Milano Via Turati 25-27", "Berlin Office", "Italy - Rome", "Remote - Italy", "Dallas-Fort Worth", "10117 Berlin"]
    refused = ["New London", "New Berlin", "New South Wales"]
    # Each posting has its own company: one job per title and company would keep only one of them.
    path = catalogue(tmp_path, [(location, "Engineer", location, location) for location in kept + refused])
    places = ("London", "Milan", "Milano", "Dallas", "Berlin", "Rome", "Italy", "Wales")
    jobs = found(path, Filters(include=("Engineer",), locations=places), 60, tmp_path)
    assert {job.id for job in jobs} == set(kept)


def test_the_own_city_comes_first_and_every_posting_stands_one_per_place(tmp_path: Path) -> None:
    # The database makes one posting of a job carried by several sites; the same role in two cities is two jobs.
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
    jobs = found(path, Filters(include=("Engineer",), locations=("Italy",), cities=("Milano",)), 60, tmp_path)
    assert jobs[0].id == "milan"
    assert {job.id for job in jobs} == {"rome", "milan", "other", "no-name-1", "no-name-2"}


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
        return {job.id for job in found(path, filters, 60, tmp_path)}

    assert keys(Filters(include=("java",))) == {"1", "3"}
    assert keys(Filters(include=("java",), locations=("roma",))) == {"1"}
    assert keys(Filters(include=("c++",))) == {"4"}
    assert keys(Filters(include=("developer",), exclude=("JAVA",))) == {"2", "4"}


def test_names_come_without_html_entities(tmp_path: Path) -> None:
    jobs = found(database(tmp_path), Filters(include=("accountant",)), 60, tmp_path)
    assert [(job.title, job.company) for job in jobs] == [("Accountant & Clerk", "B&A")]


def test_search_needs_titles_and_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="no job titles"):
        found(database(tmp_path), Filters(), 60, tmp_path)


def test_a_database_that_cannot_be_read_says_so(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="cannot be read"):
        load_manifest(str(tmp_path / "none.json"))


def test_a_database_that_cannot_be_reached_says_so_in_plain_words_and_keeps_the_detail_out_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: object, **__: object) -> None:
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    monkeypatch.setattr("urllib.request.urlopen", down)
    with pytest.raises(DatabaseError) as raised:
        load_manifest("https://example.test/manifest.json")
    assert str(raised.value) == "Cannot reach the job list. Check your internet connection, then start AIHawk again."
    assert "getaddrinfo" not in str(raised.value) and isinstance(raised.value.__cause__, urllib.error.URLError)


def test_nothing_here_yet_finds_nothing(tmp_path: Path) -> None:
    assert search(Library(tmp_path / "library"), Filters(include=("python",)), 60) == []


def test_a_posting_with_no_description_needs_a_title_twice_as_fitting(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany("INSERT INTO t VALUES (?, ?, 'Python Engineer', ?, 'Rome', ?, NULL)", [
        ("bare", "https://a.example/1", "Bare Co", ""),
        ("told", "https://a.example/2", "Told Co", "Builds Python services. " * 20),
    ])
    jobs = found(publish(connection, tmp_path, 2), Filters(include=("python",)), 60, tmp_path)
    assert [job.id for job in jobs] == ["told", "bare"]
    assert jobs[1].lexical == pytest.approx(UNDESCRIBED)


def test_descriptions_come_in_order_from_this_computer_and_empty_where_there_is_none(tmp_path: Path) -> None:
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany("INSERT INTO t VALUES (?, ?, 'Python Engineer', 'Co', 'Rome', ?, NULL)", [
        ("told", "https://a.example/1", "Build Python services. " * 12), ("short", "https://a.example/2", "Too short"),
    ])
    location = publish(connection, tmp_path, 2)
    library = Library(tmp_path / "library")
    library.sync(load_manifest(location), location)
    urls = ["https://a.example/2", "https://a.example/1", "https://no.such/posting"]
    assert library.descriptions(urls) == ["", "Build Python services. " * 12, ""]


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


def test_the_list_weighs_the_score_and_the_title_together_and_keeps_what_jev_passed_over_below(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    for name, lexical in (("judged-fit", 0.2), ("same-score-better-title", 1.0), ("unjudged", 0.9), ("passed", 1.0)):
        store.add(Job(name, "A", "T", "L", "u", "a", "d", lexical))
    store.set_score("judged-fit", 4.0)
    store.set_score("same-score-better-title", 4.0)
    store.set_statuses(["passed"], "passed_over")
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
    assert [job.id for job in found(path, Filters(include=("java",)), 60, tmp_path)] == ["web"]


def test_a_full_disk_while_the_jobs_download_says_the_disk_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Unreadable(io.BytesIO):
        def read(self, *_: object) -> bytes:
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_, **__: Unreadable())
    manifest = {"build": "b1", "chunks": [{"name": "jobs_0.parquet", "rows": 1}], "deltas": [], "base_url": "https://a.example/b1"}
    with pytest.raises(LibraryError, match="The disk is full"):
        Library(tmp_path).sync(manifest, "https://a.example/manifest.json")
    assert not list(tmp_path.rglob("*.part"))


def test_a_store_from_before_the_key_changed_keeps_one_row_per_posting_the_one_furthest_along(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "jobs.db"
    store = Store(path)
    link = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Ankeny-IA/Sales-Rep_R-1"
    # The rows as a store of that time held them: one posting under two keys, each its own row.
    old = [
        ("acme.wd5.myworkdayjobs.com/en-us/external/job/ankeny-ia/sales-rep_r-1", link, "new"),
        ("acme.wd5.myworkdayjobs.com/external/job/sales-rep_r-1", link.replace("en-US/", "").replace("Ankeny-IA/", ""), "submitted"),
        ("greenhouse.io/acme/jobs/1", "https://boards.greenhouse.io/acme/jobs/1", "new"),
    ]
    store._db.executemany(
        "INSERT INTO jobs (id, company, title, location, url, apply_url, description, status, first_seen, updated) VALUES (?, 'Acme', 'T', '', ?, 'u', '', ?, 'x', 'x')", old
    )
    store._db.execute("UPDATE jobs SET key = NULL")
    store._db.commit()
    store.close()
    sqlite3.connect(path).execute("PRAGMA user_version = 1").connection.commit()
    store = Store(path)
    # One row per posting, the one furthest along, now under its address.
    ids = {item.job.id: item.status for item in store.all()}
    assert ids == {link.replace("en-US/", "").replace("Ankeny-IA/", ""): "submitted", "https://boards.greenhouse.io/acme/jobs/1": "new"}
    store.close()


def test_a_posting_already_known_under_another_of_its_links_is_not_added_again(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    link = "https://acme.wd5.myworkdayjobs.com/External/job/Sales-Rep_R-7"
    assert store.add(Job(link, "Acme", "Sales Rep", "", link, link, "")) is True
    store.set_status(link, "submitted", "sent")
    # The same Workday posting under its other address: the same key, so the job sent is not offered again.
    other = "https://wd3.myworkdaysite.com/recruiting/acme/External/job/Sales-Rep_R-7"
    assert store.add_all([Job(other, "Acme", "Sales Rep", "", other, other, "")]) == 0
    assert [item.job.id for item in store.all()] == [link]
    store.close()


def test_a_job_seen_again_keeps_its_score_and_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    job = Job("x", "Co", "T", "L", "https://a.example/", "https://a.example/", "d")
    assert store.add(job) is True
    store.set_score(job.id, 4.5)
    store.set_status(job.id, "blocked", "a puzzle")
    assert store.add(job) is False
    item = store.get(job.id)
    assert item is not None and (item.status, item.score) == ("blocked", 4.5)


def test_a_new_folder_defaults_to_the_published_database(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.model == "deepseek/deepseek-v4.1-flash" and config.database == "https://huggingface.co/datasets/feder-cr/jobs/resolve/main/library/manifest.json"
    # A settings file that names the layout before 2026-11-09 reads the new one.
    (tmp_path / "aihawk.yaml").write_text("database: https://huggingface.co/datasets/feder-cr/jobs/resolve/main/manifest.json\n", encoding="utf-8")
    assert load_config(tmp_path).database.endswith("/library/manifest.json")



def chunk(path: Path, rows: list[tuple[str, str, str | None]]) -> None:
    """A chunk, or a delta when the rows start with their `op`: (url, title, description), or (op, url, title, description)."""
    connection = duckdb.connect()
    ops = len(rows[0]) == 4
    connection.execute(f"CREATE TABLE c ({'op VARCHAR, ' if ops else ''}url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, country VARCHAR, posted_at TIMESTAMP, description VARCHAR)")
    connection.executemany(f"INSERT INTO c ({'op, ' if ops else ''}url, title, description) VALUES ({', '.join('?' for _ in rows[0])})", rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY c TO '{path.as_posix()}' (FORMAT parquet)")


def titles_here(library: Library) -> dict[str, str]:
    connection = duckdb.connect()
    try:
        return dict(connection.execute("SELECT url, title FROM postings").fetchall()) if library.view(connection) else {}
    finally:
        connection.close()


def test_the_chunks_come_one_by_one_and_a_search_reads_what_has_come(tmp_path: Path) -> None:
    site = tmp_path / "site" / "b1"
    chunk(site / "jobs_0.parquet", [("https://a/1", "Newest", "x" * 300)])
    chunk(site / "titles_0.parquet", [("https://a/2", "No text", None)])
    manifest = {"build": "b1", "base_url": site.as_posix(), "deltas": [],
                "chunks": [{"name": "jobs_0.parquet", "rows": 1}, {"name": "titles_0.parquet", "rows": 1}]}
    library = Library(tmp_path / "here")
    seen: list[dict[str, str]] = []
    told: list[tuple[int, int]] = []
    library.sync(manifest, "unused", lambda done, size: told.append((done, size)), lambda: seen.append(titles_here(library)))
    assert seen == [{"https://a/1": "Newest"}, {"https://a/1": "Newest", "https://a/2": "No text"}], "a search reads each chunk as it comes"
    assert told == [(0, 2), (1, 2), (2, 2)] and library.complete()
    # A second start downloads nothing.
    library.sync(manifest, "unused", arrived=lambda: seen.append({}))
    assert len(seen) == 2


def test_the_deltas_after_the_build_here_bring_it_up_to_date_and_the_newest_action_on_an_address_wins(tmp_path: Path) -> None:
    site = tmp_path / "site"
    chunk(site / "b1" / "jobs_0.parquet", [("https://a/1", "Old title", "x" * 300), ("https://a/2", "Gone", "y" * 300), ("https://a/3", "Same", "z" * 300)])
    chunk(site / "d2.parquet", [("add", "https://a/1", "New title", "x" * 300), ("drop", "https://a/2", None, None)])
    chunk(site / "d3.parquet", [("add", "https://a/4", "Added", None), ("add", "https://a/2", "Back again", "w" * 300)])
    first = {"build": "b1", "base_url": (site / "b1").as_posix(), "deltas": [], "chunks": [{"name": "jobs_0.parquet", "rows": 3}]}
    library = Library(tmp_path / "here")
    library.sync(first, "unused")
    deltas = [{"build": "b2", "after": "b1", "url": (site / "d2.parquet").as_posix()}, {"build": "b3", "after": "b2", "url": (site / "d3.parquet").as_posix()}]
    # Named as the dataset names them: the newest last.
    deltas[0]["url"], deltas[1]["url"] = (site / "delta-b2.parquet").as_posix(), (site / "delta-b3.parquet").as_posix()
    (site / "d2.parquet").replace(site / "delta-b2.parquet")
    (site / "d3.parquet").replace(site / "delta-b3.parquet")
    library.sync({**first, "build": "b3", "deltas": deltas}, "unused")
    assert titles_here(library) == {"https://a/1": "New title", "https://a/2": "Back again", "https://a/3": "Same", "https://a/4": "Added"}
    connection = duckdb.connect()
    library.view(connection)
    assert dict(connection.execute("SELECT url, described FROM postings").fetchall())["https://a/4"] is False


def test_a_chain_that_cannot_be_followed_starts_over_from_the_newest_build(tmp_path: Path) -> None:
    site = tmp_path / "site"
    chunk(site / "b1" / "jobs_0.parquet", [("https://a/1", "From b1", "x" * 300)])
    chunk(site / "b9" / "jobs_0.parquet", [("https://a/9", "From b9", "x" * 300)])
    library = Library(tmp_path / "here")
    library.sync({"build": "b1", "base_url": (site / "b1").as_posix(), "deltas": [], "chunks": [{"name": "jobs_0.parquet", "rows": 1}]}, "unused")
    # b9's deltas start after b5: nothing leads there from b1.
    later = {"build": "b9", "base_url": (site / "b9").as_posix(), "chunks": [{"name": "jobs_0.parquet", "rows": 1}],
             "deltas": [{"build": "b9", "after": "b5", "url": "unused"}]}
    library.sync(later, "unused")
    assert titles_here(library) == {"https://a/9": "From b9"}


def test_many_deltas_become_one_and_a_search_finds_the_same(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.library as library_module

    monkeypatch.setattr(library_module, "DELTAS_APART", 2)
    site = tmp_path / "site"
    chunk(site / "b0" / "jobs_0.parquet", [("https://a/1", "First", "x" * 300)])
    deltas = []
    for number, rows in enumerate([[("add", "https://a/1", "Second", "x" * 300)], [("add", "https://a/2", "Other", None)], [("drop", "https://a/1", None, None)]], 1):
        chunk(site / f"delta-b{number}.parquet", rows)
        deltas.append({"build": f"b{number}", "after": f"b{number - 1}", "url": (site / f"delta-b{number}.parquet").as_posix()})
    library = Library(tmp_path / "here")
    library.sync({"build": "b0", "base_url": (site / "b0").as_posix(), "deltas": [], "chunks": [{"name": "jobs_0.parquet", "rows": 1}]}, "unused")
    library.sync({"build": "b3", "base_url": (site / "b0").as_posix(), "deltas": deltas, "chunks": [{"name": "jobs_0.parquet", "rows": 1}]}, "unused")
    assert [path.name for path in library.delta_files()] == ["delta-b3.parquet"]
    assert titles_here(library) == {"https://a/2": "Other"}, "the posting dropped last stays gone"


def test_only_the_chunks_of_the_searchs_days_are_downloaded_all_of_them_before_it_runs(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    site = tmp_path / "site" / "b1"
    chunk(site / "jobs_0.parquet", [("https://a/1", "New", "x" * 300)])
    chunk(site / "jobs_1.parquet", [("https://a/2", "Older", "x" * 300)])
    chunk(site / "titles_0.parquet", [("https://a/3", "New, no text", None)])

    def ago(days: int) -> str:
        return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None).isoformat()

    manifest = {"build": "b1", "base_url": site.as_posix(), "deltas": [], "chunks": [
        {"name": "jobs_0.parquet", "rows": 10, "newest": ago(1)}, {"name": "jobs_1.parquet", "rows": 90, "newest": ago(30)},
        {"name": "titles_0.parquet", "rows": 5, "newest": ago(2)},
    ]}
    library = Library(tmp_path / "here")
    library.days = 15
    seen: list[tuple[float, bool]] = []
    library.sync(manifest, "unused", arrived=lambda: seen.append((round(library.readiness(), 2), library.ready())))
    assert seen == [(0.67, False), (1.0, True)], "both new chunks, and a search only then"
    assert titles_here(library) == {"https://a/1": "New", "https://a/3": "New, no text"}
    # More days in the settings: the chunk they reach comes too.
    library.days = 60
    assert not library.ready()
    library.sync(manifest, "unused")
    assert library.ready() and "https://a/2" in titles_here(library)

