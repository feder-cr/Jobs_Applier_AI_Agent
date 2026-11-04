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

from aihawk.apply import MODEL_TOOLS, _open_browser, _server, apply_to_job, offered, server_parameters
from aihawk.database import Job
from aihawk.documents import write_cv_pdf
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
# The same send on the same site, by a script: the page stays where it is and only its text changes.
SAME_SITE_SELECT = b"""<!doctype html><title>Pick</title>
<select id="pick" name="pick" onchange="fetch('/sent', {method: 'POST', body: new URLSearchParams({pick: this.value})}).then(() => { document.body.innerHTML = '<h1>Thank you, your application was received</h1>'; })">
<option value="">Choose</option><option value="a">A</option></select>"""


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
        [tool_call("finish_form", status="ready", summary="name and resume", submit_selector="#send", questions=[])],
        json.dumps({"outcome": "submitted", "evidence": "thank you page"}),
    ]


def run(site: str, tmp_path: Path) -> Any:
    cv = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Ada Rossi", cv)
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


@pytest.fixture
def elsewhere() -> Any:
    Elsewhere.posts = []
    server = ThreadingHTTPServer(("127.0.0.2", 0), Elsewhere)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.2:{server.server_port}/"
    server.shutdown()


def run_select(site: str, path: str, tmp_path: Path, model: ScriptedModel) -> Any:
    cv = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Ada Rossi", cv)
    job = Job("t:2", "Acme", "Engineer", "Rome", site, site + path, "d")
    return asyncio.run(apply_to_job(model, "m", job, "cv", "answers", cv, tmp_path, tmp_path / "browser-profile"))  # type: ignore[arg-type]


def test_a_select_whose_form_leaves_for_another_host_is_unconfirmed_and_the_post_is_counted(site: str, elsewhere: str, tmp_path: Path) -> None:
    Site.leave_to = elsewhere + "sent"
    # The change submits the form, and the page navigates away. While it loads the page cannot be read: the check reads
    # it again until it can, and the host of the page it then shows is what stops the form.
    model = ScriptedModel([tool_call("browser_select_option", selector="#pick", value="a")])
    outcome = run_select(site, "leave", tmp_path, model)
    assert outcome.status == "unconfirmed", outcome.summary
    assert "the page went to" in outcome.summary and "127.0.0.2" in outcome.summary
    assert len(model.requests) == 1  # asked for the select, and never again
    assert len(Elsewhere.posts) == 1
    assert b"pick=a" in Elsewhere.posts[0]
    assert Site.posts == []


def test_a_select_that_sends_on_the_same_site_is_not_detected_and_the_model_goes_on(site: str, tmp_path: Path) -> None:
    # Known gap, written down: a send that stays on the site is not seen. The script posts and rewrites the page to say it
    # was received, the address does not change, and the model is asked for its next step as if nothing had been sent.
    model = ScriptedModel(
        [tool_call("browser_select_option", selector="#pick", value="a")],
        [tool_call("finish_form", status="blocked", summary="sent already", submit_selector="", questions=[])],
    )
    outcome = run_select(site, "select", tmp_path, model)
    assert outcome.status == "blocked"
    assert len(Site.posts) == 1
    assert b"pick=a" in Site.posts[0]
    assert len(model.requests) == 2


def test_the_newest_server_still_has_what_aihawk_relies_on(tmp_path: Path) -> None:
    async def check() -> None:
        async with stdio_client(server_parameters(tmp_path, tmp_path), errlog=sys.stderr) as (read, write), ClientSession(read, write) as session:
            started = await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            # Each is called by name, and the first two by their arguments, in apply.py.
            assert MODEL_TOOLS | {"browser_open", "browser_close", "browser_evaluate"} <= set(tools)
            assert {"selector"} <= set(tools["browser_click"].inputSchema["properties"])
            assert {"x", "y"} <= set(tools["browser_click_at"].inputSchema["properties"])
            assert {"expression"} <= set(tools["browser_evaluate"].inputSchema["properties"])
            assert {"url"} <= set(tools["browser_navigate"].inputSchema["properties"])
            assert {"profile"} <= set(tools["browser_open"].inputSchema["properties"])
            # What the model reads about driving a page is the server's, and a tool that acts must not pass for one that reads.
            assert started.instructions
            assert not any(offered(tool) for name, tool in tools.items() if name in {"browser_open", "browser_close"})

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

