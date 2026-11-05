"""The rule that matters: the model cannot send an application, and only the person's yes does, once for the selection."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from aihawk import apply as apply_module
from aihawk import session as session_module
from aihawk.apply import (
    FINISH_TOOL,
    MODEL_TOOLS,
    PAGE_CHARS,
    SYSTEM,
    Outcome,
    apply_to_job,
    fill_form,
    instructions,
    offered,
    refusal,
    run_on_page,
    sends_form,
    server_parameters,
    sign_in,
)
from aihawk.database import Job
from aihawk.documents import score_from, score_job, tailor_cv, write_cv_pdf
from aihawk.session import Session
from fakes import ScriptedBrowser, ScriptedModel, tool_call

JOB = Job("g:a:1", "Acme", "Engineer", "Rome", "https://u", "https://u/apply", "Build things")
SUBMIT = {"tag": "button", "type": "submit", "in_form": True, "text": "Submit application"}
VERIFIED = json.dumps({"outcome": "submitted", "evidence": "received"})


def ready(selector: str = "#send") -> list[Any]:
    return [tool_call("finish_form", status="ready", summary="filled", submit_selector=selector, questions=[])]


@pytest.mark.parametrize(
    ("element", "touched", "sends"),
    [
        (SUBMIT, False, True),
        ({"tag": "input", "type": "submit", "in_form": True, "text": ""}, False, True),
        ({"tag": "button", "type": "", "in_form": True, "text": ""}, False, True),
        ({"tag": "div", "type": "", "in_form": False, "text": "Invia candidatura"}, False, True),
        ({"tag": "a", "type": "", "in_form": False, "text": "Submit"}, False, True),
        ({"tag": "button", "type": "button", "in_form": False, "text": "Apply"}, True, True),
        ({"tag": "button", "type": "", "in_form": True, "text": "Apply now"}, False, True),
        ({"tag": "a", "type": "", "in_form": False, "text": "Apply for this job"}, False, False),
        ({"tag": "button", "type": "button", "in_form": False, "text": "Apply"}, False, False),
        ({"tag": "button", "type": "submit", "in_form": True, "text": "Next"}, True, False),
        ({"tag": "button", "type": "", "in_form": True, "text": "Yes"}, True, False),
        ({"tag": "button", "type": "", "in_form": True, "text": "No"}, True, False),
        ({"tag": "button", "type": "", "in_form": True, "text": "Upload File"}, True, False),
        # Gmail's send control is a role=button div outside any form: its text is "Send", or "Schedule send" in the menu.
        ({"tag": "div", "type": "", "in_form": False, "text": "Send"}, True, True),
        ({"tag": "div", "type": "", "in_form": False, "text": "Schedule send"}, True, True),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Submit application"}, True, True),
        ({"tag": "button", "type": "", "in_form": True, "text": "Presenta candidatura"}, True, True),
        ({"tag": "button", "type": "", "in_form": True, "text": "Submit Application"}, True, True),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Add another"}, True, False),
        ({"tag": "input", "type": "checkbox", "in_form": True, "text": ""}, True, False),
        # A captcha's box is an iframe in the page: its own words are not the page's, and nothing in it is a send.
        ({"tag": "iframe", "type": "", "in_form": True, "text": ""}, True, False),
        ({"tag": "iframe", "type": "", "in_form": False, "text": "Widget containing a Cloudflare security challenge"}, True, False),
        ({"tag": "iframe", "type": "", "in_form": True, "text": "reCAPTCHA"}, False, False),
        # Candidatar-me is a Spanish send from a plain button. A submit-type Continue is the next step of a multi-step form,
        # and a Continue in a div is one too: neither is held back by its words.
        ({"tag": "button", "type": "button", "in_form": True, "text": "Candidatar-me"}, True, True),
        ({"tag": "button", "type": "submit", "in_form": True, "text": "Continue"}, True, False),
        ({"tag": "div", "type": "", "in_form": False, "text": "Continue"}, True, False),
        # Confirm, Finish or Complete about an email address or a profile is a step, even as a submit button.
        ({"tag": "button", "type": "submit", "in_form": True, "text": "Confirm email address"}, True, False),
        ({"tag": "button", "type": "", "in_form": True, "text": "Complete your profile"}, True, False),
        ({"tag": "div", "type": "", "in_form": False, "text": "Conferma indirizzo email"}, False, False),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Completa il profilo"}, True, False),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Finish setting up"}, True, False),
        # Confirm, Finish or Complete with an application or a submission after it sends.
        ({"tag": "button", "type": "button", "in_form": True, "text": "Confirm application"}, True, True),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Complete your job application"}, True, True),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Finish submission"}, True, True),
        ({"tag": "div", "type": "", "in_form": False, "text": "Confirmar candidatura"}, False, True),
    ],
)
def test_sends_form(element: dict[str, Any], touched: bool, sends: bool) -> None:
    assert sends_form(element, touched) is sends


@pytest.mark.parametrize(
    "word",
    [
        "Send", "Senden", "Inviar", "Verzenden", "Versturen", "Skicka", "Wyślij", "Odeslat", "Отправить", "送信", "提交", "제출",
        # The other forms of the verbs: Candidatar-me, Inviare, Envoyez, Soumettez, Einreichen, Versenden.
        "Candidatar-me", "Candidatarme", "Candidatevi", "Inviare", "Envoyez", "Soumettez", "Einreichen", "Versenden",
        "Solliciteer", "Submeter", "Başvurun", "Başvuru yap", "Подать", "提出", "投递", "申し込み", "지원하기", "تقديم",
    ],
)
def test_a_control_that_sends_in_another_language_is_held_back_even_with_no_form_around_it(word: str) -> None:
    assert sends_form({"tag": "div", "type": "", "in_form": False, "text": word}, True)


# Confirm, Finish and Complete send from any element, a div or a type=button included. A stepper's "Complete" is refused too.
@pytest.mark.parametrize(
    "element",
    [
        {"tag": "div", "type": "", "in_form": False, "text": "Confirm"},
        {"tag": "span", "type": "", "in_form": True, "text": "Finish"},
        {"tag": "button", "type": "button", "in_form": True, "text": "Complete"},
        {"tag": "div", "type": "", "in_form": True, "text": "Complete"},
        {"tag": "div", "type": "", "in_form": False, "text": "Conferma"},
        {"tag": "a", "type": "", "in_form": False, "text": "Confirm"},
    ],
)
def test_a_confirm_finish_or_complete_control_is_held_back_whatever_its_tag(element: dict[str, Any]) -> None:
    assert sends_form(element, False)


# Not every clickable sends: a form's step, a link to read on, a button that edits, all pass.
@pytest.mark.parametrize(
    "element",
    [
        {"tag": "div", "type": "", "in_form": True, "text": "Next"},
        {"tag": "span", "type": "", "in_form": False, "text": "Show more"},
        {"tag": "button", "type": "button", "in_form": True, "text": "Edit details"},
        {"tag": "a", "type": "", "in_form": False, "text": "Home"},
    ],
)
def test_a_clickable_that_does_not_send_passes(element: dict[str, Any]) -> None:
    assert not sends_form(element, True)


def test_the_browser_is_given_the_environment_without_the_model_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-default-name")
    monkeypatch.setenv("MY_MODEL_KEY", "sk-named-in-settings")
    environment = server_parameters(tmp_path, tmp_path, "MY_MODEL_KEY").env or {}
    assert "ORCAROUTER_API_KEY" not in environment and "MY_MODEL_KEY" not in environment
    assert "sk-default-name" not in environment.values() and "sk-named-in-settings" not in environment.values()
    # Everything else stays, the PATH included, so the browser can still start.
    assert environment["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize("key", ["Enter", "enter", "Control+Enter"])
def test_enter_is_refused(key: str) -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_press_key", {"key": key}, False))


def test_other_keys_pass() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_press_key", {"key": "Tab"}, False)) is None


def test_a_new_tool_of_the_server_is_offered_if_it_only_reads_and_held_back_if_it_acts() -> None:
    reading = SimpleNamespace(name="browser_something_new", annotations=SimpleNamespace(readOnlyHint=True))
    acting = SimpleNamespace(name="browser_something_new", annotations=SimpleNamespace(readOnlyHint=False))
    silent = SimpleNamespace(name="browser_something_new", annotations=None)
    assert offered(reading)
    assert not offered(acting)
    assert not offered(silent)
    assert offered(SimpleNamespace(name="browser_click", annotations=None))


def test_a_click_that_cannot_be_checked_is_refused() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_click", {"selector": "text=Whatever"}, False))


def test_the_model_is_not_offered_what_cannot_be_checked() -> None:
    assert not MODEL_TOOLS & {"browser_open", "browser_close"}
    assert {"browser_click_at", "browser_take_screenshot"} <= MODEL_TOOLS
    assert FINISH_TOOL["function"]["name"] == "finish_form"


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


def run(model: ScriptedModel, browser: Any, shot: Path, guidance: str = "", reaches: frozenset[str] = frozenset()) -> Outcome:
    return asyncio.run(run_on_page(browser, [], model, "m", JOB, "cv", "answers", Path("cv.pdf"), shot, guidance, reaches=reaches))  # type: ignore[arg-type]


def test_the_models_own_click_on_submit_never_reaches_the_browser(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT})
    model = ScriptedModel([tool_call("browser_click", selector="#send")], ready(), VERIFIED)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "submitted"
    # The one click is the program's, after the form was named ready: the model's was refused.
    assert browser.clicks() == ["#send"]
    refused = [message for message in model.requests[1]["messages"] if message.get("role") == "tool"]
    assert "Refused" in refused[0]["content"]


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


def test_a_form_that_refuses_it_comes_back_as_a_refusal_with_the_question(tmp_path: Path) -> None:
    verdict = json.dumps({"outcome": "rejected", "evidence": "phone is required"})
    outcome = run(ScriptedModel(ready(), verdict), ScriptedBrowser({"#send": SUBMIT}), tmp_path / "proof.png")
    assert (outcome.status, outcome.questions) == ("refused", ["phone is required"])


def test_what_the_files_do_not_say_comes_back_as_questions_and_nothing_is_sent(tmp_path: Path) -> None:
    browser = ScriptedBrowser()
    step = [tool_call("finish_form", status="needs_input", summary="missing", submit_selector="", questions=["Salary?"])]
    outcome = run(ScriptedModel(step), browser, tmp_path / "proof.png")
    assert (outcome.status, outcome.questions) == ("needs_input", ["Salary?"])
    assert browser.clicks() == []


@pytest.mark.parametrize(
    "step",
    [
        [tool_call("finish_form", status="blocked", summary="captcha", submit_selector="", questions=[])],
        [tool_call("finish_form", status="ready", summary="no selector", submit_selector="", questions=[])],
    ],
)
def test_blocked_or_no_control_sends_nothing(step: list[Any], tmp_path: Path) -> None:
    browser = ScriptedBrowser()
    assert run(ScriptedModel(step), browser, tmp_path / "proof.png").status == "blocked"
    assert browser.clicks() == []


def test_a_tool_the_model_was_not_offered_is_refused() -> None:
    browser = ScriptedBrowser()
    report = asyncio.run(fill_form(browser, [], ScriptedModel([tool_call("browser_close")], ready()), "m", "brief"))  # type: ignore[arg-type]
    assert report["status"] == "ready"
    assert browser.calls == []


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


def test_a_click_at_a_point_on_the_submit_control_is_refused_and_it_asks_about_that_point() -> None:
    browser = PageAtAPoint(SUBMIT)
    assert asyncio.run(refusal(browser, "browser_click_at", {"x": 10, "y": 20}, True))
    assert "elementFromPoint(10.0, 20.0)" in browser.calls[0][1]["expression"]


def test_a_click_at_a_point_on_anything_else_passes() -> None:
    field = {"tag": "input", "type": "text", "in_form": True, "text": ""}
    assert asyncio.run(refusal(PageAtAPoint(field), "browser_click_at", {"x": 10, "y": 20}, True)) is None


def test_a_click_at_a_captcha_box_in_its_frame_passes() -> None:
    frame = {"tag": "iframe", "type": "", "in_form": True, "text": ""}
    assert asyncio.run(refusal(PageAtAPoint(frame), "browser_click_at", {"x": 10, "y": 20}, True)) is None


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


def test_a_captcha_box_is_clicked_twice_at_most_and_the_form_is_still_sent_after_it(tmp_path: Path) -> None:
    browser = CaptchaPage()
    captcha = tool_call("browser_click_at", x=40, y=50)
    model = ScriptedModel([captcha], [tool_call("browser_click_at", call_id="again", x=40, y=50)], [tool_call("browser_click", selector="#apply")], ready(), VERIFIED)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "submitted"
    assert [name for name, _ in browser.calls].count("browser_click_at") == 2
    # The box is not typed text: it does not count as touching the form, so an Apply outside it stays a link.
    assert browser.clicks() == ["#apply", "#send"]


def test_script_is_offered_as_the_last_rung_and_refusal_does_not_check_it() -> None:
    tool = SimpleNamespace(name="browser_evaluate", annotations=SimpleNamespace(readOnlyHint=True))
    assert offered(tool)
    assert "browser_evaluate" in MODEL_TOOLS
    # Known gap, written down: a script that sends the form is not refused, by design of the server's own check.
    send = {"expression": "document.querySelector('#send').requestSubmit?.()"}
    assert asyncio.run(refusal(PageAtAPoint(SUBMIT), "browser_evaluate", send, True)) is None


@pytest.mark.parametrize("arguments", [{"key": " "}, {"key": "Space"}, {"renamed": "space"}])
def test_space_on_the_submit_control_is_refused(arguments: dict[str, str]) -> None:
    browser = PageAtAPoint(SUBMIT)
    assert asyncio.run(refusal(browser, "browser_press_key", arguments, True))
    assert "activeElement" in browser.calls[0][1]["expression"]


def test_space_on_a_checkbox_passes() -> None:
    box = {"tag": "input", "type": "checkbox", "in_form": True, "text": ""}
    assert asyncio.run(refusal(PageAtAPoint(box), "browser_press_key", {"key": " "}, True)) is None


def test_enter_is_refused_whatever_the_argument_is_called() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_press_key", {"renamed": "Enter"}, True))


def tag_field(placeholder: str = "", aria_label: str = "", tag: str = "input", kind: str = "text") -> dict[str, Any]:
    return {"tag": tag, "type": kind, "in_form": True, "text": "", "placeholder": placeholder, "aria_label": aria_label}


@pytest.mark.parametrize(
    "placeholder",
    [
        "Type a skill and press enter",
        "Premi invio dopo ogni competenza",
        "Drücke Enter nach jeder Fähigkeit",
        "Appuyez sur Entrée pour ajouter",
        "Pulsa Intro tras cada habilidad",
    ],
)
def test_enter_is_allowed_in_a_field_whose_own_placeholder_asks_for_it(placeholder: str) -> None:
    browser = PageAtAPoint(tag_field(placeholder))
    assert asyncio.run(refusal(browser, "browser_press_key", {"key": "Enter"}, True)) is None
    assert "activeElement" in browser.calls[0][1]["expression"]


def test_enter_is_allowed_when_the_aria_label_asks_for_it() -> None:
    assert asyncio.run(refusal(PageAtAPoint(tag_field(aria_label="Skills, press enter to add")), "browser_press_key", {"key": "Enter"}, True)) is None


@pytest.mark.parametrize(
    "element",
    [
        tag_field("Search jobs"),
        tag_field(""),
        tag_field("press enter", tag="button", kind="button"),
        tag_field("press enter", kind="submit"),
        tag_field("press enter", kind="checkbox"),
        tag_field("press enter", tag="div", kind=""),
    ],
)
def test_enter_is_refused_unless_the_focused_field_is_a_text_input_that_asks_for_it(element: dict[str, Any]) -> None:
    assert asyncio.run(refusal(PageAtAPoint(element), "browser_press_key", {"key": "Enter"}, True))


def test_enter_is_refused_when_the_focus_cannot_be_told() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_press_key", {"key": "Enter"}, True))


@pytest.mark.parametrize("key", ["Control+Enter", "Shift+Enter", "Enter+Control", "NumpadEnter", "Return"])
def test_a_combination_with_enter_is_refused_even_in_the_tag_field(key: str) -> None:
    assert asyncio.run(refusal(PageAtAPoint(tag_field("Type a skill and press enter")), "browser_press_key", {"key": key}, True))


def test_a_textarea_that_asks_for_enter_is_allowed() -> None:
    area = tag_field("press enter to add", tag="textarea", kind="")
    assert asyncio.run(refusal(PageAtAPoint(area), "browser_press_key", {"key": "Enter"}, True)) is None


def test_a_new_line_is_refused_except_in_a_text_area() -> None:
    field = {"tag": "input", "type": "text", "in_form": True, "text": ""}
    area = {"tag": "textarea", "type": "", "in_form": True, "text": ""}
    arguments = {"selector": "#a", "text": "one\ntwo"}
    assert asyncio.run(refusal(PageAtAPoint(field), "browser_type", arguments, True))
    assert asyncio.run(refusal(PageAtAPoint(area), "browser_type", arguments, True)) is None
    assert asyncio.run(refusal(PageAtAPoint(field), "browser_type", {"selector": "#a", "text": "one"}, True)) is None


def test_an_image_input_sends_and_a_word_with_no_edge_is_found() -> None:
    assert sends_form({"tag": "input", "type": "image", "in_form": True, "text": ""}, True)
    assert sends_form({"tag": "div", "type": "", "in_form": False, "text": "送信する"}, True)


def test_a_named_control_that_does_not_send_is_not_clicked_even_after_the_form_is_ready(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#delete": {"tag": "button", "type": "button", "in_form": True, "text": "Delete my account"}})
    outcome = run(ScriptedModel(ready("#delete")), browser, tmp_path / "proof.png")
    assert outcome.status == "blocked"
    assert browser.clicks() == []


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


def test_a_model_that_never_finishes_is_stopped_with_what_it_did_and_nothing_is_sent() -> None:
    told = tool_call("finish_form", status="blocked", summary="typed Test into First name", submit_selector="", questions=[])
    model = ScriptedModel(*["thinking"] * 80, [told])
    report = asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report["status"] == "blocked"
    assert report["summary"].startswith("Stopped: the form was not finished in 80 steps.") and "typed Test into First name" in report["summary"]
    assert model.requests[-1]["tool_choice"]["function"]["name"] == "finish_form"


def test_the_same_refused_call_three_times_stops_the_form_with_its_questions_and_is_never_run() -> None:
    click = [tool_call("browser_click", selector="#a >> #b")]
    told = tool_call("finish_form", status="needs_input", summary="typed Test", submit_selector="", questions=["Which office?"])
    model = ScriptedModel(click, click, click, [told])
    browser = ScriptedBrowser()
    report = asyncio.run(fill_form(browser, [], model, "m", "brief"))  # type: ignore[arg-type]
    assert (report["status"], report["questions"]) == ("needs_input", ["Which office?"])
    assert report["summary"].startswith("Stopped: the same call was refused three times")
    assert not [name for name, _ in browser.calls if name == "browser_click"]
    # The model's list is kept whole, so the second refusal is the second tool message, and only that one is told once.
    answers = [m["content"] for m in model.requests[-1]["messages"] if m.get("role") == "tool"]
    assert "twice" in answers[1] and "twice" not in answers[0] and "stopped" in answers[2]


def test_a_refused_selector_is_told_to_be_clicked_by_its_centre_instead() -> None:
    message = asyncio.run(refusal(ScriptedBrowser(), "browser_click", {"selector": "#a >> #b"}, False))
    assert message and "browser_click_at" in message and "shadow" in message


def test_the_plugins_text_reaches_the_verifying_model_as_its_rules_while_the_page_stays_text_to_check(tmp_path: Path) -> None:
    model = ScriptedModel(ready(), VERIFIED)
    guidance = "After sending: a page that says Thank you has sent it."
    run(model, ScriptedBrowser({"#send": SUBMIT}), tmp_path / "proof.png", guidance)
    verifying = model.requests[-1]["messages"]
    assert guidance in verifying[0]["content"]
    assert verifying[1]["content"].endswith("Thank you, your application was received")
    assert guidance not in verifying[1]["content"]


def test_a_send_slow_to_show_is_judged_on_the_page_it_reached_with_its_address(tmp_path: Path) -> None:
    model = ScriptedModel(ready(), json.dumps({"outcome": "submitted", "evidence": "the page"}))
    outcome = run(model, SlowSend(reads_until_moved=8), tmp_path / "shot.png")
    judged = model.requests[-1]["messages"][-1]["content"]
    assert "https://u/thanks" in judged and "Application received" in judged and "the form" not in judged
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
    model = ScriptedModel([tool_call("finish_form", status="blocked", summary="closed", submit_selector="", questions=[])])
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


def test_sign_in_opens_the_profile_on_a_blank_page_and_closes_it_when_the_person_is_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer()
    scripted_server(monkeypatch, server)
    profile = tmp_path / "browser-profile"
    waited: list[tuple[str, ...]] = []

    async def wait() -> None:
        waited.append(tuple(name for name, _ in server.calls))

    asyncio.run(sign_in(profile, tmp_path, wait))
    assert waited == [("browser_open", "browser_navigate")]
    assert server.calls == [("browser_open", {"profile": str(profile)}), ("browser_navigate", {"url": "about:blank"}), ("browser_close", {})]


def test_sign_in_closes_the_browser_even_when_the_wait_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ScriptedServer()
    scripted_server(monkeypatch, server)

    async def broken() -> None:
        raise RuntimeError("the window was closed")

    with pytest.raises(RuntimeError, match="window was closed"):
        asyncio.run(sign_in(tmp_path / "browser-profile", tmp_path, broken))
    assert server.calls[-1] == ("browser_close", {})


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


def test_the_model_may_go_to_the_forms_site_and_to_a_mailbox_a_plugin_reaches_and_nowhere_else() -> None:
    browser = ScriptedBrowser()
    mailbox = frozenset({"u", "mail.google.com"})
    assert asyncio.run(refusal(browser, "browser_navigate", {"url": "https://u/other"}, False, mailbox)) is None
    assert asyncio.run(refusal(browser, "browser_navigate", {"url": "https://mail.google.com/mail/u/0/"}, False, mailbox)) is None
    assert asyncio.run(refusal(browser, "browser_navigate", {"url": "https://evil.example/confirm?action=submit"}, False, mailbox))


def test_a_navigation_by_the_model_to_another_site_is_refused_and_never_reaches_the_browser(tmp_path: Path) -> None:
    browser = ScriptedBrowser({"#send": SUBMIT}, page=FORM_PAGE)
    model = ScriptedModel([tool_call("browser_navigate", url="https://evil.example/")], ready(), VERIFIED)
    assert run(model, browser, tmp_path / "proof.png").status == "submitted"
    assert [args["url"] for name, args in browser.calls if name == "browser_navigate"] == []


def test_a_change_event_that_moves_the_page_off_the_form_stops_as_unconfirmed_and_nothing_is_retried(tmp_path: Path) -> None:
    browser = Moves(after={"url": "https://thanks.example/ok", "text": "Thanks"})
    model = ScriptedModel(SELECT, ready(), VERIFIED)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "unconfirmed"
    assert "thanks.example" in outcome.summary
    assert browser.clicks() == []
    assert len(model.requests) == 1


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


def test_a_script_that_moves_the_page_off_the_form_stops_as_unconfirmed_and_nothing_is_retried(tmp_path: Path) -> None:
    browser = ScriptMoves(after={"url": "https://thanks.example/ok", "text": "Thanks"})
    model = ScriptedModel([tool_call("browser_evaluate", expression="location.href = 'https://thanks.example/ok'")], ready(), VERIFIED)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "unconfirmed"
    assert "thanks.example" in outcome.summary
    assert browser.clicks() == []
    assert len(model.requests) == 1


def test_a_change_that_stays_on_the_site_goes_on_to_the_form_so_a_same_site_send_is_not_detected(tmp_path: Path) -> None:
    # The address alone cannot tell a multi-step form from a send that stays on the site: this is the gap, written down.
    browser = Moves(after={"url": "https://u/apply", "text": "Thank you, your application was received"})
    model = ScriptedModel(SELECT, ready(), VERIFIED)
    assert run(model, browser, tmp_path / "proof.png").status == "submitted"


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_module, "READ_WAIT_S", 0)


def test_a_page_still_loading_after_a_change_is_read_again_until_its_host_shows(no_wait: None, tmp_path: Path) -> None:
    browser = Moves(after={"url": "https://thanks.example/ok", "text": "Thanks"}, unreadable=2)
    model = ScriptedModel(SELECT)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "unconfirmed"
    assert "thanks.example" in outcome.summary
    assert browser.clicks() == []
    assert len(model.requests) == 1


def test_a_page_that_stays_unreadable_after_a_change_does_not_end_the_form(no_wait: None, tmp_path: Path) -> None:
    browser = Moves(after={"url": "https://u/apply", "text": "x"}, unreadable=100)
    model = ScriptedModel(SELECT, ready(), VERIFIED)
    assert run(model, browser, tmp_path / "proof.png").status == "submitted"
    assert browser.clicks() == ["#send"]


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


def test_a_read_that_fails_after_a_type_does_not_end_the_form_and_the_form_is_sent(no_wait: None, tmp_path: Path) -> None:
    browser = Typing()
    model = ScriptedModel([tool_call("browser_type", selector="#name", text="Ada Rossi")], ready(), VERIFIED)
    outcome = run(model, browser, tmp_path / "proof.png")
    assert outcome.status == "submitted"
    assert browser.clicks() == ["#send"]
    assert len(model.requests) == 3


def test_a_mail_trip_to_the_mailbox_and_back_does_not_stop_the_form(tmp_path: Path) -> None:
    class Mail(ScriptedBrowser):
        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            if name == "browser_click":
                self.page = {"url": "https://mail.google.com/mail/u/0/#inbox/1", "text": "Your code is 123456"}
            return await super().call_tool(name, arguments)

    steps = [
        [tool_call("browser_navigate", url="https://mail.google.com/mail/u/0/")],
        [tool_call("browser_click", selector="#msg")],
        [tool_call("browser_navigate", url="https://u/apply")],
        ready(),
        VERIFIED,
    ]
    browser = Mail({"#send": SUBMIT, "#msg": {"tag": "a", "type": "", "in_form": False, "text": "Verification code"}}, page=FORM_PAGE)
    model = ScriptedModel(*steps)
    outcome = run(model, browser, tmp_path / "proof.png", reaches=frozenset({"mail.google.com"}))
    assert outcome.status == "submitted"
    # No verdict was asked for the mail click: the five requests are the four steps and the verdict on the submit.
    assert len(model.requests) == 5


@pytest.mark.parametrize(
    ("answers", "score"),
    [
        ({"role": "yes", "must_haves": "yes", "level": "yes", "location": "yes"}, 5.0),
        ({"role": "yes", "must_haves": "yes", "level": "unclear", "location": "unclear"}, 4.5),
        ({"role": "yes", "must_haves": "unclear", "level": "unclear", "location": "unclear"}, 3.5),
        ({"role": "unclear", "must_haves": "unclear", "level": "unclear", "location": "unclear"}, 2.5),
        ({"role": "yes", "must_haves": "no", "level": "yes", "location": "yes"}, 2.5),
        ({"role": "yes", "must_haves": "yes", "level": "yes", "location": "no"}, 1.0),
        ({"role": "no", "must_haves": "yes", "level": "yes", "location": "yes"}, 2.5),
        ({"role": "no", "must_haves": "no", "level": "no", "location": "no"}, 1.0),
    ],
)
def test_the_score_comes_from_closed_answers_and_unclear_moves_nothing(answers: dict[str, str], score: float) -> None:
    assert score_from(answers) == score


def test_the_goals_reach_the_model_and_the_score_is_computed_not_asked() -> None:
    reply = {"role": "yes", "must_haves": "yes", "level": "unclear", "location": "unclear", "summary": "s", "gaps": [], "legitimacy": "credible"}
    model = ScriptedModel(json.dumps(reply))
    report = asyncio.run(score_job(model, "m", JOB, "the cv", "remote only"))  # type: ignore[arg-type]
    assert report["score"] == 4.5
    assert "remote only" in model.requests[0]["messages"][1]["content"]


def test_tailor_drops_a_code_fence() -> None:
    model = ScriptedModel("```markdown\n# Name\n```")
    assert asyncio.run(tailor_cv(model, "m", JOB, "cv")) == "# Name"  # type: ignore[arg-type]


def test_a_cv_in_a_script_the_font_cannot_draw_is_not_uploaded_and_the_persons_own_is_sent_instead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def tailored(client: Any, model: str, job: Job, cv: str) -> str:
        return "# Иван Петров\n\n- Опыт работы"

    monkeypatch.setattr(session_module, "tailor_cv", tailored)
    session = Session(tmp_path)
    session._client = SimpleNamespace()  # type: ignore[assignment]  # tailor_cv is stubbed, so no call is made
    own = tmp_path / "data" / "cvs" / "original-cv.pdf"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"%PDF-1.4 the person's CV")
    session.store.add(JOB)
    said: list[str] = []
    path = asyncio.run(session._cv_for(session.store.with_status("new")[0], "cv", said.append))
    session.close()
    assert path == own
    assert list((tmp_path / "data" / "cvs").glob("*.pdf")) == [own]
    assert said[0] == "writing a CV for this job"
    assert len(said) == 2 and said[1].startswith("the CV written for this job cannot show ") and "your own CV is sent" in said[1]


def test_the_pdf_holds_the_text_accents_and_markup_safely(tmp_path: Path) -> None:
    path = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Niccolò Rossi\n\n## Experience\n- **Lead** at A&B <Corp>\n\nPlain line", path)
    text = " ".join(page.extract_text() for page in PdfReader(path).pages)
    for piece in ("Niccolò Rossi", "Experience", "Lead", "A&B <Corp>", "Plain line"):
        assert piece in text


def test_asking_for_a_sign_in_code_is_not_a_send_but_submitting_one_is() -> None:
    def button(text: str) -> dict[str, object]:
        return {"tag": "button", "type": "submit", "in_form": True, "text": text}

    for text in (
        "Send code", "Send verification code", "Send me a code", "Invia codice", "Enviar código", "Code senden",
        "Solicitar código de verificación",
    ):
        assert not sends_form(button(text), True), text
    # A send word beside the code request still sends: the request is taken out and the rest is judged.
    for text in (
        "Submit code", "Send", "Send application", "Submit application and send code", "Send application and code",
        "Solicitar empleo", "Solicitar",
    ):
        assert sends_form(button(text), True), text
