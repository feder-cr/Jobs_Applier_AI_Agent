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
    PAGE_CHARS,
    SYSTEM,
    Outcome,
    apply_to_job,
    fill_form,
    instructions,
    run_on_page,
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
    return [tool_call("finish_form", status="ready", summary="filled", submit_selector=selector)]


# Confirm, Finish and Complete send from any element, a div or a type=button included. A stepper's "Complete" is refused too.


# Not every clickable sends: a form's step, a link to read on, a button that edits, all pass.


def test_the_browser_is_given_the_environment_without_the_model_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-default-name")
    monkeypatch.setenv("MY_MODEL_KEY", "sk-named-in-settings")
    environment = server_parameters(tmp_path, tmp_path, "MY_MODEL_KEY").env or {}
    assert "ORCAROUTER_API_KEY" not in environment and "MY_MODEL_KEY" not in environment
    assert "sk-default-name" not in environment.values() and "sk-named-in-settings" not in environment.values()
    # Everything else stays, the PATH included, so the browser can still start.
    assert environment["PATH"] == os.environ["PATH"]


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


def test_a_model_that_never_finishes_is_blocked_and_nothing_is_sent() -> None:
    model = ScriptedModel(*["thinking"] * 80)
    report = asyncio.run(fill_form(ScriptedBrowser(), [], model, "m", "brief"))  # type: ignore[arg-type]
    assert report == {"status": "blocked", "summary": "The form was not finished in 80 steps."}


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
    assert said[0] == f"{JOB.company}: writing a CV for this job"
    assert len(said) == 2 and said[1].startswith(f"{JOB.company}: the CV written for this job cannot show ") and "your own CV is sent" in said[1]


def test_the_pdf_holds_the_text_accents_and_markup_safely(tmp_path: Path) -> None:
    path = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Niccolò Rossi\n\n## Experience\n- **Lead** at A&B <Corp>\n\nPlain line", path)
    text = " ".join(page.extract_text() for page in PdfReader(path).pages)
    for piece in ("Niccolò Rossi", "Experience", "Lead", "A&B <Corp>", "Plain line"):
        assert piece in text


