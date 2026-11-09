"""AIHawk in the terminal: give it your resume, get the jobs that fit, select them and apply."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Static

from aihawk.apply import MAILBOXES, Outcome, mail_provider
from aihawk.config import KEY_ENV, KEY_URL, ConfigError
from aihawk.database import DatabaseError
from aihawk.library import LibraryError
from aihawk.llm import explain, refused
from aihawk.screens import WIDE_ENOUGH, JobDetails, Rows, Said, SendBatch, SentDetails, SentList, Settings, SignIn, TextPrompt
from aihawk.session import Session
from aihawk.store import SUBMITTED, Stored

SEARCH_SETTINGS = {"places", "exclude", "max_age_days"}
# Jobs on a page: all scored, sorted once, and never moved while the person reads them.
PAGE = 50
# Scored jobs kept in reserve beyond the pages made, a round of them: the next pages are there when the person turns.
AHEAD = 200
RESUME_QUESTION = "Where is your resume?"
MAIL_QUESTION = "The email you apply with."
MAIL_HINT = "Job sites send their codes there, and AIHawk reads only those · Enter · Esc for later"
KEY_QUESTION = "Your OrcaRouter key."
KEY_HINT = f"Get one at {KEY_URL} · it stays on this computer · Enter saves it"
MAIL_NAMES = {"gmail": "Gmail", "outlook": "Outlook"}
RESUME_HINT = "PDF, .docx or text · Enter reads it · Esc for later"


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
    #log { height: 3; border-top: solid $primary; padding: 0 1; scrollbar-size-vertical: 0; }
    ModalScreen { align: center middle; background: $background 60%; }
    #dialog { width: 80%; max-width: 100; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #wide { width: 95%; height: auto; max-height: 85%; padding: 1 2; background: $surface; border: thick $primary; }
    #sent, #settings { height: auto; max-height: 24; }
    #question { text-style: bold; margin-bottom: 1; }
    #summary { height: auto; max-height: 24; margin: 1 0; }
    .hint { color: $text-muted; }
    """
    BINDINGS: ClassVar = [
        Binding("space", "toggle", "Select"),
        Binding("enter", "details", show=False),
        Binding("a", "apply", "Apply"),
        Binding("s", "sent", show=False),
        Binding("o", "open_link", show=False),
        Binding("e", "settings", "Settings"),
        Binding("r", "new_resume", show=False),
        Binding("q", "quit", "Quit"),
        # Textual's own Ctrl+C only says "press ctrl+q", and an input under it copies: at any prompt Ctrl+C now leaves.
        Binding("ctrl+c", "quit", show=False, priority=True),
    ]

    def __init__(self, workspace: Path, resume: Path | None = None) -> None:
        super().__init__()
        self.session = Session(workspace)
        self.resume = resume
        self.selected: set[str] = set()
        # The pages made so far, each its jobs' ids in the order shown; the page shown; and one asked for before it was ready.
        self.pages: list[list[str]] = []
        self.page = 0
        self.wanted: int | None = None
        # The chunks of the database on this computer, and of how many, while they come; and whether a search has to run
        # again over the chunks come since the last one.
        self.progress: tuple[int, int] | None = None
        self.more_found = False
        # The chunks this computer had when it was last short of a fifth of the database, plus one.
        self.ready_at = 0
        self.signing_in = False
        self.columns_for = 0
        self.with_location = True

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
        for label, size in (("", 2), ("Score", 5), *words):
            table.add_column(label, key=label or "mark", width=size)
        self.refresh_jobs(current)

    def on_unmount(self) -> None:
        self.session.close()

    # What the person sees.

    def say(self, line: str) -> None:
        self.query_one(Said).say(line)

    def failed(self, error: Exception) -> None:
        """Says what went wrong, in plain words. A key OrcaRouter refused is forgotten when it came from the file, and asked for again."""
        if not refused(error):
            self.say(explain(error))
            return
        self.say(self.session.refuse_key())
        if self.session.needs_key():
            self.start()

    def show_heading(self) -> None:
        # A flow that ends while the app quits finds its screen gone: there is no heading left to write to.
        if not self.is_running:
            return
        config = self.session.config
        wanted = config.filters.titles() or "no resume yet"
        place = ", ".join(config.filters.locations[:3])
        self.query_one("#heading", Static).update(" · ".join(part for part in ("AIHawk", wanted, place) if part))

    def row(self, item: Stored) -> tuple[Text | str, ...]:
        # One sign, or none: selected, or sent. What else happened to a job is in its details, on Enter.
        mark = "●" if item.job.id in self.selected else "✓" if item.status in SUBMITTED else ""
        score = f"{item.score:.1f}" if item.score is not None else "-"
        # Text, because a string cell is read as markup, and a posting's words are a source's: "[/x] Engineer" would stop the screen.
        place = (Text(item.job.location),) if self.with_location else ()
        return (Text(mark, style="bold green"), score, Text(item.job.company), Text(item.job.title), *place)

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
        # The whole database's jobs, not the search's: what AIHawk looks through for the person.
        total = self.session.database_jobs
        # While the chunks come, how many jobs are here of the database's: the person sees them grow.
        here = self.session.library.postings_here()
        found = (f"{here:,} of " if here < total and not self.session.library.complete() else "") + f"{total:,} job" + ("" if total == 1 else "s") if total else ""
        self.query_one("#count", Static).update(" · ".join(part for part in (f"Page {self.page + 1}" if self.pages else "", found) if part))
        ids = [item.job.id for item in page]
        if current in ids:
            table.move_cursor(row=ids.index(current))

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
        self.pages, self.page, self.wanted = [], 0, None
        self.refresh_jobs()
        session = self.session
        try:
            if session.needs_key():
                key = await self.push_screen_wait(TextPrompt(KEY_QUESTION, placeholder="sk-orca-...", password=True, hint=KEY_HINT))
                if not key:
                    self.say("Without a key AIHawk cannot read. Press e when you have one.")
                    return
                session.save_key(key)
            if self.resume or session.needs_resume():
                path = self.resume
                while path is None:
                    answer = await self.push_screen_wait(TextPrompt(RESUME_QUESTION, placeholder=str(Path.home() / "resume.pdf"), hint=RESUME_HINT))
                    if not answer:
                        self.say("Press e when you have a resume.")
                        return
                    path = Path(answer.strip("\"' ")).expanduser()
                    if not path.is_file():
                        self.say(f"No file at {path}.")
                        path = None
                self.resume = None
                self.say(f"Reading {path.name}.")
                await session.load_resume(path)
                self.show_heading()
            if not session.mail()[1]:
                await self.set_up_mail()
            # The jobs scored before, by an earlier search: the first pages, sorted as they are.
            scored = [item.job.id for item in session.store.all() if item.score is not None]
            self.pages = [scored[start : start + PAGE] for start in range(0, len(scored), PAGE)]
            self.refresh_jobs()
            # The jobs come in the background, a chunk at a time. Until a fifth of them is here nothing is searched: over
            # the first chunk alone the best matches were few and poor. From there, each chunk that comes searches again.
            self.bring_jobs()
            if not session.library.ready():
                self.show_progress()
                return
            await session.find(self.say)
            await self.score()
        except (ConfigError, DatabaseError) as error:
            self.say(str(error))
        except Exception as error:  # noqa: BLE001  The screen stays up and says what went wrong, instead of a traceback over it.
            self.failed(error)
        finally:
            self.show_heading()

    async def score(self) -> None:
        """Pages, PAGE jobs each, up to the one after the page shown, and a round of scored jobs no page holds yet in
        reserve: Jev reads the next round while the person reads, so a page they turn to is there. A round takes Jev a few
        seconds; until the first jobs are on this computer, the line below counts the chunks coming."""
        session = self.session
        store = session.store
        while True:
            self.make_pages()
            if not store.with_status("new") or (len(self.pages) >= self.page + 2 and len(self.spare()) >= AHEAD):
                break
            self.show_progress()
            await session.read(self.say)
        self.make_pages()

    @work(exclusive=True, group="library")
    async def bring_jobs(self) -> None:
        """The database brought to this computer, the newest jobs first; each chunk that comes is searched."""
        try:
            await self.session.sync(self.chunks_came, self.found_more)
        except (LibraryError, DatabaseError) as error:
            self.say(str(error))
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
        finally:
            self.progress = None
            if self.is_running:
                self.refresh_jobs()

    def chunks_came(self, done: int, size: int) -> None:
        # Whether the person was waiting for the first fifth: told by the count before this chunk, not by this one's.
        waiting = self.progress is not None and self.progress[0] < self.ready_at
        self.progress = (done, size)
        if not self.session.library.ready():
            self.ready_at = done + 1
        if not self.is_running:
            return
        self.refresh_jobs()
        if waiting and self.session.library.ready():
            # The count of the wait gives way: the person sees it is over, and that more is coming.
            self.query_one(Said).say("Ready. More jobs keep arriving in the background.", again=True)
        else:
            self.show_progress()

    def found_more(self) -> None:
        """New jobs on this computer: the search runs again over them, once for however many came while it ran. Not
        before a fifth of the database is here."""
        if not self.session.library.ready():
            return
        self.more_found = True
        if self.is_running and not any(worker.group == "find" and worker.is_running for worker in self.workers):
            self.find_again()

    @work(group="find")
    async def find_again(self) -> None:
        try:
            while self.more_found and self.is_running:
                self.more_found = False
                await self.session.find(self.say)
                self.keep_ahead()
        except DatabaseError as error:
            self.say(str(error))

    def spare(self) -> list[str]:
        """The scored jobs no page holds yet, the best first."""
        paged = {job_id for page in self.pages for job_id in page}
        return [item.job.id for item in self.session.store.all() if item.score is not None and item.job.id not in paged]

    def make_pages(self) -> None:
        """Pages from the jobs scored and not on one, up to the one after the page shown. A page is PAGE of them, sorted
        once; a shorter one only when Jev has nothing left to read. A page the person waited for opens."""
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

    def show_progress(self) -> None:
        """How far the page the person waits for is, on one line: only while they wait, the first page or one turned to.
        Until a fifth of the database is on this computer, how far that is."""
        if self.wanted is None and len(self.pages) > self.page and self.session.library.ready():
            return
        said = self.query_one(Said)
        if not self.session.library.ready():
            said.say(f"Getting the jobs ready, only the first time… {int(self.session.library.readiness() * 100)}%", again=True)
            return
        said.say(f"Preparing page {(self.wanted if self.wanted is not None else self.page) + 1}…", again=True)

    def turn(self, step: int) -> None:
        """To the page before or after, on its first row. A page not ready yet opens once it is; the line below says how
        far it is."""
        target = self.page + step
        if target < 0:
            return
        if target < len(self.pages):
            self.page = target
            self.wanted = None
            self.refresh_jobs()
            table = self.query_one(DataTable)
            table.move_cursor(row=0)
            self.keep_ahead()
            return
        store = self.session.store
        paged = {job_id for page in self.pages for job_id in page}
        nothing_left = not store.with_status("new") and all(item.job.id in paged for item in store.all() if item.score is not None)
        # Unless more are on their way: the page opens when the chunks still coming bring its jobs.
        if nothing_left and self.session.library.complete():
            self.say("No more jobs for this search.")
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
        # Down a row, and no further: on the page's last row, selecting is not turning the page.
        table.move_cursor(row=min(table.cursor_row + 1, table.row_count - 1))

    @on(DataTable.RowSelected)
    def row_chosen(self) -> None:
        self.action_details()

    def action_details(self) -> None:
        """The job under the cursor: what was sent and what the page said when it was tried, otherwise why it is listed."""
        item = self.current()
        if not item:
            return
        tried = next((sent for sent in self.session.store.sent() if sent.job.id == item.job.id), None)
        self.push_screen(SentDetails(tried) if tried else JobDetails(item))

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
        sent = self.session.store.sent()
        if sent:
            self.push_screen(SentList(sent))
        else:
            self.say("Nothing sent yet.")

    def setting_rows(self) -> list[tuple[str, str, str]]:
        # What to look for is not here: the titles and their graph come from the resume. A new resume changes them.
        config = self.session.config
        return [
            ("places", "Where", ", ".join(config.filters.locations)),
            ("exclude", "Leave out", ", ".join(config.filters.exclude)),
            ("max_age_days", "Posted within (days)", str(config.max_age_days)),
            ("resume", "Resume", "loaded" if config.cv.is_file() else "none yet"),
            ("answers", "Your answers", f"{len(self.session.store.answers())}, given with the CV to every form"),
            ("mail", "Your mail", self.mail_words()),
            ("key", "OrcaRouter key", f"from {KEY_ENV}" if config.key_from_environment() else "saved" if config.api_key() else "none yet"),
            ("model", "Forms model", config.model),
            ("quick_model", "Search plan model", config.quick_model),
            ("decision_model", "Scoring model", config.decision_model),
            ("folder", "Your files", str(config.workspace)),
        ]

    def answer_rows(self) -> list[tuple[str, str, str]]:
        """What the forms are told about the person: the answers in the store, and a row to add one."""
        rows = [(f"answer:{name}", name.replace("_", " "), value) for name, value in self.session.store.answers().items()]
        return [*rows, ("answer:", "Add an answer", "name: value")]

    def action_settings(self) -> None:
        if self.applying() or self.signing_in:
            self.say("The browser is busy.")
        else:
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
                await self.set_up_mail()
            elif choice == "answers":
                await self.change_answers()
            elif choice == "key":
                await self.change_key()
            else:
                label, now = next((label, now) for key, label, now in self.setting_rows() if key == choice)
                answer = await self.push_screen_wait(TextPrompt(label, value=now, hint="Enter saves it · Esc leaves it"))
                if answer is not None:
                    try:
                        self.session.change_setting(choice, answer)
                        search_again = search_again or choice in SEARCH_SETTINGS
                    except ConfigError as error:
                        self.say(str(error))
        self.show_heading()
        if search_again:
            self.say("Searching again.")
            self.start()

    def mail_words(self) -> str:
        email, provider = self.session.mail()
        if provider in MAILBOXES:
            return f"{MAIL_NAMES[provider]}, {email}"
        return "not set up"

    async def set_up_mail(self) -> bool:
        """The mailbox the job sites write to: its address, then the browser open on it for the person to sign in once, and the
        inbox checked. Esc at any point leaves it for later. Whether the inbox was reached."""
        session = self.session
        email, provider = session.mail()
        answer = await self.push_screen_wait(TextPrompt(MAIL_QUESTION, value=email, placeholder="you@gmail.com", hint=MAIL_HINT))
        if not answer or "@" not in answer:
            session.set_mail(email, "later")
            self.say("Mail left for later: e, then Your mail.")
            return False
        email = answer.strip()
        provider = mail_provider(email) or (provider if provider in MAILBOXES else "")
        while provider not in MAILBOXES:
            choice = await self.push_screen_wait(TextPrompt(f"Where is {email}?", placeholder="gmail", hint="gmail or outlook · Enter · Esc for later"))
            if choice is None:
                session.set_mail(email, "later")
                self.say("Mail left for later: e, then Your mail.")
                return False
            provider = choice.strip().lower()
        session.set_mail(email, provider)
        name = MAIL_NAMES[provider]
        while True:
            self.signing_in = True
            try:

                async def wait() -> None:
                    await self.push_screen_wait(SignIn(f"{name} is open in the browser. Sign in with {email}.", hint="Enter here when you see your inbox"))

                reached = await session.sign_in_mail(provider, wait)
            except Exception as error:  # noqa: BLE001  Said on the screen, as for the other browser openings.
                self.say(f"The browser did not open: {error}")
                return False
            finally:
                self.signing_in = False
            if reached:
                self.say(f"Signed in to {name}.")
                return True
            again = await self.push_screen_wait(TextPrompt(f"{name} still shows its sign-in page.", hint="Enter opens it again · Esc for later"))
            if again is None:
                self.say("Mail left for later: e, then Your mail.")
                return False

    async def change_answers(self) -> None:
        """One answer per row. Enter edits it, an empty value removes it; the last row adds one as `name: value`."""
        store = self.session.store
        while (choice := await self.push_screen_wait(Settings(self.answer_rows(), title="Your answers"))) is not None:
            name = choice.removeprefix("answer:")
            if name:
                value = await self.push_screen_wait(TextPrompt(name.replace("_", " "), value=store.answers().get(name, ""), hint="Enter saves it · empty removes it · Esc leaves it"))
                if value is not None:
                    store.set_answer(name, value)
            else:
                line = await self.push_screen_wait(TextPrompt("A new answer, as name: value", placeholder="notice period: one month", hint="Enter saves it · Esc goes back"))
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
        key = await self.push_screen_wait(TextPrompt(KEY_QUESTION, placeholder="sk-orca-...", password=True, hint=KEY_HINT))
        if key:
            self.session.save_key(key)

    def action_new_resume(self) -> None:
        self.new_resume()

    @work(exclusive=True, group="flow")
    async def new_resume(self) -> None:
        answer = await self.push_screen_wait(TextPrompt(RESUME_QUESTION, placeholder=str(Path.home() / "resume.pdf"), hint=RESUME_HINT))
        if answer:
            self.resume = Path(answer.strip("\"' ")).expanduser()
            self.start()

    def action_apply(self) -> None:
        if self.signing_in or self.applying():
            self.say("The browser is busy.")
            return
        self.apply_selected()

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
        unread = [item for item, outcome in results if outcome.needs_mail]
        if unread:
            await self.mail_then_retry(unread)
        # The applications used some of what lay ahead: fill it again, as a cursor move would.
        self.keep_ahead()

    async def send(self, items: list[Stored]) -> list[tuple[Stored, Outcome]]:
        """The applications, each with what came of it; a failure is said on the screen, and the batch stops where it failed."""
        try:
            return await self.session.send(items, self.say, self.refresh_jobs)
        except Exception as error:  # noqa: BLE001  Said on the screen, as in start.
            self.failed(error)
            return []
        finally:
            self.refresh_jobs()

    async def mail_then_retry(self, items: list[Stored]) -> None:
        """A site sent a code to the mail and the mailbox was not signed in: the sign-in now, then those applications again."""
        names = ", ".join(item.job.company for item in items)
        self.say(f"{names}: a code was sent to your mail, and the mailbox is not signed in.")
        if await self.set_up_mail():
            await self.send(items)

    def can_send(self, item: Stored) -> bool:
        """Whether the job goes in the batch: all of them, but one already sent."""
        if item.status in SUBMITTED:
            self.say(f"{item.job.company}: {item.job.title}: already sent, skipped.")
            return False
        return True
