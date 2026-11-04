"""AIHawk in the terminal: give it a resume, get the jobs that fit best, select them, apply."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, RichLog, Static

from aihawk.config import ConfigError
from aihawk.database import DatabaseError
from aihawk.documents import QUESTIONS
from aihawk.session import Session
from aihawk.store import SUBMITTED, Stored

STATUS_TEXT = {
    "new": "",
    "scored": "",
    "shortlisted": "",
    "passed_over": "passed over",
    "submitted": "sent",
    "unconfirmed": "clicked, not confirmed",
    "declined": "not sent",
    "needs_input": "needs an answer",
    "blocked": "blocked",
    "failed": "failed",
}


class TextPrompt(ModalScreen[str | None]):
    """One question, one line of answer. Enter answers, Escape skips."""

    BINDINGS: ClassVar = [Binding("escape", "skip", "Skip")]

    def __init__(self, question: str, *, placeholder: str = "", password: bool = False) -> None:
        super().__init__()
        self.question = question
        self.placeholder = placeholder
        self.password = password

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="question", markup=False)
            yield Input(placeholder=self.placeholder, password=self.password, id="answer")
            yield Static("Enter to answer · Esc to skip", classes="hint")

    @on(Input.Submitted)
    def answered(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_skip(self) -> None:
        self.dismiss(None)


class ConfirmSend(ModalScreen[bool]):
    """The one place an application is allowed to leave."""

    BINDINGS: ClassVar = [Binding("y", "answer(True)", "Send it"), Binding("n,escape", "answer(False)", "Do not send")]

    def __init__(self, title: str, summary: str) -> None:
        super().__init__()
        self.heading = title
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"Send your application to {self.heading}?", id="question", markup=False)
            yield Static("Check the form in the browser window.", classes="hint")
            with VerticalScroll(id="summary"):
                yield Static(self.summary, markup=False)
            yield Footer()

    def action_answer(self, send: bool) -> None:
        self.dismiss(send)


class JobDetails(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape,enter,q", "close", "Back")]

    def __init__(self, item: Stored) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        job, report = self.item.job, self.item.report or {}
        lines = [f"{job.title}", f"{job.company} · {job.location}", job.url, ""]
        if report:
            answers = " · ".join(f"{name.replace('_', ' ')}: {report.get(name, '?')}" for name in QUESTIONS)
            lines += [f"Score {report.get('score', 0):.1f}/5 · posting looks {report.get('legitimacy', '?')}", answers, "", report.get("summary", "")]
            if report.get("gaps"):
                lines += ["", "Gaps:", *(f"  - {gap}" for gap in report["gaps"])]
        if self.item.note:
            lines += ["", "Last attempt:", self.item.note]
        with Vertical(id="dialog"):
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)


class SignIn(ModalScreen[None]):
    """The browser window is open for the person to sign in. Nothing is typed for them; a key here says they are done."""

    BINDINGS: ClassVar = [Binding("enter,escape", "done", "Done")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("The browser window is open. Sign in there to your email, and to any site you apply on.", id="question", markup=False)
            yield Static("Press Enter here when you are done. The window closes then.", classes="hint")

    def action_done(self) -> None:
        self.dismiss(None)


class AIHawk(App[None]):
    TITLE = "AIHawk"
    CSS = """
    #heading { height: 1; padding: 0 1; background: $primary; color: $text; text-style: bold; }
    #jobs { height: 1fr; }
    #log { height: 7; border-top: solid $primary; padding: 0 1; }
    ModalScreen { align: center middle; background: $background 60%; }
    #dialog { width: 80%; max-width: 100; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #question { text-style: bold; margin-bottom: 1; }
    #summary { height: auto; max-height: 24; margin: 1 0; }
    .hint { color: $text-muted; }
    """
    BINDINGS: ClassVar = [
        Binding("space", "toggle", "Select"),
        Binding("enter", "details", "Details"),
        Binding("a", "apply", "Apply"),
        Binding("o", "open_link", "Open link"),
        Binding("m", "more", "More jobs"),
        Binding("r", "new_resume", "New resume"),
        Binding("l", "sign_in", "Sign in"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, workspace: Path, resume: Path | None = None, auto_submit: bool = False) -> None:
        super().__init__()
        self.session = Session(workspace)
        self.resume = resume
        self.auto_submit = auto_submit
        self.selected: set[str] = set()
        self.busy = False
        self.signing_in = False

    def compose(self) -> ComposeResult:
        yield Static("AIHawk", id="heading", markup=False)
        yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
        yield RichLog(id="log", wrap=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (("", 2), ("Score", 5), ("Apply", 5), ("Company", 22), ("Role", 44), ("Location", 24), ("Status", 20)):
            table.add_column(label, key=label or "mark", width=width)
        table.focus()
        self.show_heading()
        self.start()

    def on_unmount(self) -> None:
        self.session.close()

    # What the person sees.

    def say(self, line: str) -> None:
        self.query_one(RichLog).write(line)

    def show_heading(self, progress: str = "") -> None:
        config = self.session.config
        wanted = config.filters.titles() or "no resume yet"
        place = ", ".join(config.filters.locations[:3])
        left = " · ".join(part for part in (config.name, wanted, place) if part)
        self.query_one("#heading", Static).update(f"AIHawk  {left}" + (f"   [{progress}]" if progress else ""))

    def row(self, item: Stored) -> tuple[Text | str, ...]:
        mark = "●" if item.job.id in self.selected else ""
        score = f"{item.score:.1f}" if item.score is not None else "-"
        can_apply = "auto" if self.session.plugin(item) else "link"
        status = STATUS_TEXT.get(item.status, item.status)
        # A score from the title and the place alone: the person should know the posting's text was not read.
        if item.score is not None and not item.job.description.strip():
            status = ", ".join(part for part in (status, "no text") if part)
        return (Text(mark, style="bold green"), score, can_apply, item.job.company, item.job.title, item.job.location, status)

    def refresh_jobs(self) -> None:
        """The list, best first. The cursor stays on the job it was on."""
        table = self.query_one(DataTable)
        current = self.current_id()
        table.clear()
        for item in self.session.store.all():
            table.add_row(*self.row(item), key=item.job.id)
        if current and current in {key.value for key in table.rows}:
            table.move_cursor(row=table.get_row_index(current))

    def current_id(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def current(self) -> Stored | None:
        job_id = self.current_id()
        return self.session.store.get(job_id) if job_id else None

    # The flow: key, resume, find, judge.

    @work(exclusive=True, group="flow")
    async def start(self) -> None:
        self.refresh_jobs()
        session = self.session
        try:
            if session.needs_key():
                key = await self.push_screen_wait(TextPrompt("Your OrcaRouter API key (from orcarouter.ai). It stays on this computer.", placeholder="sk-orca-...", password=True))
                if not key:
                    self.say("AIHawk needs a model key to read your resume and judge jobs. Quit and start again when you have one.")
                    return
                session.save_key(key)
            if self.resume or session.needs_resume():
                path = self.resume
                while path is None:
                    answer = await self.push_screen_wait(TextPrompt("Where is your resume? PDF, Word or text.", placeholder="C:/Users/you/resume.pdf"))
                    if not answer:
                        self.say("Press r when you have a resume to give me.")
                        return
                    path = Path(answer.strip("\"' ")).expanduser()
                    if not path.is_file():
                        self.say(f"No file at {path}.")
                        path = None
                self.resume = None
                self.say(f"Reading {path.name}...")
                self.show_heading("reading your resume")
                await session.load_resume(path)
                self.show_heading()
                self.say(f"Looking for: {session.config.filters.titles()}")
                self.say(f"In: {', '.join(session.config.filters.locations)}")
            self.show_heading("searching")
            await session.find(self.say)
            self.refresh_jobs()
            await self.judge()
        except (ConfigError, DatabaseError) as error:
            self.say(str(error))
        except Exception as error:  # noqa: BLE001  The screen stays up and says what went wrong, instead of a traceback over it.
            self.say(f"Something failed: {error}")
        finally:
            self.show_heading()

    async def judge(self) -> None:
        session = self.session
        await session.funnel(self.say, self.show_heading)
        self.refresh_jobs()
        items = session.store.with_status("shortlisted")
        if not items:
            self.say("Every posting found so far has been read.")
            return
        done = 0

        def scored() -> None:
            nonlocal done
            done += 1
            self.show_heading(f"scoring {done}/{len(items)}")
            self.refresh_jobs()

        self.show_heading(f"scoring 0/{len(items)}")
        self.say(f"jevos picked {len(items)} postings to score. Press m for the next batch when they are done.")
        await session.score(items, scored, self.say)

    # The keys.

    def action_toggle(self) -> None:
        item = self.current()
        if item is None:
            return
        if item.job.id in self.selected:
            self.selected.discard(item.job.id)
        else:
            self.selected.add(item.job.id)
        self.query_one(DataTable).update_cell(item.job.id, "mark", self.row(item)[0])
        self.query_one(DataTable).action_cursor_down()

    @on(DataTable.RowSelected)
    def row_chosen(self) -> None:
        self.action_details()

    def action_details(self) -> None:
        item = self.current()
        if item:
            self.push_screen(JobDetails(item))

    def action_open_link(self) -> None:
        item = self.current()
        if item:
            webbrowser.open(item.job.url)

    def action_more(self) -> None:
        self.more_jobs()

    @work(exclusive=True, group="flow")
    async def more_jobs(self) -> None:
        try:
            await self.judge()
        finally:
            self.show_heading()

    def action_new_resume(self) -> None:
        self.new_resume()

    @work(exclusive=True, group="flow")
    async def new_resume(self) -> None:
        answer = await self.push_screen_wait(TextPrompt("Where is your resume? PDF, Word or text.", placeholder="C:/Users/you/resume.pdf"))
        if answer:
            self.resume = Path(answer.strip("\"' ")).expanduser()
            self.start()

    def action_apply(self) -> None:
        if self.signing_in:
            self.say("One moment: the browser is open for signing in.")
            return
        if self.applying():
            self.say("One moment: still applying.")
            return
        self.apply_selected()

    def applying(self) -> bool:
        """Whether an application holds the browser: one running, or one just started and not yet running."""
        return self.busy or any(worker.group == "apply" and not worker.is_finished for worker in self.workers)

    def action_sign_in(self) -> None:
        # One browser per profile: an application and the sign-in window never open it at the same time.
        if self.signing_in:
            self.say("The browser is already open for signing in.")
        elif self.applying():
            self.say("Not now: an application is using the browser. Wait until it is done.")
        else:
            self.sign_in()

    @work(group="sign_in")
    async def sign_in(self) -> None:
        self.signing_in = True
        self.say("Opening the browser for you to sign in.")

        async def wait() -> None:
            await self.push_screen_wait(SignIn())

        try:
            await self.session.sign_in(wait)
            self.say("The browser is closed. Applications use the same browser profile, so the sign-ins carry over.")
        except Exception as error:  # noqa: BLE001  The screen stays up and says what went wrong, as in start.
            self.say(f"The sign-in browser did not open: {error}")
        finally:
            self.signing_in = False

    @work(exclusive=True, group="apply")
    async def apply_selected(self) -> None:
        store = self.session.store
        chosen = [item for item in (store.get(job_id) for job_id in self.selected) if item]
        if not chosen:
            current = self.current()
            chosen = [current] if current else []
        chosen.sort(key=lambda item: -(item.score or 0))
        self.busy = True
        try:
            for index, item in enumerate(chosen, 1):
                self.show_heading(f"applying {index}/{len(chosen)}")
                await self.apply_one(item)
                self.selected.discard(item.job.id)
                self.refresh_jobs()
        finally:
            self.busy = False
            self.show_heading()

    async def apply_one(self, item: Stored) -> None:
        name = f"{item.job.company}: {item.job.title}"
        if item.status in SUBMITTED:
            self.say(f"{name}: already sent, skipped.")
            return
        if self.session.plugin(item) is None:
            self.say(f"{name}: AIHawk cannot fill this site yet. Press o to open it and apply by hand.")
            return
        self.say(f"Applying: {name}")

        async def confirm(summary: str) -> bool:
            return await self.app.push_screen_wait(ConfirmSend(name, summary))

        async def ask(question: str) -> str | None:
            return await self.app.push_screen_wait(TextPrompt(f"{item.job.company} asks: {question}"))

        try:
            outcome = await self.session.apply(item, None if self.auto_submit else confirm, ask, self.say)
        except ConfigError as error:
            self.say(str(error))
            return
        self.say(f"{STATUS_TEXT.get(outcome.status, outcome.status) or outcome.status}: {name}")
