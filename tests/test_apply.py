"""The rule that matters: the model cannot send an application, and only a yes (or --auto-submit) does."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from aihawk.apply import FINISH_TOOL, MODEL_TOOLS, SYSTEM, Outcome, fill_form, instructions, offered, refusal, run_on_page, sends_form
from aihawk.database import Job
from aihawk.documents import score_from, score_job, screen_titles, tailor_cv, write_cv_pdf
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
        ({"tag": "button", "type": "button", "in_form": True, "text": "Submit application"}, True, True),
        ({"tag": "button", "type": "", "in_form": True, "text": "Presenta candidatura"}, True, True),
        ({"tag": "button", "type": "", "in_form": True, "text": "Submit Application"}, True, True),
        ({"tag": "button", "type": "button", "in_form": True, "text": "Add another"}, True, False),
        ({"tag": "input", "type": "checkbox", "in_form": True, "text": ""}, True, False),
    ],
)
def test_sends_form(element: dict[str, Any], touched: bool, sends: bool) -> None:
    assert sends_form(element, touched) is sends


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
    assert {"browser_click_at", "browser_take_screenshot", "browser_evaluate"} <= MODEL_TOOLS
    assert FINISH_TOOL["function"]["name"] == "finish_form"


def run(model: ScriptedModel, browser: ScriptedBrowser, confirm: Any) -> Outcome:
    return asyncio.run(run_on_page(browser, [], model, "m", JOB, "cv", "answers", Path("cv.pdf"), confirm))  # type: ignore[arg-type]


def test_the_models_own_click_on_submit_never_reaches_the_browser() -> None:
    browser = ScriptedBrowser({"#send": SUBMIT})
    model = ScriptedModel([tool_call("browser_click", selector="#send")], ready(), VERIFIED)
    asked: list[str] = []

    async def no(summary: str) -> bool:
        asked.append(summary)
        return False

    outcome = run(model, browser, no)
    assert outcome.status == "declined"
    assert browser.clicks() == []
    assert asked == ["filled"]
    refused = [message for message in model.requests[1]["messages"] if message.get("role") == "tool"]
    assert "Refused" in refused[0]["content"]


def test_a_yes_clicks_once_and_the_page_confirms() -> None:
    browser = ScriptedBrowser({"#send": SUBMIT})
    model = ScriptedModel(ready(), VERIFIED)

    async def yes(summary: str) -> bool:
        return True

    outcome = run(model, browser, yes)
    assert outcome.status == "submitted"
    assert browser.clicks() == ["#send"]


def test_auto_submit_does_not_ask_and_still_verifies() -> None:
    browser = ScriptedBrowser()
    outcome = run(ScriptedModel(ready(), VERIFIED), browser, None)
    assert outcome.status == "submitted"
    assert browser.clicks() == ["#send"]


def test_a_click_the_page_does_not_confirm_is_not_called_sent() -> None:
    verdict = json.dumps({"outcome": "unknown", "evidence": "page unchanged"})
    assert run(ScriptedModel(ready(), verdict), ScriptedBrowser(), None).status == "unconfirmed"


def test_a_form_that_refuses_it_comes_back_as_a_question() -> None:
    verdict = json.dumps({"outcome": "rejected", "evidence": "phone is required"})
    outcome = run(ScriptedModel(ready(), verdict), ScriptedBrowser(), None)
    assert (outcome.status, outcome.questions) == ("needs_input", ["phone is required"])


def test_what_the_files_do_not_say_comes_back_as_questions_and_nothing_is_sent() -> None:
    browser = ScriptedBrowser()
    step = [tool_call("finish_form", status="needs_input", summary="missing", submit_selector="", questions=["Salary?"])]
    outcome = run(ScriptedModel(step), browser, None)
    assert (outcome.status, outcome.questions) == ("needs_input", ["Salary?"])
    assert browser.clicks() == []


@pytest.mark.parametrize("step", [
    [tool_call("finish_form", status="blocked", summary="captcha", submit_selector="", questions=[])],
    [tool_call("finish_form", status="ready", summary="no selector", submit_selector="", questions=[])],
])
def test_blocked_or_no_control_sends_nothing(step: list[Any]) -> None:
    browser = ScriptedBrowser()
    assert run(ScriptedModel(step), browser, None).status == "blocked"
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


@pytest.mark.parametrize("expression", ["fetch('/apply', {method: 'POST'})", "new XMLHttpRequest()", "navigator.sendBeacon('/a')"])
def test_script_that_reaches_out_is_refused(expression: str) -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_evaluate", {"expression": expression}, True))


def test_script_that_reads_passes() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_evaluate", {"expression": "document.title"}, True)) is None


def test_a_screenshot_reaches_the_model_after_the_answers_it_must_follow() -> None:
    model = ScriptedModel([tool_call("browser_take_screenshot")], ready())
    asyncio.run(fill_form(PageAtAPoint(SUBMIT), [], model, "m", "brief"))  # type: ignore[arg-type]
    last = model.requests[1]["messages"][-2]
    assert last["role"] == "user"
    assert last["content"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]
    assert model.requests[1]["messages"][-3]["role"] == "tool"


def test_the_servers_way_of_driving_a_page_comes_after_the_rules_and_before_the_site() -> None:
    text = instructions("About a site.", "Try selectors first.")
    assert text.index(SYSTEM) < text.index("Try selectors first.") < text.index("About a site.")


def test_a_model_that_never_finishes_fails() -> None:
    model = ScriptedModel(*["thinking"] * 80)
    with pytest.raises(RuntimeError, match="did not finish"):
        asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("answers", "score"),
    [
        ({"role": "yes", "must_haves": "yes", "level": "yes", "location": "yes"}, 5.0),
        ({"role": "yes", "must_haves": "yes", "level": "unclear", "location": "unclear"}, 4.5),
        ({"role": "yes", "must_haves": "unclear", "level": "unclear", "location": "unclear"}, 3.5),
        ({"role": "unclear", "must_haves": "unclear", "level": "unclear", "location": "unclear"}, 2.5),
        ({"role": "yes", "must_haves": "no", "level": "yes", "location": "yes"}, 2.5),
        ({"role": "yes", "must_haves": "yes", "level": "yes", "location": "no"}, 3.0),
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


def test_screening_drops_only_a_plain_no() -> None:
    jobs = [JOB, replace(JOB, id="b", title="Cook"), replace(JOB, id="c", title="Unsure")]
    kept = asyncio.run(screen_titles(ScriptedModel(json.dumps({"ratings": [3, 0, 1]})), "m", jobs, "goals"))  # type: ignore[arg-type]
    assert [job.id for job in kept] == [JOB.id, "c"]


def test_a_screening_that_does_not_rate_every_title_drops_nothing() -> None:
    jobs = [JOB, replace(JOB, id="b")]
    kept = asyncio.run(screen_titles(ScriptedModel(json.dumps({"ratings": [0]})), "m", jobs, "goals"))  # type: ignore[arg-type]
    assert kept == jobs


def test_tailor_drops_a_code_fence() -> None:
    model = ScriptedModel("```markdown\n# Name\n```")
    assert asyncio.run(tailor_cv(model, "m", JOB, "cv")) == "# Name"  # type: ignore[arg-type]


def test_the_pdf_holds_the_text_accents_and_markup_safely(tmp_path: Path) -> None:
    path = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Niccolò Rossi\n\n## Experience\n- **Lead** at A&B <Corp>\n\nPlain line", path)
    text = " ".join(page.extract_text() for page in PdfReader(path).pages)
    for piece in ("Niccolò Rossi", "Experience", "Lead", "A&B <Corp>", "Plain line"):
        assert piece in text
