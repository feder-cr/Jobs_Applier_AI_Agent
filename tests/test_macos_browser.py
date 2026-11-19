"""A headed Chrome dry run against a local form; this test never sends an application."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aihawk import apply as apply_module
from aihawk.apply import apply_to_job, server_parameters
from aihawk.config import DRY_RUN_ENV
from aihawk.database import Job
from fakes import ScriptedModel, tool_call

pytestmark = pytest.mark.browser


class LocalForm(BaseHTTPRequestHandler):
    submissions: ClassVar[list[bytes]] = []

    def do_GET(self) -> None:
        body = b"""<!doctype html><title>Local application</title>
<form method="post" action="/submitted" enctype="multipart/form-data">
<label>Name <input id="name" name="name"></label>
<label>Resume <input id="resume" name="resume" type="file"></label>
<button id="send" type="submit">Submit application</button></form>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        LocalForm.submissions.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture
def local_form() -> str:
    LocalForm.submissions = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalForm)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()


@pytest.fixture
def home_workspace() -> Any:
    with tempfile.TemporaryDirectory(prefix="aihawk-macos-smoke-", dir=Path.home()) as directory:
        yield Path(directory)


def test_macos_chrome_fills_local_form_and_dry_run_keeps_it_unsent(
    local_form: str, home_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "darwin":
        pytest.skip("the headed Chrome fallback is specific to macOS")
    monkeypatch.setattr(apply_module, "PLATFORM", "darwin")
    monkeypatch.setenv(DRY_RUN_ENV, "1")
    params = server_parameters(home_workspace, home_workspace, home_workspace / "chrome-profile")
    assert "--headless" not in params.args

    cv = home_workspace / "resumes" / "cv.pdf"
    cv.parent.mkdir()
    cv.write_bytes(b"%PDF-1.4 local test resume")
    model = ScriptedModel(
        [tool_call("browser_snapshot")],
        [tool_call("browser_type", target="#name", text="Ada Rossi")],
        [tool_call("browser_click", target="#resume")],
        [tool_call("browser_file_upload", paths=[str(cv)])],
        [tool_call("browser_evaluate", function="() => document.querySelector('#resume').files[0]?.name")],
        [tool_call("finish_form", status="ready", summary="local form filled", submit_selector="#send")],
    )
    job = Job("local:1", "Local Test", "Engineer", "Local", local_form, local_form, "Test only")

    outcome = asyncio.run(
        apply_to_job(
            model,
            "fake-model",
            job,
            "Ada Rossi",
            "name: Ada Rossi",
            cv,
            home_workspace,
            home_workspace / "chrome-profile",
        )
    )  # type: ignore[arg-type]

    assert outcome.reason == "dry_run"
    assert "nothing sent" in outcome.summary
    assert outcome.proof.url == local_form, (home_workspace / "trace.log").read_text(encoding="utf-8")
    assert outcome.proof.screenshot, (home_workspace / "trace.log").read_text(encoding="utf-8")
    assert Path(outcome.proof.screenshot).read_bytes().startswith(b"\x89PNG")
    assert any(
        message.get("role") == "tool" and cv.name in message.get("content", "")
        for message in model.requests[-1]["messages"]
    )
    assert LocalForm.submissions == []
