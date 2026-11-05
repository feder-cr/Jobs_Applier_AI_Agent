"""AIHawk in the terminal: give it your resume, get the jobs that fit, select them and apply."""

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
from aihawk.llm import explain, refused
from aihawk.session import Session
from aihawk.store import SUBMITTED, Sent, Stored, said, sent_word

STATUS_TEXT = {
    "new": "",
    "scored": "",
    "shortlisted": "",
    "passed_over": "passed over",
    "submitted": "sent",
    "unconfirmed": "clicked, not confirmed",
    "refused": "refused by the form",
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
            yield Static("Enter to answer · Esc to skip · Ctrl+C quits", classes="hint")

    @on(Input.Submitted)
    def answered(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_skip(self) -> None:
        self.dismiss(None)


class SendBatch(ModalScreen[bool]):
    """The one question before anything is sent: these jobs, now. Enter sends them all, Escape cancels."""

    BINDINGS: ClassVar = [Binding("enter", "answer(True)", "Send them"), Binding("escape", "answer(False)", "Cancel")]

    def __init__(self, items: list[Stored]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        count = len(self.items)
        lines = [f"{index}. {item.job.company}: {item.job.title}" for index, item in enumerate(self.items, 1)]
        with Vertical(id="dialog"):
            yield Static(f"Send {count} application{'' if count == 1 else 's'} now?", id="question", markup=False)
            yield Static("They will be sent now, in the browser window. Enter sends them all; Escape cancels; Ctrl+C quits.", classes="hint")
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

    def action_answer(self, send: bool) -> None:
        self.dismiss(send)


def short(text: str) -> str:
    return " ".join(text.split())[:110]


class SentList(ModalScreen[None]):
    """Every job tried, the newest first. Enter opens one in full."""

    BINDINGS: ClassVar = [Binding("escape,q,s", "close", "Back")]

    def __init__(self, items: list[Sent]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="wide"):
            yield Static("Sent", id="question", markup=False)
            yield DataTable(id="sent", cursor_type="row")
            yield Static("Enter opens a job in full · Esc goes back", classes="hint")
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (("When", 17), ("Company", 18), ("Role", 26), ("Status", 20), ("What the page said", 46)):
            table.add_column(label, key=label, width=width)
        for item in self.items:
            cells = (item.when, item.job.company, item.job.title, sent_word(item), short(said(item)))
            table.add_row(*(Text(cell) for cell in cells), key=item.job.id)
        table.focus()

    @on(DataTable.RowSelected)
    def chosen(self, event: DataTable.RowSelected) -> None:
        # Stopped here: the same event would reach the app and open the main list's job as well.
        event.stop()
        item = next(item for item in self.items if item.job.id == event.row_key.value)
        self.app.push_screen(SentDetails(item))

    def action_close(self) -> None:
        self.dismiss(None)


class SentDetails(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape,enter,q", "close", "Back")]

    def __init__(self, item: Sent) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        item, proof = self.item, self.item.proof
        lines = [
            item.job.title,
            f"{item.job.company} · {item.job.location}",
            "",
            f"{sent_word(item)} · {item.when}",
            f"Posting: {item.job.url}",
            f"Address after the send: {proof.url or '-'}",
            f"Screenshot: {proof.screenshot or '-'}",
            "",
            f"Filled in:\n{item.note or '-'}",
            "",
            f"What the page said: {proof.evidence or '-'}",
            "",
            proof.text or "The page's text was not read.",
        ]
        with Vertical(id="dialog"):
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)


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
            yield Static("Press Enter here when you are done. The window closes then. Ctrl+C quits.", classes="hint")

    def action_done(self) -> None:
        self.dismiss(None)


class AIHawk(App[None]):
    TITLE = "AIHawk"
    # The footer of 80 columns has room for the keys only; with the palette's key, it hid Quit.
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #heading { height: 1; padding: 0 1; background: $primary; color: $text; text-style: bold; }
    #jobs { height: 1fr; }
    #log { height: 7; border-top: solid $primary; padding: 0 1; }
    ModalScreen { align: center middle; background: $background 60%; }
    #dialog { width: 80%; max-width: 100; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #wide { width: 95%; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #sent { height: auto; max-height: 24; }
    #question { text-style: bold; margin-bottom: 1; }
    #summary { height: auto; max-height: 24; margin: 1 0; }
    .hint { color: $text-muted; }
    """
    BINDINGS: ClassVar = [
        Binding("space", "toggle", "Select"),
        Binding("enter", "details", "Details"),
        Binding("a", "apply", "Apply"),
        Binding("s", "sent", "Sent"),
        Binding("o", "open_link", "Open"),
        Binding("m", "more", "More"),
        Binding("r", "new_resume", "New resume"),
        Binding("l", "sign_in", "Sign in"),
        Binding("q", "quit", "Quit"),
        # Textual's own Ctrl+C only says "press ctrl+q", and an input under it copies: at any prompt Ctrl+C now leaves.
        Binding("ctrl+c", "quit", show=False, priority=True),
    ]

    def __init__(self, workspace: Path, resume: Path | None = None) -> None:
        super().__init__()
        self.session = Session(workspace)
        self.resume = resume
        self.selected: set[str] = set()
        self.busy = False
        self.signing_in = False

    def compose(self) -> ComposeResult:
        yield Static("AIHawk", id="heading", markup=False)
        yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
        # min_width=1: RichLog wraps at 78 columns by default, under its own scrollbar, so the last words were hidden.
        yield RichLog(id="log", wrap=True, min_width=1, markup=False)
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

    def failed(self, error: Exception) -> None:
        """Says what went wrong, in plain words. A key OrcaRouter refused is forgotten when it came from the file, and asked for again."""
        if not refused(error):
            self.say(explain(error))
            return
        self.say(self.session.refuse_key())
        if self.session.needs_key():
            self.start()

    def show_heading(self, progress: str = "") -> None:
        # A flow that ends while the app quits finds its screen gone: there is no heading left to write to.
        if not self.is_running:
            return
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
        # Text, because a string cell is read as markup, and a posting's words are a source's: "[/x] Engineer" would stop the screen.
        return (Text(mark, style="bold green"), score, can_apply, Text(item.job.company), Text(item.job.title), Text(item.job.location), status)

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
                    answer = await self.push_screen_wait(TextPrompt("Where is your resume? PDF, .docx or text.", placeholder="C:/Users/you/resume.pdf"))
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
            self.failed(error)
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
        self.say(f"Jev picked {len(items)} postings to score. Press m for the next batch when they are done.")
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
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
        finally:
            self.show_heading()

    def action_sent(self) -> None:
        self.push_screen(SentList(self.session.store.sent()))

    def action_new_resume(self) -> None:
        self.new_resume()

    @work(exclusive=True, group="flow")
    async def new_resume(self) -> None:
        answer = await self.push_screen_wait(TextPrompt("Where is your resume? PDF, .docx or text.", placeholder="C:/Users/you/resume.pdf"))
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
        ready = [item for item in chosen if self.can_send(item)]
        if not ready:
            return
        if not await self.push_screen_wait(SendBatch(ready)):
            self.say("Nothing was sent.")
            return
        self.busy = True
        self.show_heading("applying")
        try:
            await self.session.send(ready, self.answer, self.say, self.refresh_jobs)
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start; the batch stops where it failed.
            self.failed(error)
        finally:
            self.busy = False
            self.selected -= {item.job.id for item in chosen}
            self.refresh_jobs()
            self.show_heading()

    def can_send(self, item: Stored) -> bool:
        """Whether the job goes in the batch; one that cannot says why."""
        name = f"{item.job.company}: {item.job.title}"
        if item.status in SUBMITTED:
            self.say(f"{name}: already sent, skipped.")
        elif self.session.plugin(item) is None:
            self.say(f"{name}: AIHawk cannot fill this site yet. Press o to open it and apply by hand.")
        else:
            return True
        return False

    async def answer(self, question: str) -> str | None:
        return await self.push_screen_wait(TextPrompt(question))
