"""The real invisible-playwright-mcp and its Firefox on a real form: only the model is scripted."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from aihawk.apply import _open_browser, _server, apply_to_job, fill_form, server_parameters, tool_text
from aihawk.database import Job
from fakes import ScriptedModel, tool_call

pytestmark = pytest.mark.browser

FORM = b"""<!doctype html><title>Apply</title>
<form method="post" action="/sent" enctype="multipart/form-data">
<label>Name <input id="name" name="name"></label>
<label>Resume <input id="resume" type="file" name="resume"></label>
<input id="clicked" type="hidden" name="clicked" value="no">
<button id="add" type="button" onclick="document.getElementById('clicked').value='harmless-click-reached-the-page'">Add another</button>
<button id="send">Submit application</button>
</form>"""

# A form that sends itself when its select changes: the change is the send, and no button is pressed. It leaves for
# another host (a second loopback address) by a form submit, which navigates the page.
SELECT_FORM = b"""<!doctype html><title>Pick</title>
<form method="post" action="%s"><select id="pick" name="pick" onchange="this.form.submit()">
<option value="">Choose</option><option value="a">A</option></select></form>"""
# A tag field like Paylocity's skills: its Enter adds a tag and stops there, as a page's script does. The form has a submit
# button, so an Enter that reached the form would send it: the POST count shows that it did not.
TAG_FORM = b"""<!doctype html><title>Skills</title>
<form method="post" action="/sent">
<input id="skill" type="text" placeholder="Type a skill and press enter">
<ul id="tags"></ul>
<button id="send">Submit application</button>
</form>
<script>
const field = document.getElementById('skill');
field.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  if (field.value) { const tag = document.createElement('li'); tag.textContent = field.value; document.getElementById('tags').append(tag); field.value = ''; }
});
</script>"""
# The same send on the same site, by a script: the page stays where it is and only its text changes.
SAME_SITE_SELECT = b"""<!doctype html><title>Pick</title>
<select id="pick" name="pick" onchange="fetch('/sent', {method: 'POST', body: new URLSearchParams({pick: this.value})}).then(() => { document.body.innerHTML = '<h1>Thank you, your application was received</h1>'; })">
<option value="">Choose</option><option value="a">A</option></select>"""


# The send control sits inside an open shadow root of a custom element, as SmartRecruiters' form does. The host is the only
# thing a document-level look at that point finds, so the check must read into the root to see what the mouse reaches.
SHADOW_FORM = b"""<!doctype html><title>Shadow</title>
<x-field id="host" style="position:absolute;left:100px;top:100px;display:block;width:200px;height:60px"></x-field>
<script>
document.getElementById('host').attachShadow({mode: 'open'}).innerHTML =
  '<form method="post" action="/"><button id="send" type="submit" style="width:200px;height:60px;margin:0">Submit application</button></form>';
</script>"""

# Buttons that record their click in window.clicked, so a test sees which element the click reached.
RECORD = "window.clicked = (window.clicked || []).concat(%r)"
# A harmless button inside an open shadow root, and a send button of the same shape: the first is the host's `#inner`.
SHADOW_INNER = b"""<!doctype html><title>Inner</title>
<x-field id="host" style="position:absolute;left:100px;top:100px;display:block;width:200px;height:60px"></x-field>
<script>
const root = document.getElementById('host').attachShadow({mode: 'open'});
root.innerHTML = '<button id="inner" style="width:200px;height:60px;margin:0">Add another</button>';
root.getElementById('inner').onclick = () => { %s };
</script>""" % (RECORD % "inner").encode()


def mixed(light: str, shadow: str) -> bytes:
    """Two buttons with the same class: one in the page, one inside an open shadow root. The selector `.go` matches both."""
    return (
        b"""<!doctype html><title>Mixed</title>
<button class="go" id="light" style="position:absolute;left:400px;top:300px;width:200px;height:60px;margin:0">%s</button>
<x-field id="host" style="position:absolute;left:100px;top:100px;display:block;width:200px;height:60px"></x-field>
<script>
const root = document.getElementById('host').attachShadow({mode: 'open'});
root.innerHTML = '<button class="go" id="shadow" style="width:200px;height:60px;margin:0">%s</button>';
document.getElementById('light').onclick = () => { %s };
root.getElementById('shadow').onclick = () => { %s };
</script>"""
        % (light.encode(), shadow.encode(), (RECORD % "light").encode(), (RECORD % "shadow").encode())
    )


# The page's own match is a send button and the shadow one is harmless; the click by `.go` must be refused.
MIXED_SEND_LIGHT = mixed("Submit application", "Add another")
# The page's own match is harmless and the shadow one is a send button; the click by `.go` must reach the page's own one.
MIXED_HARMLESS_LIGHT = mixed("Add another", "Submit application")


class Elsewhere(BaseHTTPRequestHandler):
    """Another host of the loopback: a form that leaves the site lands here."""

    posts: ClassVar[list[bytes]] = []

    def do_POST(self) -> None:
        Elsewhere.posts.append(self.rfile.read(int(self.headers["Content-Length"])))
        body = b"<title>Thanks</title><h1>Thank you, your application was received</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class Site(BaseHTTPRequestHandler):
    posts: ClassVar[list[bytes]] = []
    leave_to: ClassVar[str] = ""  # where the /leave form posts: another host

    def _page(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/select":
            self._page(SAME_SITE_SELECT)
        elif self.path == "/leave":
            self._page(SELECT_FORM % Site.leave_to.encode())
        elif self.path == "/skills":
            self._page(TAG_FORM)
        elif self.path == "/shadow":
            self._page(SHADOW_FORM)
        elif self.path == "/inner":
            self._page(SHADOW_INNER)
        elif self.path == "/mixed":
            self._page(MIXED_HARMLESS_LIGHT)
        elif self.path == "/mixed-send":
            self._page(MIXED_SEND_LIGHT)
        else:
            self._page(FORM)

    def do_POST(self) -> None:
        Site.posts.append(self.rfile.read(int(self.headers["Content-Length"])))
        self._page(b"<title>Done</title><h1>Thank you, your application was received</h1>")

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def site() -> Any:
    Site.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def steps(cv: Path) -> list[Any]:
    return [
        [tool_call("browser_read_html")],
        [tool_call("browser_type", selector="#name", text="Ada Rossi")],
        [tool_call("browser_upload_files", selector="#resume", paths=[str(cv)])],
        # A click that does not send the form goes through the check and reaches the page.
        [tool_call("browser_click", selector="#add")],
        # The model tries to send it: nothing may reach the site.
        [tool_call("browser_click", selector="#send")],
        [tool_call("browser_press_key", key="Enter")],
        [tool_call("finish_form", status="ready", summary="name and resume", submit_selector="#send")],
        json.dumps({"outcome": "submitted", "evidence": "thank you page"}),
    ]


def run(site: str, tmp_path: Path) -> Any:
    cv = tmp_path / "cvs" / "cv.pdf"
    cv.parent.mkdir(parents=True, exist_ok=True)
    cv.write_bytes(b"%PDF-1.4 Ada Rossi")
    model = ScriptedModel(*steps(cv))
    job = Job("t:1", "Acme", "Engineer", "Rome", site, site, "d")
    return asyncio.run(apply_to_job(model, "m", job, "cv", "answers", cv, tmp_path, tmp_path / "browser-profile"))  # type: ignore[arg-type]


def test_the_form_is_sent_once_with_the_name_and_the_cv_and_what_the_page_said_is_kept(site: str, tmp_path: Path) -> None:
    outcome = run(site, tmp_path)
    assert outcome.status == "submitted", outcome.summary
    assert len(Site.posts) == 1
    assert b"Ada Rossi" in Site.posts[0]
    assert b"%PDF" in Site.posts[0]
    assert b"harmless-click-reached-the-page" in Site.posts[0]
    assert outcome.proof.verdict == "submitted" and "Thank you" in outcome.proof.text
    assert Path(outcome.proof.screenshot).read_bytes().startswith(b"\x89PNG")


def test_a_form_whose_send_control_is_in_a_shadow_root_reaches_ready_and_is_sent_once_by_the_app(site: str, tmp_path: Path) -> None:
    model = ScriptedModel(
        [tool_call("finish_form", status="ready", summary="nothing to fill", submit_selector="#send")],
        json.dumps({"outcome": "submitted", "evidence": "thank you page"}),
    )
    job = Job("t:3", "Acme", "Engineer", "Rome", site, site + "shadow", "d")
    outcome = asyncio.run(apply_to_job(model, "m", job, "cv", "answers", tmp_path / "cv.pdf", tmp_path, tmp_path / "browser-profile"))  # type: ignore[arg-type]
    assert outcome.status == "submitted", outcome.summary
    assert len(Site.posts) == 1


def drive(site: str, path: str, tmp_path: Path, calls: list[Any]) -> tuple[dict[str, Any], ScriptedModel, list[str]]:
    """Calls by the model on a page of the site, then finish_form; returns the report, the model, and what the page recorded."""

    async def go() -> tuple[dict[str, Any], ScriptedModel, list[str]]:
        async with _server(tmp_path, tmp_path / "browser") as (session, _):
            await _open_browser(session, tmp_path / "browser-profile")
            try:
                await session.call_tool("browser_navigate", {"url": site + path})
                model = ScriptedModel(*calls, [tool_call("finish_form", status="blocked", summary="checked", submit_selector="")])
                report = await fill_form(session, [], model, "m", "brief", form_url=site)
                clicked = json.loads(tool_text(await session.call_tool("browser_evaluate", {"expression": "window.clicked || []"})))
                return report, model, clicked
            finally:
                await session.call_tool("browser_close", {})

    return asyncio.run(go())


@pytest.fixture
def elsewhere() -> Any:
    Elsewhere.posts = []
    server = ThreadingHTTPServer(("127.0.0.2", 0), Elsewhere)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.2:{server.server_port}/"
    server.shutdown()


def run_select(site: str, path: str, tmp_path: Path, model: ScriptedModel) -> Any:
    cv = tmp_path / "cvs" / "cv.pdf"
    cv.parent.mkdir(parents=True, exist_ok=True)
    cv.write_bytes(b"%PDF-1.4 Ada Rossi")
    job = Job("t:2", "Acme", "Engineer", "Rome", site, site + path, "d")
    return asyncio.run(apply_to_job(model, "m", job, "cv", "answers", cv, tmp_path, tmp_path / "browser-profile"))  # type: ignore[arg-type]


def test_the_browser_starts_with_the_model_key_kept_out_of_its_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-test-not-real")

    async def check() -> None:
        async with _server(tmp_path, tmp_path / "browser") as (session, _):
            await _open_browser(session, tmp_path / "browser-profile")
            assert not (await session.call_tool("browser_navigate", {"url": "about:blank"})).isError

    asyncio.run(check())


class Jar(BaseHTTPRequestHandler):
    """Sets a cookie on /set, and records the cookie the browser sends on /check."""

    sent: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        if self.path == "/check":
            Jar.sent.append(self.headers.get("Cookie") or "")
        self.send_response(200)
        if self.path == "/set":
            # A session cookie, with no lifetime, is dropped when the browser closes: a profile keeps the ones that last.
            self.send_header("Set-Cookie", "aihawk_profile=kept; Path=/; Max-Age=86400")
        body = b"<title>Jar</title>"
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def test_a_cookie_kept_in_the_profile_is_there_the_next_time_it_opens(tmp_path: Path) -> None:
    Jar.sent = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Jar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    async def visit(path: str, profile: Path) -> None:
        async with _server(tmp_path, tmp_path / "browser") as (session, _):
            await _open_browser(session, profile)
            try:
                await session.call_tool("browser_navigate", {"url": base + path})
            finally:
                await session.call_tool("browser_close", {})

    try:
        profile = tmp_path / "browser-profile"
        asyncio.run(visit("/set", profile))
        asyncio.run(visit("/check", profile))
        asyncio.run(visit("/check", tmp_path / "another-profile"))
    finally:
        server.shutdown()
    # The same profile carries the cookie to its next open; another profile never sees it.
    assert Jar.sent == ["aihawk_profile=kept", ""]


def test_the_newest_server_still_has_what_aihawk_calls(tmp_path: Path) -> None:
    async def check() -> None:
        async with stdio_client(server_parameters(tmp_path, tmp_path), errlog=sys.stderr) as (read, write), ClientSession(read, write) as session:
            started = await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert {"browser_open", "browser_close", "browser_navigate", "browser_click", "browser_read_text", "browser_take_screenshot", "browser_evaluate"} <= set(tools)
            assert {"selector"} <= set(tools["browser_click"].inputSchema["properties"])
            assert {"expression"} <= set(tools["browser_evaluate"].inputSchema["properties"])
            assert {"url"} <= set(tools["browser_navigate"].inputSchema["properties"])
            assert {"profile"} <= set(tools["browser_open"].inputSchema["properties"])
            assert started.instructions

    asyncio.run(check())
