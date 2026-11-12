"""AIHawk in the terminal: give it your resume, get the jobs that fit, select them and apply."""

from __future__ import annotations

import asyncio
import math
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Input, Static

from aihawk import errors
from aihawk.apply import MAILBOXES, Outcome, mail_provider, why_not_sent
from aihawk.config import KEY_ENV, KEY_URL, ConfigError
from aihawk.database import DatabaseError
from aihawk.library import LibraryError, Stopped
from aihawk.llm import account_problem, error_name, explain, not_answering, refused
from aihawk.scoring import NotAnswering
from aihawk.screens import WIDE_ENOUGH, Confirm, JobDetails, Rows, Said, SendBatch, SentList, Settings, SignIn, StopSending, TextPrompt, clipped
from aihawk.session import Session
from aihawk.store import OWN_ANSWERS, SENT_MARKS, SENT_WORDS, SUBMITTED, Stored

SEARCH_SETTINGS = {"places", "exclude", "max_age_days"}


def whole_days(answer: str) -> str | None:
    """What is wrong with the days a person typed, said in the dialog; an empty answer goes back to the default."""
    return None if not answer or (answer.isdigit() and int(answer) > 0) else "Type a whole number of days, like 30."


# What each setting asks, and what makes an answer wrong.
SETTING_QUESTIONS = {
    "places": ("Where (cities or countries, separated by commas)", None),
    "exclude": ("Leave out (words in job titles, separated by commas)", None),
    "max_age_days": ("Posted within (days)", whole_days),
}
# Jobs on a page: all scored, sorted once, and never moved while the person reads them.
PAGE = 50
# Scored jobs kept in reserve beyond the pages made, a round of them: the next pages are there when the person turns.
AHEAD = 200
RESUME_QUESTION = "Where is your resume?"
RESUME_PLACEHOLDER = "Drag your resume file into this window, or type where it is"
MAIL_QUESTION = "Some job sites email a code to confirm it is you. Which email do you apply with?"
MAIL_CODE_QUESTION = "A site sent a code to your email. Which email is it, so AIHawk can read it?"
MAIL_HINT = "AIHawk opens that inbox in its browser only to read those codes · Enter · Esc later"
KEY_QUESTION = "Paste your OrcaRouter key"
KEY_HINT = (
    "AIHawk uses OrcaRouter, a paid AI service, to read your resume and fill the forms: a few cents per application.\n"
    f"Get a key at {KEY_URL.removeprefix('https://www.')}\n"
    "Ctrl+O opens it in your browser · It stays on this computer\n"
    "Enter saves · Esc later"
)
KEY_REFUSED = "OrcaRouter did not accept that key. Check it and paste it again."
NO_CREDIT_QUESTION = (
    "This key works, but its OrcaRouter account has no credit yet. AIHawk needs a few dollars there to read your resume and the jobs: "
    "add them at orcarouter.ai."
)
NO_CREDIT_AGAIN = "Still no credit on that account. AIHawk needs a few dollars there to read your resume and the jobs: add them at orcarouter.ai."
NO_CREDIT_HINT = "Enter checks again · Ctrl+O opens orcarouter.ai · Esc goes on without"
# What to press after a failure at the resume, whose file is remembered.
RESUME_REMEMBERED = "press r: your resume is remembered."
MAIL_LATER = "Mail left for later: e, then Your mail."
MAIL_NAMES = {"gmail": "Gmail", "outlook": "Outlook"}
RESUME_HINT = "PDF, .docx or text · Enter reads it · Esc later"
MAIL_UNREADABLE = "Only Gmail and Outlook can be read for now; the rest works without."
SENDING = "Applications are being sent: wait for them to finish."
NO_KEY = "No key yet. Press e, then OrcaRouter key."
NO_RESUME = "No resume yet. Press r and give the file."
NO_JOBS = "No jobs found for these places and days. Press e to change them."
# How long scoring waits after each request that got no answer, in seconds, before it asks again. After the last wait one more
# failure stops it: the person asks again with the right arrow.
RETRY_PAUSES = (15, 30, 60, 120)
# The speed of a download is read from the last half minute of it, and not before it has run this long.
SPEED_SECONDS = 30
SPEED_AFTER = 3
# The colour of a tried job's sign in the list: sent is green, sent but not confirmed is yellow, not sent is red.
MARK_COLOURS = {"submitted": "green", "unconfirmed": "yellow", "refused": "red", "blocked": "red", "failed": "red"}


class Pages(Rows):
    """The jobs' table, a page at a time. Only the side arrows turn pages: going down or up stays on the page, its last
    row and its first are where it ends."""

    BINDINGS: ClassVar = [Binding("right", "turn(1)", "Page", key_display="← →"), Binding("left", "turn(-1)", show=False)]

    def action_turn(self, step: int) -> None:
        self.app.turn(step)  # type: ignore[attr-defined]


class AIHawk(App[None]):
    TITLE = "AIHawk"
    # The footer of 80 columns has room for the keys only; with the palette's key, it hid Quit.
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #top { height: 1; background: $primary; color: $text; }
    #heading { width: 1fr; padding: 0 1; text-style: bold; }
    #count { width: auto; padding: 0 1; }
    #jobs { height: 1fr; }
    #log { height: 4; border-top: solid $primary; padding: 0 1; scrollbar-size-vertical: 0; }
    ModalScreen { align: center middle; background: $background 60%; }
    #dialog { width: 80%; max-width: 100; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #wide { width: 95%; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #sent, #settings { height: auto; max-height: 24; }
    #question { text-style: bold; margin-bottom: 1; }
    #summary { height: auto; max-height: 24; margin: 1 0; }
    #reader { width: 90%; max-width: 110; height: 90%; padding: 1 2; background: $surface; border: thick $primary; }
    #reader #question { margin-bottom: 0; }
    #text { height: 1fr; margin: 1 0; }
    .hint { color: $text-muted; }
    #problem { color: $error; }
    """
    BINDINGS: ClassVar = [
        # Space and Enter both select: the table's own Enter comes as RowSelected, below. i reads the job.
        Binding("space", "toggle", "Select"),
        Binding("i", "details", "Details"),
        Binding("a", "apply", "Apply"),
        Binding("s", "sent", "Sent"),
        Binding("o", "open_link", show=False),
        Binding("e", "settings", "Settings"),
        # r is in the footer only while there is no resume: it is the next step then, and a key that always shows is noise.
        Binding("r", "give_resume", "Resume"),
        Binding("r", "new_resume", show=False),
        Binding("q", "quit", "Quit"),
        # Textual's own Ctrl+C only says "press ctrl+q", and an input under it copies: at any prompt Ctrl+C now leaves.
        Binding("ctrl+c", "leave", show=False, priority=True),
    ]

    def __init__(self, workspace: Path, resume: Path | None = None) -> None:
        super().__init__()
        self.session = Session(workspace)
        self.resume = resume
        # The error of a resume that failed to be read, which the start has told already.
        self.reported: Exception | None = None
        # The file of a resume that failed for the account or the service, not for the file: r asks for it again with its path in.
        self.remembered: Path | None = None
        self.selected: set[str] = set()
        # The pages made so far, each its jobs' ids in the order shown; the page shown; and one asked for before it was ready.
        self.pages: list[list[str]] = []
        self.page = 0
        self.wanted: int | None = None
        # The chunks of the database on this computer, and of how many, while they come; and whether a search has to run
        # again over the chunks come since the last one.
        self.progress: tuple[int, int] | None = None
        self.more_found = False
        # The bytes of the jobs here and the bytes they come to, and when each count was told: the last half minute of them gives
        # the speed. Only one round of scoring runs at a time, whoever asks for it.
        self.bytes: tuple[int, int] = (0, 0)
        self.speed: deque[tuple[float, int]] = deque()
        self.scoring = asyncio.Lock()
        # Whether the person had to wait for the jobs, and was told they are all here; whether the first job was shown.
        self.waited = False
        self.told_ready = False
        self.first_job_shown = False
        # What the log says about scoring that did not go through, which the count of the download must not cover.
        self.stuck = ""
        self.signing_in = False
        # Whether the person left the mail for later in this run: a site's code is asked about once, not at every application.
        self.mail_left = False
        # Why the key is being asked for again, for the question to say; and whether the start stopped for want of one.
        self.key_problem = ""
        self.sending = ""
        self.waiting_for_key = False
        self.restart = False
        self.columns_for = 0
        self.with_location = True
        # The room each of Company, Role and Location has, for the ellipsis where a word is cut.
        self.word_widths: list[int] = [20, 40, 20]

    def compose(self) -> ComposeResult:
        # What is being looked for, and on the right, out of the way, how many jobs the list holds: nothing else about
        # the search is said, the person did not ask how big the database is.
        with Horizontal(id="top"):
            yield Static("AIHawk", id="heading", markup=False)
            yield Static("", id="count", markup=False)
        yield Pages(id="jobs", cursor_type="row", zebra_stripes=True)
        # min_width=1: RichLog wraps at 78 columns by default, under its own scrollbar, so the last words were hidden.
        yield Said(id="log", wrap=True, min_width=1, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).focus()
        self.show_heading()
        self.session.telemetry.started()
        self.start()

    def on_resize(self, event: events.Resize) -> None:
        # The event's size: the app's own still says the old one here, and the columns were cut for the screen before.
        self.fit_columns(event.size.width)

    def fit_columns(self, width: int | None = None) -> None:
        """The columns for this screen's width: all of them from WIDE_ENOUGH up, and below it no location, which the details show."""
        width = width or self.size.width
        if width == self.columns_for:
            return
        self.columns_for = width
        current = self.current_id()
        table = self.query_one(DataTable)
        table.clear(columns=True)
        self.with_location = width >= WIDE_ENOUGH
        # A column's padding is 2, and the scrollbar takes 2.
        left = width - 2 - 2 * (5 if self.with_location else 4) - 2 - 5
        words = [("Company", left // 4), ("Role", left // 2), ("Location", left // 4)] if self.with_location else [("Company", left // 3), ("Role", left - left // 3)]
        self.word_widths = [size for _, size in words]
        for label, size in (("", 2), ("Score", 5), *words):
            table.add_column(label, key=label or "mark", width=size)
        self.refresh_jobs(current)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # While there is no resume the footer names r, the next step, in place of the keys that act on a list there is not:
        # the other r, the one that always works, is not listed. Both keep working.
        if action == "give_resume":
            return self.session.needs_resume()
        if action in ("toggle", "details", "apply"):
            return not self.session.needs_resume()
        return True

    def on_unmount(self) -> None:
        # Whether the person searched before closing, and where the start was: who leaves, and at which step.
        self.session.telemetry.closed()
        self.session.close()

    # What the person sees.

    def say(self, line: str, again: bool = False, replace: bool = False) -> None:
        """A line in the log. With `again`, it takes the place of the line before it when that was one too: a state that changes
        is one line, not a list. With `replace`, it ends such a state: it takes its place and stays."""
        self.query_one(Said).say(line, again, replace)

    def failed(self, error: Exception, then: str = "", step: str = "press → to go on.") -> None:
        """Says what went wrong, in plain words, and `then`, the next step; for an account that must be mended first, `step` is
        what to press after. A key the AI service refused is forgotten when it came from the file, and asked for again."""
        if not refused(error):
            self.say(f"{errors.explained(error, step)}{then}")
            return
        line = self.session.refuse_key()
        self.say(line)
        if self.session.needs_key():
            # Said in the key question that comes next, which covers the log.
            self.key_problem = line
            self.start()

    def show_heading(self) -> None:
        # A flow that ends while the app quits finds its screen gone: there is no heading left to write to.
        if not self.is_running:
            return
        self.refresh_bindings()
        config = self.session.config
        wanted = config.filters.titles() or "no resume yet"
        self.query_one("#heading", Static).update(" · ".join(part for part in ("AIHawk", wanted, self.places()) if part))

    def places(self) -> str:
        """Where the search looks, for the heading: a city once under its variants (Milan, Milano) and the country after it."""
        filters = self.session.config.filters
        shown: list[str] = []
        for place in dict.fromkeys(filters.locations):
            if not any(known.lower().startswith(place.lower()) or place.lower().startswith(known.lower()) for known in shown):
                shown.append(place)
        return ", ".join(shown[:3])

    def row(self, item: Stored) -> tuple[Text | str, ...]:
        # One sign, or none: selected, or how it went when tried. Why is in the applications, on s.
        selected = item.job.id in self.selected
        mark = Text("●" if selected else SENT_MARKS.get(item.status, ""), style="bold " + ("green" if selected else MARK_COLOURS.get(item.status, "green")))
        score = f"{item.score:.1f}" if item.score is not None else "-"
        # Text, because a string cell is read as markup, and a posting's words are a source's: "[/x] Engineer" would stop the screen.
        words = (item.job.company, item.job.title, *([item.job.location] if self.with_location else []))
        return (mark, score, *(clipped(word, width) for word, width in zip(words, self.word_widths, strict=False)))

    def refresh_jobs(self, current: str | None = None) -> None:
        """The page shown, as it was made: sorted once, by Jev's score, when all of it was scored. A job tried since
        shows its sign, and nothing moves. The cursor stays on the job it was on."""
        table = self.query_one(DataTable)
        current = current or self.current_id()
        items = self.session.store.all()
        by_id = {item.job.id: item for item in items}
        page = [by_id[job_id] for job_id in self.pages[self.page] if job_id in by_id] if self.pages else []
        table.clear()
        for item in page:
            table.add_row(*self.row(item), key=item.job.id)
        self.show_count()
        if page and not self.first_job_shown:
            # The moment there is something to look at, for how long a person waits to see their first job.
            self.first_job_shown = True
            self.session.telemetry.step("first_jobs")
        ids = [item.job.id for item in page]
        if current in ids:
            table.move_cursor(row=ids.index(current))

    def show_count(self) -> None:
        """On the right of the heading: the page, how many jobs AIHawk looks through, and how many are selected."""
        # The whole database's jobs, not the search's: what AIHawk looks through for the person.
        total = self.session.database_jobs
        found = f"{total:,} job" + ("" if total == 1 else "s") if total else ""
        chosen = f"{len(self.selected)} selected" if self.selected else ""
        self.query_one("#count", Static).update(" · ".join(part for part in (f"Page {self.page + 1}" if self.pages else "", found, chosen) if part))

    def current_id(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def current(self) -> Stored | None:
        job_id = self.current_id()
        return self.session.store.get(job_id) if job_id else None

    # The flow: key, resume, find, score.

    @work(exclusive=True, group="flow")
    async def start(self) -> None:
        # A new search is a new list: its pages are made again.
        self.pages, self.page, self.wanted, self.stuck = [], 0, None, ""
        self.refresh_jobs()
        session = self.session
        try:
            if session.needs_key() and not await self.ask_key():
                return
            if (self.resume or session.needs_resume()) and not await self.read_resume():
                return
            # The jobs scored before, by an earlier search: the first pages, sorted as they are. While the jobs still come
            # there is the one page, the best so far, which each round of scoring makes again.
            scored = [item.job.id for item in session.store.all() if item.score is not None]
            complete = session.library.complete()
            if complete:
                self.pages = [scored[start : start + PAGE] for start in range(0, len(scored), PAGE)]
            else:
                self.pages = [scored[:PAGE]] if scored else []
            self.refresh_jobs()
            # The jobs come in the background, a chunk at a time, the newest first. What is here is searched as soon as there is
            # something, and again after each chunk.
            self.bring_jobs()
            if not complete:
                session.telemetry.step("jobs_downloading")
                self.waited = True
                self.show_progress()
            else:
                session.bring_engine()
            if not session.library.has_postings():
                return
            await session.find()
            await self.score()
        except (ConfigError, DatabaseError) as error:
            self.start_failed(error)
            self.say(f"{error}{self.resume_next()}")
        except Exception as error:  # noqa: BLE001  The screen stays up and says what went wrong, instead of a traceback over it.
            self.start_failed(error)
            if self.remembered and account_problem(error):
                self.failed(error, step=RESUME_REMEMBERED)
            else:
                self.failed(error, self.resume_next())
        finally:
            self.show_heading()

    def resume_next(self) -> str:
        """What to press when a start stopped for want of a resume: the file is given again with r, and when it is remembered
        the question has it already."""
        if self.remembered:
            return f" {RESUME_REMEMBERED.capitalize()}"
        return " Press r and give the file again." if self.session.needs_resume() else ""

    def start_failed(self, error: Exception) -> None:
        """A start that ended in an error, told once: a resume that could not be read said so already, with its kind."""
        if error is not self.reported:
            self.session.telemetry.step("start_error", error=error_name(error))

    async def ask_key(self) -> bool:
        """The key asked for and kept; False when the person has none for now."""
        telemetry = self.session.telemetry
        telemetry.step("key_asked")
        key = await self.take_key()
        if not key:
            telemetry.step("key_missing")
            self.say(NO_KEY, again=True)
            self.waiting_for_key = True
            return False
        self.session.save_key(key)
        telemetry.step("key_saved")
        await self.check_credit(key)
        return True

    async def check_credit(self, key: str) -> None:
        """A new OrcaRouter account has a key that works and no credit, and the first call, reading the resume, would be refused.
        Said before it: Enter looks again, Esc goes on without. Nothing is said when the service does not say."""
        telemetry = self.session.telemetry
        if not await self.session.has_no_credit(key):
            return
        telemetry.step("no_credit_asked")
        question = NO_CREDIT_QUESTION
        while await self.push_screen_wait(Confirm(question, NO_CREDIT_HINT, link=KEY_URL)):
            self.say("Looking for the credit…", again=True)
            if not await self.session.has_no_credit(key):
                telemetry.step("credit_added")
                self.say("The credit is there.", again=True)
                return
            telemetry.step("no_credit_checked")
            question = NO_CREDIT_AGAIN
        telemetry.step("no_credit_skipped")

    async def take_key(self) -> str | None:
        """A key the person pasted and the service accepts, or None when they leave the question. A key it refuses is asked for
        again, with why in the question. One it cannot answer for, with no connection, is kept: the check never blocks."""
        while True:
            key = await self.key_prompt()
            if not key:
                return None
            self.say("Checking the key…", again=True)
            if await self.session.key_accepted(key):
                return key
            self.key_problem = KEY_REFUSED

    async def read_resume(self) -> bool:
        """The resume given on the command line or asked for, until it is a file, and read; False when the person has none for now."""
        telemetry = self.session.telemetry
        path = self.resume
        if path is None:
            telemetry.step("resume_asked")
        while path is None:
            path = await self.resume_prompt()
            if path is None:
                telemetry.step("resume_missing")
                self.say(NO_RESUME, again=True)
                return False
            if not path.is_file():
                telemetry.step("resume_not_found")
                self.say("No file at that path.")
                path = None
        self.resume = None
        self.say("Reading your resume: about half a minute…", again=True)
        try:
            await self.session.load_resume(path)
        except Exception as error:
            telemetry.step("resume_failed", error=error_name(error), kind=path.suffix.lower())
            self.reported = error
            # A file that is fine, and a key, an account or a service that was not: the same file is tried again with one key press.
            self.remembered = path if account_problem(error) or not_answering(error) or refused(error) else None
            raise
        self.remembered = None
        telemetry.step("resume_read", kind=path.suffix.lower())
        # A new resume is a new search: the jobs of the old one that nobody applied to give way to the new one's.
        self.forget_untried()
        self.show_heading()
        return True

    def forget_untried(self) -> None:
        """The jobs not applied to are dropped, so the list is the new search's alone; what was sent stays."""
        self.session.store.forget_untried()
        self.selected &= {item.job.id for item in self.session.store.all()}

    async def key_prompt(self) -> str | None:
        problem, self.key_problem = self.key_problem, ""
        return await self.push_screen_wait(TextPrompt(KEY_QUESTION, placeholder="sk-orca-...", password=True, hint=KEY_HINT, problem=problem, link=KEY_URL))

    async def resume_prompt(self) -> Path | None:
        """The file the person names, as a path; None when they name none."""
        def missing(answer: str) -> str | None:
            # In the dialog, which stays open, so that the person sees it: the log is behind it.
            if answer and not self.as_path(answer).is_file():
                self.session.telemetry.step("resume_not_found")
                return "No file at that path."
            return None

        answer = await self.push_screen_wait(
            TextPrompt(RESUME_QUESTION, placeholder=RESUME_PLACEHOLDER, hint=RESUME_HINT, check=missing, value=str(self.remembered) if self.remembered else None)
        )
        return self.as_path(answer) if answer else None

    @staticmethod
    def as_path(answer: str) -> Path:
        """The file an answer names: a file dragged into the window comes with quotes round its path."""
        return Path(answer.strip("\"' ")).expanduser()

    async def score(self) -> None:
        """Pages, PAGE jobs each, up to the one after the page shown, and a round of scored jobs no page holds yet in
        reserve: Jev reads the next round while the person reads, so a page they turn to is there. A round takes Jev a few
        seconds; until the first jobs are on this computer, the line below counts the chunks coming. A round the AI service
        does not answer is asked again after a pause that grows, and after the last one the person is told how to ask."""
        async with self.scoring:
            await self.read_rounds()

    async def read_rounds(self) -> None:
        """The rounds themselves, one at a time. While the jobs still come every job found is read, the best first, and the
        page shown is made again after each round; when they are all here, only as many as the pages need."""
        session = self.session
        store = session.store
        failures = 0
        while True:
            self.make_pages()
            complete = session.library.complete()
            if not store.with_status("new") or (complete and len(self.pages) >= self.page + 2 and len(self.spare()) >= AHEAD):
                break
            self.show_progress()
            try:
                await session.read(self.say)
            except NotAnswering:
                if failures == len(RETRY_PAUSES):
                    self.stuck = "Still no answer. Check your connection, then press → to try again."
                    self.say(self.stuck, again=True)
                    return
                self.stuck = f"The AI service is not answering. Trying again in {RETRY_PAUSES[failures]} seconds…"
                self.say(self.stuck, again=True)
                await asyncio.sleep(RETRY_PAUSES[failures])
                failures += 1
            except Exception as error:
                if not account_problem(error):
                    raise
                # Asking again changes nothing until the account does: the jobs stay as they were, and the person is told what to do.
                self.stuck = explain(error)
                self.say(self.stuck, again=True)
                return
            else:
                failures = 0
                self.stuck = ""
        self.make_pages()
        self.check_empty()

    async def read_new(self) -> None:
        """The jobs a search just found are read, unless an application holds the browser: it ends by asking again."""
        if self.applying():
            return
        try:
            await self.score()
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)

    def check_empty(self) -> None:
        """Says so when the search is over and found no job: not while the jobs are still coming, or some wait to be read."""
        if self.pages or self.session.store.with_status("new") or not self.session.library.complete():
            return
        self.say(NO_JOBS, again=True)

    @work(exclusive=True, group="library")
    async def bring_jobs(self) -> None:
        """The database brought to this computer, the newest jobs first; each chunk that comes is searched."""
        try:
            await self.session.sync(self.chunks_came, self.found_more, self.bytes_came)
        except Stopped:
            pass
        except (LibraryError, DatabaseError) as error:
            self.say(str(error))
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
        finally:
            self.progress = None
            if self.is_running:
                self.refresh_jobs()

    def chunks_came(self, done: int, size: int) -> None:
        self.progress = (done, size)
        if not self.is_running:
            return
        if self.session.library.complete() and self.waited and not self.told_ready:
            # The count of the wait gives way: the person sees it is over.
            self.told_ready = True
            self.session.telemetry.step("jobs_ready", chunks=done)
            self.query_one(Said).say("All the jobs are here.", again=True)
            self.session.bring_engine()
        else:
            self.show_progress()

    def bytes_came(self, done: int, total: int) -> None:
        """How many bytes of the jobs are here, of how many. Told at least every two seconds while a chunk comes."""
        now = time.monotonic()
        self.bytes = (done, total)
        self.speed.append((now, done))
        while len(self.speed) > 2 and now - self.speed[0][0] > SPEED_SECONDS:
            self.speed.popleft()
        if self.is_running and not self.session.library.complete():
            self.show_progress()

    def download_line(self) -> str:
        """The wait for the jobs on one line: how many bytes are here of how many, about how long is left once the speed is
        known, and whether there is something to look at already."""
        done, total = self.bytes
        below = "The first ones are already below." if self.pages and self.pages[0] else "The first ones appear in a moment."
        if not total:
            return f"Getting the jobs, first time only: {int(self.session.library.readiness() * 100)}%. {below}"
        line = f"Getting the jobs, first time only: {done / 1e6:.0f} of {total / 1e6:.0f} MB"
        start, first = self.speed[0] if self.speed else (0.0, 0)
        end, last = self.speed[-1] if self.speed else (0.0, 0)
        if len(self.speed) > 1 and end - start >= SPEED_AFTER and last > first:
            left = (total - done) / ((last - first) / (end - start))
            line += ", less than a minute left" if left <= 60 else f", about {math.ceil(left / 60)} minutes left"
        return f"{line}. {below}"

    def found_more(self) -> None:
        """New jobs on this computer: the search runs again over them, once for however many came while it ran. As soon as
        the first chunk is here, and after each one."""
        if not self.session.library.has_postings():
            return
        self.more_found = True
        if self.is_running and not any(worker.group == "find" and worker.is_running for worker in self.workers):
            self.find_again()

    @work(group="find")
    async def find_again(self) -> None:
        try:
            while self.more_found and self.is_running:
                self.more_found = False
                await self.session.find()
                await self.read_new()
                self.check_empty()
        except DatabaseError as error:
            self.say(str(error))

    def spare(self) -> list[str]:
        """The scored jobs no page holds yet, the best first."""
        paged = {job_id for page in self.pages for job_id in page}
        return [item.job.id for item in self.session.store.all() if item.score is not None and item.job.id not in paged]

    def make_pages(self) -> None:
        """Pages from the jobs scored and not on one, up to the one after the page shown. A page is PAGE of them, sorted
        once; a shorter one only when Jev has nothing left to read. A page the person waited for opens."""
        if not self.session.library.complete():
            self.live_page()
            return
        said = self.query_one(Said)
        while len(self.pages) < self.page + 2:
            spare = self.spare()
            if not spare or (len(spare) < PAGE and self.session.store.with_status("new")):
                return
            self.pages.append(spare[:PAGE])
            number = len(self.pages)
            if self.wanted == number - 1:
                self.wanted = None
                self.page = number - 1
                self.query_one(DataTable).move_cursor(row=0)
            if said.counting or number == 1:
                said.say(f"Page {number} ready.", again=True)
            self.refresh_jobs()

    def live_page(self) -> None:
        """While the jobs still come there is the one page, the best of what is scored so far, and it is made again after each
        round: the rows from the top to the cursor are kept as they are, so what the person is reading does not move; a job they
        selected stays; the rest of the page is the best of the others. What does not fit is in the reserve, for the later
        pages. With the cursor on the first row nothing is kept, and the page is just the best so far."""
        ranked = [item.job.id for item in self.session.store.all() if item.score is not None]
        if not ranked:
            return
        rank = {job_id: number for number, job_id in enumerate(ranked)}
        shown = self.pages[0] if self.pages else []
        table = self.query_one(DataTable)
        row = table.cursor_row if table.row_count else 0
        held = [job_id for job_id in shown[: row + 1] if job_id in rank] if row > 0 else []
        chosen = [job_id for job_id in self.selected if job_id in rank and job_id not in held]
        taken = {*held, *chosen}
        room = max(PAGE - len(held) - len(chosen), 0)
        page = [*held, *sorted([*chosen, *[job_id for job_id in ranked if job_id not in taken][:room]], key=rank.__getitem__)]
        if self.wanted == 0:
            self.wanted = None
        if page != shown:
            self.pages = [page]
            self.refresh_jobs()
            if not row:
                # A person who has not moved down the page is reading from its top: that is the best job, whichever it is now.
                table.move_cursor(row=0)
            # The line of the wait says there is something to look at.
            self.show_progress()

    def show_progress(self) -> None:
        """The one line that says what is being waited for: the jobs coming, with how far and how long, or the scoring of them,
        which only the first page or one turned to waits on. A scoring that did not go through keeps its own line."""
        said = self.query_one(Said)
        if not self.session.library.complete():
            if not self.stuck:
                said.say(self.download_line(), again=True)
            return
        if self.wanted is None and len(self.pages) > self.page:
            return
        said.say("Reading the jobs against your resume…", again=True)

    def turn(self, step: int) -> None:
        """To the page before or after, on its first row. A page not ready yet opens once it is; the line below says how
        far it is."""
        target = self.page + step
        if target < 0:
            return
        if not self.pages:
            # No page yet: the arrow asks for the first, which is what the person who sees an empty list is trying.
            target = 0
        elif target >= len(self.pages) and not self.session.library.complete():
            self.say("More pages when all the jobs are here.", again=True)
            return
        if target < len(self.pages):
            self.page = target
            self.wanted = None
            self.refresh_jobs()
            table = self.query_one(DataTable)
            table.move_cursor(row=0)
            self.keep_ahead()
            return
        nothing_left = not self.session.store.with_status("new") and not self.spare()
        # Unless more are on their way: the page opens when the chunks still coming bring its jobs.
        if nothing_left and self.session.library.complete():
            self.say("No more jobs for this search." if self.pages else NO_JOBS, again=not self.pages)
            return
        self.wanted = target
        self.show_progress()
        self.keep_ahead()

    # The keys.

    def action_toggle(self) -> None:
        item = self.current()
        if item is None:
            return
        if item.job.id in self.selected:
            self.selected.discard(item.job.id)
        else:
            self.selected.add(item.job.id)
        table = self.query_one(DataTable)
        table.update_cell(item.job.id, "mark", self.row(item)[0])
        self.show_count()
        # Down a row, and no further: on the page's last row, selecting is not turning the page.
        table.move_cursor(row=min(table.cursor_row + 1, table.row_count - 1))

    @on(DataTable.RowSelected)
    def row_chosen(self) -> None:
        self.action_toggle()

    def action_details(self) -> None:
        """The job under the cursor, as its posting says it."""
        item = self.current()
        if not item:
            return
        if not item.job.description.strip():
            # A job listed before its text was kept with it: the text is in the database on this computer.
            text = self.session.library.descriptions([item.job.url])[0]
            if text:
                self.session.store.set_description(item.job.id, text)
                item = self.session.store.get(item.job.id) or item
        self.push_screen(JobDetails(item))

    def action_open_link(self) -> None:
        item = self.current()
        if item:
            webbrowser.open(item.job.url)

    def keep_ahead(self) -> None:
        """The pages and the round in reserve are made, unless a flow is already at it, or an application holds the
        browser. A page turn still queued while the app quits finds the store closed: there is nothing left to score for."""
        if not self.is_running or self.applying() or any(worker.group == "flow" and worker.is_running for worker in self.workers):
            return
        if self.session.store.with_status("new") or self.spare():
            self.more_jobs()

    @work(exclusive=True, group="flow")
    async def more_jobs(self) -> None:
        try:
            await self.score()
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
        finally:
            self.show_heading()

    def action_sent(self) -> None:
        if self.applying():
            self.say(SENDING)
            return
        sent = self.session.store.sent()
        if sent:
            self.push_screen(SentList(sent))
        else:
            self.say("No applications yet. Select jobs with space, then press a.")

    def setting_rows(self) -> list[tuple[str, str, str]]:
        # What to look for is not here: the titles and their graph come from the resume. A new resume changes them.
        config = self.session.config
        return [
            ("places", "Where", ", ".join(config.filters.locations)),
            ("exclude", "Leave out", ", ".join(config.filters.exclude)),
            ("max_age_days", "Posted within (days)", str(config.max_age_days)),
            ("resume", "Resume", (self.session.resume_name() or "loaded") if config.cv.is_file() else "none yet"),
            ("answers", "Your answers", str(len(self.own_answers()))),
            ("mail", "Your mail", self.mail_words()),
            ("key", "OrcaRouter key", "saved" if config.api_key() else "none yet"),
            # The models are not here: a person does not choose one, and aihawk.yaml still changes them.
            ("folder", "Your files", str(config.workspace)),
        ]

    def own_answers(self) -> dict[str, str]:
        """The answers that are the person's: AIHawk's own, a password it made and where the mail is, are not for them to read or change."""
        return {name: value for name, value in self.session.store.answers().items() if name not in OWN_ANSWERS}

    def answer_rows(self) -> list[tuple[str, str, str]]:
        """What the forms are told about the person: the answers in the store, and a row to add one."""
        rows = [(f"answer:{name}", name.replace("_", " "), value) for name, value in self.own_answers().items()]
        return [*rows, ("answer:", "Add an answer", "name: value")]

    def action_settings(self) -> None:
        if not self.busy():
            self.settings()

    @work(group="settings")
    async def settings(self) -> None:
        search_again = False
        while (choice := await self.push_screen_wait(Settings(self.setting_rows()))) is not None:
            if choice == "resume":
                self.new_resume()
                return
            if choice == "folder":
                webbrowser.open(self.session.config.workspace.as_uri())
            elif choice == "mail":
                self.mail_left = not await self.set_up_mail()
            elif choice == "answers":
                await self.change_answers()
            elif choice == "key":
                await self.change_key()
            else:
                now = next(now for key, _, now in self.setting_rows() if key == choice)
                question, check = SETTING_QUESTIONS[choice]
                answer = await self.push_screen_wait(TextPrompt(question, value=now, hint="Enter saves · Esc goes back", check=check))
                if answer is not None:
                    try:
                        self.session.change_setting(choice, answer)
                        search_again = search_again or choice in SEARCH_SETTINGS
                    except ConfigError as error:
                        self.say(str(error))
        self.show_heading()
        if search_again:
            self.say("Searching again.")
            self.forget_untried()
            self.start()
        elif self.restart:
            self.restart = False
            self.start()

    def mail_words(self) -> str:
        email, provider = self.session.mail()
        if provider in MAILBOXES:
            return f"{MAIL_NAMES[provider]}, {email}"
        return "not set up"

    async def set_up_mail(self, question: str = MAIL_QUESTION) -> bool:
        """The mailbox the job sites write to: its address, then the browser open on it for the person to sign in once, and the
        inbox checked. Esc at any point leaves it for later. Whether the inbox was reached."""
        session = self.session
        email, provider = session.mail()
        answer = await self.push_screen_wait(TextPrompt(question, value=email, placeholder="you@gmail.com", hint=MAIL_HINT))
        if not answer or "@" not in answer:
            session.set_mail(email, "later")
            self.say(MAIL_LATER)
            return False
        email = answer.strip()
        provider = mail_provider(email) or (provider if provider in MAILBOXES else "")
        if provider not in MAILBOXES:
            # A company's own address may still be a Gmail or an Outlook inbox: asked once, and left for later when it is not.
            choice = await self.push_screen_wait(TextPrompt(f"Where is {email}?", placeholder="gmail", hint="gmail or outlook · Enter · Esc later"))
            if choice is None:
                session.set_mail(email, "later")
                self.say(MAIL_LATER)
                return False
            provider = choice.strip().lower()
            if provider not in MAILBOXES:
                session.set_mail(email, "later")
                self.say(MAIL_UNREADABLE)
                return False
        session.set_mail(email, provider)
        name = MAIL_NAMES[provider]
        while True:
            self.signing_in = True
            done = True
            try:

                async def wait() -> None:
                    nonlocal done
                    done = await self.push_screen_wait(SignIn(f"The browser opened {name}. Sign in as {email}, then come back to this window and press Enter."))

                reached = await session.sign_in_mail(provider, wait)
            except Exception:  # noqa: BLE001  Said on the screen, as for the other browser openings.
                self.say("The browser did not open. Close other AIHawk windows and try again.")
                return False
            finally:
                self.signing_in = False
            if not done:
                self.say(MAIL_LATER)
                return False
            if reached:
                self.say(f"Signed in to {name}.")
                return True
            if not await self.push_screen_wait(Confirm(f"{name} still shows its sign-in page.", "Enter opens it again · Esc later")):
                self.say(MAIL_LATER)
                return False

    async def change_answers(self) -> None:
        """One answer per row. Enter edits it, an empty value removes it; the last row adds one as `name: value`."""
        store = self.session.store
        while (choice := await self.push_screen_wait(Settings(self.answer_rows(), title="Your answers"))) is not None:
            name = choice.removeprefix("answer:")
            if name:
                value = await self.push_screen_wait(TextPrompt(name.replace("_", " "), value=store.answers().get(name, ""), hint="Enter saves · empty removes it · Esc goes back"))
                if value is not None:
                    store.set_answer(name, value)
            else:
                line = await self.push_screen_wait(TextPrompt("A new answer, as name: value", placeholder="notice period: one month", hint="Enter saves · Esc goes back"))
                if line and ":" in line:
                    new_name, new_value = line.split(":", 1)
                    store.set_answer(new_name.strip().lower().replace(" ", "_"), new_value)
                elif line:
                    self.say("Write it as name: value.")

    async def change_key(self) -> None:
        config = self.session.config
        if config.key_from_environment():
            self.say(f"The key comes from the {KEY_ENV} variable, which wins over one typed here.")
            return
        key = await self.take_key()
        if key:
            self.session.save_key(key)
            await self.check_credit(key)
            if self.waiting_for_key:
                # The start stopped for want of a key: with one, it goes on with the resume and the rest.
                self.waiting_for_key = False
                self.restart = True

    def action_leave(self) -> None:
        """Ctrl+C: copies what is selected on the screen and stays, as everywhere else; with nothing selected it leaves."""
        focused = self.focused
        if isinstance(focused, Input) and not focused.password and focused.selected_text:
            focused.action_copy()
            return
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            self.notify("Copied.", timeout=2)
            return
        self.action_quit()

    def action_new_resume(self) -> None:
        self.new_resume()

    @work(exclusive=True, group="flow")
    async def new_resume(self) -> None:
        path = await self.resume_prompt()
        if path:
            self.resume = path
            self.start()
        else:
            self.say("No resume given." if self.session.needs_resume() else "Your resume was not changed.")

    def action_apply(self) -> None:
        if self.busy():
            return
        self.apply_selected()

    def busy(self) -> bool:
        """Whether the browser is held, which it says: applications being sent, or a sign-in the person is in the middle of."""
        if self.applying():
            self.say(SENDING)
        elif self.signing_in:
            self.say("Finish signing in first: the browser is open for it.")
        return self.applying() or self.signing_in

    def action_quit(self) -> None:  # type: ignore[override]
        """Leaves; but not while applications are being sent without asking once, since leaving stops them."""
        if self.applying() and not isinstance(self.screen, StopSending):
            self.push_screen(StopSending(), lambda quit: quit and self.exit())
        else:
            self.exit()

    def applying(self) -> bool:
        """Whether an application holds the browser: one running, or one just started and not yet running."""
        return any(worker.group == "apply" and not worker.is_finished for worker in self.workers)

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
        results = await self.send(ready)
        self.selected -= {item.job.id for item in chosen}
        self.refresh_jobs()
        # How each ended, the last try of each: a job sent again after the mail was signed in counts once.
        ended = {item.job.id: outcome for item, outcome in results}
        unread = [item for item, outcome in results if outcome.needs_mail]
        if unread:
            ended.update({item.job.id: outcome for item, outcome in await self.mail_then_retry(unread)})
        if ended:
            self.say(self.summary(list(ended.values())))
        # The applications used some of what lay ahead: fill it again, as a cursor move would.
        self.keep_ahead()

    @staticmethod
    def summary(outcomes: list[Outcome]) -> str:
        """One line for the batch: how many were sent, how many sent but not confirmed, how many not sent."""
        sent = sum(outcome.status == "submitted" for outcome in outcomes)
        unsure = sum(outcome.status == "unconfirmed" for outcome in outcomes)
        counts = ((sent, "sent"), (unsure, "sent but not confirmed"), (len(outcomes) - sent - unsure, "not sent"))
        parts = [f"{count} {words}" for count, words in counts if count]
        return f"{', '.join(parts)}. Press s to see {'why' if len(parts) > 1 or not sent else 'them'}."

    def started(self, number: int, total: int, item: Stored) -> None:
        """The line while a batch is sent: which one it is on, and that the person can watch the browser. It changes in place."""
        self.sending = f"Sending {number} of {total}: {item.job.company}. A few minutes each; you can watch the browser."
        self.say(self.sending, again=True)

    def waiting_note(self, line: str) -> None:
        """What an application waits for, in the line of the batch: the service asked it to slow down, or the browser is coming.
        An empty line is the work going on again."""
        self.say(line or self.sending, again=True)

    def ended(self, item: Stored, outcome: Outcome) -> None:
        """What came of one application, in a line, and the list's sign for it."""
        self.refresh_jobs()
        company = clipped(item.job.company, 40).plain
        if outcome.status in SUBMITTED:
            evidence = " ".join(outcome.proof.evidence.split())
            tail = f" {evidence[:100]}{'…' if len(evidence) > 100 else ''}" if evidence else ""
            self.say(f"{company}: {SENT_WORDS[outcome.status]}.{tail}", replace=True)
        else:
            self.say(f"{company}: not sent, {why_not_sent(outcome.reason) or 'something went wrong'}.", replace=True)

    async def send(self, items: list[Stored]) -> list[tuple[Stored, Outcome]]:
        """The applications, each with what came of it; a failure is said on the screen, and the batch stops where it failed."""
        try:
            return await self.session.send(items, self.say, self.started, self.ended, self.waiting_note)
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
            return []
        finally:
            self.refresh_jobs()

    async def mail_then_retry(self, items: list[Stored]) -> list[tuple[Stored, Outcome]]:
        """A site sent a code to the mail and the mailbox was not signed in: the question about it now, the sign-in, then those
        applications again. This is the first place the mail is asked about, since only a code needs it. One left for later is not
        asked again until AIHawk is opened again: the next ones say it and go on."""
        self.say("A site sent you a code by email, and AIHawk cannot read your mailbox yet.")
        if self.mail_left:
            self.say(MAIL_LATER)
            return []
        telemetry = self.session.telemetry
        telemetry.step("mail_asked")
        ready = await self.set_up_mail(MAIL_CODE_QUESTION)
        telemetry.step("mail_ready" if ready else "mail_later")
        self.mail_left = not ready
        return await self.send(items) if ready else []

    def can_send(self, item: Stored) -> bool:
        """Whether the job goes in the batch: all of them, but one already sent."""
        if item.status in SUBMITTED:
            self.say(f"{item.job.company}: {item.job.title}: already sent, skipped.")
            return False
        return True
