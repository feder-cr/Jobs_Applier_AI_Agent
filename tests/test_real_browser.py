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

from aihawk.apply import MODEL_TOOLS, apply_to_job, offered, server_parameters
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


class Site(BaseHTTPRequestHandler):
    posts: ClassVar[list[bytes]] = []

    def _page(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
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


def run(site: str, tmp_path: Path, confirm: Any) -> Any:
    cv = tmp_path / "cvs" / "cv.pdf"
    write_cv_pdf("# Ada Rossi", cv)
    model = ScriptedModel(*steps(cv))
    job = Job("t:1", "Acme", "Engineer", "Rome", site, site, "d")
    return asyncio.run(apply_to_job(model, "m", job, "cv", "answers", cv, tmp_path, confirm))  # type: ignore[arg-type]


def test_nothing_reaches_the_site_until_the_person_says_yes(site: str, tmp_path: Path) -> None:
    seen: list[int] = []

    async def no(summary: str) -> bool:
        seen.append(len(Site.posts))
        return False

    outcome = run(site, tmp_path, no)
    assert outcome.status == "declined"
    assert seen == [0]
    assert Site.posts == []


def test_a_yes_sends_the_form_with_the_name_and_the_cv(site: str, tmp_path: Path) -> None:
    async def yes(summary: str) -> bool:
        assert Site.posts == []
        return True

    outcome = run(site, tmp_path, yes)
    assert outcome.status == "submitted", outcome.summary
    assert len(Site.posts) == 1
    assert b"Ada Rossi" in Site.posts[0]
    assert b"%PDF" in Site.posts[0]
    assert b"harmless-click-reached-the-page" in Site.posts[0]


def test_the_newest_server_still_has_what_aihawk_relies_on(tmp_path: Path) -> None:
    async def check() -> None:
        async with stdio_client(server_parameters(tmp_path, tmp_path), errlog=sys.stderr) as (read, write), ClientSession(read, write) as session:
            started = await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            # Each is called by name, and the first two by their arguments, in apply.py.
            assert MODEL_TOOLS | {"browser_open", "browser_close"} <= set(tools)
            assert {"selector"} <= set(tools["browser_click"].inputSchema["properties"])
            assert {"x", "y"} <= set(tools["browser_click_at"].inputSchema["properties"])
            assert {"expression"} <= set(tools["browser_evaluate"].inputSchema["properties"])
            assert {"url"} <= set(tools["browser_navigate"].inputSchema["properties"])
            # What the model reads about driving a page is the server's, and a tool that acts must not pass for one that reads.
            assert started.instructions
            assert not any(offered(tool) for name, tool in tools.items() if name in {"browser_open", "browser_close"})

    asyncio.run(check())

