"""The screen, driven key by key, with a scripted model and a local database: what a person actually does."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pypdf import PdfWriter
from textual.widgets import DataTable, Input

from aihawk.apply import Outcome
from aihawk.config import ConfigError, load_config
from aihawk.profile import add_answers, answer_question, install_resume, read_answers, read_resume
from aihawk.tui import AIHawk, ConfirmSend, JobDetails, TextPrompt

PROFILE = {
    "name": "Ada Rossi",
    "titles": ["Python Engineer"],
    "locations": ["Milan", "Milano"],
    "goals": "Senior Python work.",
    "answers": {"email": "ada@example.com", "phone": "", "location": "Milan", "linkedin": "", "website": "", "current_company": ""},
}
FIELDS = {"summary": "Fits.", "gaps": ["No Kubernetes"], "legitimacy": "credible", "confidence": "high", "cv_match": "", "goal_alignment": "", "compensation": "", "culture": "", "red_flags": ""}
CV_TEXT = "# Ada Rossi\n\nSenior Python Engineer at Acme for five years, working on backend services.\n"


def report(score: float) -> str:
    return json.dumps({"score": score, **FIELDS})


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
    connection.execute(f"COPY t TO '{(folder / 'jobs_0.parquet').as_posix()}' (FORMAT parquet)")
    connection.execute(f"""COPY (SELECT * EXCLUDE (description, file_row_number), 'jobs_0.parquet' AS part, file_row_number AS row_number
        FROM read_parquet('{(folder / 'jobs_0.parquet').as_posix()}', file_row_number = true)) TO '{(folder / 'index.parquet').as_posix()}' (FORMAT parquet)""")
    manifest = {"rows_out": 4, "built_at": "2026-11-04T00:00:00", "index": {"name": "index.parquet", "bytes": 1}, "files": [{"name": "jobs_0.parquet"}]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(folder / "manifest.json")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "test-key")
    folder = tmp_path / "home"
    folder.mkdir()
    (folder / "aihawk.yaml").write_text(f"database: {published(tmp_path)}\n", encoding="utf-8")
    return folder


@pytest.fixture
def resume(tmp_path: Path) -> Path:
    path = tmp_path / "resume.txt"
    path.write_text(CV_TEXT, encoding="utf-8")
    return path


def scripted_app(home: Path, resume: Path | None, **options: Any) -> AIHawk:
    app = AIHawk(home, resume, **options)
    model = ScriptedModelForApp()
    app.session._client = model  # noqa: SLF001
    return app


class ScriptedModelForApp:
    """Answers each kind of request the way a model would."""

    def __init__(self) -> None:
        from types import SimpleNamespace  # noqa: PLC0415

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.requests: list[dict[str, Any]] = []

    async def _create(self, **request: Any) -> Any:
        from types import SimpleNamespace  # noqa: PLC0415

        self.requests.append(request)
        name = (request.get("response_format") or {}).get("json_schema", {}).get("name")
        if name == "search_profile":
            content = json.dumps(PROFILE)
        elif name == "job_report":
            text = request["messages"][1]["content"]
            content = report(4.9 if "Bestco" in text else 4.0 if "Lowly" in text else 2.0)
        else:
            content = "# Ada Rossi"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


async def settle(pilot: Any, app: AIHawk) -> None:
    """Until the flow's workers are done or waiting on the person."""
    for _ in range(200):
        await pilot.pause(0.05)
        running = [worker for worker in app.workers if worker.is_running and worker.group == "flow"]
        if not running or isinstance(app.screen, (TextPrompt, ConfirmSend)):
            return
    raise AssertionError("the flow never settled")


def companies(app: AIHawk) -> list[str]:
    table = app.query_one(DataTable)
    return [table.get_row_at(index)[3] for index in range(table.row_count)]


def test_a_resume_is_all_it_takes_to_get_the_best_jobs_first(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            table = app.query_one(DataTable)
            assert table.get_row_at(0)[1] == "4.9"
            assert table.get_row_at(0)[2] == "auto"
            assert table.get_row_at(2)[2] == "link"

    asyncio.run(run())
    config = load_config(home)
    assert config.filters.include == ("Python Engineer",)
    assert config.cv.read_text(encoding="utf-8").startswith("# Ada Rossi")
    assert read_answers(config) == {"email": "ada@example.com", "location": "Milan"}


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
    async def apply_to_job(client: Any, model: str, job: Any, cv: str, answers: str, cv_file: Path, data_dir: Path, confirm: Any, guidance: str = "") -> Outcome:
        calls.append({"job": job, "answers": answers, "guidance": guidance, "cv_file": cv_file})
        outcome = outcomes.pop(0)
        if outcome.status == "submitted" and confirm is not None and not await confirm("the form's summary"):
            return Outcome("declined", "not sent")
        return outcome

    import aihawk.session as session_module  # noqa: PLC0415

    session_module.apply_to_job = apply_to_job  # type: ignore[assignment]


@pytest.fixture
def restored_apply() -> Any:
    import aihawk.session as session_module  # noqa: PLC0415

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
        if not app.busy:
            return
    raise AssertionError("applying never finished")


def test_select_apply_and_nothing_leaves_without_the_yes(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent"), Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("space", "a")
            await wait_for_screen(pilot, ConfirmSend)
            await pilot.press("n")
            await wait_for_apply(pilot, app)
            table = app.query_one(DataTable)
            assert table.get_row_at(0)[6] == "not sent"
            assert app.selected == set()
            table.move_cursor(row=0)
            await pilot.press("a")
            await wait_for_screen(pilot, ConfirmSend)
            await pilot.press("y")
            await wait_for_apply(pilot, app)
            assert app.query_one(DataTable).get_row_at(0)[6] == "sent"

    asyncio.run(run())
    assert calls[0]["job"].apply_url == "https://jobs.lever.co/beta/2/apply"
    assert "Upload the CV" in calls[0]["guidance"]
    assert Path(calls[0]["cv_file"]).is_file()


def test_a_job_already_sent_is_not_sent_twice(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, ConfirmSend)
            await pilot.press("y")
            await wait_for_apply(pilot, app)
            app.query_one(DataTable).move_cursor(row=0)
            await pilot.press("a")
            await wait_for_apply(pilot, app)
            await pilot.pause(0.2)

    asyncio.run(run())
    assert len(calls) == 1


def test_a_site_no_plugin_knows_says_so_and_nothing_runs(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            app.query_one(DataTable).move_cursor(row=2)
            await pilot.press("a")
            await pilot.pause(0.2)
            await wait_for_apply(pilot, app)

    asyncio.run(run())
    assert calls == []


def test_what_a_form_asks_is_asked_remembered_and_tried_again(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("needs_input", "missing", ["Your salary?"]), Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, TextPrompt)
            app.screen.query_one(Input).value = "50000 EUR"
            await pilot.press("enter")
            await wait_for_screen(pilot, ConfirmSend)
            await pilot.press("y")
            await wait_for_apply(pilot, app)

    asyncio.run(run())
    assert "50000 EUR" in calls[1]["answers"]
    assert read_answers(load_config(home))["Your salary?"] == "50000 EUR"


def test_a_question_skipped_stops_trying_and_is_recorded(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("needs_input", "missing", ["Your salary?"])], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, TextPrompt)
            await pilot.press("escape")
            await wait_for_apply(pilot, app)
            assert app.query_one(DataTable).get_row_at(0)[6] == "needs an answer"

    asyncio.run(run())
    assert len(calls) == 1


def test_auto_submit_does_not_ask(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume, auto_submit=True)
        fake_apply(app, [Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await pilot.pause(0.2)
            await wait_for_apply(pilot, app)
            assert app.query_one(DataTable).get_row_at(0)[6] == "sent"

    asyncio.run(run())


def test_enter_shows_why_a_job_fits(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("enter")
            await wait_for_screen(pilot, JobDetails)
            assert app.screen.item.report["gaps"] == ["No Kubernetes"]
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


@pytest.mark.parametrize("name", ["cv.txt", "cv.md"])
def test_a_text_resume_is_read(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text(CV_TEXT, encoding="utf-8")
    assert read_resume(path).startswith("# Ada")


def test_a_pdf_with_no_text_says_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    with path.open("wb") as out:
        writer.write(out)
    with pytest.raises(ConfigError, match="scanned PDF"):
        read_resume(path)


def test_a_word_resume_is_read(tmp_path: Path) -> None:
    import zipfile  # noqa: PLC0415

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


def test_an_answer_the_person_gave_outlives_a_new_resume(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    answer_question(config, "email", "typed@example.com")
    add_answers(config, {"email": "from-cv@example.com", "phone": "1"})
    assert read_answers(config) == {"email": "typed@example.com", "phone": "1"}
    answer_question(config, "email", "new@example.com")
    assert read_answers(config)["email"] == "new@example.com"


def test_a_new_resume_never_invents_a_contact_detail(home: Path, resume: Path) -> None:
    config = load_config(home)
    asyncio.run(install_resume(ScriptedModelForApp(), config, resume))  # type: ignore[arg-type]
    assert "phone" not in read_answers(config)
