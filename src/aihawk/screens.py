"""The screens AIHawk opens over its list: a question, the batch to send, what was sent, a job's details, the settings, the
browser left open for a sign-in. Each is self-contained and reports back by what it dismisses with."""

from __future__ import annotations

import html
import re
import webbrowser
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, RichLog, Static

from aihawk.apply import why_not_sent
from aihawk.llm import money
from aihawk.store import SENT_MARKS, SUBMITTED, Sent, Stored, sent_word


class Said(RichLog):
    """The log, written again when its width changes: a line wrapped for a wider log was cut, one for a narrower one left
    the rest of the line empty."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.said: list[str] = []
        self.counting = False
        self.wrapped_for = 0

    def say(self, line: str, again: bool = False, replace: bool = False) -> None:
        """A line at the bottom. With `again`, a count that changes: it takes the place of the count before it, and the
        log keeps one line for it, not one per step. With `replace`, a line that ends a count: it takes the place of the
        count, and stays."""
        if (again or replace) and self.counting and self.said:
            self.said[-1] = line
            self.clear()
            for kept in self.said:
                self.write(kept)
        else:
            self.said.append(line)
            self.write(line)
        self.counting = again

    def on_resize(self, event: events.Resize) -> None:
        width = self.scrollable_content_region.width
        if self.wrapped_for and width != self.wrapped_for:
            self.clear()
            for line in self.said:
                self.write(line)
        self.wrapped_for = width


class Rows(DataTable):
    """A table read row by row: Home and End take the cursor to the first and the last row. DataTable's own Home and End
    only scroll the view, and left the cursor behind, out of sight, where the next arrow brought the view back."""

    BINDINGS: ClassVar = [Binding("home", "scroll_top", show=False), Binding("end", "scroll_bottom", show=False)]

# Below this width there is no room for the location beside the company and the role.
WIDE_ENOUGH = 100


class TextPrompt(ModalScreen[str | None]):
    """One question, one line of answer. Enter answers, Escape skips."""

    BINDINGS: ClassVar = [Binding("escape", "skip", "Skip")]

    def __init__(
        self, question: str, *, placeholder: str = "", password: bool = False, hint: str = "Enter · Esc goes back", value: str | None = None,
        check: Callable[[str], str | None] | None = None, problem: str = "",
    ) -> None:
        super().__init__()
        # With a value to change, Enter on an empty line clears it, and only Esc leaves it as it is.
        self.value = value
        self.question = question
        self.placeholder = placeholder
        self.password = password
        self.hint = hint
        # What is wrong with an answer, in a few words, or None when it will do: said in this dialog, which stays open, because
        # the log it would otherwise go to is behind it.
        self.check = check
        # Something already wrong when the question opens, such as the key that was refused and is asked for again.
        self.problem = problem

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="question", markup=False)
            yield Input(value=self.value or "", placeholder=self.placeholder, password=self.password, id="answer")
            yield Static("", id="problem", markup=False)
            yield Static(self.hint, classes="hint", markup=False)

    def on_mount(self) -> None:
        if self.problem:
            self.query_one("#problem", Static).update(self.problem)
        self.query_one("#problem").display = bool(self.problem)

    @on(Input.Changed)
    def edited(self) -> None:
        self.query_one("#problem").display = False

    @on(Input.Submitted)
    def answered(self, event: Input.Submitted) -> None:
        answer = event.value.strip()
        if self.check and (problem := self.check(answer)):
            self.query_one("#problem", Static).update(problem)
            self.query_one("#problem").display = True
            return
        self.dismiss(answer if self.value is not None else answer or None)

    def action_skip(self) -> None:
        self.dismiss(None)


class SendBatch(ModalScreen[bool]):
    """The one question before anything is sent: these jobs, now. Enter sends them all, Escape cancels."""

    BINDINGS: ClassVar = [Binding("enter", "answer(True)", "Send"), Binding("escape", "answer(False)", "Cancel")]

    def __init__(self, items: list[Stored]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        count = len(self.items)
        lines = [f"{index}. {item.job.company}: {item.job.title}" for index, item in enumerate(self.items, 1)]
        with Vertical(id="dialog"):
            yield Static(f"Send {count} application{'' if count == 1 else 's'} now?", id="question", markup=False)
            yield Static(
                "AIHawk fills each company's form in the browser with your resume and your answers (e shows them), and sends it in your name. A few minutes each.",
                classes="hint", markup=False,
            )
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

    def action_answer(self, send: bool) -> None:
        self.dismiss(send)


def short(text: str) -> str:
    return " ".join(text.split())[:110]


def when_short(when: str) -> str:
    """A time as a person reads it, on their own clock: 2026-11-05 10:00. One the store wrote in UTC is converted; one with no zone is as it is."""
    try:
        moment = datetime.fromisoformat(when)
    except ValueError:
        return when[:16].replace("T", " ")
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def status_words(item: Sent) -> str:
    """How it went, with the sign the list gives it: ✓ sent and confirmed, ? sent but not confirmed, ✗ not sent and why."""
    words = sent_word(item) if item.status in SUBMITTED else why_not_sent(item.reason) or "not sent"
    return f"{SENT_MARKS.get(item.status, '')} {words}".strip()


def clipped(text: str, width: int) -> Text:
    """A cell's text that fits its column, with an ellipsis where it is cut: the table would cut it mid-word without one."""
    cell = Text(" ".join(text.split()), no_wrap=True)
    cell.truncate(max(width, 1), overflow="ellipsis")
    return cell


# The sent list's status column: the longest of the words that fit, the rest is in the details.
STATUS_WIDTH = 28


class SentList(ModalScreen[None]):
    """Every job tried, the newest first. Enter opens one in full."""

    BINDINGS: ClassVar = [Binding("escape,q,s", "close", "Back")]

    def __init__(self, items: list[Sent]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="wide"):
            yield Static("Applications", id="question", markup=False)
            yield Rows(id="sent", cursor_type="row")
            yield Static("Enter opens one · Esc goes back", classes="hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        # What is left beside the time and the status, inside this screen's 95% and its padding, goes to the words. What
        # the page said is in each one's details: cut to a column, it was half a sentence.
        wide = self.app.size.width >= WIDE_ENOUGH
        left = int(self.app.size.width * 0.95) - 8 - 2 * (4 if wide else 3) - 16 - STATUS_WIDTH
        words = [("Company", left * 2 // 5), ("Role", left - left * 2 // 5)] if wide else [("Company", left)]
        widths = (16, *(width for _, width in words), STATUS_WIDTH)
        for (label, width) in zip(("When", *(label for label, _ in words), "Status"), widths, strict=True):
            table.add_column(label, key=label, width=width)
        for item in self.items:
            cells = (when_short(item.when), item.job.company, *([item.job.title] if wide else []), status_words(item))
            table.add_row(*(clipped(cell, width) for cell, width in zip(cells, widths, strict=True)), key=item.job.id)
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
        # How it went, in a person's words: what the form was filled with and the page's files stay in the store.
        item = self.item
        sent = item.status in SUBMITTED
        lines = [f"{sent_word(item).capitalize() if sent else 'Not sent'} · {when_short(item.when)}"]
        if not sent and (why := why_not_sent(item.reason)):
            lines.append(why[0].upper() + why[1:] + ".")
        if item.status == "unconfirmed":
            lines.append("The page did not confirm it. Check your email, or the company's site, to be sure.")
        if item.proof.evidence:
            lines.append(f"The page said: {item.proof.evidence}")
        elif not sent and item.note and item.note != why_not_sent(item.reason):
            # What the form did say, in its own words; a sent one's note is what its form was filled with.
            lines.append(item.note)
        if item.cost is not None:
            lines.append(f"Cost: {money(item.cost)}")
        with Vertical(id="dialog"):
            yield Static(Text(item.job.title), id="question")
            yield Static(Text(" · ".join(part for part in (item.job.company, item.job.location) if part)), classes="hint")
            with VerticalScroll(id="summary"):
                yield Static(Text("\n".join(lines)))
            yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)


def readable(text: str) -> str:
    """A posting's text as a person reads it. Sources write HTML, some of it escaped once or twice over: here it is
    paragraphs and bullets, with no tag left."""
    for _ in range(3):
        if not re.search(r"&(amp;)*lt;/?[a-zA-Z]", text):
            break
        text = html.unescape(text)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", "", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)<br\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|h[1-6]|ul|ol|table|tr|section|article|header|footer|blockquote)\b[^>]*>", "\n\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines: list[str] = []
    for line in (" ".join(line.split()) for line in text.splitlines()):
        # One empty line between paragraphs, never two.
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


class JobDetails(ModalScreen[None]):
    """The job as its posting says it: the role, the company, where, and the posting's text. Nothing else."""

    BINDINGS: ClassVar = [
        Binding("space,enter", "select", "Select", key_display="space"),
        Binding("escape,i,q", "close", "Back", key_display="esc"),
        Binding("o", "open_link", "Open the posting"),
    ]

    def __init__(self, item: Stored) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        job = self.item.job
        with Vertical(id="reader"):
            yield Static(Text(job.title), id="question")
            yield Static(Text(" · ".join(part for part in (job.company, job.location) if part)), classes="hint")
            with VerticalScroll(id="text"):
                yield Static(Text(readable(job.description) or "This posting has no description. You can still apply: the form is filled from your resume."))
            yield Footer()

    def on_mount(self) -> None:
        # The arrows and Page Down read the text.
        self.query_one("#text").focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_select(self) -> None:
        self.dismiss(None)
        self.app.action_toggle()  # type: ignore[attr-defined]

    def action_open_link(self) -> None:
        webbrowser.open(self.item.job.url)


class Settings(ModalScreen[str | None]):
    """Everything that can be changed, one row each. Enter changes the row, Esc goes back."""

    BINDINGS: ClassVar = [Binding("escape,q,e", "close", "Back")]

    def __init__(self, rows: list[tuple[str, str, str]], title: str = "Settings") -> None:
        super().__init__()
        self.rows = rows
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="wide"):
            yield Static(self.title_text, id="question", markup=False)
            yield Rows(id="settings", cursor_type="row", show_header=False)
            yield Static("Enter changes it · Esc goes back", classes="hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        room = max(20, int(self.app.size.width * 0.95) - 8 - 4 - 22)
        table.add_column("What", key="what", width=22)
        table.add_column("Now", key="now", width=room)
        for key, label, now in self.rows:
            table.add_row(Text(label), Text(now), key=key)
        table.focus()

    @on(DataTable.RowSelected)
    def chosen(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.dismiss(event.row_key.value)

    def action_close(self) -> None:
        self.dismiss(None)


class SignIn(ModalScreen[bool]):
    """The browser window is open for the person to sign in. Nothing is typed for them; Enter here says they are done, Escape
    says later, as it does at every other question."""

    BINDINGS: ClassVar = [Binding("enter", "done(True)", "Done"), Binding("escape", "done(False)", "Later")]

    def __init__(self, text: str, hint: str = "Enter when you see your inbox · Esc later") -> None:
        super().__init__()
        self.text = text
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.text, id="question", markup=False)
            yield Static(self.hint, classes="hint", markup=False)

    def action_done(self, done: bool) -> None:
        self.dismiss(done)


class Confirm(ModalScreen[bool]):
    """A question with no answer to type: Enter says yes, Escape says no."""

    BINDINGS: ClassVar = [Binding("enter", "answer(True)", "Yes"), Binding("escape", "answer(False)", "No")]

    def __init__(self, question: str, hint: str) -> None:
        super().__init__()
        self.question = question
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="question", markup=False)
            yield Static(self.hint, classes="hint", markup=False)

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class StopSending(Confirm):
    """Asked once when the person leaves while applications are being sent: leaving stops them."""

    def __init__(self) -> None:
        super().__init__("Applications are being sent. Quit and stop them?", "Enter quits · Esc goes back")
