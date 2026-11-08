"""A scripted model and a scripted browser server, for the tests that need no network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def tool_call(name: str, call_id: str = "call", **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class _Message(SimpleNamespace):
    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        calls = [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in self.tool_calls or []
        ]
        return {"role": "assistant", "content": self.content, **({"tool_calls": calls} if calls else {})}


class ScriptedModel:
    """chat.completions.create answers the next scripted step: a list of tool calls, or a text."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        step = self.steps.pop(0)
        message = _Message(content=step, tool_calls=None) if isinstance(step, str) else _Message(content=None, tool_calls=step)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedBrowser:
    """call_tool records every call; browser_evaluate says what the page's element is."""

    def __init__(self, elements: dict[str, dict[str, Any]] | None = None, page: dict[str, str] | None = None) -> None:
        self.elements = elements or {}
        self.page = page or {"url": "", "text": ""}  # the page's address and text; navigation moves the address
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> SimpleNamespace:
        arguments = arguments or {}
        self.calls.append((name, arguments))
        text = "ok"
        if name == "browser_evaluate" and "location.href" in arguments["expression"]:
            text = json.dumps(self.page)
        elif name == "browser_evaluate":
            text = json.dumps(next((value for key, value in self.elements.items() if json.dumps(key) in arguments["expression"]), None))
        elif name == "browser_navigate":
            self.page = {"url": arguments["url"], "text": self.page["text"]}
        elif name == "browser_read_text":
            text = "Thank you, your application was received"
        elif name == "browser_take_screenshot":
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type="image", mimeType="image/png", data="UE5H")])
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=text)])

    def clicks(self) -> list[str]:
        return [arguments["selector"] for name, arguments in self.calls if name == "browser_click"]


def publish(connection: Any, folder: Path, rows_out: int) -> str:
    """Table t as the pipeline publishes it: a description file in key order whose range the manifest names, the index
    beside it, the manifest. Returns the manifest's path."""
    connection.execute("ALTER TABLE t ADD COLUMN IF NOT EXISTS country VARCHAR; ALTER TABLE t ADD COLUMN IF NOT EXISTS remote BOOLEAN")
    connection.execute(f"COPY (SELECT * FROM t ORDER BY key) TO '{(folder / 'jobs_0.parquet').as_posix()}' (FORMAT parquet)")
    connection.execute(f"""COPY (SELECT * EXCLUDE (description), length(coalesce(description, '')) >= 200 AS described FROM t)
        TO '{(folder / 'index.parquet').as_posix()}' (FORMAT parquet)""")
    first, last = connection.execute("SELECT min(key), max(key) FROM t").fetchone()
    manifest = {"schema": 2, "rows_out": rows_out, "built_at": "2026-11-04T00:00:00", "index": {"name": "index.parquet", "bytes": 1},
                "files": [{"name": "jobs_0.parquet", "first_key": first, "last_key": last}]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(folder / "manifest.json")
