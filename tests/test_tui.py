"""The screen, driven key by key, with a scripted model and a local database: what a person actually does."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx
import pytest
from click.testing import CliRunner
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, InternalServerError, PermissionDeniedError, RateLimitError
from pypdf import PdfWriter
from textual.widgets import DataTable, Input, RichLog, Static

import aihawk.tui as tui_module
from aihawk import apply as apply_module
from aihawk import session as session_module
from aihawk import telemetry
from aihawk.apply import Outcome
from aihawk.cli import main as cli_main
from aihawk.config import Config, ConfigError, load_config
from aihawk.database import Job
from aihawk.library import Library
from aihawk.llm import cause, error_name, explain, refused
from aihawk.profile import import_answers_file, install_resume, read_resume
from aihawk.screens import Confirm, JobDetails, Said, SendBatch, SentDetails, SentList, Settings, SignIn, StopSending, TextPrompt
from aihawk.session import Session
from aihawk.store import Proof, Store, Stored
from aihawk.tui import AIHawk
from fakes import ScriptedBrowser, publish

PROFILE = {
    "plan": {
        "nodes": [{"id": "role", "weight": 1.5, "forms": [{"text": "Python Engineer", "weight": 1.0}, {"text": "Python Developer", "weight": 1.0}]}],
        "arcs": [],
    },
    "cities": ["Milan", "Milano"],
    "locations": ["Italy"],
    "countries": ["it"],
    "goals": "Senior Python work.",
    "answers": {"email": "ada@example.com", "phone": "", "location": "Milan", "linkedin": "", "website": "", "current_company": ""},
}
CV_TEXT = "# Ada Rossi\n\nSenior Python Engineer at Acme for five years, working on backend services.\n"


def answers_of(config: Config) -> dict[str, str]:
    store = Store(config.store_file)
    try:
        return store.answers()
    finally:
        store.close()


def published(folder: Path) -> str:
    """A database as the pipeline publishes it, with two postings that fit and one that does not."""
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, 'Milan', 'A posting.', NULL)",
        [
            ("low", "https://jobs.lever.co/acme/1", "Python Engineer", "Lowly"),
            ("high", "https://jobs.lever.co/beta/2", "Senior Python Engineer", "Bestco"),
            ("nosite", "https://nobody.example/3", "Python Engineer", "Noplugin"),
            ("other", "https://jobs.lever.co/gamma/4", "Accountant", "Gamma"),
        ],
    )
    return publish(connection, folder, 4)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "test-key")
    folder = tmp_path / "home"
    folder.mkdir()
    (folder / "aihawk.yaml").write_text(f"database: {published(tmp_path)}\n", encoding="utf-8")
    # A home where the mail was already left for later: the first-run question about it has its own tests.
    store = Store(folder / "data" / "jobs.db")
    store.set_answer("mail_provider", "later")
    store.close()
    return folder


@pytest.fixture
def resume(tmp_path: Path) -> Path:
    path = tmp_path / "resume.txt"
    path.write_text(CV_TEXT, encoding="utf-8")
    return path


def scripted_app(home: Path, resume: Path | None, **options: Any) -> AIHawk:
    app = AIHawk(home, resume, **options)
    model = ScriptedModelForApp()
    app.session._client = model
    return app


class ScriptedModelForApp:
    """Answers each kind of request the way a model would."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.requests: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
        """The decision endpoint: sure of Bestco, less of Lowly, and of anything else it says no."""
        self.posts.append(body)

        def sure(instructions: str) -> float:
            return 0.875 if "Bestco" in instructions else 0.625 if "Lowly" in instructions else 0.125

        return {"answers": {name: {"type": "noul", "noul": sure(question["instructions"])} for name, question in body["questions"].items()}}

    async def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        name = (request.get("response_format") or {}).get("json_schema", {}).get("name")
        if name == "search_profile":
            content = json.dumps(PROFILE)
        else:
            content = "# Ada Rossi"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


async def settle(pilot: Any, app: AIHawk) -> None:
    """Until the flow's workers are done or waiting on the person: the scoring, the jobs coming and the searches over them."""
    for _ in range(200):
        await pilot.pause(0.05)
        running = [worker for worker in app.workers if worker.is_running and worker.group in ("flow", "library", "find")]
        if not running or isinstance(app.screen, (TextPrompt, SendBatch)):
            return
    raise AssertionError("the flow never settled")


def test_a_scored_posting_with_no_text_keeps_its_score_and_the_row_carries_no_status(home: Path) -> None:
    app = AIHawk(home)
    judged = Stored(Job("x", "Co", "Engineer", "Milan", "u", "u", ""), "scored", 3.0, None)
    read = Stored(Job("y", "Co", "Engineer", "Milan", "u", "u", "A posting."), "scored", 3.0, None)
    # The row says the score and nothing else; that the text was missing is said in the details, on Enter.
    assert app.row(judged)[1] == "3.0" and len(app.row(judged)) == len(app.row(read)) == 5
    assert str(app.row(judged)[0]) == "" and str(app.row(read)[0]) == ""


def companies(app: AIHawk) -> list[str]:
    table = app.query_one(DataTable)
    return [str(table.get_row_at(index)[2]) for index in range(table.row_count)]


def test_a_resume_is_all_it_takes_to_get_the_best_jobs_first(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            table = app.query_one(DataTable)
            assert [str(table.get_row_at(row)[1]) for row in range(3)] == ["4.5", "3.5", "1.5"]
            # How many jobs the whole database holds, four, not the three this search found, on the right of the heading.
            assert str(app.query_one("#count", Static).render()) == "Page 1 · 4 jobs"
            assert not any("database" in line.text for line in app.query_one(RichLog).lines)

    asyncio.run(run())
    config = load_config(home)
    assert config.filters.include == ("role",)
    assert [form.text for form in config.filters.plan.nodes[0].forms] == ["Python Engineer", "Python Developer"]
    assert config.filters.locations == ("Milan", "Milano", "Italy")
    assert config.filters.cities == ("Milan", "Milano")
    assert config.cv.read_text(encoding="utf-8").startswith("# Ada Rossi")
    answers = answers_of(config)
    assert {name: answers[name] for name in ("email", "location")} == {"email": "ada@example.com", "location": "Milan"}
    # The one password for the sites that need an account, made at the first start and kept with the answers.
    assert len(answers["account_password"]) == 20


def test_no_resume_asks_for_one_and_a_wrong_path_asks_again(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, None)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            app.screen.query_one(Input).value = "/no/such/file.pdf"
            await pilot.press("enter")
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            app.screen.query_one(Input).value = str(resume)
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())


def test_no_key_asks_for_one_and_keeps_it_on_this_computer(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            assert app.screen.query_one(Input).password is True
            app.screen.query_one(Input).value = "sk-orca-typed"
            app.session.save_key = lambda key: (type(app.session.config).save_key(app.session.config, key), setattr(app.session, "_client", ScriptedModelForApp()))  # type: ignore[method-assign]
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app)

    asyncio.run(run())
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-typed"


def fake_apply(app: AIHawk, outcomes: list[Outcome], calls: list[Any]) -> None:
    async def apply_to_job(client: Any, model: str, job: Any, cv: str, answers: str, cv_file: Path, data_dir: Path, profile: Path, guidance: str = "", reaches: frozenset[str] = frozenset(), key_env: str = "") -> Outcome:
        calls.append({"job": job, "answers": answers, "guidance": guidance, "cv_file": cv_file, "profile": profile})
        return outcomes.pop(0)

    import aihawk.session as session_module

    session_module.apply_to_job = apply_to_job  # type: ignore[assignment]


@pytest.fixture
def restored_apply() -> Any:
    import aihawk.session as session_module

    original = session_module.apply_to_job
    yield
    session_module.apply_to_job = original


async def wait_for_screen(pilot: Any, kind: type) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        if isinstance(pilot.app.screen, kind):
            return
    raise AssertionError(f"{kind.__name__} never came")


async def wait_for_apply(pilot: Any, app: AIHawk) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.applying():
            return
    raise AssertionError("applying never finished")


def test_space_space_a_then_enter_sends_each_selected_job_once_and_asks_no_question(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent"), Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("space", "space", "a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.1)
            assert not isinstance(app.screen, TextPrompt)
            table = app.query_one(DataTable)
            assert [str(table.get_row_at(index)[0]) for index in range(2)] == ["✓", "✓"]
            assert app.selected == set()

    asyncio.run(run())
    assert [call["job"].company for call in calls] == ["Bestco", "Lowly"]
    assert calls[0]["job"].apply_url == "https://jobs.lever.co/beta/2/apply"
    assert "Upload the CV" in calls[0]["guidance"]
    assert Path(calls[0]["cv_file"]).is_file()


async def send_applications(home: Path, resume: Path, keys: list[str], outcomes: list[Outcome]) -> None:
    """The jobs the keys select, applied to with scripted outcomes."""
    app = scripted_app(home, resume)
    fake_apply(app, outcomes, [])
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press(*keys, "a")
        await wait_for_screen(pilot, SendBatch)
        await pilot.press("enter")
        await wait_for_apply(pilot, app)


def test_an_application_is_sent_to_posthog_with_how_it_went_and_the_tokens_and_no_price(
    home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]]
) -> None:
    asyncio.run(send_applications(home, resume, ["space", "space"], [Outcome("submitted", "sent", steps=12), Outcome("blocked", "a puzzle", reason="captcha", steps=4)]))
    applications = [event for event in tracked if event["event"] == "application"]
    assert [(event["properties"]["outcome"], event["properties"]["reason"], event["properties"]["steps"]) for event in applications] == [
        ("submitted", "", 12), ("blocked", "captcha", 4)]
    sent = applications[0]
    assert sent["api_key"] == telemetry.PROJECT_KEY and sent["properties"]["site"] == "lever" and sent["properties"]["plugin"] is True
    assert sent["properties"]["gateway"] == "api.orcarouter.ai" and sent["properties"]["forms_model"]
    assert {"input_tokens", "output_tokens", "models", "tokens_by_model"} <= set(sent["properties"]) and "cost" not in sent["properties"]
    assert {"app_started", "search", "jobs_scored"} <= {event["event"] for event in tracked}


def test_no_event_carries_anything_about_the_person_or_the_job_and_all_have_one_id(
    home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]]
) -> None:
    outcomes = [Outcome("submitted", "sent"), Outcome("submitted", "sent"), Outcome("submitted", "sent")]
    asyncio.run(send_applications(home, resume, ["space", "space", "space"], outcomes))
    assert len({event["distinct_id"] for event in tracked}) == 1
    text = json.dumps(tracked)
    assert not any(word in text for word in ("Ada", "ada@example.com", "Bestco", "Lowly", "Noplugin", "jobs.lever.co/beta", "nobody.example"))


def test_a_form_on_a_company_host_is_the_site_other_and_its_host_is_in_no_event(
    home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]]
) -> None:
    asyncio.run(send_applications(home, resume, ["down", "down", "space"], [Outcome("submitted", "sent")]))
    (application,) = [event for event in tracked if event["event"] == "application"]
    assert application["properties"]["site"] == "other" and application["properties"]["plugin"] is False
    assert "nobody" not in json.dumps(tracked)


def test_a_dry_run_is_not_counted(home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIHAWK_DRY_RUN", "1")
    asyncio.run(send_applications(home, resume, ["space"], [Outcome("blocked", "Dry run: nothing sent.")]))
    assert tracked == []


def test_escape_on_the_batch_sends_none_and_the_selection_stays(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("space", "a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("escape")
            await wait_for_apply(pilot, app)
            assert app.selected == {"https://jobs.lever.co/beta/2"}, "a job is known by its address"
            assert str(app.query_one(DataTable).get_row_at(0)[0]) == "●"

    asyncio.run(run())
    assert calls == []


def test_on_a_mac_the_start_says_applying_does_not_work_and_a_sends_nothing(
    home: Path, resume: Path, restored_apply: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aihawk.apply as apply_module

    monkeypatch.setattr(apply_module, "PLATFORM", "darwin")
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert "On a Mac AIHawk finds and scores jobs" in " ".join(app.query_one(Said).said)
            assert companies(app)[0] == "Bestco", "the jobs are found and scored all the same"
            await pilot.press("a")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, SendBatch)

    asyncio.run(run())
    assert calls == []


def test_a_with_nothing_selected_asks_about_the_current_row_alone(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            assert len(app.screen.items) == 1
            await pilot.press("enter")
            await wait_for_apply(pilot, app)

    asyncio.run(run())
    assert [call["job"].company for call in calls] == ["Bestco"]


def test_a_job_already_sent_is_not_sent_twice(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            app.query_one(DataTable).move_cursor(row=0)
            await pilot.press("a")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            assert not isinstance(app.screen, SendBatch)

    asyncio.run(run())
    assert len(calls) == 1


def test_the_auto_submit_flag_is_gone() -> None:
    result = CliRunner().invoke(cli_main, ["--auto-submit"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_s_lists_what_was_tried_newest_first_and_enter_opens_one_in_full(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        store = app.session.store
        store.add(Job("sent", "Acme", "Engineer", "Rome", "https://jobs.lever.co/acme/1", "https://jobs.lever.co/acme/1/apply", "d"))
        store.set_status("sent", "submitted", "Filled in: the CV")
        store.set_proof("sent", Proof("2026-11-03T09:00:00", "https://jobs.lever.co/acme/thanks", "submitted", "Thank you", "the whole page", "/shots/sent.png"))
        store.set_cost("sent", 0.04)
        store.add(Job("stuck", "Beta", "Designer", "Rome", "https://jobs.lever.co/beta/2", "https://jobs.lever.co/beta/2/apply", "d"))
        store.set_status("stuck", "blocked", "I could not get past it.", "captcha")
        store.set_proof("stuck", Proof())
        # A job with no click is ordered by when its status changed: an older change comes after the send.
        store._db.execute("UPDATE jobs SET updated = ? WHERE id = ?", ("2026-11-01T12:00:00", "stuck"))
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("s")
            await wait_for_screen(pilot, SentList)
            table = app.screen.query_one(DataTable)
            rows = [[str(table.get_row_at(index)[column]) for column in (1, 3)] for index in range(table.row_count)]
            assert rows == [["Acme", "✓ sent and confirmed"], ["Beta", "✗ a captcha stopped it"]]
            await pilot.press("enter")
            await wait_for_screen(pilot, SentDetails)
            assert app.screen.item.job.id == "sent"
            # How it went in a person's words: what the page said, and not the form's fields or the page's files.
            text = str(app.screen.query_one("#summary Static").render())
            assert "Sent and confirmed · 2026-11-03 09:00" in text and "The page said: Thank you" in text and "Cost: 4 cents" in text
            assert "Filled in" not in text and "shots" not in text
            await pilot.press("escape")
            await pilot.press("escape")

    asyncio.run(run())


def logged(app: AIHawk) -> str:
    return "\n".join(strip.text for strip in app.query_one(RichLog).lines)


def test_s_with_nothing_tried_says_how_to_send(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("s")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, SentList)
            assert "No applications yet" in logged(app)

    asyncio.run(run())


def test_each_send_says_how_it_went_and_where_to_read_more(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "Filled.", proof=Proof("2026-11-05T10:00:00+00:00", "u", "submitted", "The page says thank you.", "t", ""))], calls)
        async with app.run_test(size=(200, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            log = logged(app)
            assert "Bestco: sent and confirmed. The page says thank you." in log

    asyncio.run(run())


def test_at_80_columns_the_role_is_the_last_column_and_at_100_the_location_is(home: Path, resume: Path) -> None:
    async def run() -> None:
        for width, labels in ((80, 5), (100, 6)):  # the columns before the status column was dropped; one fewer now
            app = scripted_app(home, resume)
            async with app.run_test(size=(width, 24)) as pilot:
                await settle(pilot, app)
                table = app.query_one(DataTable)
                assert len(table.ordered_columns) == labels - 1 and str(table.ordered_columns[-1].label) == ("Location" if width >= 100 else "Role")
                assert sum(column.width + 2 for column in table.ordered_columns) <= width - 2

    asyncio.run(run())


def test_enter_selects_a_job_as_space_does_and_a_second_one_unselects_it(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("enter", "space")
            assert len(app.selected) == 2 and not isinstance(app.screen, JobDetails)
            await pilot.press("up", "enter")
            table = app.query_one(DataTable)
            assert [str(table.get_row_at(index)[0]) for index in range(2)] == ["●", ""]

    asyncio.run(run())


def test_a_jobs_details_can_select_it_and_the_row_shows_the_mark(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("i")
            await wait_for_screen(pilot, JobDetails)
            await pilot.press("space")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, JobDetails) and len(app.selected) == 1
            assert str(app.query_one(DataTable).get_row_at(0)[0]) == "●"

    asyncio.run(run())


def test_i_shows_the_job_as_its_posting_says_it_and_nothing_else(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("i")
            await wait_for_screen(pilot, JobDetails)
            # The role, the company, where, and the posting's text: no score, no address.
            text = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
            item = app.current()
            assert item and item.job.title in text and "Bestco" in text and item.job.location in text
            assert "Score" not in text and "https://" not in text
            await pilot.press("escape")

    asyncio.run(run())


def test_a_second_start_goes_straight_to_the_list_without_asking(home: Path, resume: Path) -> None:
    async def run(with_resume: Path | None) -> list[int]:
        app = scripted_app(home, with_resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert not isinstance(app.screen, TextPrompt)
            assert companies(app)[0] == "Bestco"
            return [len(app.session.client.requests)]

    asyncio.run(run(resume))
    assert asyncio.run(run(None)) == [0]


def test_a_start_says_each_step_and_closing_says_where_it_was_and_whether_it_searched(
    home: Path, resume: Path, tracked: list[dict[str, Any]]
) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)

    asyncio.run(run())
    steps = [event["properties"]["step"] for event in tracked if event["event"] == "start_step"]
    # The first job is on screen once, and the jobs are all here once; which comes first depends on how fast the chunks come.
    assert steps[:2] == ["resume_read", "jobs_downloading"] and sorted(steps[2:]) == ["first_jobs", "jobs_ready"]
    (closed,) = [event for event in tracked if event["event"] == "app_closed"]
    assert closed["properties"]["searched"] is True and closed["properties"]["last_step"] == steps[-1]
    assert all(isinstance(event["properties"]["seconds"], float) for event in tracked if event["properties"].get("step") == "first_jobs")


def test_a_resume_that_cannot_be_read_is_one_event_with_its_kind_and_not_two(home: Path, tmp_path: Path, tracked: list[dict[str, Any]]) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("too short", encoding="utf-8")

    async def run() -> None:
        app = scripted_app(home, empty)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)

    asyncio.run(run())
    failures = [event["properties"] for event in tracked if event["event"] == "start_step"]
    assert [(step["step"], step["error"], step["kind"]) for step in failures] == [("resume_failed", "ConfigError", ".txt")]


@pytest.mark.parametrize("name", ["cv.txt", "cv.md"])
def test_a_text_resume_is_read(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text(CV_TEXT, encoding="utf-8")
    assert read_resume(path).startswith("# Ada")


def test_a_resume_and_the_files_beside_it_saved_with_a_byte_order_mark_are_read(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows editors write a byte-order mark at the start of a text file: it is not part of what the file says.
    bom = b"\xef\xbb\xbf"
    path = tmp_path / "cv.txt"
    path.write_bytes(bom + CV_TEXT.encode("utf-8"))
    assert read_resume(path).startswith("# Ada")
    (home / "answers.yaml").write_bytes(bom + b"email: ada@example.com\n")
    (home / "aihawk.yaml").write_bytes(bom + f"database: {published(tmp_path)}\n".encode())
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_bytes(bom + b"sk-orca-typed\n")
    config = load_config(home)
    store = Store(config.store_file)
    import_answers_file(config, store)
    assert store.answers()["email"] == "ada@example.com"
    store.close()
    assert config.api_key() == "sk-orca-typed"


def test_a_pdf_with_no_text_says_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    with path.open("wb") as out:
        writer.write(out)
    with pytest.raises(ConfigError, match="looks like a scan"):
        read_resume(path)


def test_a_word_resume_is_read(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "cv.docx"
    words = "Senior Python Engineer at Acme for five years, on backend services and data."
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Ada Rossi</w:t></w:r></w:p><w:p><w:r><w:t>{words}</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    assert read_resume(path) == f"Ada Rossi\n{words}"


def test_an_old_word_file_says_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "cv.doc"
    path.write_bytes(b"old")
    with pytest.raises(ConfigError, match="PDF or .docx"):
        read_resume(path)


def test_a_new_resume_never_invents_a_contact_detail(home: Path, resume: Path) -> None:
    config = load_config(home)
    asyncio.run(install_resume(ScriptedModelForApp(), config, resume, Store(config.store_file)))  # type: ignore[arg-type]
    assert "phone" not in answers_of(config)


def test_a_new_resume_replaces_the_plan_and_keeps_what_the_person_excluded(home: Path, resume: Path) -> None:
    (home / "aihawk.yaml").write_text("filters:\n  exclude: [intern]\n", encoding="utf-8")
    asyncio.run(install_resume(ScriptedModelForApp(), load_config(home), resume, Store(load_config(home).store_file)))  # type: ignore[arg-type]
    config = load_config(home)
    assert config.filters.exclude == ("intern",)
    assert config.filters.include == tuple(node.id for node in config.filters.plan.nodes)


def test_the_mail_plugins_join_every_form_after_the_sites_own_and_the_profile_is_the_workspaces(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("blocked", "closed")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_apply(pilot, app)

    asyncio.run(run())
    guidance = calls[0]["guidance"]
    assert guidance.index("The form is one page") < guidance.index("mail.google.com") < guidance.index("outlook.live.com")
    assert calls[0]["profile"] == home / "browser-profile"


class ScriptedServer(ScriptedBrowser):
    """The browser server as the tests see it: every call is recorded, and there are no tools to list."""

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[])


def scripted_server(monkeypatch: pytest.MonkeyPatch, server: ScriptedServer) -> None:
    @asynccontextmanager
    async def started(upload_dir: Path, home: Path, key_env: str = "") -> AsyncIterator[tuple[ScriptedServer, str]]:
        yield server, ""

    monkeypatch.setattr(apply_module, "_server", started)


async def wait_for_sign_in_to_close(pilot: Any, app: AIHawk) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.signing_in:
            return
    raise AssertionError("the sign-in browser never closed")




REQUEST = httpx.Request("POST", "https://api.orcarouter.ai/v1/chat/completions")


def status_error(kind: type, code: int, body: Any = None) -> Exception:
    return kind(f"Error code: {code}", response=httpx.Response(code, request=REQUEST), body=body)


class RefusingModel:
    """Every call is refused for its key, as OrcaRouter refuses a wrong one."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **request: Any) -> Any:
        raise status_error(AuthenticationError, 401)


def log_text(app: AIHawk) -> str:
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


def test_a_key_from_the_file_that_orcarouter_refuses_is_forgotten_and_asked_for_again(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_text("sk-orca-wrong", encoding="utf-8")

    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = RefusingModel()  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            assert "forgot it" in log_text(app)
            # And said in the question, which covers the log.
            assert "forgot it" in str(app.screen.query_one("#problem", Static).content) and app.screen.query_one("#problem").display
            assert "sk-orca-wrong" not in log_text(app)
            await pilot.press("escape")

    asyncio.run(run())
    assert not (home / "key").exists()


def test_a_key_from_the_environment_that_orcarouter_refuses_is_named_and_not_asked_for(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = RefusingModel()  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert not isinstance(app.screen, TextPrompt)
            assert "ORCAROUTER_API_KEY variable" in log_text(app)
            assert "test-key" not in log_text(app)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (APIConnectionError(request=REQUEST), "Cannot reach the AI service"),
        (APITimeoutError(request=REQUEST), "did not answer in time"),
        (status_error(RateLimitError, 429), "is busy"),
        (status_error(InternalServerError, 503), "problem on its side"),
    ],
)
def test_a_failed_model_call_says_in_plain_words_what_happened_and_what_to_do(error: Exception, words: str) -> None:
    text = explain(error)
    assert words in text
    # The error's own text is for the makers: no code, no brackets, no number.
    assert str(error) not in text and "(" not in text


def test_ctrl_c_at_the_key_prompt_leaves(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            assert "Enter saves" in str(app.screen.query_one(".hint", Static).content)
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert not app.is_running

    asyncio.run(run())


def test_leaving_while_a_prompt_is_open_raises_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)

    asyncio.run(run())


def test_at_80_columns_a_message_wraps_whole_and_quit_is_on_the_footer(home: Path, tmp_path: Path) -> None:
    scan = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    with scan.open("wb") as out:
        writer.write(out)

    async def run() -> None:
        app = scripted_app(home, scan)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.3)  # settle waits for the flow, not for the first paint of the footer
            assert "Save it as a PDF with text, or as .docx." in " ".join(line.text.strip() for line in app.query_one(RichLog).lines)
            rows = ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]
            assert any("q Quit" in row for row in rows)

    asyncio.run(run())


def test_a_posting_whose_words_look_like_markup_is_shown_as_it_is(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        app.session.store.add(Job("odd", "Co [/y]", "[/x] Engineer", "Milan [bold", "https://a.example/", "https://a.example/", ""))
        app.start = lambda: None  # type: ignore[method-assign]
        async with app.run_test(size=(140, 40)) as pilot:
            app.pages = [["odd"]]
            app.refresh_jobs()
            await pilot.pause()
            row = app.query_one(DataTable).get_row_at(0)
            assert (str(row[2]), str(row[3]), str(row[4])) == ("Co [/y]", "[/x] Engineer", "Milan [bold")

    asyncio.run(run())


def test_a_page_shows_whole_and_sorted_and_a_later_score_never_moves_it(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        store = app.session.store
        for n in range(4):
            store.add(Job(f"j{n}", f"Firm {n}", "Engineer", "Milan", f"https://a.example/{n}", f"https://a.example/{n}", "d"))
        for job_id, score in (("j0", 2.0), ("j1", 4.0), ("j2", 3.0)):
            store.set_score(job_id, score)
        app.start = lambda: None  # type: ignore[method-assign]
        app.keep_ahead = lambda: None  # type: ignore[method-assign]
        async with app.run_test(size=(140, 40)) as pilot:
            app.pages = [["j1", "j2", "j0"]]
            app.refresh_jobs()
            await pilot.pause()
            assert companies(app) == ["Firm 1", "Firm 2", "Firm 0"]
            await pilot.press("down")
            # A job scored later is not this page's: the page, and the cursor on it, stay as they are.
            store.set_score("j3", 5.0)
            app.refresh_jobs()
            await pilot.pause()
            assert companies(app) == ["Firm 1", "Firm 2", "Firm 0"] and app.current_id() == "j2"

    asyncio.run(run())


def test_home_and_end_take_the_cursor_and_a_new_width_refits_the_columns_and_the_log(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        for n in range(80):
            app.session.store.add(Job(f"j{n}", f"Firm {n}", "Engineer", "Milan", f"https://a.example/{n}", f"https://a.example/{n}", "d"))
        app.start = lambda: None  # type: ignore[method-assign]
        app.keep_ahead = lambda: None  # type: ignore[method-assign]
        async with app.run_test(size=(140, 30)) as pilot:
            app.pages = [[f"j{n}" for n in range(80)]]
            app.refresh_jobs()
            app.say("word " * 40)
            await pilot.pause()
            table = app.query_one(DataTable)
            # DataTable's own End scrolled the view and left the cursor on the first row, out of sight.
            await pilot.press("end")
            await pilot.pause()
            assert table.cursor_row == 79
            await pilot.press("home")
            await pilot.pause()
            assert table.cursor_row == 0
            log = app.query_one(RichLog)
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            # Cut for the screen it now has, not the one before; the log wrapped again, nothing past its edge.
            assert table.virtual_size.width <= table.size.width
            assert all(len(line.text) <= log.scrollable_content_region.width for line in log.lines)
            await pilot.resize_terminal(140, 30)
            await pilot.pause()
            assert len(table.columns) == 5 and table.virtual_size.width <= table.size.width

    asyncio.run(run())


def test_a_posting_with_no_description_is_scored_on_its_title_and_no_page_is_read(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.store.add(Job("bare", "Firm", "Python Engineer", "Milan", "https://a.example/1", "https://a.example/1", "", 1.0))
    session.store.add(Job("told", "Firm", "Python Engineer", "Milan", "https://a.example/2", "https://a.example/2", "Stored text.", 1.0))

    def no_page(*_: Any, **__: Any) -> None:
        raise AssertionError("a page was read from the person's computer")

    monkeypatch.setattr("urllib.request.urlopen", no_page)
    # The descriptions come from the jobs on this computer, by address.
    monkeypatch.setattr(session.library, "descriptions", lambda urls: ["Stored text." if url.endswith("/2") else "" for url in urls])
    asyncio.run(session.read(lambda _: None))
    assert session.store.get("bare").score is not None and session.store.get("bare").job.description == ""  # type: ignore[union-attr]
    asked = json.dumps(session._client.posts)  # type: ignore[union-attr]
    assert "Stored text." in asked
    session.close()


def test_settings_change_the_search_from_the_screen_and_search_again(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            rows = [key.value for key in app.screen.query_one(DataTable).rows]
            app.screen.query_one(DataTable).move_cursor(row=rows.index("exclude"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "Senior, Intern"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")
            await settle(pilot, app)
            assert app.session.config.filters.exclude == ("Senior", "Intern")
            assert "Searching again" in logged(app)

    asyncio.run(run())


def test_a_setting_that_would_not_load_is_refused_and_the_file_stays_as_it_was(home: Path) -> None:
    session = Session(home)
    before = (home / "aihawk.yaml").read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="max_age_days"):
        session.change_setting("max_age_days", "soon")
    assert (home / "aihawk.yaml").read_text(encoding="utf-8") == before
    session.close()


def test_places_replace_the_ones_read_from_the_cv_and_a_model_clears_back_to_its_default(home: Path, resume: Path) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.change_setting("places", "London, Leeds")
    filters = session.config.filters
    assert (filters.locations, filters.cities, filters.countries) == (("London", "Leeds"), ("London", "Leeds"), ())
    default = session.config.model
    session.change_setting("model", "some/model")
    assert session.config.model == "some/model"
    session.change_setting("model", "")
    assert session.config.model == default
    session.close()


def test_the_persons_own_cv_goes_with_the_form_and_a_new_resume_replaces_it(home: Path, resume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.store.add(Job("one", "Firm", "Python Engineer", "Milan", "https://jobs.lever.co/a/1", "https://jobs.lever.co/a/1", "A posting.", 1.0))
    files: list[str] = []

    async def fill(_client: Any, _model: str, _job: Any, _cv: str, _answers: str, cv_file: Path, *_rest: Any, **_options: Any) -> Outcome:
        files.append(cv_file.read_text(encoding="utf-8"))
        return Outcome("blocked", "a puzzle")

    monkeypatch.setattr(session_module, "apply_to_job", fill)
    asyncio.run(session.apply(session.store.get("one"), lambda _: None))  # type: ignore[arg-type]
    newer = tmp_path / "newer.txt"
    newer.write_text(CV_TEXT + "A later role at Beta.", encoding="utf-8")
    asyncio.run(session.load_resume(newer))
    asyncio.run(session.apply(session.store.get("one"), lambda _: None))  # type: ignore[arg-type]
    assert files == [CV_TEXT, CV_TEXT + "A later role at Beta."]
    session.close()


def test_a_site_with_no_plugin_is_tried_like_any_other(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    url = "https://careers.nobody.example/job/1"
    session.store.add(Job("any", "Firm", "Python Engineer", "Milan", url, url, "A posting.", 1.0))
    asked: list[Any] = []

    async def fill(_client: Any, _model: str, job: Any, *_rest: Any, **_options: Any) -> Outcome:
        asked.append(job.apply_url)
        return Outcome("blocked", "a sign-in")

    monkeypatch.setattr(session_module, "apply_to_job", fill)
    outcome = asyncio.run(session.apply(session.store.get("any"), lambda _: None))  # type: ignore[arg-type]
    assert asked == [url] and outcome.status == "blocked"
    session.close()


NO_MAIL = Outcome("blocked", "the mailbox is not signed in", reason="mailbox_signed_out")


async def apply_to_the_first_job(pilot: Any, app: AIHawk, outcomes: list[Outcome], calls: list[Any] | None = None) -> None:
    """The first job selected and sent with scripted outcomes, up to the first question the sending asks."""
    fake_apply(app, outcomes, [] if calls is None else calls)
    await settle(pilot, app)
    await pilot.press("space", "a")
    await wait_for_screen(pilot, SendBatch)
    await pilot.press("enter")
    await wait_for_screen(pilot, TextPrompt)


def steps_sent(tracked: list[dict[str, Any]]) -> list[str]:
    return [event["properties"]["step"] for event in tracked if event["event"] == "start_step"]


def test_the_mail_is_not_asked_at_the_start_and_is_asked_at_the_first_application_that_needs_a_code_and_that_one_is_sent_again(
    home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    server = ScriptedServer(page={"url": "https://mail.google.com/mail/u/0/#inbox", "text": "Inbox"})
    scripted_server(monkeypatch, server)
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            # The jobs are on the screen and nothing was asked about the mail.
            assert not isinstance(app.screen, TextPrompt) and companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert not {"mail_asked", "mail_ready", "mail_later"} & set(steps_sent(tracked))
            await apply_to_the_first_job(pilot, app, [NO_MAIL, Outcome("submitted", "sent")], calls)
            assert app.screen.question == "A site sent a code to your email. Which email is it, so AIHawk can read it?"
            assert app.screen.query_one(Input).value == "ada@example.com"
            app.screen.query_one(Input).value = "ada@gmail.com"
            await pilot.press("enter")
            await wait_for_screen(pilot, SignIn)
            assert "The browser opened Gmail" in app.screen.text and "ada@gmail.com" in app.screen.text
            assert ("browser_navigate", {"url": "https://mail.google.com/mail/u/0/"}) in server.calls
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            assert str(app.query_one(DataTable).get_row_at(0)[0]) == "✓"

    asyncio.run(run())
    assert server.calls[-1] == ("browser_close", {})
    assert [call["job"].company for call in calls] == ["Bestco", "Bestco"]
    answers = answers_of(load_config(home))
    assert answers["email"] == "ada@gmail.com" and answers["mail_provider"] == "gmail"
    assert [step for step in steps_sent(tracked) if step.startswith("mail")] == ["mail_asked", "mail_ready"]


def test_the_mail_left_for_later_is_not_asked_again_by_the_next_application_of_the_same_run(
    home: Path, resume: Path, restored_apply: Any, tracked: list[dict[str, Any]]
) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await apply_to_the_first_job(pilot, app, [NO_MAIL, NO_MAIL], calls)
            await pilot.press("escape")
            await wait_for_apply(pilot, app)
            assert "Mail left for later: e, then Your mail." in app.query_one(Said).said
            assert app.session.mail()[1] == "later" and len(calls) == 1
            # The next application that needs a code says so and goes on: there is no second question in this run.
            await pilot.press("space", "a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            assert not isinstance(app.screen, TextPrompt) and len(calls) == 2
            assert app.query_one(Said).said.count("Mail left for later: e, then Your mail.") == 2

    asyncio.run(run())
    assert [step for step in steps_sent(tracked) if step.startswith("mail")] == ["mail_asked", "mail_later"]


def test_the_mail_question_is_plain_and_an_address_that_is_neither_gmail_nor_outlook_is_asked_once_then_left(
    home: Path, resume: Path, restored_apply: Any
) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await apply_to_the_first_job(pilot, app, [NO_MAIL])
            assert app.screen.question == "A site sent a code to your email. Which email is it, so AIHawk can read it?"
            assert str(app.screen.query_one(".hint", Static).content) == "AIHawk opens that inbox in its browser only to read those codes · Enter · Esc later"
            app.screen.query_one(Input).value = "ada@yahoo.com"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.screen.question == "Where is ada@yahoo.com?"
            app.screen.query_one(Input).value = "yahoo"
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            # Said once, and left for later: it is not asked again.
            assert not isinstance(app.screen, TextPrompt)
            assert app.query_one(Said).said.count("Only Gmail and Outlook can be read for now; the rest works without.") == 1
            assert app.session.mail()[1] == "later"

    asyncio.run(run())


def test_a_mailbox_that_still_shows_its_sign_in_page_is_a_question_with_no_empty_input(
    home: Path, resume: Path, restored_apply: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    server = ScriptedServer(page={"url": "https://accounts.google.com/signin", "text": "Sign in"})

    async def stuck(name: str, arguments: dict[str, Any] | None = None) -> Any:
        server.calls.append((name, arguments or {}))
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    server.call_tool = stuck  # type: ignore[method-assign]
    scripted_server(monkeypatch, server)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await apply_to_the_first_job(pilot, app, [NO_MAIL])
            app.screen.query_one(Input).value = "ada@gmail.com"
            await pilot.press("enter")
            await wait_for_screen(pilot, SignIn)
            assert app.screen.text == "The browser opened Gmail. Sign in as ada@gmail.com, then come back to this window and press Enter."
            await pilot.press("enter")
            await wait_for_screen(pilot, Confirm)
            assert app.screen.question == "Gmail still shows its sign-in page."
            assert not list(app.screen.query(Input))
            await pilot.press("escape")
            await wait_for_sign_in_to_close(pilot, app)
            await wait_for_apply(pilot, app)
            assert "Mail left for later: e, then Your mail." in app.query_one(Said).said

    asyncio.run(run())


def test_escape_on_the_sign_in_is_later_not_done(home: Path, resume: Path, restored_apply: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    # The inbox would show if it were looked at: Escape must not count as having signed in.
    server = ScriptedServer(page={"url": "https://mail.google.com/mail/u/0/#inbox", "text": "Inbox"})
    scripted_server(monkeypatch, server)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await apply_to_the_first_job(pilot, app, [NO_MAIL])
            app.screen.query_one(Input).value = "ada@gmail.com"
            await pilot.press("enter")
            await wait_for_screen(pilot, SignIn)
            await pilot.press("escape")
            await wait_for_sign_in_to_close(pilot, app)
            await wait_for_apply(pilot, app)
            assert "Signed in to Gmail." not in app.query_one(Said).said
            assert "Mail left for later: e, then Your mail." in app.query_one(Said).said

    asyncio.run(run())


def test_jev_scores_one_page_ahead_and_only_the_side_arrows_turn_the_pages(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A page of one job, and Jev reading two at a time: the three postings make three pages.
    import aihawk.session as session_module

    monkeypatch.setattr(session_module, "WIDE", 2)
    monkeypatch.setattr(tui_module, "PAGE", 1)
    monkeypatch.setattr(tui_module, "AHEAD", 1)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            # The page shown and the one after it, and one scored job in reserve: the second round was read ahead.
            assert len(app.pages) == 2 and len(app.session.store.with_status("scored")) == 3
            assert app.query_one(DataTable).row_count == 1 and logged(app).splitlines()[-1] == "Page 2 ready."
            assert not any(line.startswith("Preparing page") for line in logged(app).splitlines()), "the count gave way to the result"
            # Down and up stay on the page: its last row and its first are where it ends.
            await pilot.press("down", "up")
            await settle(pilot, app)
            assert app.page == 0
            # The right arrow is the next page, and it asks for the one after.
            await pilot.press("right")
            await settle(pilot, app)
            assert app.page == 1 and len(app.pages) == 3 and len(app.session.store.with_status("scored")) == 3
            assert str(app.query_one("#count", Static).render()) == "Page 2 · 4 jobs"
            await pilot.press("right", "right")
            await settle(pilot, app)
            assert app.page == 2 and "No more jobs for this search." in logged(app)
            await pilot.press("left")
            assert app.page == 1 and app.query_one(DataTable).cursor_row == 0
            # The footer says which keys turn the pages.
            rows = ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]
            assert any("← → Page" in row for row in rows) and not any(" More" in row for row in rows)

    asyncio.run(run())


def test_the_right_arrow_says_so_when_there_is_nothing_left_to_read(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("right")
            await settle(pilot, app)
            assert "No more jobs for this search." in logged(app)

    asyncio.run(run())


def test_a_postings_html_reads_as_paragraphs_and_bullets_even_when_escaped() -> None:
    from aihawk.screens import readable

    html_text = "<h2>About us</h2><p>We build <strong>tools</strong>.</p><ul><li>Python</li><li>Go &amp; Rust</li></ul><script>x()</script>"
    expected = "About us\n\nWe build tools.\n\n• Python\n• Go & Rust"
    assert readable(html_text) == expected
    # Some sources escape their HTML, once or twice over.
    assert readable(html_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) == expected
    assert readable("Plain text,   with  spaces.\n\n\n\nAnd a second paragraph.") == "Plain text, with spaces.\n\nAnd a second paragraph."


class Flaky(ScriptedModelForApp):
    """The scoring requests lose their connection `failures` times, then answer."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
        self.attempts += 1
        if self.failures:
            self.failures -= 1
            raise APIConnectionError(request=REQUEST)
        return await super().post(path, body=body, cast_to=cast_to)


def last_line(app: AIHawk) -> str:
    """The last thing the log was told, whole: what it shows is wrapped to its width."""
    return app.query_one(Said).said[-1]


def footer_text(app: AIHawk) -> str:
    return " ".join("".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips())


def test_a_round_the_service_does_not_answer_is_asked_again_and_no_job_is_set_aside(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tui_module, "RETRY_PAUSES", (0.01, 0.01, 0.01, 0.01))

    async def run() -> None:
        app = AIHawk(home, resume)
        model = Flaky(2)
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert model.attempts == 3
            assert not app.session.store.with_status("passed_over")
            assert "could not be read" not in logged(app) and "not answering" not in logged(app)

    asyncio.run(run())


def test_after_four_pauses_it_stops_says_how_to_ask_again_and_the_right_arrow_asks(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tui_module, "RETRY_PAUSES", (0.01, 0.01, 0.01, 0.01))

    async def run() -> None:
        app = AIHawk(home, resume)
        model = Flaky(100)
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert model.attempts == 5
            assert last_line(app) == "Still no answer. Check your connection, then press → to try again."
            # The jobs wait as they were, and nothing says there are no more.
            assert len(app.session.store.with_status("new")) == 3 and not app.session.store.with_status("passed_over")
            assert "No more jobs" not in logged(app) and "No jobs found" not in logged(app)
            model.failures = 0
            await pilot.press("right")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())


def test_a_key_the_service_refuses_while_scoring_still_asks_for_another(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = RefusingModel()  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert "refused the key" in log_text(app) and not app.session.store.with_status("passed_over")

    asyncio.run(run())


def test_a_search_that_finds_nothing_says_so_and_what_to_change(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        # Every posting is a Python one: leaving Python out leaves none.
        app.session.change_setting("exclude", "Python")
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert companies(app) == []
            assert last_line(app) == "No jobs found for these places and days. Press e to change them."
            assert "Preparing" not in logged(app)
            await pilot.press("right")
            await pilot.pause(0.1)
            assert "No more jobs" not in logged(app)

    asyncio.run(run())


def test_nothing_is_said_about_no_jobs_while_the_jobs_are_still_coming(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        app.session.change_setting("exclude", "Python")
        hold = asyncio.Event()

        async def coming(progress: Any, arrived: Any) -> None:
            await hold.wait()

        app.session.sync = coming  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.5)
            assert "No jobs found" not in logged(app)
            await pilot.press("right")
            await pilot.pause(0.2)
            assert "No jobs found" not in logged(app) and "No more jobs" not in logged(app)
            hold.set()

    asyncio.run(run())


def test_with_no_key_the_line_says_the_next_key_and_it_works(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("escape")
            await settle(pilot, app)
            assert last_line(app) == "No key yet. Press e, then OrcaRouter key."
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            assert ("key", "OrcaRouter key", "none yet") in app.setting_rows()

    asyncio.run(run())


def test_with_no_resume_the_line_says_r_the_footer_shows_it_and_esc_on_it_says_so(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            await pilot.press("escape")
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert last_line(app) == "No resume yet. Press r and give the file."
            assert "r Resume" in footer_text(app) and "q Quit" in footer_text(app)
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert last_line(app) == "No resume given."
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = str(resume)
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert "r Resume" not in footer_text(app)
            # r still works with a resume, and leaving it says nothing changed.
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert last_line(app) == "Your resume was not changed."

    asyncio.run(run())


def test_a_resume_that_cannot_be_read_says_to_press_r(home: Path, tmp_path: Path) -> None:
    old = tmp_path / "cv.doc"
    old.write_bytes(b"old")

    async def run() -> None:
        app = scripted_app(home, old)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert last_line(app).endswith("Press r and give the file again.")

    asyncio.run(run())


def test_a_new_search_lists_its_own_jobs_and_keeps_what_was_sent(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            app.session.store.set_status("https://jobs.lever.co/beta/2", "submitted", "sent")
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            rows = [key.value for key in app.screen.query_one(DataTable).rows]
            app.screen.query_one(DataTable).move_cursor(row=rows.index("exclude"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "Senior"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")
            await settle(pilot, app)
            await pilot.pause(0.2)
            # The jobs nobody applied to were dropped and the new search found its own again; the one sent stays, as sent, though
            # the new search would leave it out.
            assert sorted(item.job.company for item in app.session.store.all()) == ["Bestco", "Lowly", "Noplugin"]
            assert app.session.store.get("https://jobs.lever.co/beta/2").status == "submitted"  # type: ignore[union-attr]

    asyncio.run(run())


def test_leaving_a_word_out_drops_the_listed_jobs_that_have_it(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            rows = [key.value for key in app.screen.query_one(DataTable).rows]
            app.screen.query_one(DataTable).move_cursor(row=rows.index("exclude"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "Python"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert companies(app) == [] and app.session.store.all() == []
            assert last_line(app) == "No jobs found for these places and days. Press e to change them."

    asyncio.run(run())


def test_the_answers_the_person_sees_and_counts_leave_out_aihawks_own(home: Path) -> None:
    app = AIHawk(home)
    store = app.session.store
    store.set_answer("account_password", "G-secret")
    store.set_answer("phone", "123")
    store.set_answer("mail_provider", "gmail")
    assert [row[0] for row in app.answer_rows()] == ["answer:phone", "answer:"]
    assert {key: now for key, _, now in app.setting_rows()}["answers"] == "1"
    assert "G-secret" not in str(app.answer_rows())


def test_every_reason_a_form_stops_for_has_plain_words_and_an_unknown_one_does_not_blame_the_service() -> None:
    from aihawk.apply import BLOCKED_BY, WHY_NOT_SENT, why_not_sent

    for reason in (*BLOCKED_BY, "stuck", "no_code", "mailbox_signed_out", "never_clicked", "form_not_loaded", "refused_by_form", "browser_did_not_open", "browser_not_ready"):
        assert reason in WHY_NOT_SENT, reason
    assert why_not_sent("captcha") == "a captcha stopped it"
    assert why_not_sent("missing_info") == "a required answer is missing: press e, Your answers"
    assert why_not_sent("APIConnectionError") == "the AI service gave an error"
    assert why_not_sent("KeyError") == "something went wrong"
    assert why_not_sent("") == ""


def test_a_time_the_store_wrote_in_utc_is_shown_on_the_persons_own_clock() -> None:
    from datetime import datetime

    from aihawk.screens import when_short

    moment = datetime.fromisoformat("2026-11-05T10:00:00+00:00").astimezone()
    assert when_short("2026-11-05T10:00:00+00:00") == moment.strftime("%Y-%m-%d %H:%M")
    assert when_short("2026-11-05T10:00:00") == "2026-11-05 10:00"


def test_a_store_from_before_reasons_were_kept_gets_the_column(tmp_path: Path) -> None:
    import sqlite3

    old = sqlite3.connect(tmp_path / "jobs.db")
    old.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL, url TEXT NOT NULL, apply_url TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', score REAL, lexical REAL NOT NULL DEFAULT 0, note TEXT, sent_at TEXT, final_url TEXT, verdict TEXT, verdict_evidence TEXT, page_text TEXT, screenshot TEXT, first_seen TEXT NOT NULL, updated TEXT NOT NULL, cost REAL)")
    old.commit()
    old.close()
    store = Store(tmp_path / "jobs.db")
    store.add(Job("a", "Firm", "Engineer", "Rome", "https://a.example/1", "https://a.example/1", ""))
    store.set_status("a", "blocked", "stuck on a captcha", "captcha")
    assert [item.reason for item in store.sent()] == ["captcha"]


async def send_batch(app: AIHawk, pilot: Any, presses: list[str]) -> None:
    await settle(pilot, app)
    await pilot.press(*presses, "a")
    await wait_for_screen(pilot, SendBatch)


def test_the_batch_confirmation_says_what_it_will_do_and_the_keys_are_in_the_footer(home: Path, resume: Path, restored_apply: Any) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [], [])
        async with app.run_test(size=(80, 24)) as pilot:
            await send_batch(app, pilot, ["space"])
            hint = str(app.screen.query_one(".hint", Static).content)
            assert hint.startswith("AIHawk fills each company's form in the browser with your resume and your answers (e shows them), and sends it in your name.")
            assert "Enter" not in hint and "Esc" not in hint
            assert "Send" in footer_text(app) and "Cancel" in footer_text(app)
            await pilot.press("escape")

    asyncio.run(run())


def test_a_batch_says_which_one_it_is_on_in_one_line_and_ends_with_one_summary_and_a_sign_for_each(home: Path, resume: Path, restored_apply: Any) -> None:
    outcomes = [
        Outcome("submitted", "ok", Proof("2026-11-05T10:00:00+00:00", "u", "submitted", "Thank you.", "t", "")),
        Outcome("unconfirmed", "clicked", Proof("2026-11-05T10:01:00+00:00", "u", "", "The page did not change.", "t", "")),
        Outcome("blocked", "I could not get past it.", reason="captcha"),
    ]

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, outcomes, [])
        async with app.run_test(size=(140, 40)) as pilot:
            await send_batch(app, pilot, ["space", "space", "space"])
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            said = app.query_one(Said).said
            assert said[-1] == "1 sent, 1 sent but not confirmed, 1 not sent. Press s to see why."
            assert "Bestco: sent and confirmed. Thank you." in said
            assert "Lowly: sent and confirmed." not in said
            assert "Noplugin: not sent, a captcha stopped it." in said
            assert not any("under a cent" in line for line in said)
            # The sign of each: sent, sent but not confirmed, not sent.
            table = app.query_one(DataTable)
            assert [str(table.get_row_at(index)[0]) for index in range(3)] == ["✓", "?", "✗"]

    asyncio.run(run())


def test_while_a_batch_is_sent_one_line_names_the_job_it_is_on_and_changes_in_place(home: Path, resume: Path, restored_apply: Any) -> None:
    first_gate, second_gate = asyncio.Event(), asyncio.Event()
    gates = [first_gate, second_gate]

    async def run() -> None:
        app = scripted_app(home, resume)

        async def slow(*arguments: Any, **keywords: Any) -> Outcome:
            await gates.pop(0).wait()
            return Outcome("submitted", "ok")

        import aihawk.session as session_module

        session_module.apply_to_job = slow  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await send_batch(app, pilot, ["space", "space"])
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert last_line(app) == "Sending 1 of 2: Bestco. A few minutes each; you can watch the browser."
            first = len(app.query_one(Said).said)
            await pilot.pause(0.1)
            assert len(app.query_one(Said).said) == first
            first_gate.set()
            await pilot.pause(0.3)
            assert last_line(app) == "Sending 2 of 2: Lowly. A few minutes each; you can watch the browser."
            # The first one's result is a line of its own; the Sending line took the place of the Sending line before it.
            assert app.query_one(Said).said.count("Bestco: sent and confirmed.") == 1
            assert not [line for line in app.query_one(Said).said if line.startswith("Sending 1")]
            second_gate.set()
            await wait_for_apply(pilot, app)

    asyncio.run(run())


def test_the_log_keeps_one_sending_line_not_one_per_job(home: Path, resume: Path, restored_apply: Any) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "ok"), Outcome("submitted", "ok")], [])
        async with app.run_test(size=(140, 40)) as pilot:
            await send_batch(app, pilot, ["space", "space"])
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            assert not [line for line in app.query_one(Said).said if line.startswith("Sending")]

    asyncio.run(run())


def test_the_sent_list_and_its_details_say_why_a_job_was_not_sent_in_the_same_words(home: Path, resume: Path, restored_apply: Any) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("blocked", "I saw a puzzle.", reason="captcha"), Outcome("unconfirmed", "clicked", Proof("2026-11-05T10:01:00", "u", "", "No change.", "t", ""))], [])
        async with app.run_test(size=(80, 24)) as pilot:
            await send_batch(app, pilot, ["space", "space"])
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.press("s")
            await wait_for_screen(pilot, SentList)
            table = app.screen.query_one(DataTable)
            assert sorted(str(table.get_row_at(index)[2]) for index in range(table.row_count)) == ["? sent, not confirmed", "✗ a captcha stopped it"]
            for index in range(table.row_count):
                table.move_cursor(row=index)
                await pilot.press("enter")
                await wait_for_screen(pilot, SentDetails)
                text = str(app.screen.query_one("#summary Static").render())
                if app.screen.item.status == "blocked":
                    assert "Not sent" in text and "A captcha stopped it." in text and "I saw a puzzle." in text
                else:
                    assert "Check your email" in text
                await pilot.press("escape")

    asyncio.run(run())


def test_quitting_while_applications_are_sent_asks_once_and_escape_goes_back(home: Path, resume: Path, restored_apply: Any) -> None:
    gate = asyncio.Event()

    async def run() -> None:
        app = scripted_app(home, resume)

        async def slow(*arguments: Any, **keywords: Any) -> Outcome:
            await gate.wait()
            return Outcome("submitted", "ok")

        import aihawk.session as session_module

        session_module.apply_to_job = slow  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await send_batch(app, pilot, ["space"])
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("q")
            await wait_for_screen(pilot, StopSending)
            assert "Quit and stop them?" in app.screen.question
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.is_running and not isinstance(app.screen, StopSending)
            await pilot.press("ctrl+c")
            await wait_for_screen(pilot, StopSending)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert not app.is_running

    asyncio.run(run())


def test_quitting_with_nothing_being_sent_asks_nothing(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("q")
            await pilot.pause(0.2)
            assert not app.is_running

    asyncio.run(run())


def test_s_a_and_e_while_applications_are_sent_say_to_wait(home: Path, resume: Path, restored_apply: Any) -> None:
    gate = asyncio.Event()

    async def run() -> None:
        app = scripted_app(home, resume)

        async def slow(*arguments: Any, **keywords: Any) -> Outcome:
            await gate.wait()
            return Outcome("submitted", "ok")

        import aihawk.session as session_module

        session_module.apply_to_job = slow  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await send_batch(app, pilot, ["space"])
            await pilot.press("enter")
            await pilot.pause(0.3)
            for key in ("s", "a", "e"):
                await pilot.press(key)
                await pilot.pause(0.1)
                assert last_line(app) == "Applications are being sent: wait for them to finish.", key
                assert not isinstance(app.screen, (SentList, SendBatch, Settings))
            gate.set()
            await wait_for_apply(pilot, app)

    asyncio.run(run())


def test_the_key_prompt_says_what_orcarouter_is_that_it_costs_and_that_esc_is_allowed(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert app.screen.question == "Paste your OrcaRouter key"
            hint = str(app.screen.query_one(".hint", Static).content).splitlines()
            assert hint[0] == "AIHawk uses OrcaRouter, a paid AI service, to read your resume and fill the forms: a few cents per application."
            # The address on a line of its own, whole, the referral path in it.
            assert hint[1] == "Get a key at orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"
            assert hint[2] == "Ctrl+O opens it in your browser · It stays on this computer"
            assert hint[3] == "Enter saves · Esc later"
            rows = [row.rstrip() for row in ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]]
            assert any("orcarouter.ai/ref/ref_accd17afb78fc95ac4d7" in row for row in rows)

    asyncio.run(run())


def test_the_resume_prompt_says_a_wrong_path_inside_itself_and_stays_open(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert app.screen.query_one(Input).placeholder == "Drag your resume file into this window, or type where it is"
            app.screen.query_one(Input).value = "/no/such/file.pdf"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert isinstance(app.screen, TextPrompt)
            assert str(app.screen.query_one("#problem", Static).content) == "No file at that path."
            assert app.screen.query_one("#problem").display
            # Typing again takes it away; a file dragged in comes with quotes.
            app.screen.query_one(Input).value = f'"{resume}"'
            await pilot.pause(0.1)
            assert not app.screen.query_one("#problem").display
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())


def test_days_that_are_not_a_whole_number_are_said_in_the_prompt(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            rows = [key.value for key in app.screen.query_one(DataTable).rows]
            app.screen.query_one(DataTable).move_cursor(row=rows.index("max_age_days"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.question == "Posted within (days)"
            app.screen.query_one(Input).value = "soon"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert isinstance(app.screen, TextPrompt)
            assert str(app.screen.query_one("#problem", Static).content) == "Type a whole number of days, like 30."
            app.screen.query_one(Input).value = "30"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            assert app.session.config.max_age_days == 30

    asyncio.run(run())


def test_the_settings_say_the_resumes_name_the_key_as_saved_and_never_an_environment_variable(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            rows = {key: now for key, _, now in app.setting_rows()}
            assert rows["resume"] == "resume.txt" and rows["key"] == "saved"
            assert "ORCAROUTER" not in " ".join(rows.values())
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            assert not list(app.screen.query("Footer"))
            table = app.screen.query_one(DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.question == "Where (cities or countries, separated by commas)"
            assert str(app.screen.query_one(".hint", Static).content) == "Enter saves · Esc goes back"
            await pilot.press("escape")
            await wait_for_screen(pilot, Settings)
            app.screen.query_one(DataTable).move_cursor(row=1)
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.question == "Leave out (words in job titles, separated by commas)"

    asyncio.run(run())


def test_the_heading_says_how_many_are_selected_names_a_place_once_and_a_cut_word_ends_in_an_ellipsis(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert str(app.query_one("#heading", Static).render()) == "AIHawk · Python Engineer · Milan, Italy"
            assert str(app.query_one("#count", Static).render()) == "Page 1 · 4 jobs"
            await pilot.press("space", "space")
            assert str(app.query_one("#count", Static).render()) == "Page 1 · 4 jobs · 2 selected"
            await pilot.press("up", "space")
            assert str(app.query_one("#count", Static).render()) == "Page 1 · 4 jobs · 1 selected"
            app.session.store.add(Job("long", "A Very Long Company Name That Cannot Fit Here Ltd", "Principal Python Engineer, Platform and Data Infrastructure", "Milan", "https://x.example/1", "https://x.example/1", ""))
            app.session.store.set_score("long", 5.0)
            app.pages = [["long"]]
            app.refresh_jobs()
            row = app.query_one(DataTable).get_row_at(0)
            assert str(row[2]).endswith("…") and str(row[3]).endswith("…")

    asyncio.run(run())


def test_a_job_with_no_description_says_it_can_still_be_applied_to_and_the_details_name_o(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        app.start = lambda: None  # type: ignore[method-assign]
        job = Job("x", "Co", "Engineer", "Milan", "https://a.example/", "https://a.example/", "")
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(JobDetails(Stored(job, "scored", 4.0, None)))
            # The footer is drawn a moment after the screen opens, later on a slow machine: waited for, not timed.
            for _ in range(60):
                await pilot.pause(0.05)
                if "o Open the posting" in footer_text(app):
                    break
            assert str(app.screen.query_one("#text Static").render()) == "This posting has no description. You can still apply: the form is filled from your resume."
            assert "o Open the posting" in footer_text(app)

    asyncio.run(run())


def test_the_log_shows_three_lines_so_a_message_of_three_lines_is_whole(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.say("one " * 17 + "\n" if False else ("word " * 45).strip())
            await pilot.pause(0.2)
            shown = [row.rstrip() for row in ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]]
            assert sum(row.lstrip().startswith("word") for row in shown) >= 3

    asyncio.run(run())


def two_chunk_home(home: Path, tmp_path: Path) -> None:
    """The home's database in two chunks, as the pipeline publishes them: one with a posting that has a text, one with a title only."""
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (key VARCHAR, url VARCHAR, title VARCHAR, company VARCHAR, location VARCHAR, description VARCHAR, posted_at VARCHAR)")
    connection.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, 'Milan', ?, NULL)",
        [("high", "https://jobs.lever.co/beta/2", "Senior Python Engineer", "Bestco", "A posting. " * 30), ("low", "https://jobs.lever.co/acme/1", "Python Engineer", "Lowly", "A posting.")],
    )
    site = tmp_path / "two"
    site.mkdir()
    (home / "aihawk.yaml").write_text(f"database: {publish(connection, site, 2)}\n", encoding="utf-8")


def hold_second_chunk(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """The second chunk waits for the event the test sets: the jobs come one chunk at a time, as they do over a slow line."""
    gate = threading.Event()
    fetch = Library._fetch

    def slow(source: str, path: Path, read: Any = lambda count: None) -> None:
        if path.name.startswith("titles_"):
            gate.wait(20)
        fetch(source, path, read)

    monkeypatch.setattr(Library, "_fetch", staticmethod(slow))
    return gate


def test_the_jobs_of_the_first_chunk_are_shown_while_the_rest_still_comes_and_the_first_page_freezes_when_all_are_here(
    home: Path, resume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]
) -> None:
    two_chunk_home(home, tmp_path)
    gate = hold_second_chunk(monkeypatch)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if companies(app):
                    break
            assert companies(app) == ["Bestco"] and not app.session.library.complete()
            line = last_line(app)
            assert line.startswith("Getting the jobs, first time only:") and line.endswith(" MB. The first ones are already below.")
            await pilot.press("space")
            assert app.selected == {"https://jobs.lever.co/beta/2"}
            gate.set()
            await settle(pilot, app)
            await pilot.pause(0.3)
            assert app.session.library.complete()
            # Page 1 is as it was when the jobs were all here; the job that came after is on the next.
            assert app.pages == [["https://jobs.lever.co/beta/2"], ["https://jobs.lever.co/acme/1"]]
            assert last_line(app) in ("All the jobs are here.", "Page 2 ready.")

    asyncio.run(run())
    steps = [event["properties"]["step"] for event in tracked if event["event"] == "start_step"]
    assert steps.count("first_jobs") == 1 and steps.count("jobs_ready") == 1 and steps.index("first_jobs") < steps.index("jobs_ready")


def test_while_the_jobs_come_the_right_arrow_says_when_more_pages_come(home: Path, resume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    two_chunk_home(home, tmp_path)
    gate = hold_second_chunk(monkeypatch)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if companies(app):
                    break
            await pilot.press("right")
            assert last_line(app) == "More pages when all the jobs are here."
            assert app.page == 0
            gate.set()
            await settle(pilot, app)

    asyncio.run(run())


def live_app(home: Path, monkeypatch: pytest.MonkeyPatch, count: int, page: int) -> AIHawk:
    """An app whose jobs are still coming, with `count` scored jobs, the best first: job 0 is the best."""
    monkeypatch.setattr(tui_module, "PAGE", page)
    app = AIHawk(home)
    app.start = lambda: None  # type: ignore[method-assign]
    for number in range(count):
        job = Job(f"j{number:02d}", f"Firm {number:02d}", "Engineer", "Milan", f"https://x.example/{number:02d}", f"https://x.example/{number:02d}", "")
        app.session.store.add(job)
        app.session.store.set_score(job.id, 5.0 - number / 10)
    app.session.library.complete = lambda: False  # type: ignore[method-assign]
    return app


def shown(app: AIHawk) -> list[str]:
    return [str(row[2]).replace("Firm ", "") for row in (app.query_one(DataTable).get_row_at(index) for index in range(app.query_one(DataTable).row_count))]


def test_the_live_page_with_the_cursor_on_the_first_row_is_just_the_best_so_far(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = live_app(home, monkeypatch, 4, 3)
        async with app.run_test(size=(120, 40)) as pilot:
            app.make_pages()
            await pilot.pause(0.1)
            assert shown(app) == ["00", "01", "02"]
            # A better job is scored: it takes the top, and the one it pushes out is in the reserve.
            job = Job("best", "Firm best", "Engineer", "Milan", "https://x.example/best", "https://x.example/best", "")
            app.session.store.add(job)
            app.session.store.set_score("best", 5.5)
            app.make_pages()
            await pilot.pause(0.1)
            assert shown(app) == ["best", "00", "01"] and app.spare()[:2] == ["j02", "j03"]
            # The cursor is on the first row still: the best job, not the one it was on before.
            assert app.query_one(DataTable).cursor_row == 0 and app.current_id() == "best"

    asyncio.run(run())


def test_the_live_page_keeps_the_rows_down_to_the_cursor_where_they_are_and_the_cursor_on_its_job(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = live_app(home, monkeypatch, 6, 4)
        async with app.run_test(size=(120, 40)) as pilot:
            app.make_pages()
            await pilot.pause(0.1)
            assert shown(app) == ["00", "01", "02", "03"]
            app.query_one(DataTable).move_cursor(row=1)
            job = Job("best", "Firm best", "Engineer", "Milan", "https://x.example/best", "https://x.example/best", "")
            app.session.store.add(job)
            app.session.store.set_score("best", 5.5)
            app.make_pages()
            await pilot.pause(0.1)
            # Rows 0 and 1 are as they were; the better job comes after them, above the rest.
            assert shown(app) == ["00", "01", "best", "02"]
            assert app.current_id() == "j01" and app.query_one(DataTable).cursor_row == 1

    asyncio.run(run())


def test_a_selected_job_stays_on_the_live_page_even_when_better_ones_come(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        app = live_app(home, monkeypatch, 6, 3)
        async with app.run_test(size=(120, 40)) as pilot:
            app.make_pages()
            await pilot.pause(0.1)
            app.selected = {"j05"}
            for number in range(3):
                job = Job(f"new{number}", f"Firm new{number}", "Engineer", "Milan", f"https://x.example/new{number}", f"https://x.example/new{number}", "")
                app.session.store.add(job)
                app.session.store.set_score(job.id, 5.5 - number / 10)
            app.make_pages()
            await pilot.pause(0.1)
            # The selected one stays, last because it ranks last, and the best of the others fill the rest of the page.
            assert shown(app) == ["new0", "new1", "05"]

    asyncio.run(run())


def test_the_wait_line_counts_megabytes_and_says_how_long_once_the_speed_is_known(home: Path) -> None:
    app = AIHawk(home)
    app.bytes = (12_000_000, 667_000_000)
    assert app.download_line() == "Getting the jobs, first time only: 12 of 667 MB. The first ones appear in a moment."
    # One megabyte a second over the last half minute: 547 seconds left, ten minutes rounded up.
    app.speed.extend([(100.0, 0), (130.0, 30_000_000)])
    app.bytes = (120_000_000, 667_000_000)
    app.pages = [["a"]]
    assert app.download_line() == "Getting the jobs, first time only: 120 of 667 MB, about 10 minutes left. The first ones are already below."
    app.bytes = (640_000_000, 667_000_000)
    assert "less than a minute left" in app.download_line()
    # Under three seconds of speed is no speed yet.
    app.speed.clear()
    app.speed.extend([(100.0, 0), (101.0, 1_000_000)])
    assert "left" not in app.download_line()
    # A manifest with no sizes falls back to the share of the rows.
    app.bytes = (0, 0)
    assert app.download_line().startswith("Getting the jobs, first time only: 0%.")


def test_the_download_line_does_not_cover_a_scoring_that_did_not_go_through(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        app.start = lambda: None  # type: ignore[method-assign]
        async with app.run_test(size=(120, 40)) as pilot:
            app.bytes = (1_000_000, 10_000_000)
            app.stuck = "Still no answer. Check your connection, then press → to try again."
            app.say(app.stuck, again=True)
            app.bytes_came(2_000_000, 10_000_000)
            await pilot.pause(0.1)
            assert last_line(app) == app.stuck
            app.stuck = ""
            app.bytes_came(3_000_000, 10_000_000)
            await pilot.pause(0.1)
            assert last_line(app).startswith("Getting the jobs, first time only: 3 of 10 MB")

    asyncio.run(run())


def accepting(*answers: bool) -> Any:
    """What the service says to each key typed, in turn: yes or no."""
    given = list(answers)
    keys: list[str] = []

    async def accepted(key: str) -> bool:
        keys.append(key)
        return given.pop(0)

    accepted.keys = keys  # type: ignore[attr-defined]
    return accepted


def test_a_key_the_service_refuses_is_asked_for_again_with_why_in_the_question(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = scripted_app(home, resume)
        app.session.key_accepted = accepting(False, True)  # type: ignore[method-assign]
        app.session.save_key = lambda key: (type(app.session.config).save_key(app.session.config, key), setattr(app.session, "_client", ScriptedModelForApp()))  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.screen.query_one(Input).value = "sk-orca-wrong"
            await pilot.press("enter")
            await pilot.pause(0.3)
            # The resume is not asked: the same question, with why.
            assert isinstance(app.screen, TextPrompt) and app.screen.question == "Paste your OrcaRouter key"
            assert str(app.screen.query_one("#problem", Static).content) == "OrcaRouter did not accept that key. Check it and paste it again."
            assert app.screen.query_one("#problem").display
            app.screen.query_one(Input).value = "sk-orca-right"
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert app.session.key_accepted.keys == ["sk-orca-wrong", "sk-orca-right"]  # type: ignore[attr-defined]

    asyncio.run(run())
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-right"


@pytest.mark.parametrize(("error", "kept"), [(status_error(AuthenticationError, 401), False), (APIConnectionError(request=REQUEST), True), (status_error(InternalServerError, 503), True), (APITimeoutError(request=REQUEST), True)])
def test_only_a_refusal_is_a_no_to_a_key_and_a_service_that_does_not_answer_never_blocks_it(home: Path, error: Exception, kept: bool) -> None:
    session = Session(home)

    async def get(path: str, cast_to: type) -> Any:
        assert path == "/models"
        raise error

    session.key_client = lambda key: SimpleNamespace(get=get)  # type: ignore[method-assign,assignment]
    assert asyncio.run(session.key_accepted("sk-orca-x")) is kept
    session.close()


def test_a_key_the_service_accepts_is_a_yes(home: Path) -> None:
    session = Session(home)

    async def get(path: str, cast_to: type) -> Any:
        return {"data": []}

    session.key_client = lambda key: SimpleNamespace(get=get)  # type: ignore[method-assign,assignment]
    assert asyncio.run(session.key_accepted("sk-orca-x")) is True
    session.close()


def test_a_key_given_from_the_settings_after_the_start_stopped_for_want_of_one_goes_on_with_the_start(
    home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = scripted_app(home, resume)
        app.session.key_accepted = accepting(True)  # type: ignore[method-assign]
        app.session.save_key = lambda key: (type(app.session.config).save_key(app.session.config, key), setattr(app.session, "_client", ScriptedModelForApp()))  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("escape")
            await settle(pilot, app)
            assert companies(app) == []
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            rows = [key.value for key in app.screen.query_one(DataTable).rows]
            app.screen.query_one(DataTable).move_cursor(row=rows.index("key"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "sk-orca-new"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")
            await settle(pilot, app)
            await pilot.pause(0.3)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())


def test_a_key_changed_from_the_settings_is_checked_too_and_a_refused_one_is_asked_again(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_text("sk-orca-old", encoding="utf-8")

    async def run() -> None:
        app = scripted_app(home, resume)
        app.session.key_accepted = accepting(False, True)  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            app.screen.query_one(DataTable).move_cursor(row=[key.value for key in app.screen.query_one(DataTable).rows].index("key"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "sk-orca-bad"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, TextPrompt) and app.screen.query_one("#problem").display
            app.screen.query_one(Input).value = "sk-orca-good"
            await pilot.press("enter")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")

    asyncio.run(run())
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-good"


def test_ctrl_c_copies_what_is_selected_and_stays_and_with_nothing_selected_it_leaves(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []

    async def run() -> None:
        app = AIHawk(home)
        app.start = lambda: None  # type: ignore[method-assign]
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(80, 24)) as pilot:
            monkeypatch.setattr(type(app.screen), "get_selected_text", lambda self: "orcarouter.ai/ref/ref_accd17afb78fc95ac4d7")
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert app.is_running and copied == ["orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"]
            monkeypatch.setattr(type(app.screen), "get_selected_text", lambda self: None)
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert not app.is_running

    asyncio.run(run())


def test_ctrl_c_on_a_selected_answer_in_a_text_question_copies_it_and_a_password_is_never_copied(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, None)
        copied: list[str] = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            field = app.screen.query_one(Input)
            field.value = "C:/Users/me/cv.pdf"
            field.select_all()
            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert app.is_running and copied == ["C:/Users/me/cv.pdf"]
            await pilot.press("escape")

    asyncio.run(run())


def test_ctrl_o_at_the_key_question_opens_the_address_in_the_browser_and_the_hint_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    opened: list[str] = []
    import aihawk.screens as screens_module

    monkeypatch.setattr(screens_module.webbrowser, "open", lambda url: opened.append(url) or True)

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert "Ctrl+O opens it in your browser" in str(app.screen.query_one(".hint", Static).content)
            await pilot.press("ctrl+o")
            await pilot.pause(0.1)
            assert opened == ["https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"]
            await pilot.press("escape")

    asyncio.run(run())


class NoCredit(ScriptedModelForApp):
    """The account has no credit: every scoring request is answered 402."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0
        self.broke = True

    async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
        self.attempts += 1
        if self.broke:
            raise status_error(APIStatusError, 402)
        return await super().post(path, body=body, cast_to=cast_to)


def test_with_no_credit_the_jobs_stay_new_scoring_stops_and_says_what_to_do_and_the_arrow_goes_on(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        model = NoCredit()
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert model.attempts == 1, "not retried in a loop"
            assert last_line(app) == "Your OrcaRouter account has no credit yet. Add a few dollars at orcarouter.ai, then press → to go on."
            assert len(app.session.store.with_status("new")) == 3 and not app.session.store.with_status("passed_over")
            # The line is not covered by the count of the jobs coming, and nothing says there are no more.
            assert "No more jobs" not in logged(app) and "No jobs found" not in logged(app)
            model.broke = False
            await pilot.press("right")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())


def test_an_application_that_fails_for_want_of_credit_says_so_and_the_batch_stops(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[str] = []

    async def run() -> None:
        app = scripted_app(home, resume)

        async def broke(client: Any, model: str, job: Any, *rest: Any, **keywords: Any) -> Outcome:
            calls.append(job.company)
            raise status_error(APIStatusError, 402)

        import aihawk.session as session_module

        session_module.apply_to_job = broke  # type: ignore[assignment]
        async with app.run_test(size=(120, 40)) as pilot:
            await send_batch(app, pilot, ["space", "space"])
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            said = app.query_one(Said).said
            assert "Bestco: not sent, your OrcaRouter credit is finished: add credit at orcarouter.ai." in said
            assert calls == ["Bestco"], "the second one was not tried"
            (item,) = app.session.store.sent()
            assert item.status == "failed" and item.reason == "no_credit"
            assert item.note == "Your OrcaRouter account has no credit yet. Add a few dollars at orcarouter.ai, then press → to go on."

    asyncio.run(run())


def test_what_an_application_waits_for_is_said_in_the_line_of_the_batch_and_the_batch_line_comes_back(home: Path, resume: Path, restored_apply: Any) -> None:
    gate = asyncio.Event()
    waiting = asyncio.Event()

    async def run() -> None:
        app = scripted_app(home, resume)

        async def slowed(client: Any, model: str, job: Any, *rest: Any, **keywords: Any) -> Outcome:
            note = apply_module.NOTE.get()
            assert note is not None
            note("The AI service asked to slow down: waiting 40 seconds…")
            waiting.set()
            await gate.wait()
            note("")
            return Outcome("submitted", "ok")

        import aihawk.session as session_module

        session_module.apply_to_job = slowed  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await send_batch(app, pilot, ["space"])
            await pilot.press("enter")
            await asyncio.wait_for(waiting.wait(), 5)
            await pilot.pause(0.1)
            assert last_line(app) == "The AI service asked to slow down: waiting 40 seconds…"
            gate.set()
            await wait_for_apply(pilot, app)
            assert apply_module.NOTE.get() is None, "the note is the application's own and ends with it"

    asyncio.run(run())


def test_the_engine_is_brought_in_the_background_once_and_only_when_it_is_not_here(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    import subprocess

    started: list[tuple[list[str], dict[str, Any]]] = []

    class Process:
        def __init__(self, command: list[str], **keywords: Any) -> None:
            started.append((command, keywords))
            self.ended = False

        def poll(self) -> int | None:
            return 0 if self.ended else None

        def terminate(self) -> None:
            self.ended = True

    monkeypatch.setattr(subprocess, "Popen", Process)
    session = Session(home)
    session.bring_engine()
    assert started == [], "the engine is here: nothing is brought"
    monkeypatch.setattr(Session, "engine_here", staticmethod(lambda: False))
    session.bring_engine()
    session.bring_engine()
    assert len(started) == 1
    command, keywords = started[0]
    import sys as system

    assert command == [system.executable, "-m", "invisible_playwright", "fetch"]
    assert keywords["stdin"] == keywords["stdout"] == keywords["stderr"] == subprocess.DEVNULL
    # It does not outlive AIHawk.
    process = session.engine
    assert process is not None and not process.ended  # type: ignore[attr-defined]
    session.close()
    assert process.ended  # type: ignore[attr-defined]


def test_the_engine_is_not_brought_while_a_browser_is_open_or_opening(monkeypatch: pytest.MonkeyPatch, home: Path, resume: Path) -> None:
    """The jobs can finish in the middle of the first application, whose server is downloading the engine itself: a second
    download of it clears the folder the first is starting the browser from (2026-11-12, an application that failed after 98 s)."""
    import subprocess

    started: list[list[str]] = []

    class Process:
        def __init__(self, command: list[str], **keywords: Any) -> None:
            started.append(command)

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(Session, "engine_here", staticmethod(lambda: False))
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.store.add(Job("one", "Firm", "Python Engineer", "Milan", "https://jobs.lever.co/a/1", "https://jobs.lever.co/a/1", "A posting.", 1.0))
    during: list[int] = []

    async def fill(*_arguments: Any, **_options: Any) -> Outcome:
        session.bring_engine()
        during.append(len(started))
        return Outcome("blocked", "a puzzle")

    monkeypatch.setattr(session_module, "apply_to_job", fill)
    asyncio.run(session.apply(session.store.get("one"), lambda _: None))  # type: ignore[arg-type]
    assert during == [0] and session.engine is None and session.browsers == 0
    session.bring_engine()
    assert len(started) == 1, "with no browser open it is brought, as before"
    session.close()


def test_the_engine_is_not_brought_while_the_mailbox_browser_opens_and_it_waits_for_one_already_coming(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    import subprocess

    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **keywords: started.append(command))
    monkeypatch.setattr(Session, "engine_here", staticmethod(lambda: False))
    session = Session(home)
    seen: list[tuple[int, int]] = []

    async def sign_in(profile: Path, folder: Path, wait: Any, url: str) -> str:
        session.bring_engine()
        seen.append((len(started), session.browsers))
        return "https://mail.google.com/mail/u/0/"

    monkeypatch.setattr(session_module, "sign_in_browser", sign_in)
    assert asyncio.run(session.sign_in_mail("gmail", lambda: asyncio.sleep(0))) is True
    assert seen == [(0, 1)] and session.browsers == 0

    class Coming:
        polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return None if self.polls < 3 else 0

    async def real_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", real_sleep)
    session.engine = Coming()  # type: ignore[assignment]
    seen.clear()
    asyncio.run(session.sign_in_mail("gmail", lambda: asyncio.sleep(0)))
    assert session.engine.polls >= 3, "the mailbox browser opened only once the engine had come"  # type: ignore[attr-defined]
    session.engine = None
    session.close()


def test_a_browser_that_did_not_open_ends_the_application_with_its_name_and_not_the_servers_words(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.store.add(Job("one", "Firm", "Python Engineer", "Milan", "https://jobs.lever.co/a/1", "https://jobs.lever.co/a/1", "A posting.", 1.0))
    failures: list[Exception] = [
        apply_module.BrowserDidNotOpen("browser_did_not_open", "the browser did not open: C:/Users/someone/engine is gone"),
        apply_module.BrowserDidNotOpen("browser_not_ready", "the browser was not ready in time: starting"),
        RuntimeError("something else"),
    ]

    async def fail(*_arguments: Any, **_options: Any) -> Outcome:
        raise failures.pop(0)

    monkeypatch.setattr(session_module, "apply_to_job", fail)
    outcomes = [asyncio.run(session.apply(session.store.get("one"), lambda _: None)) for _ in range(3)]  # type: ignore[arg-type]
    assert [outcome.reason for outcome in outcomes] == ["browser_did_not_open", "browser_not_ready", "RuntimeError"]
    assert all(outcome.status == "failed" and "someone" not in outcome.summary for outcome in outcomes)
    assert "C:/Users/someone/engine is gone" in (session.config.data_dir / "errors.log").read_text(encoding="utf-8")
    assert session.browsers == 0
    session.close()


def test_an_engine_that_cannot_be_started_is_no_ones_concern(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    import subprocess

    def refuse(*arguments: Any, **keywords: Any) -> None:
        raise OSError("no")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(Session, "engine_here", staticmethod(lambda: False))
    session = Session(home)
    session.bring_engine()
    assert session.engine is None
    session.close()


def test_an_application_waits_for_the_engine_that_is_coming_and_says_so(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return None if self.polls < 4 else 0

    async def run() -> list[str]:
        session = Session(home)
        session.engine = Process()  # type: ignore[assignment]
        told: list[str] = []
        real = asyncio.sleep

        async def quick(seconds: float) -> None:
            await real(0)

        monkeypatch.setattr(asyncio, "sleep", quick)
        try:
            await session.engine_coming(told.append)
        finally:
            session.engine = None
            session.close()
        return told

    told = asyncio.run(run())
    assert told[0] == "Getting the browser, only the first time: about 270 MB…" and told[-1] == ""


def test_the_engine_is_brought_when_the_jobs_are_all_here_and_not_before(home: Path, resume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    two_chunk_home(home, tmp_path)
    gate = hold_second_chunk(monkeypatch)
    brought: list[bool] = []
    monkeypatch.setattr(Session, "bring_engine", lambda self: brought.append(self.library.complete()))

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(200):
                await pilot.pause(0.05)
                if companies(app):
                    break
            assert brought == [], "the line of the wait is shared: not while the jobs come"
            gate.set()
            await settle(pilot, app)
            await pilot.pause(0.2)

    asyncio.run(run())
    assert brought == [True]


class Broken(ScriptedModelForApp):
    """A model that fails in a way nobody foresaw."""

    async def _create(self, **request: Any) -> Any:
        raise KeyError("the private detail")


def test_an_unexpected_error_says_where_the_details_are_and_shows_none_of_them(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = Broken()  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            line = last_line(app)
            assert line == "Something went wrong. Try again in a moment. Details are in errors.log. Press r and give the file again."
            assert "private detail" not in logged(app)

    asyncio.run(run())
    assert "KeyError: 'the private detail'" in (home / "data" / "errors.log").read_text(encoding="utf-8")


def test_while_the_resume_is_read_the_screen_says_how_long_it_takes(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        said: list[str] = []
        original = app.session.load_resume

        async def slow(path: Path) -> None:
            said.append(last_line(app))
            await original(path)

        app.session.load_resume = slow  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
        assert said == ["Reading your resume: about half a minute…"]

    asyncio.run(run())


# What OrcaRouter says to a refusal (https://docs.orcarouter.ai/operations/errors.md), as the SDK hands it over: the body
# without its "error" wrapper. Every message ends with the request's id.
def refusal(code: int, message: str, service_code: str = "", kind: type = APIStatusError) -> Exception:
    body = {"message": f"{message} (request id: 20261112abc)", "type": "orcarouter_api_error", "code": service_code}
    return status_error(kind, code, body)


NO_CREDIT_WORDS = "Your OrcaRouter account has no credit yet. Add a few dollars at orcarouter.ai, then {}"
GO_ON = "press → to go on."
REFUSALS = [
    (refusal(402, "insufficient quota", "insufficient_user_quota"), "no_credit", NO_CREDIT_WORDS.format(GO_ON)),
    (status_error(APIStatusError, 402), "no_credit", NO_CREDIT_WORDS.format(GO_ON)),
    (
        refusal(402, "free calls used up", "free_quota_exhausted"), "free_calls_used",
        f"The free calls for today are used up. Add credit at orcarouter.ai, then {GO_ON}",
    ),
    (
        refusal(403, "token quota is not enough", "pre_consume_token_quota_failed", PermissionDeniedError), "key_limit",
        f"This key's own limit is used up. Raise it, or make a new key, at orcarouter.ai. Then {GO_ON}",
    ),
    (
        refusal(403, "token cycle spend limit reached, resets at 2026-11-13", "access_denied", PermissionDeniedError), "spend_limit",
        f"This key reached its spending limit for now. Raise it at orcarouter.ai, or wait until it resets. Then {GO_ON}",
    ),
    (
        refusal(403, "monthly budget gone", "budget_exceeded", PermissionDeniedError), "budget",
        f"Your OrcaRouter budget for this month is used up. Then {GO_ON}",
    ),
    (
        refusal(403, "This token has no access to model deepseek-v4", "", PermissionDeniedError), "model_not_allowed",
        f"This key is not allowed to use the models AIHawk needs. Make a key with no model restriction at orcarouter.ai. Then {GO_ON}",
    ),
    (
        refusal(403, "IP 1.2.3.4 is not allowed", "access_denied", PermissionDeniedError), "refused_other",
        f"OrcaRouter refused this key for the request: IP 1.2.3.4 is not allowed. Then {GO_ON}",
    ),
    (status_error(PermissionDeniedError, 403), "refused_other", f"OrcaRouter refused this key for the request. Then {GO_ON}"),
]


@pytest.mark.parametrize(("error", "name", "words"), REFUSALS)
def test_each_refusal_of_the_account_has_its_cause_and_its_own_words(error: Exception, name: str, words: str) -> None:
    assert cause(error) == name
    assert explain(error) == words
    assert "request id" not in explain(error)


def test_the_cause_is_read_from_a_body_with_or_without_its_wrapper_or_none_at_all() -> None:
    inner = {"message": "token quota is not enough (request id: x)", "code": "pre_consume_token_quota_failed"}
    assert cause(status_error(PermissionDeniedError, 403, inner)) == "key_limit"
    assert cause(status_error(PermissionDeniedError, 403, {"error": inner})) == "key_limit"
    # No body: the error's own text is what there is to read.
    plain = PermissionDeniedError("Error code: 403 - token cycle spend limit reached", response=httpx.Response(403, request=REQUEST), body=None)
    assert cause(plain) == "spend_limit"
    assert cause(status_error(PermissionDeniedError, 403, "not json")) == "refused_other"
    assert cause(status_error(PermissionDeniedError, 403, {"error": "text", "code": 5})) == "refused_other"
    # Not about the account.
    assert cause(status_error(RateLimitError, 429)) == "" and cause(status_error(AuthenticationError, 401)) == "" and cause(ValueError("x")) == ""


def test_an_unexplained_refusal_shows_the_services_words_cut_and_without_a_key_or_a_request_id() -> None:
    long = "not allowed from here " * 20
    said = explain(refusal(403, long, "access_denied", PermissionDeniedError))
    shown = said.removeprefix("OrcaRouter refused this key for the request: ").removesuffix(f" Then {GO_ON}")
    assert len(shown) <= 121 and shown.endswith("….") and "request id" not in said
    assert "sk-orca" not in explain(refusal(403, "bad key sk-orca-secret123456", "x", PermissionDeniedError))


def test_a_step_names_what_to_press_in_the_place_it_failed() -> None:
    step = "press r: your resume is remembered."
    assert explain(refusal(402, "x", "insufficient_user_quota"), step).endswith(f"then {step}")
    assert explain(refusal(403, "x", "budget_exceeded", PermissionDeniedError), step).endswith(f"Then {step}")


def test_only_a_401_is_a_refused_key_a_403_never_makes_the_key_forgotten() -> None:
    assert refused(status_error(AuthenticationError, 401))
    for error, _, _ in REFUSALS:
        assert not refused(error)


@pytest.mark.parametrize(
    ("error", "name"),
    [
        (refusal(402, "x", "insufficient_user_quota"), "APIStatusError 402 insufficient_user_quota"),
        (refusal(403, "x", "pre_consume_token_quota_failed", PermissionDeniedError), "PermissionDeniedError 403 pre_consume_token_quota_failed"),
        (status_error(PermissionDeniedError, 403), "PermissionDeniedError 403"),
        (status_error(AuthenticationError, 401), "AuthenticationError 401"),
        (refusal(403, "x", "a code with spaces and <markup>", PermissionDeniedError), "PermissionDeniedError 403"),
        (APIConnectionError(request=REQUEST), "APIConnectionError"),
        (ValueError("x"), "ValueError"),
    ],
)
def test_an_error_is_told_to_the_makers_with_its_status_and_the_services_code(error: Exception, name: str) -> None:
    assert error_name(error) == name


class ResumeRefused(ScriptedModelForApp):
    """The account refuses what reading the resume asks, until `error` is None."""

    def __init__(self, error: Exception | None) -> None:
        super().__init__()
        self.error = error

    async def _create(self, **request: Any) -> Any:
        if self.error is not None:
            raise self.error
        return await super()._create(**request)


def test_no_credit_at_the_resume_says_so_and_r_asks_for_the_same_file_again(home: Path, resume: Path, tracked: list[dict[str, Any]]) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        model = ResumeRefused(refusal(402, "no quota", "insufficient_user_quota"))
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert last_line(app) == NO_CREDIT_WORDS.format("press r: your resume is remembered.")
            assert "Press r and give the file again" not in logged(app)
            model.error = None
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.query_one(Input).value == str(resume)
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            # Read: nothing is remembered any more, and r asks an empty question.
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.query_one(Input).value == ""
            await pilot.press("escape")

    asyncio.run(run())
    failed = [event["properties"] for event in tracked if event["event"] == "start_step" and event["properties"]["step"] == "resume_failed"]
    assert [(step["error"], step["kind"]) for step in failed] == [("APIStatusError 402 insufficient_user_quota", ".txt")]
    assert "resume_read" in steps_of(tracked)


@pytest.mark.parametrize(
    "error",
    [
        refusal(403, "x", "budget_exceeded", PermissionDeniedError), status_error(RateLimitError, 429), status_error(InternalServerError, 503),
        APIConnectionError(request=REQUEST), status_error(AuthenticationError, 401),
    ],
)
def test_the_resume_is_remembered_across_every_failure_of_the_account_or_the_service(home: Path, resume: Path, error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_text("sk-orca-kept", encoding="utf-8")

    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = ResumeRefused(error)  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            if isinstance(error, RateLimitError):
                # Told in a question of its own, whose Esc leaves the line checked below.
                await wait_for_screen(pilot, Confirm)
                await pilot.press("escape")
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert app.remembered == resume
            if isinstance(error, AuthenticationError):
                # The key is asked for again, and the resume after it, with the file in.
                assert isinstance(app.screen, TextPrompt) and app.screen.question == "Paste your OrcaRouter key"
                await pilot.press("escape")
                return
            assert "press r: your resume is remembered" in last_line(app).lower()
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.query_one(Input).value == str(resume)
            await pilot.press("escape")

    asyncio.run(run())


def test_a_403_keeps_the_key_and_a_401_forgets_it(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_text("sk-orca-mine", encoding="utf-8")

    async def run(error: Exception) -> None:
        app = AIHawk(home, resume)
        app.session._client = ResumeRefused(error)  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            await pilot.press("escape")

    asyncio.run(run(refusal(403, "This token has no access to model x", "", PermissionDeniedError)))
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-mine"
    asyncio.run(run(status_error(AuthenticationError, 401)))
    assert not (home / "key").exists()


@pytest.mark.parametrize(("error", "name", "words"), REFUSALS[2:])
def test_a_refusal_at_the_resume_says_its_cause_and_the_resume_step(home: Path, resume: Path, error: Exception, name: str, words: str) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = ResumeRefused(error)  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert last_line(app) == words.replace(GO_ON, "press r: your resume is remembered.")

    asyncio.run(run())


def test_a_file_that_is_not_a_document_or_not_a_file_is_not_remembered(home: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("too short", encoding="utf-8")

    async def run() -> None:
        app = scripted_app(home, empty)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert app.remembered is None
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.query_one(Input).value == ""
            # A path that is no file stays in the question's own check and is remembered nowhere.
            app.screen.query_one(Input).value = str(tmp_path / "nothing.pdf")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert "No file at that path." in str(app.screen.query_one("#problem", Static).content) and app.remembered is None
            await pilot.press("escape")

    asyncio.run(run())


def test_an_application_refused_for_a_key_limit_says_the_cause_and_keeps_its_name(home: Path, resume: Path, restored_apply: Any) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)

        async def limited(client: Any, model: str, job: Any, *rest: Any, **keywords: Any) -> Outcome:
            raise refusal(403, "token cycle spend limit reached, resets at 2026-11-13", "access_denied", PermissionDeniedError)

        session_module.apply_to_job = limited  # type: ignore[assignment]
        async with app.run_test(size=(120, 40)) as pilot:
            await send_batch(app, pilot, ["space", "space"])
            await pilot.press("enter")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)
            assert "Bestco: not sent, this key reached its spending limit for now." in app.query_one(Said).said
            item = next(item for item in app.session.store.sent() if item.job.company == "Bestco")
            assert item.status == "failed" and item.reason == "spend_limit"
            assert item.note.startswith("This key reached its spending limit for now. Raise it at orcarouter.ai")

    asyncio.run(run())


def test_scoring_stopped_by_a_key_limit_says_the_cause(home: Path, resume: Path) -> None:
    class Limited(NoCredit):
        async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
            self.attempts += 1
            raise refusal(403, "token quota is not enough", "pre_consume_token_quota_failed", PermissionDeniedError)

    async def run() -> None:
        app = AIHawk(home, resume)
        model = Limited()
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert last_line(app) == f"This key's own limit is used up. Raise it, or make a new key, at orcarouter.ai. Then {GO_ON}"
            assert model.attempts == 1

    asyncio.run(run())


class Billing:
    """What the balance page of OrcaRouter says to each ask, in turn (the last one stays): a number of dollars, an error to
    raise, or any other answer as it is."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.asked = 0

    async def get(self, path: str, cast_to: type) -> Any:
        assert path == "/tools/balance"
        self.asked += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, float | int) and not isinstance(answer, bool):
            return {"success": True, "data": {"wallet_usd": answer, "spendable_usd": answer}}
        return answer


def with_billing(app: AIHawk, billing: Billing) -> None:
    """A key typed in is accepted and kept, and its account answers as `billing` says."""
    app.session.key_accepted = accepting(True, True, True)  # type: ignore[method-assign]
    app.session.save_key = lambda key: (type(app.session.config).save_key(app.session.config, key), setattr(app.session, "_client", ScriptedModelForApp()))  # type: ignore[method-assign]
    app.session.key_client = lambda key: SimpleNamespace(get=billing.get)  # type: ignore[method-assign,assignment]


def steps_of(tracked: list[dict[str, Any]]) -> list[str]:
    return [event["properties"]["step"] for event in tracked if event["event"] == "start_step"]


def test_a_key_with_no_credit_says_so_before_the_resume_and_enter_checks_again_until_there_is(
    home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]
) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    billing = Billing(0, 0.0, 3.5)
    opened: list[str] = []
    import aihawk.screens as screens_module

    monkeypatch.setattr(screens_module.webbrowser, "open", lambda url: opened.append(url) or True)

    async def run() -> None:
        app = scripted_app(home, resume)
        with_billing(app, billing)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.screen.query_one(Input).value = "sk-orca-new"
            await pilot.press("enter")
            await wait_for_screen(pilot, Confirm)
            assert app.screen.question.startswith("This key works, but its OrcaRouter account has no credit yet.")
            assert "add them at orcarouter.ai" in app.screen.question
            assert app.screen.hint == "Enter checks again · Ctrl+O opens orcarouter.ai · Esc goes on without"
            assert companies(app) == []
            await pilot.press("ctrl+o")
            await pilot.press("enter")
            await pilot.pause(0.3)
            # Still none: the same question, said once more.
            assert isinstance(app.screen, Confirm) and app.screen.question.startswith("Still no credit")
            await pilot.press("enter")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())
    assert opened == ["https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"]
    steps = steps_of(tracked)
    assert steps[:5] == ["key_asked", "key_saved", "no_credit_asked", "no_credit_checked", "credit_added"] and steps[5] == "resume_read"
    assert billing.asked == 3


def test_escape_on_the_credit_question_goes_on_with_the_resume_without_credit(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = scripted_app(home, resume)
        with_billing(app, Billing(0))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.screen.query_one(Input).value = "sk-orca-new"
            await pilot.press("enter")
            await wait_for_screen(pilot, Confirm)
            await pilot.press("escape")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())
    assert steps_of(tracked)[:5] == ["key_asked", "key_saved", "no_credit_asked", "no_credit_skipped", "resume_read"]
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-new"


@pytest.mark.parametrize(
    "answer",
    [
        2.5, 1e-9, APIConnectionError(request=REQUEST), status_error(InternalServerError, 503), status_error(AuthenticationError, 401),
        {}, {"data": None}, {"data": {}}, {"data": {"spendable_usd": None}}, {"data": {"spendable_usd": "0"}},
        {"data": {"spendable_usd": True}}, {"data": {"spendable_usd": False}}, {"spendable_usd": 0}, ["x"], "text",
        # What the OpenAI-shaped billing page said for a new account with no credit: it is not the balance page's answer.
        {"object": "billing_subscription", "hard_limit_usd": 0},
    ],
)
def test_the_credit_question_is_never_asked_when_the_service_says_there_is_some_or_says_nothing_usable(
    home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]], answer: Any
) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = scripted_app(home, resume)
        with_billing(app, Billing(answer))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.screen.query_one(Input).value = "sk-orca-new"
            await pilot.press("enter")
            await settle(pilot, app)
            assert not isinstance(app.screen, Confirm)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())
    assert not any(step.startswith("no_credit") or step == "credit_added" for step in steps_of(tracked))


def test_a_key_changed_in_the_settings_is_asked_about_its_credit_too(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")
    (home / "key").write_text("sk-orca-old", encoding="utf-8")

    async def run() -> None:
        app = scripted_app(home, resume)
        with_billing(app, Billing(0))
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.press("e")
            await wait_for_screen(pilot, Settings)
            app.screen.query_one(DataTable).move_cursor(row=[key.value for key in app.screen.query_one(DataTable).rows].index("key"))
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "sk-orca-new"
            await pilot.press("enter")
            await wait_for_screen(pilot, Confirm)
            await pilot.press("escape")
            await wait_for_screen(pilot, Settings)
            await pilot.press("escape")

    asyncio.run(run())
    assert (home / "key").read_text(encoding="utf-8") == "sk-orca-new"
    assert steps_of(tracked)[-2:] == ["no_credit_asked", "no_credit_skipped"]


def test_the_balance_page_is_asked_through_the_key_client_and_only_a_number_up_to_zero_says_no_credit(home: Path) -> None:
    session = Session(home)
    paths: list[str] = []

    async def get(path: str, cast_to: type) -> Any:
        paths.append(path)
        return {"success": True, "data": {"wallet_usd": 0, "spendable_usd": 0}}

    session.key_client = lambda key: SimpleNamespace(get=get)  # type: ignore[method-assign,assignment]
    assert asyncio.run(session.has_no_credit("sk-orca-x")) is True
    assert paths == ["/tools/balance"]
    session.close()


def test_every_cause_about_the_account_has_its_words_in_the_list_of_applications() -> None:
    from aihawk import llm
    from aihawk.apply import WHY_NOT_SENT

    # A batch stops at the first of these: the next application would fail the same way.
    assert llm.ACCOUNT_CAUSES <= set(WHY_NOT_SENT)
    assert {"no_credit", "key_limit", "spend_limit", "model_not_allowed"} <= llm.ACCOUNT_CAUSES


FREE_PLAN_WORDS = "OrcaRouter is limiting this account: its free plan is not enough for AIHawk. Add a few dollars at orcarouter.ai, then try again."


def test_a_resume_refused_as_too_many_requests_says_the_free_plan_is_not_enough_and_enter_reads_it_again(
    home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]
) -> None:
    opened: list[str] = []
    import aihawk.screens as screens_module

    monkeypatch.setattr(screens_module.webbrowser, "open", lambda url: opened.append(url) or True)

    async def run() -> None:
        app = AIHawk(home, resume)
        model = ResumeRefused(status_error(RateLimitError, 429))
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_for_screen(pilot, Confirm)
            assert app.screen.question == FREE_PLAN_WORDS
            assert app.screen.hint == "Enter tries again · Ctrl+O opens orcarouter.ai · Esc later"
            assert companies(app) == []
            await pilot.press("ctrl+o")
            # The credit is added: the resume is read again from the remembered file, and the jobs come.
            model.error = None
            await pilot.press("enter")
            await settle(pilot, app)
            assert not isinstance(app.screen, Confirm)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert app.remembered is None

    asyncio.run(run())
    assert opened == ["https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"]
    steps = steps_of(tracked)
    assert steps[steps.index("free_plan_told") :][:3] == ["free_plan_told", "free_plan_retry", "resume_read"]
    assert "free_plan_later" not in steps


def test_a_resume_still_refused_as_too_many_requests_is_told_again_on_each_try(home: Path, resume: Path, tracked: list[dict[str, Any]]) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = ResumeRefused(status_error(RateLimitError, 429))  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_for_screen(pilot, Confirm)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await wait_for_screen(pilot, Confirm)
            assert app.screen.question == FREE_PLAN_WORDS
            await pilot.press("escape")
            await settle(pilot, app)

    asyncio.run(run())
    assert [step for step in steps_of(tracked) if step.startswith("free_plan")] == ["free_plan_told", "free_plan_retry", "free_plan_told", "free_plan_later"]


def test_escape_on_the_free_plan_question_leaves_the_line_that_says_r_reads_the_resume_again(
    home: Path, resume: Path, tracked: list[dict[str, Any]]
) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        model = ResumeRefused(status_error(RateLimitError, 429))
        app.session._client = model  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_for_screen(pilot, Confirm)
            await pilot.press("escape")
            await settle(pilot, app)
            assert not isinstance(app.screen, Confirm)
            assert last_line(app) == "The AI service is busy. Wait a minute. Press r: your resume is remembered."
            assert companies(app) == [] and app.remembered == resume
            model.error = None
            await pilot.press("r")
            await wait_for_screen(pilot, TextPrompt)
            assert app.screen.query_one(Input).value == str(resume)
            await pilot.press("escape")

    asyncio.run(run())
    steps = steps_of(tracked)
    assert "free_plan_told" in steps and "free_plan_later" in steps and "free_plan_retry" not in steps


def test_too_many_requests_while_scoring_does_not_say_the_free_plan_is_not_enough(
    home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch, tracked: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(tui_module, "RETRY_PAUSES", (0.01, 0.01, 0.01, 0.01))

    class Busy(ScriptedModelForApp):
        async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
            raise status_error(RateLimitError, 429)

    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = Busy()  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert not isinstance(app.screen, Confirm)

    asyncio.run(run())
    assert not any(step.startswith("free_plan") for step in steps_of(tracked))
    assert "resume_read" in steps_of(tracked)


def test_no_credit_at_the_resume_is_not_the_free_plan_question(home: Path, resume: Path, tracked: list[dict[str, Any]]) -> None:
    async def run() -> None:
        app = AIHawk(home, resume)
        app.session._client = ResumeRefused(refusal(402, "no quota", "insufficient_user_quota"))  # type: ignore[assignment]
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            await pilot.pause(0.2)
            assert not isinstance(app.screen, Confirm)
            assert last_line(app) == NO_CREDIT_WORDS.format("press r: your resume is remembered.")

    asyncio.run(run())
    assert not any(step.startswith("free_plan") for step in steps_of(tracked))
