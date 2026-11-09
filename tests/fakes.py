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
    """Table t as the pipeline publishes it: a chunk of the postings with a description of 200 characters or more, one of
    the others with none, and the manifest naming them. A posting is known by its address: rows that share one get their
    key added to it. `keys.json` beside the manifest says each address's key, for the tests that name postings by it.
    Returns the manifest's path."""
    connection.execute("ALTER TABLE t ADD COLUMN IF NOT EXISTS country VARCHAR; ALTER TABLE t ADD COLUMN IF NOT EXISTS remote BOOLEAN")
    connection.execute("""CREATE OR REPLACE TABLE posted AS SELECT key,
        CASE WHEN count(*) OVER (PARTITION BY url) > 1 THEN url || key ELSE url END AS url, title, company,
        CASE WHEN remote AND coalesce(country, '') = '' AND lower(trim(coalesce(location, ''))) IN ('', 'remote', 'anywhere', 'worldwide', 'global')
            THEN 'Remote' ELSE location END AS location,
        country, TRY_CAST(posted_at AS TIMESTAMP) AS posted_at, description FROM t""")
    chunks = []
    for name, described in (("jobs_0.parquet", True), ("titles_0.parquet", False)):
        test = "length(coalesce(description, '')) >= 200"
        rows = connection.execute(f"SELECT count(*) FROM posted WHERE {'' if described else 'NOT '}{test}").fetchone()[0]
        if not rows:
            continue
        connection.execute(f"""COPY (SELECT url, title, company, location, country, posted_at, {'description' if described else 'NULL::VARCHAR AS description'}
            FROM posted WHERE {'' if described else 'NOT '}{test}) TO '{(folder / name).as_posix()}' (FORMAT parquet)""")
        chunks.append({"name": name, "bytes": (folder / name).stat().st_size, "rows": rows, "titles_only": not described})
    keys = dict(connection.execute("SELECT url, key FROM posted").fetchall())
    (folder / "keys.json").write_text(json.dumps(keys), encoding="utf-8")
    manifest = {"schema": 3, "build": "jobs-20261104-000000", "rows_out": rows_out, "built_at": "2026-11-04T00:00:00", "chunks": chunks, "deltas": []}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(folder / "manifest.json")
