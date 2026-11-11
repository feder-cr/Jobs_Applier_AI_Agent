"""The rule that matters: the model cannot send an application, and only the person's yes does, once for the selection."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from aihawk import apply as apply_module
from aihawk.apply import (
    LOOP_STOP,
    PAGE_CHARS,
    SYSTEM,
    Outcome,
    Report,
    apply_to_job,
    fill_form,
    instructions,
    run_on_page,
    server_parameters,
    sign_in,
)
from aihawk.config import DRY_RUN_ENV
from aihawk.database import Job
from fakes import ScriptedBrowser, ScriptedModel, tool_call

JOB = Job("g:a:1", "Acme", "Engineer", "Rome", "https://u", "https://u/apply", "Build things")
SUBMIT = {"tag": "button", "type": "submit", "in_form": True, "text": "Submit application"}
VERIFIED = json.dumps({"outcome": "submitted", "evidence": "received"})


def ready(selector: str = "#send") -> list[Any]:
    return [tool_call("finish_form", status="ready", summary="filled", submit_selector=selector)]


# Confirm, Finish and Complete send from any element, a div or a type=button included. A stepper's "Complete" is refused too.


# Not every clickable sends: a form's step, a link to read on, a button that edits, all pass.


def test_the_browser_is_given_the_environment_without_the_model_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "secret")
    monkeypatch.setenv("PATH_ALIKE", "kept")
    environment = server_parameters(tmp_path, tmp_path).env
    assert "ORCAROUTER_API_KEY" not in environment and environment["PATH_ALIKE"] == "kept"


@pytest.fixture(autouse=True)
def no_wait_for_a_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "SEND_POLL_S", 0)


class SlowSend(ScriptedBrowser):
    """The form stays on the page for some reads after the click, then the address moves and the text changes."""

    def __init__(self, reads_until_moved: int) -> None:
        super().__init__({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
        self.reads_until_moved = reads_until_moved

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self.clicks() and name == "browser_evaluate" and "location.href" in (arguments or {}).get("expression", ""):
            self.reads_until_moved -= 1
            if self.reads_until_moved <= 0:
                self.page = {"url": "https://u/thanks", "text": "Application received"}
        if name == "browser_read_text":
            self.calls.append((name, arguments or {}))
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=self.page["text"])])
        return await super().call_tool(name, arguments)


def run(model: ScriptedModel, browser: Any, shot: Path, guidance: str = "") -> Outcome:
    return asyncio.run(run_on_page(browser, [], model, "m", JOB, "cv", "answers", Path("cv.pdf"), shot, guidance))  # type: ignore[arg-type]


def test_a_send_is_clicked_once_and_the_page_confirms_it_with_no_question(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT})
    outcome = run(ScriptedModel(ready(), VERIFIED), browser, tmp_path / "proof.png")
    assert outcome.status == "submitted"
    assert browser.clicks() == ["#send"]


def test_a_click_the_page_does_not_confirm_is_not_called_sent(tmp_path: Path) -> None:
    verdict = json.dumps({"outcome": "unknown", "evidence": "page unchanged"})
    assert run(ScriptedModel(ready(), verdict), ScriptedBrowser({"#send": SUBMIT}), tmp_path / "proof.png").status == "unconfirmed"


class ButtonDisablesItself(ScriptedBrowser):
    """A form that disables its button as it sends: the click answers with an error, and may well have gone."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await super().call_tool(name, arguments)
        if name == "browser_click":
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="the action went out but did not reach the element")])
        return result


@pytest.mark.parametrize(
    ("verdict", "status"),
    [("submitted", "submitted"), ("unknown", "unconfirmed")],
)
def test_a_click_that_answers_with_an_error_is_never_called_failed_because_it_may_have_gone(verdict: str, status: str, tmp_path: Path) -> None:
    browser = ButtonDisablesItself({"#send": SUBMIT})
    outcome = run(ScriptedModel(ready(), json.dumps({"outcome": verdict, "evidence": "the page"})), browser, tmp_path / "proof.png")
    assert outcome.status == status
    assert browser.clicks() == ["#send"]


@pytest.mark.parametrize(
    "step",
    [
        [tool_call("finish_form", status="blocked", summary="captcha", submit_selector="")],
        [tool_call("finish_form", status="ready", summary="no selector", submit_selector="")],
    ],
)
def test_blocked_or_no_control_sends_nothing(step: list[Any], tmp_path: Path) -> None:
    browser = ScriptedBrowser()
    assert run(ScriptedModel(step), browser, tmp_path / "proof.png").status == "blocked"
    assert browser.clicks() == []


class PageAtAPoint:
    """A page where every point holds the same element, and every tool answers with a picture and no text."""

    def __init__(self, element: dict[str, Any]) -> None:
        self.element = element
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, arguments or {}))
        if name == "browser_evaluate":
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=json.dumps(self.element))])
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type="image", mimeType="image/png", data="QUJD")])


class CaptchaPage(ScriptedBrowser):
    """A form with a captcha box in a frame at one point, and a send control; the page stays on the form's address."""

    def __init__(self) -> None:
        super().__init__({"#send": SUBMIT, "#apply": {"tag": "button", "type": "button", "in_form": False, "text": "Apply"}}, page={"url": "https://u/apply", "text": "Form"})

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> SimpleNamespace:
        if name == "browser_evaluate" and "elementFromPoint" in (arguments or {}).get("expression", ""):
            self.calls.append((name, arguments or {}))
            frame = {"tag": "iframe", "type": "", "in_form": True, "text": ""}
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps(frame))])
        return await super().call_tool(name, arguments)


def tag_field(placeholder: str = "", aria_label: str = "", tag: str = "input", kind: str = "text") -> dict[str, Any]:
    return {"tag": tag, "type": kind, "in_form": True, "text": "", "placeholder": placeholder, "aria_label": aria_label}


def test_a_failure_to_read_the_result_after_the_click_is_not_a_failed_application(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT})
    outcome = run(ScriptedModel(ready(), "not json"), browser, tmp_path / "proof.png")
    assert outcome.status == "unconfirmed"
    assert browser.clicks() == ["#send"]


def test_a_screenshot_reaches_the_model_after_the_answers_it_must_follow() -> None:
    model = ScriptedModel([tool_call("browser_take_screenshot")], ready())
    asyncio.run(fill_form(PageAtAPoint(SUBMIT), [], model, "m", "brief"))  # type: ignore[arg-type]
    last = model.requests[1]["messages"][-2]
    assert last["role"] == "user"
    assert last["content"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]
    assert model.requests[1]["messages"][-3]["role"] == "tool"


def test_only_the_latest_picture_stays_in_the_prompt_and_an_older_one_is_a_note() -> None:
    model = ScriptedModel([tool_call("browser_take_screenshot")], [tool_call("browser_take_screenshot")], ready())
    asyncio.run(fill_form(PageAtAPoint(SUBMIT), [], model, "m", "brief"))  # type: ignore[arg-type]
    messages = model.requests[-1]["messages"]
    pictures = [m for m in messages if m.get("role") == "user" and isinstance(m.get("content"), list)]
    assert len(pictures) == 1
    assert sum(isinstance(m.get("content"), str) and "taken here" in m["content"] for m in messages) == 1


def test_the_servers_way_of_driving_a_page_comes_after_the_rules_and_before_the_site() -> None:
    text = instructions("About a site.", "Try selectors first.")
    assert text.index(SYSTEM) < text.index("Try selectors first.") < text.index("About a site.")


def test_the_same_call_with_the_same_answer_is_warned_then_stopped_and_nothing_is_sent() -> None:
    browser = ScriptedBrowser()
    model = ScriptedModel(*[[tool_call("browser_press_key", key="End")] for _ in range(LOOP_STOP)])
    report = asyncio.run(fill_form(browser, [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.status == "blocked" and "going round in circles: browser_press_key" in report.summary
    told = [m["content"] for m in model.requests[-1]["messages"] if isinstance(m, dict) and m.get("role") == "user"]
    assert sum("nothing on the page changes" in str(text) for text in told) == 1
    assert len(browser.calls) == LOOP_STOP and browser.clicks() == []


class _MovingList(ScriptedBrowser):
    """A list whose highlighted row moves down at every key."""

    async def call_tool(self, name: str, arguments: dict | None = None):  # type: ignore[override]
        result = await super().call_tool(name, arguments)
        result.content[0].text = f"row {len(self.calls)} highlighted"
        return result


def test_a_key_that_moves_down_a_list_is_not_a_loop() -> None:
    steps = [[tool_call("browser_press_key", key="ArrowDown")] for _ in range(3 * LOOP_STOP)]
    model = ScriptedModel(*steps, [tool_call("finish_form", status="blocked", summary="list read", submit_selector="")])
    report = asyncio.run(fill_form(_MovingList(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.summary == "list read"


def test_a_model_that_only_talks_is_stopped() -> None:
    model = ScriptedModel("thinking", "still thinking", "nearly")
    report = asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.status == "blocked" and "without using the tools" in report.summary


def test_waiting_for_a_code_that_never_comes_ends_at_the_fifth_look_with_nothing_new() -> None:
    wait = "new Promise(done => setTimeout(done, 20000))"
    look = [tool_call("browser_evaluate", expression=wait), tool_call("browser_read_text", selector="body")]
    model = ScriptedModel(*[look for _ in range(LOOP_STOP)])
    report = asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.status == "blocked" and "browser_evaluate, browser_read_text" in report.summary


@pytest.mark.parametrize(("confidence", "stopped"), [(0.95, True), (0.5, False)])
def test_a_loop_no_fingerprint_sees_is_stopped_when_a_model_is_sure_of_it(monkeypatch: pytest.MonkeyPatch, confidence: float, stopped: bool) -> None:
    monkeypatch.setattr(apply_module, "LOOP_CHECK_AFTER", 2)
    verdict = json.dumps({"stuck": True, "confidence": confidence, "reason": "uploads the CV and fills the fields again"})
    steps = [[tool_call("browser_press_key", key="ArrowDown")], [tool_call("browser_press_key", key="ArrowDown")], verdict]
    model = ScriptedModel(*steps, [tool_call("finish_form", status="blocked", summary="went on", submit_selector="")])
    report = asyncio.run(fill_form(_MovingList(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert (report.summary == "Stopped going round in circles: uploads the CV and fills the fields again") is stopped
    assert (report.summary == "went on") is not stopped


class SignedOutMailbox(ScriptedBrowser):
    """A mailbox that sends the browser to its sign-in page."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await super().call_tool(name, arguments)
        if name == "browser_navigate" and "mail.google.com" in (arguments or {}).get("url", ""):
            self.page = {"url": "https://accounts.google.com/signin", "text": "Sign in"}
        return result


def test_a_mailbox_that_sends_the_browser_to_its_sign_in_page_ends_the_form_for_the_person_to_sign_in(tmp_path: Path) -> None:
    model = ScriptedModel([tool_call("browser_navigate", url="https://mail.google.com/mail/u/0/")])
    outcome = asyncio.run(run_on_page(SignedOutMailbox(), [], model, "m", JOB, "cv", "a", Path("cv.pdf"), tmp_path / "proof.png"))  # type: ignore[arg-type]
    assert outcome.status == "blocked" and outcome.needs_mail and outcome.summary == apply_module.MAIL_NOT_SIGNED_IN


def test_a_signed_in_mailbox_goes_on() -> None:
    model = ScriptedModel([tool_call("browser_navigate", url="https://mail.google.com/mail/u/0/")], [tool_call("finish_form", status="blocked", summary="code read", submit_selector="")])
    report = asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.summary == "code read" and not report.blocked_by


def test_a_send_control_that_matches_several_elements_goes_back_to_the_model() -> None:
    browser = ScriptedBrowser({"button[type='button']": 3})
    model = ScriptedModel(
        [tool_call("finish_form", status="ready", summary="filled", submit_selector="button[type='button']")],
        [tool_call("finish_form", status="ready", summary="filled", submit_selector="#send")],
    )
    report = asyncio.run(fill_form(browser, [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.submit_selector == "#send"
    told = [m["content"] for m in model.requests[-1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
    assert "matches 3 elements" in told[-1]
    assert browser.clicks() == []


def test_the_plugins_text_reaches_the_verifying_model_as_its_rules_while_the_page_stays_text_to_check(tmp_path: Path) -> None:
    model = ScriptedModel(ready(), VERIFIED)
    guidance = "After sending: a page that says Thank you has sent it."
    run(model, ScriptedBrowser({"#send": SUBMIT}), tmp_path / "proof.png", guidance)
    verifying = model.requests[-1]["messages"]
    assert guidance in verifying[0]["content"]
    assert verifying[1]["content"].endswith("Thank you, your application was received")
    assert guidance not in verifying[1]["content"]


def test_a_send_slow_to_show_is_read_on_the_page_it_reached_with_its_address(tmp_path: Path) -> None:
    model = ScriptedModel(ready(), json.dumps({"outcome": "submitted", "evidence": "the page"}))
    outcome = run(model, SlowSend(reads_until_moved=8), tmp_path / "shot.png")
    read = model.requests[-1]["messages"][-1]["content"]
    assert "https://u/thanks" in read and "Application received" in read and "the form" not in read
    assert outcome.proof.text == "Application received" and outcome.proof.url == "https://u/thanks"


@pytest.mark.parametrize(
    ("verdict", "status"),
    [("submitted", "submitted"), ("unknown", "unconfirmed"), ("rejected", "refused")],
)
def test_a_click_keeps_its_time_address_verdict_text_and_picture(verdict: str, status: str, tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/thanks", "text": ""})
    shot = tmp_path / "proofs" / "g-a-1.png"
    outcome = run(ScriptedModel(ready(), json.dumps({"outcome": verdict, "evidence": "the page"})), browser, shot)
    assert outcome.status == status
    proof = outcome.proof
    assert (proof.verdict, proof.evidence, proof.url) == (verdict, "the page", "https://u/thanks")
    assert proof.at and proof.text == "Thank you, your application was received"
    assert proof.screenshot == str(shot) and shot.read_bytes() == b"PNG"


class LongPage(ScriptedBrowser):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_read_text":
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text="x" * 9000)])
        return await super().call_tool(name, arguments)


def test_the_page_text_kept_is_a_few_thousand_characters_at_most(tmp_path: Path) -> None:
    outcome = run(ScriptedModel(ready(), VERIFIED), LongPage({"#send": SUBMIT}), tmp_path / "proof.png")
    assert len(outcome.proof.text) == PAGE_CHARS


class NoPicture(ScriptedBrowser):
    """A page whose picture cannot be taken: the tool answers with an error, or fails outright."""

    def __init__(self, fails: bool) -> None:
        super().__init__({"#send": SUBMIT})
        self.fails = fails

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_take_screenshot":
            if self.fails:
                raise RuntimeError("the window is gone")
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="no page")])
        return await super().call_tool(name, arguments)


@pytest.mark.parametrize("fails", [True, False])
def test_a_picture_that_cannot_be_taken_changes_no_verdict(fails: bool, tmp_path: Path) -> None:
    shot = tmp_path / "proof.png"
    outcome = run(ScriptedModel(ready(), VERIFIED), NoPicture(fails), shot)
    assert outcome.status == "submitted"
    assert outcome.proof.screenshot == "" and not shot.exists()


class ScriptedServer(ScriptedBrowser):
    """The browser server as the tests see it: every call is recorded, and there are no tools to list."""

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[])


def scripted_server(monkeypatch: pytest.MonkeyPatch, server: ScriptedServer) -> None:
    @asynccontextmanager
    async def started(upload_dir: Path, home: Path, key_env: str = "") -> AsyncIterator[tuple[ScriptedServer, str]]:
        yield server, ""

    monkeypatch.setattr(apply_module, "_server", started)


def test_the_profile_reaches_browser_open_and_the_browser_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer()
    scripted_server(monkeypatch, server)
    profile = tmp_path / "browser-profile"
    model = ScriptedModel([tool_call("finish_form", status="blocked", summary="closed", submit_selector="")])
    outcome = asyncio.run(apply_to_job(model, "m", JOB, "cv", "answers", tmp_path / "cv.pdf", tmp_path, profile))  # type: ignore[arg-type]
    assert outcome.status == "blocked"
    assert server.calls[0] == ("browser_open", {"profile": str(profile)})
    assert server.calls[-1] == ("browser_close", {})


def test_a_sent_form_keeps_its_picture_under_data_proofs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripted_server(monkeypatch, ScriptedServer({"#send": SUBMIT}))
    outcome = asyncio.run(apply_to_job(ScriptedModel(ready(), VERIFIED), "m", JOB, "cv", "answers", tmp_path / "cv.pdf", tmp_path, tmp_path / "profile"))  # type: ignore[arg-type]
    assert outcome.status == "submitted"
    assert outcome.proof.screenshot == str(tmp_path / "proofs" / "g-a-1.png")
    assert (tmp_path / "proofs" / "g-a-1.png").read_bytes() == b"PNG"


FORM_PAGE = {"url": "https://u/apply", "text": "Apply here"}
SELECT = [tool_call("browser_select_option", selector="#pick", value="a")]


class Moves(ScriptedBrowser):
    """A form whose select, when changed, puts the page where the test says: a change event that leaves, or confirms.
    The first `unreadable` reads of the page after the change fail, as a page that is still loading does."""

    def __init__(self, after: dict[str, str] | None = None, unreadable: int = 0) -> None:
        super().__init__({"#send": SUBMIT}, page=FORM_PAGE)
        self.after = after
        self.unreadable = unreadable
        self.changed = False

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_select_option":
            self.changed = True
            self.page = self.after or self.page
        if name == "browser_evaluate" and self.changed and self.unreadable and "location.href" in str(arguments):
            self.unreadable -= 1
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="the page is loading")])
        return await super().call_tool(name, arguments)


class ScriptMoves(ScriptedBrowser):
    """A page whose script, when it runs `location.href = ...`, leaves for another site."""

    def __init__(self, after: dict[str, str]) -> None:
        super().__init__({"#send": SUBMIT}, page=FORM_PAGE)
        self.after = after

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_evaluate" and str((arguments or {}).get("expression", "")).startswith("location.href ="):
            self.calls.append((name, arguments or {}))
            self.page = self.after
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])
        return await super().call_tool(name, arguments)


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "READ_WAIT_S", 0)


class Typing(ScriptedBrowser):
    """A form still being typed into, as on Breezy: its page cannot be read for a while after the text is typed."""

    def __init__(self) -> None:
        super().__init__({"#send": SUBMIT}, page=FORM_PAGE)
        self.busy = 0

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_type":
            self.busy = 3
        if name == "browser_evaluate" and self.busy and "location.href" in str(arguments):
            self.busy -= 1
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="still typing")])
        return await super().call_tool(name, arguments)


class SaysNothingYet(ScriptedBrowser):
    """After the click the button reads "Submitting...", and the page moves on only after the first reading."""

    def __init__(self) -> None:
        super().__init__({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
        self.reads = 0

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name == "browser_read_text":
            self.reads += 1
            text = "Submitting..." if self.reads == 1 else "Application received"
            self.page = {"url": "https://u/apply" if self.reads == 1 else "https://u/thanks", "text": text}
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=text)])
        if self.clicks() and not self.reads:
            self.page = {"url": "https://u/apply", "text": "Submitting..."}
        return await super().call_tool(name, arguments)


def test_a_page_that_says_nothing_yet_is_read_again_when_it_changes(tmp_path: Path) -> None:
    says = (json.dumps({"outcome": "unknown", "evidence": "Submitting..."}), VERIFIED)
    browser = SaysNothingYet()
    outcome = run(ScriptedModel(ready(), *says), browser, tmp_path / "proof.png")
    assert outcome.status == "submitted" and outcome.proof.url == "https://u/thanks"
    assert browser.clicks() == ["#send"]


class MovesAfterItIsRead(ScriptedBrowser):
    """A Workday form: its Review step stays on the page after the click, and is read, before the address moves on."""

    def __init__(self) -> None:
        super().__init__({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "Review: Submit"})

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await super().call_tool(name, arguments)
        if name == "browser_read_text" and self.clicks():
            self.page = {"url": "https://u/completed/application", "text": "Application received"}
        return result


def test_a_page_read_before_it_moved_on_is_read_again_where_it_went(tmp_path: Path) -> None:
    says = (json.dumps({"outcome": "rejected", "evidence": "still the Review step"}), VERIFIED)
    browser = MovesAfterItIsRead()
    outcome = run(ScriptedModel(ready(), *says), browser, tmp_path / "proof.png")
    assert outcome.status == "submitted" and outcome.proof.url == "https://u/completed/application"
    assert browser.clicks() == ["#send"]


class ClickNeverLands(ScriptedBrowser):
    """A send control the browser never could click: a hidden button the selector found first."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await super().call_tool(name, arguments)
        if name == "browser_click":
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="Page.click: 'button' not actionable in 15s after 290 attempts. Last reason: missing visible")])
        return result


def test_a_send_control_never_clicked_is_blocked_so_it_can_be_tried_again(tmp_path: Path) -> None:
    unknown = json.dumps({"outcome": "unknown", "evidence": "the form is still there"})
    outcome = run(ScriptedModel(ready(), unknown), ClickNeverLands({"#send": SUBMIT}), tmp_path / "proof.png")
    assert outcome.status == "blocked" and "never clicked" in outcome.summary


def test_a_page_that_confirms_wins_over_a_click_that_never_landed(tmp_path: Path) -> None:
    outcome = run(ScriptedModel(ready(), VERIFIED), ClickNeverLands({"#send": SUBMIT}), tmp_path / "proof.png")
    assert outcome.status == "submitted"


class ClickBreaks(ScriptedBrowser):
    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await super().call_tool(name, arguments)
        if name == "browser_click":
            raise ConnectionError("the server went away")
        return result


def test_a_click_that_breaks_is_never_called_failed_because_it_may_have_gone(tmp_path: Path) -> None:
    verdict = json.dumps({"outcome": "unknown", "evidence": "page unchanged"})
    outcome = run(ScriptedModel(ready(), verdict), ClickBreaks({"#send": SUBMIT}), tmp_path / "proof.png")
    assert outcome.status == "unconfirmed" and "the server went away" in outcome.summary


@asynccontextmanager
async def _in_a_task_group() -> Any:
    async with anyio.create_task_group():
        yield None, None


class _Started:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def initialize(self) -> Any:
        return SimpleNamespace(instructions="")


def test_what_goes_wrong_in_the_browser_comes_out_as_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "stdio_client", lambda *_a, **_o: _in_a_task_group())
    monkeypatch.setattr(apply_module, "ClientSession", lambda *_a: _Started())

    async def inside() -> None:
        async with apply_module._server(tmp_path, tmp_path):
            raise ValueError("the real cause")

    with pytest.raises(ValueError, match="the real cause"):
        asyncio.run(inside())


def test_a_dry_run_fills_the_form_names_the_send_control_and_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A trial of a site or a plugin with a test CV: the environment variable stops the application before the click.
    monkeypatch.setenv(DRY_RUN_ENV, "1")
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
    outcome = asyncio.run(run_on_page(browser, [], ScriptedModel(ready()), "m", JOB, "cv", "account_password: Secret!9x\nemail: a@b.c", Path("cv.pdf"), tmp_path / "proofs" / "shot.png"))  # type: ignore[arg-type]
    assert outcome.status == "blocked"
    assert outcome.summary.startswith("Dry run (AIHAWK_DRY_RUN is set): filled, send control #send found, nothing sent.")
    assert not browser.clicks()
    assert outcome.proof.url == "https://u/apply"
    # Every step is written beside the proofs, and the password never is.
    trace = (tmp_path / "trace.log").read_text(encoding="utf-8")
    assert "== Acme: Engineer at https://u/apply" in trace and "== finish_form:" in trace and "Secret!9x" not in trace


@pytest.mark.parametrize(
    ("email", "provider"),
    [("ada@gmail.com", "gmail"), ("Ada@GoogleMail.com", "gmail"), ("ada@hotmail.it", "outlook"), ("ada@outlook.com", "outlook"), ("ada@example.org", "")],
)
def test_the_mailbox_is_read_from_the_address_when_the_domain_says_it(email: str, provider: str) -> None:
    assert apply_module.mail_provider(email) == provider


def test_a_mailbox_still_at_its_own_address_is_signed_in_and_one_sent_to_a_sign_in_page_is_not() -> None:
    assert apply_module.signed_in("gmail", "https://mail.google.com/mail/u/0/#inbox")
    assert not apply_module.signed_in("gmail", "https://accounts.google.com/v3/signin/identifier?continue=https://mail.google.com")
    assert apply_module.signed_in("outlook", "https://outlook.live.com/mail/0/")
    assert not apply_module.signed_in("outlook", "https://login.live.com/login.srf")


def test_sign_in_on_a_mailbox_opens_it_and_says_where_the_page_is_after_the_person_is_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer(page={"url": "", "text": ""})
    scripted_server(monkeypatch, server)
    url = "https://mail.google.com/mail/u/0/"

    async def wait() -> None:
        # The person signs in: the mailbox then stays at its own address when opened again.
        server.page = {"url": url + "#inbox", "text": "Inbox"}

    where = asyncio.run(sign_in(tmp_path / "browser-profile", tmp_path, wait, url))
    assert where == url  # the second opening of the address, as the fake records it
    assert [name for name, _ in server.calls] == ["browser_open", "browser_navigate", "browser_navigate", "browser_navigate", "browser_evaluate", "browser_close"]
    assert server.calls[2] == ("browser_navigate", {"url": url}) and server.calls[3] == ("browser_navigate", {"url": url})


def go(url: str) -> list[Any]:
    return [tool_call("browser_navigate", url=url)]


def mailbox_pages(browser: ScriptedBrowser) -> int:
    return sum(1 for name, arguments in browser.calls if name == "browser_navigate" and "mail.google.com" in arguments["url"])


def test_moving_about_inside_one_mailbox_visit_is_free(tmp_path: Path) -> None:
    # Finding one message takes several pages of the mailbox, so the limit counts arrivals at it, not pages of it.
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
    model = ScriptedModel(
        go("https://mail.google.com/mail/u/0/#search/subject%3Averification"),
        go("https://mail.google.com/mail/u/0/#inbox"),
        go("https://mail.google.com/mail/u/0/#search/newer_than%3A1d"),
        ready(),
    )
    report = asyncio.run(fill_form(browser, [], model, "m", "the brief"))  # type: ignore[arg-type]
    assert report.status == "ready" and mailbox_pages(browser) == 3


def test_the_third_arrival_at_the_mailbox_ends_the_form_blocked(tmp_path: Path) -> None:
    # Two visits, then the code stops the model, whatever it reasons: a code that is not there by now is not coming.
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
    model = ScriptedModel(
        go("https://mail.google.com/mail/u/0/#search/subject%3Averification"),
        go("https://u/apply"),
        go("https://mail.google.com/mail/u/0/#search/subject%3Averification"),
        go("https://u/apply"),
        go("https://mail.google.com/mail/u/0/#inbox"),
        ready(),
    )
    report = asyncio.run(fill_form(browser, [], model, "m", "the brief"))  # type: ignore[arg-type]
    assert report == Report("blocked", apply_module.NO_CODE, blocked_by=apply_module.NO_CODE_REASON, steps=5)
    assert mailbox_pages(browser) == 2


def test_the_account_a_form_made_is_reported_and_the_brief_says_whether_one_exists(tmp_path: Path) -> None:
    # The site is told to the model once: an account here, or none, so it signs in or registers without being asked.
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
    model = ScriptedModel([tool_call("finish_form", status="ready", summary="made one", submit_selector="#send", made_account=True)], VERIFIED)
    outcome = asyncio.run(
        run_on_page(browser, [], model, "m", JOB, "cv", "answers", Path("cv.pdf"), tmp_path / "shot.png", account=None)  # type: ignore[arg-type]
    )
    assert outcome.made_account is True and outcome.status == "submitted"
    assert "No account of yours is known on u: if the form needs one, make it." in model.requests[0]["messages"][1]["content"]
    known = asyncio.run(run_on_page(ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "f"}), [], (m := ScriptedModel(
        [tool_call("finish_form", status="blocked", summary="x", submit_selector="", made_account=False)])), "m", JOB, "cv", "a", Path("cv.pdf"),
        tmp_path / "s.png", account=("ada@example.com", "2026-11-07T09:00:00")))  # type: ignore[arg-type]
    assert known.status == "blocked"
    assert "Your account on u: made on 2026-11-07 with ada@example.com" in m.requests[0]["messages"][1]["content"]


def test_a_form_that_made_no_account_says_so(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT}, page={"url": "https://u/apply", "text": "the form"})
    model = ScriptedModel([tool_call("finish_form", status="blocked", summary="a puzzle", submit_selector="", made_account=False)])
    outcome = asyncio.run(run_on_page(browser, [], model, "m", JOB, "cv", "answers", Path("cv.pdf"), tmp_path / "shot.png"))  # type: ignore[arg-type]
    assert outcome.made_account is False and outcome.status == "blocked"


# A model call that gets no answer is asked again after a wait, not given up on.


def slow_down(headers: dict[str, str] | None = None, status: int = 429) -> Exception:
    import httpx
    from openai import APIStatusError, RateLimitError

    request = httpx.Request("POST", "https://api.orcarouter.ai/v1/chat/completions")
    return (RateLimitError if status == 429 else APIStatusError)(f"Error code: {status}", response=httpx.Response(status, headers=headers or {}, request=request), body=None)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The waits an application asks for, with no waiting."""
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(apply_module.asyncio, "sleep", sleep)
    return waits


class Failing:
    """A call that fails with each of `errors` in turn, then answers."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "answered"


def test_a_call_the_service_does_not_answer_waits_20_40_60_60_seconds_and_then_fails_with_its_own_error(slept: list[float]) -> None:
    from openai import RateLimitError

    assert apply_module.RATE_WAITS == (20, 40, 60, 60)
    call = Failing(*[slow_down() for _ in range(5)])
    with pytest.raises(RateLimitError):
        asyncio.run(apply_module.patiently(call))
    assert slept == [20, 40, 60, 60] and call.calls == 5


def test_a_call_that_answers_after_a_wait_is_the_same_call(slept: list[float]) -> None:
    call = Failing(slow_down(), slow_down(status=503))
    assert asyncio.run(apply_module.patiently(call)) == "answered"
    assert slept == [20, 40] and call.calls == 3


def test_the_wait_the_service_asks_for_is_the_one_taken_and_never_more_than_two_minutes(slept: list[float]) -> None:
    call = Failing(slow_down({"retry-after": "7"}), slow_down({"retry-after-ms": "2500"}), slow_down({"retry-after": "900"}), slow_down({"retry-after": "soon"}))
    assert asyncio.run(apply_module.patiently(call)) == "answered"
    assert slept == [7, 2.5, 120, 60]


@pytest.mark.parametrize("error", [ValueError("no"), slow_down(status=400), slow_down(status=402), slow_down(status=404)])
def test_an_error_that_waiting_cannot_cure_is_not_waited_on(error: Exception, slept: list[float]) -> None:
    call = Failing(error)
    with pytest.raises(type(error)):
        asyncio.run(apply_module.patiently(call))
    assert slept == [] and call.calls == 1


def test_the_screen_is_told_while_it_waits_and_when_the_work_goes_on(slept: list[float]) -> None:
    told: list[str] = []
    token = apply_module.NOTE.set(told.append)
    try:
        asyncio.run(apply_module.patiently(Failing(slow_down({"retry-after": "40"}), slow_down(status=503))))
    finally:
        apply_module.NOTE.reset(token)
    assert told == ["The AI service asked to slow down: waiting 40 seconds…", "", "The AI service is not answering: waiting 40 seconds…", ""]


class SlowedModel(ScriptedModel):
    """The first `slowed` requests are answered 429, then the script goes on; the request that was refused is kept too."""

    def __init__(self, slowed: int, *steps: Any) -> None:
        super().__init__(*steps)
        self.slowed = slowed

    async def _create(self, **request: Any) -> Any:
        if self.slowed:
            self.slowed -= 1
            self.requests.append(request)
            raise slow_down()
        return await super()._create(**request)


def test_a_form_goes_on_after_a_wait_with_what_the_model_is_sent_unchanged(slept: list[float]) -> None:
    model = SlowedModel(2, ready())
    report = asyncio.run(fill_form(ScriptedBrowser({"#send": SUBMIT}), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report.status == "ready" and slept == [20, 40]
    # Three requests, the same one each time.
    assert len(model.requests) == 3 and model.requests[0]["messages"] == model.requests[1]["messages"] == model.requests[2]["messages"]


def test_the_reading_of_the_page_after_a_send_goes_on_after_a_wait_too(slept: list[float], tmp_path: Path) -> None:
    class SlowedVerdict(ScriptedModel):
        async def _create(self, **request: Any) -> Any:
            # The verifying model is the second call: the first is the form's.
            if len(self.requests) == 1 and not getattr(self, "refused_once", False):
                self.refused_once = True
                raise slow_down()
            return await super()._create(**request)

    outcome = run(SlowedVerdict(ready(), VERIFIED), ScriptedBrowser({"#send": SUBMIT}), tmp_path / "proof.png")
    assert outcome.status == "submitted" and [wait for wait in slept if wait] == [20]


# The browser's engine: what the screen says while it comes, and what a Linux without a screen is told.


def test_the_servers_words_about_its_download_become_one_line_for_the_screen() -> None:
    words = apply_module.getting_the_browser
    assert words("the engine is not on this machine yet and is downloading now: 37% of 270 MB. Call browser_open again in a minute.") == (
        "Getting the browser, only the first time: 37% of 270 MB…"
    )
    assert words("the engine is not on this machine yet; its download is starting. Call browser_open again in a minute.") == (
        "Getting the browser, only the first time: about 270 MB…"
    )
    assert words("the engine has downloaded and is being verified, which takes a moment.") == "Getting the browser, only the first time: almost done…"
    assert words("the page did not load") == ""


def test_the_screen_is_told_while_the_browser_comes_and_when_it_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "OPEN_RETRY_S", 0)
    answers = iter(["downloading now: 10% of 270 MB.", "downloading now: 60% of 270 MB."])

    class Server:
        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            if name == "browser_open":
                return SimpleNamespace(isError=False, content=[SimpleNamespace(text="the engine is not on this machine yet and is " + next(answers, "ready"))])
            if name == "browser_navigate" and next(answers, None) is not None:
                return SimpleNamespace(isError=True, content=[SimpleNamespace(text="not open")])
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    told: list[str] = []
    token = apply_module.NOTE.set(told.append)
    try:
        asyncio.run(apply_module._open_browser(Server(), Path("profile")))  # type: ignore[arg-type]
    finally:
        apply_module.NOTE.reset(token)
    assert told[0].startswith("Getting the browser, only the first time: ") and told[-1] == ""


def test_on_linux_with_no_desktop_the_browser_is_not_tried_and_the_person_is_told(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(apply_module, "PLATFORM", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    @asynccontextmanager
    async def never(upload_dir: Path, home: Path) -> AsyncIterator[Any]:
        raise AssertionError("the browser was opened")
        yield

    monkeypatch.setattr(apply_module, "_server", never)
    outcome = asyncio.run(apply_to_job(ScriptedModel(), "m", JOB, "cv", "answers", tmp_path / "cv.pdf", tmp_path, tmp_path / "profile"))
    assert outcome.status == "blocked" and outcome.reason == "no_display"
    assert outcome.summary == "The browser needs a desktop: run AIHawk on a computer with a screen."
    assert apply_module.why_not_sent("no_display") == "the browser needs a desktop"


def test_on_linux_without_the_gtk_library_the_person_is_told_the_packages_for_debian_and_ubuntu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(apply_module, "PLATFORM", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(apply_module.ctypes.util, "find_library", lambda name: None)
    reason, words = apply_module.missing_for_the_browser() or ("", "")
    assert reason == "browser_libraries"
    # One line that works on both: the names with t64 of Ubuntu 24.04 first, the names before it after.
    assert words == (
        "The browser needs a few system packages. On Debian or Ubuntu: sudo apt-get install -y libgtk-3-0t64 libasound2t64 libx11-xcb1 libdbus-glib-1-2"
        " || sudo apt-get install -y libgtk-3-0 libasound2 libx11-xcb1 libdbus-glib-1-2"
    )
    assert apply_module.why_not_sent("browser_libraries") == "the browser needs a few system packages"
    monkeypatch.setattr(apply_module.ctypes.util, "find_library", lambda name: "libgtk-3.so.0")
    assert apply_module.missing_for_the_browser() is None
    monkeypatch.setenv("DISPLAY", "")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert apply_module.missing_for_the_browser() is None


def test_on_windows_nothing_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "PLATFORM", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert apply_module.missing_for_the_browser() is None
