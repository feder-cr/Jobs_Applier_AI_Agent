"""The rule that matters: the model cannot send an application, and only a yes (or --auto-submit) does."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from aihawk.apply import FINISH_TOOL, MODEL_TOOLS, Outcome, fill_form, refusal, run_on_page, sends_form
from aihawk.database import Job
from aihawk.documents import score_job, tailor_cv, write_cv_pdf
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


def test_a_click_that_cannot_be_checked_is_refused() -> None:
    assert asyncio.run(refusal(ScriptedBrowser(), "browser_click", {"selector": "text=Whatever"}, False))


def test_the_model_is_not_offered_what_cannot_be_checked() -> None:
    assert not MODEL_TOOLS & {"browser_evaluate", "browser_click_at", "browser_open", "browser_close"}
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
    report = asyncio.run(fill_form(browser, [], ScriptedModel([tool_call("browser_click_at", x=1, y=1)], ready()), "m", "brief"))  # type: ignore[arg-type]
    assert report["status"] == "ready"
    assert browser.calls == []


def test_a_model_that_never_finishes_fails() -> None:
    model = ScriptedModel(*["thinking"] * 80)
    with pytest.raises(RuntimeError, match="did not finish"):
        asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]


def test_score_is_held_to_its_scale_and_the_goals_reach_the_model() -> None:
    fields = {"summary": "s", "gaps": [], "legitimacy": "credible", "confidence": "high", "cv_match": "", "goal_alignment": "", "compensation": "", "culture": "", "red_flags": ""}
    model = ScriptedModel(json.dumps({"score": 9, **fields}))
    report = asyncio.run(score_job(model, "m", JOB, "the cv", "remote only"))  # type: ignore[arg-type]
    assert report["score"] == 5.0
    assert "remote only" in model.requests[0]["messages"][1]["content"]


def test_tailor_drops_a_code_fence() -> None:
    model = ScriptedModel("```markdown\n# Name\n```")
    assert asyncio.run(tailor_cv(model, "m", JOB, "cv")) == "# Name"  # type: ignore[arg-type]


def test_the_pdf_holds_the_text_accents_and_markup_safely(tmp_path: Path) -> None:
    path = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Niccolò Rossi\n\n## Experience\n- **Lead** at A&B <Corp>\n\nPlain line", path)
    text = " ".join(page.extract_text() for page in PdfReader(path).pages)
    for piece in ("Niccolò Rossi", "Experience", "Lead", "A&B <Corp>", "Plain line"):
        assert piece in text
