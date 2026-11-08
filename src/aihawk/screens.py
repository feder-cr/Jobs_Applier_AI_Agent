"""The screens AIHawk opens over its list: a question, the batch to send, what was sent, a job's details, the settings, the
browser left open for a sign-in. Each is self-contained and reports back by what it dismisses with."""

from __future__ import annotations

import webbrowser
from typing import Any, ClassVar

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, RichLog, Static

from aihawk.judge import QUESTIONS
from aihawk.llm import money
from aihawk.store import Sent, Stored, said, sent_word


class Said(RichLog):
    """The log, written again when its width changes: a line wrapped for a wider log was cut, one for a narrower one left
    the rest of the line empty."""

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.said: list[str] = []
        self.counting = False
        self.wrapped_for = 0

    def say(self, line: str, again: bool = False) -> None:
        """A line at the bottom. With `again`, a count that changes: it takes the place of the count before it, and the
        log keeps one line for it, not one per step."""
        if again and self.counting and self.said:
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

    def __init__(self, question: str, *, placeholder: str = "", password: bool = False, hint: str = "Enter · Esc goes back", value: str | None = None) -> None:
        super().__init__()
        # With a value to change, Enter on an empty line clears it, and only Esc leaves it as it is.
        self.value = value
        self.question = question
        self.placeholder = placeholder
        self.password = password
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.question, id="question", markup=False)
            yield Input(value=self.value or "", placeholder=self.placeholder, password=self.password, id="answer")
            yield Static(self.hint, classes="hint")

    @on(Input.Submitted)
    def answered(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() if self.value is not None else event.value.strip() or None)

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
            yield Static("In your name, in the browser window · Enter sends · Esc cancels", classes="hint")
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

    def action_answer(self, send: bool) -> None:
        self.dismiss(send)


def short(text: str) -> str:
    return " ".join(text.split())[:110]


def when_short(when: str) -> str:
    """A time as a person reads it: 2026-11-05 10:00."""
    return when[:16].replace("T", " ")


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
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        # What is left beside the time and the status, inside this screen's 95% and its padding, goes to the words.
        wide = self.app.size.width >= WIDE_ENOUGH
        left = int(self.app.size.width * 0.95) - 8 - 2 * (5 if wide else 3) - 16 - 19
        words = [("Company", left // 5), ("Role", left * 3 // 10)] if wide else [("Company", left)]
        for label, width in (("When", 16), *words, ("Status", 19), *([("What the page said", left // 2)] if wide else [])):
            table.add_column(label, key=label, width=width)
        for item in self.items:
            cells = (when_short(item.when), item.job.company, *([item.job.title] if wide else []), sent_word(item), *([short(said(item))] if wide else []))
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
            f"{sent_word(item)} · {item.when}" + (f" · {money(item.cost)}" if item.cost is not None else ""),
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
    BINDINGS: ClassVar = [Binding("space", "select", "Select"), Binding("o", "open_link", "Open"), Binding("escape,enter,q", "close", "Back")]

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
            if not job.description.strip():
                lines += ["", "Scored from the title alone: the posting had no text to read."]
        if self.item.note:
            lines += ["", "Last attempt:", self.item.note]
        with Vertical(id="dialog"):
            with VerticalScroll(id="summary"):
                yield Static("\n".join(lines), markup=False)
            yield Footer()

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
            yield Footer()

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


class SignIn(ModalScreen[None]):
    """The browser window is open for the person to sign in. Nothing is typed for them; a key here says they are done."""

    BINDINGS: ClassVar = [Binding("enter,escape", "done", "Done")]

    def __init__(self, text: str, hint: str = "Enter here when you are done · the window closes") -> None:
        super().__init__()
        self.text = text
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.text, id="question", markup=False)
            yield Static(self.hint, classes="hint")

    def action_done(self) -> None:
        self.dismiss(None)
