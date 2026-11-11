"""The screen, driven key by key, with a scripted model and a local database: what a person actually does."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx
import pytest
from click.testing import CliRunner
from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError, RateLimitError
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
from aihawk.llm import explain
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
    assert steps == ["resume_read", "jobs_downloading", "jobs_ready"]
    (closed,) = [event for event in tracked if event["event"] == "app_closed"]
    assert closed["properties"]["searched"] is True and closed["properties"]["last_step"] == steps[-1]


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


def status_error(kind: type, code: int) -> Exception:
    return kind(f"Error code: {code}", response=httpx.Response(code, request=REQUEST), body=None)


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


def test_after_the_resume_the_mail_is_asked_once_and_gmail_is_opened_until_the_inbox_shows(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The question comes right after the resume, in the same voice as the key: the address, then the mailbox open in the browser.
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    server = ScriptedServer(page={"url": "https://mail.google.com/mail/u/0/#inbox", "text": "Inbox"})
    scripted_server(monkeypatch, server)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt) and "Which email do you apply with" in app.screen.question
            assert app.screen.query_one(Input).value == "ada@example.com"
            app.screen.query_one(Input).value = "ada@gmail.com"
            await pilot.press("enter")
            await wait_for_screen(pilot, SignIn)
            assert "The browser opened Gmail" in app.screen.text and "ada@gmail.com" in app.screen.text
            assert ("browser_navigate", {"url": "https://mail.google.com/mail/u/0/"}) in server.calls
            await pilot.press("enter")
            await wait_for_sign_in_to_close(pilot, app)
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]

    asyncio.run(run())
    assert server.calls[-1] == ("browser_close", {})
    answers = answers_of(load_config(home))
    assert answers["email"] == "ada@gmail.com" and answers["mail_provider"] == "gmail"


def test_the_mail_question_left_with_escape_is_not_asked_again_at_the_next_start(home: Path, resume: Path) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt) and "Which email do you apply with" in app.screen.question
            await pilot.press("escape")
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            assert ("mail", "Your mail", "not set up") in app.setting_rows()

    asyncio.run(run())
    assert answers_of(load_config(home))["mail_provider"] == "later"


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

    for reason in (*BLOCKED_BY, "stuck", "no_code", "mailbox_signed_out", "never_clicked", "form_not_loaded", "refused_by_form"):
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
            assert hint[2] == "It stays on this computer · Enter saves · Esc later"
            rows = [row.rstrip() for row in ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]]
            assert any("orcarouter.ai/ref/ref_accd17afb78fc95ac4d7" in row for row in rows)

    asyncio.run(run())


def test_the_mail_question_is_plain_and_an_address_that_is_neither_gmail_nor_outlook_is_asked_once_then_left(home: Path, resume: Path) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert app.screen.question == "Some job sites email a code to confirm it is you. Which email do you apply with?"
            assert str(app.screen.query_one(".hint", Static).content) == "AIHawk opens that inbox in its browser only to read those codes · Enter · Esc later"
            app.screen.query_one(Input).value = "ada@yahoo.com"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.screen.question == "Where is ada@yahoo.com?"
            app.screen.query_one(Input).value = "yahoo"
            await pilot.press("enter")
            await settle(pilot, app)
            await pilot.pause(0.2)
            # Said once, and left for later: it is not asked again.
            assert not isinstance(app.screen, TextPrompt)
            assert app.query_one(Said).said.count("Only Gmail and Outlook can be read for now; the rest works without.") == 1
            assert app.session.mail()[1] == "later"

    asyncio.run(run())


def test_a_mailbox_that_still_shows_its_sign_in_page_is_a_question_with_no_empty_input(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            await settle(pilot, app)
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
            await settle(pilot, app)
            assert "Mail left for later: e, then Your mail." in app.query_one(Said).said

    asyncio.run(run())


def test_escape_on_the_sign_in_is_later_not_done(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(home / "data" / "jobs.db")
    store.set_answer("mail_provider", "")
    store.close()
    # The inbox would show if it were looked at: Escape must not count as having signed in.
    server = ScriptedServer(page={"url": "https://mail.google.com/mail/u/0/#inbox", "text": "Inbox"})
    scripted_server(monkeypatch, server)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            app.screen.query_one(Input).value = "ada@gmail.com"
            await pilot.press("enter")
            await wait_for_screen(pilot, SignIn)
            await pilot.press("escape")
            await wait_for_sign_in_to_close(pilot, app)
            await settle(pilot, app)
            assert "Signed in to Gmail." not in app.query_one(Said).said
            assert "Mail left for later: e, then Your mail." in app.query_one(Said).said

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
            await pilot.pause(0.2)
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
