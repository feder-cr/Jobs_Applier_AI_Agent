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

from aihawk import apply as apply_module
from aihawk import session as session_module
from aihawk.apply import Outcome
from aihawk.cli import main as cli_main
from aihawk.config import ConfigError, load_config
from aihawk.database import Job
from aihawk.documents import MissingGlyphs, missing_glyphs, write_cv_pdf
from aihawk.llm import explain
from aihawk.profile import add_answers, answer_question, install_resume, read_answers, read_resume
from aihawk.session import Session
from aihawk.store import Proof, Stored
from aihawk.tui import AIHawk, JobDetails, SendBatch, SentDetails, SentList, SignIn, TextPrompt
from fakes import ScriptedBrowser

PROFILE = {
    "name": "Ada Rossi",
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
FIELDS = {"summary": "Fits.", "gaps": ["No Kubernetes"], "legitimacy": "credible"}
CV_TEXT = "# Ada Rossi\n\nSenior Python Engineer at Acme for five years, working on backend services.\n"


def report(role: str, must_haves: str) -> str:
    return json.dumps({"role": role, "must_haves": must_haves, "level": "unclear", "location": "unclear", **FIELDS})


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
    connection.execute("ALTER TABLE t ADD COLUMN IF NOT EXISTS country VARCHAR; ALTER TABLE t ADD COLUMN IF NOT EXISTS remote BOOLEAN")
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
    app.session._client = model
    return app


class ScriptedModelForApp:
    """Answers each kind of request the way a model would."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.requests: list[dict[str, Any]] = []

    async def post(self, path: str, *, body: dict[str, Any], cast_to: type) -> dict[str, Any]:
        """The decision endpoint: no opinion on any question, so the evidence is zero and the title alone decides."""
        return {"answers": {name: {"type": "noul", "noul": 0.5} for name in body["questions"]}}

    async def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        name = (request.get("response_format") or {}).get("json_schema", {}).get("name")
        if name == "search_profile":
            content = json.dumps(PROFILE)
        elif name == "job_report":
            text = request["messages"][1]["content"]
            content = report("yes", "yes") if "Bestco" in text else report("yes", "unclear") if "Lowly" in text else report("no", "unclear")
        else:
            content = "# Ada Rossi"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


async def settle(pilot: Any, app: AIHawk) -> None:
    """Until the flow's workers are done or waiting on the person."""
    for _ in range(200):
        await pilot.pause(0.05)
        running = [worker for worker in app.workers if worker.is_running and worker.group == "flow"]
        if not running or isinstance(app.screen, (TextPrompt, SendBatch)):
            return
    raise AssertionError("the flow never settled")


def test_a_judged_posting_with_no_text_says_so_in_its_status_and_keeps_its_score(home: Path) -> None:
    app = AIHawk(home)
    judged = Stored(Job("x", "Co", "Engineer", "Milan", "u", "u", ""), "scored", 3.0, None, None, None)
    read = Stored(Job("y", "Co", "Engineer", "Milan", "u", "u", "A posting."), "scored", 3.0, None, None, None)
    assert app.row(judged)[1] == "3.0" and app.row(judged)[-1] == "no text"
    assert app.row(read)[-1] == ""


def companies(app: AIHawk) -> list[str]:
    table = app.query_one(DataTable)
    return [str(table.get_row_at(index)[3]) for index in range(table.row_count)]


def test_a_resume_is_all_it_takes_to_get_the_best_jobs_first(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            assert companies(app) == ["Bestco", "Lowly", "Noplugin"]
            table = app.query_one(DataTable)
            assert table.get_row_at(0)[1] == "4.5"
            assert table.get_row_at(0)[2] == "auto"
            assert table.get_row_at(2)[2] == "link"

    asyncio.run(run())
    config = load_config(home)
    assert config.filters.include == ("role",)
    assert [form.text for form in config.filters.plan.nodes[0].forms] == ["Python Engineer", "Python Developer"]
    assert config.filters.locations == ("Milan", "Milano", "Italy")
    assert config.filters.cities == ("Milan", "Milano")
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
        if not app.busy:
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
            assert [table.get_row_at(index)[6] for index in range(2)] == ["sent", "sent"]
            assert app.selected == set()

    asyncio.run(run())
    assert [call["job"].company for call in calls] == ["Bestco", "Lowly"]
    assert calls[0]["job"].apply_url == "https://jobs.lever.co/beta/2/apply"
    assert "Upload the CV" in calls[0]["guidance"]
    assert Path(calls[0]["cv_file"]).is_file()


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
            assert app.selected == {"high"}
            assert app.query_one(DataTable).get_row_at(0)[6] == ""

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


def test_what_a_form_asks_is_asked_after_the_others_are_sent_remembered_and_tried_again(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("needs_input", "missing", ["Your salary?"]), Outcome("submitted", "sent"), Outcome("submitted", "sent")], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("space", "space", "a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            # The other form was sent while this one waited for its answer.
            assert [call["job"].company for call in calls] == ["Bestco", "Lowly"]
            app.screen.query_one(Input).value = "50000 EUR"
            await pilot.press("enter")
            await wait_for_apply(pilot, app)

    asyncio.run(run())
    assert [call["job"].company for call in calls] == ["Bestco", "Lowly", "Bestco"]
    assert "50000 EUR" in calls[2]["answers"]
    assert read_answers(load_config(home))["Your salary?"] == "50000 EUR"


def test_a_question_skipped_stops_trying_and_is_recorded(home: Path, resume: Path, restored_apply: Any) -> None:
    calls: list[Any] = []

    async def run() -> None:
        app = scripted_app(home, resume)
        fake_apply(app, [Outcome("needs_input", "missing", ["Your salary?"])], calls)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await wait_for_screen(pilot, TextPrompt)
            await pilot.press("escape")
            await wait_for_apply(pilot, app)
            assert app.query_one(DataTable).get_row_at(0)[6] == "needs an answer"

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
        store.add(Job("stuck", "Beta", "Designer", "Rome", "https://jobs.lever.co/beta/2", "https://jobs.lever.co/beta/2/apply", "d"))
        store.set_status("stuck", "blocked", "a captcha")
        store.set_proof("stuck", Proof())
        # A job with no click is ordered by when its status changed: an older change comes after the send.
        store._db.execute("UPDATE jobs SET updated = ? WHERE id = ?", ("2026-11-01T12:00:00", "stuck"))
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("s")
            await wait_for_screen(pilot, SentList)
            table = app.screen.query_one(DataTable)
            rows = [[str(table.get_row_at(index)[column]) for column in (1, 3, 4)] for index in range(table.row_count)]
            assert rows[0] == ["Acme", "sent and confirmed", "Thank you"]
            assert rows[1][1:] == ["blocked", "a captcha"]
            await pilot.press("enter")
            await wait_for_screen(pilot, SentDetails)
            assert app.screen.item.job.id == "sent"
            await pilot.press("escape")
            await pilot.press("escape")

    asyncio.run(run())


def test_s_with_nothing_tried_shows_an_empty_list(home: Path, resume: Path) -> None:
    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("s")
            await wait_for_screen(pilot, SentList)
            assert app.screen.query_one(DataTable).row_count == 0
            await pilot.press("escape")

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
    assert read_answers(config) == {"email": "ada@example.com"}
    assert config.api_key() == "sk-orca-typed"


def test_a_pdf_with_no_text_says_what_to_do(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    with path.open("wb") as out:
        writer.write(out)
    with pytest.raises(ConfigError, match="scanned PDF"):
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


def test_an_answer_the_person_gave_outlives_a_new_resume(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    answer_question(config, "email", "typed@example.com")
    add_answers(config, {"email": "from-cv@example.com", "phone": "1"})
    assert read_answers(config) == {"email": "typed@example.com", "phone": "1"}
    answer_question(config, "email", "new@example.com")
    assert read_answers(config)["email"] == "new@example.com"


def test_a_new_resume_forgets_the_cvs_of_the_jobs_not_sent_and_keeps_those_sent(home: Path, resume: Path, tmp_path: Path) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    for job_id, status in (("not-sent", "scored"), ("sent", "submitted")):
        session.store.add(Job(job_id, "Firm", "Python Engineer", "Milan", f"https://a.example/{job_id}", "https://a.example/apply", "", "", 1.0))
        session.store.set_cv(job_id, tmp_path / f"{job_id}.pdf")
        session.store.set_status(job_id, status)
    newer = tmp_path / "newer.txt"
    newer.write_text(CV_TEXT + "\nA later role at Beta.", encoding="utf-8")
    asyncio.run(session.load_resume(newer))
    assert session.store.get("not-sent").cv_path is None  # type: ignore[union-attr]
    assert session.store.get("sent").cv_path == str(tmp_path / "sent.pdf")  # type: ignore[union-attr]
    session.close()


def test_the_cv_font_lacks_some_latin_letters_and_a_cv_with_one_is_not_written(tmp_path: Path) -> None:
    assert missing_glyphs("Łódź") == "ź"
    with pytest.raises(MissingGlyphs):
        write_cv_pdf("Ś", tmp_path / "cv.pdf")
    assert not (tmp_path / "cv.pdf").exists()


def test_a_new_resume_never_invents_a_contact_detail(home: Path, resume: Path) -> None:
    config = load_config(home)
    asyncio.run(install_resume(ScriptedModelForApp(), config, resume))  # type: ignore[arg-type]
    assert "phone" not in read_answers(config)


def test_a_new_resume_replaces_the_plan_and_keeps_what_the_person_excluded(home: Path, resume: Path) -> None:
    (home / "aihawk.yaml").write_text("filters:\n  exclude: [intern]\n", encoding="utf-8")
    asyncio.run(install_resume(ScriptedModelForApp(), load_config(home), resume))  # type: ignore[arg-type]
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


def test_the_sign_in_key_opens_the_profile_until_the_person_is_done(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer()
    scripted_server(monkeypatch, server)

    async def run() -> None:
        app = scripted_app(home, resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("l")
            await wait_for_screen(pilot, SignIn)
            assert ("browser_open", {"profile": str(home / "browser-profile")}) in server.calls
            assert server.calls[-1] == ("browser_navigate", {"url": "about:blank"})
            await pilot.press("enter")
            await wait_for_sign_in_to_close(pilot, app)

    asyncio.run(run())
    assert server.calls[-1] == ("browser_close", {})


def test_the_sign_in_key_is_refused_while_an_application_runs(home: Path, resume: Path, restored_apply: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer()
    scripted_server(monkeypatch, server)
    import aihawk.session as session_module

    async def run() -> None:
        release = asyncio.Event()

        async def slow_apply(client: Any, model: str, job: Any, cv: str, answers: str, cv_file: Path, data_dir: Path, profile: Path, guidance: str = "", reaches: frozenset[str] = frozenset(), key_env: str = "") -> Outcome:
            await release.wait()
            return Outcome("blocked", "not sent")

        app = scripted_app(home, resume)
        session_module.apply_to_job = slow_apply  # type: ignore[assignment]
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, app)
            await pilot.press("a")
            await wait_for_screen(pilot, SendBatch)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.applying()
            await pilot.press("l")
            await pilot.pause(0.2)
            assert not app.signing_in
            assert not any(name == "browser_open" for name, _ in server.calls)
            release.set()
            await wait_for_apply(pilot, app)

    asyncio.run(run())


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
            assert "forgot it" in log_text(app) and "orcarouter.ai" in log_text(app)
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
        (APIConnectionError(request=REQUEST), "cannot be reached"),
        (APITimeoutError(request=REQUEST), "did not answer in time"),
        (status_error(RateLimitError, 429), "slow down"),
        (status_error(InternalServerError, 503), "failed on its side"),
    ],
)
def test_a_failed_model_call_says_in_plain_words_what_happened_and_keeps_the_detail(error: Exception, words: str) -> None:
    text = explain(error)
    assert words in text
    assert text.endswith(f"({error})")


def test_ctrl_c_at_the_key_prompt_leaves_and_the_prompt_says_how(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY")

    async def run() -> None:
        app = AIHawk(home, None)
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, TextPrompt)
            assert "Ctrl+C quits" in str(app.screen.query_one(".hint", Static).content)
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
            assert "exported with text, or saved as .docx." in " ".join(line.text.strip() for line in app.query_one(RichLog).lines)
            rows = ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]
            assert any("q Quit" in row for row in rows)

    asyncio.run(run())


def test_a_posting_whose_words_look_like_markup_is_shown_as_it_is(home: Path) -> None:
    async def run() -> None:
        app = AIHawk(home)
        app.session.store.add(Job("odd", "Co [/y]", "[/x] Engineer", "Milan [bold", "https://a.example/", "https://a.example/", ""))
        app.start = lambda: None  # type: ignore[method-assign]
        async with app.run_test(size=(140, 40)) as pilot:
            app.refresh_jobs()
            await pilot.pause()
            row = app.query_one(DataTable).get_row_at(0)
            assert (str(row[3]), str(row[4]), str(row[5])) == ("Co [/y]", "[/x] Engineer", "Milan [bold")

    asyncio.run(run())


def test_a_cv_that_cannot_be_written_fails_that_job_alone(home: Path, resume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(home)
    session._client = ScriptedModelForApp()  # type: ignore[assignment]
    asyncio.run(session.load_resume(resume))
    session.store.add(Job("one", "Firm", "Python Engineer", "Milan", "https://jobs.lever.co/a/1", "https://jobs.lever.co/a/1", "A posting.", "", 1.0))

    async def no_cv(*_: Any) -> str:
        raise RuntimeError("the model is down")

    monkeypatch.setattr(session_module, "tailor_cv", no_cv)
    outcome = asyncio.run(session.apply(session.store.get("one"), lambda _: None))  # type: ignore[arg-type]
    assert outcome.status == "failed" and "the model is down" in outcome.summary
    assert session.store.get("one").status == "failed"  # type: ignore[union-attr]
    session.close()
