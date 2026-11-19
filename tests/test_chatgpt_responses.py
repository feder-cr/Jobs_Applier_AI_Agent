from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from typing import Any

from aihawk.chatgpt_provider import ChatGPTProvider, ModelInfo, ResponseResult, response_input, response_tools


def test_system_prompts_become_supported_developer_instructions() -> None:
    instructions, inputs = response_input([
        {"role": "system", "content": "Protect the user."},
        {"role": "user", "content": "Fill the form."},
    ])
    assert instructions == "Protect the user."
    assert inputs == [{"role": "user", "content": "Fill the form."}]


def test_chat_history_becomes_function_calls_and_outputs() -> None:
    _, inputs = response_input([
        {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "browser_click", "arguments": "{\"selector\":\"#go\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "clicked"},
    ])
    assert inputs == [
        {"type": "function_call", "call_id": "call_1", "name": "browser_click", "arguments": '{"selector":"#go"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "clicked"},
    ]


def test_tool_schemas_map_to_responses_function_tools() -> None:
    tools = response_tools([{"type": "function", "function": {"name": "finish_form", "description": "Finish", "parameters": {"type": "object"}}}])
    assert tools == [{"type": "function", "name": "finish_form", "description": "Finish", "parameters": {"type": "object"}, "strict": True}]


def test_images_are_only_sent_when_selected_model_advertises_support() -> None:
    content = [{"type": "text", "text": "Page"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
    _, text_only = response_input([{"role": "user", "content": content}], supports_images=False)
    _, multimodal = response_input([{"role": "user", "content": content}], supports_images=True)
    assert all(item.get("type") != "input_image" for item in text_only[0]["content"])
    assert multimodal[0]["content"][-1] == {"type": "input_image", "image_url": "data:image/png;base64,AA"}


def test_chatgpt_completion_uses_json_schema_and_normalizes_multiple_tool_calls(monkeypatch) -> None:
    provider = ChatGPTProvider(lambda: "token")
    sent: dict[str, Any] = {}

    async def request(model: str, input: list[Any], **options: Any) -> ResponseResult:
        sent.update(model=model, input=input, options=options)
        return ResponseResult("resp_1", "", [
            NS(type="function_call", call_id="call_a", name="browser_click", arguments='{"selector":"#a"}'),
            NS(type="function_call", call_id="call_b", name="finish_form", arguments="not-json"),
        ], NS(input_tokens=4, output_tokens=2))

    async def catalog(*args: Any, **kwargs: Any) -> list[Any]:
        return [ModelInfo("gpt-visible", "GPT Visible")]

    monkeypatch.setattr(provider, "request", request)
    monkeypatch.setattr(provider, "model_catalog", catalog)
    response = asyncio.run(provider.chat_completion(
        model="gpt-visible",
        messages=[{"role": "system", "content": "rules"}, {"role": "user", "content": "do it"}],
        tools=[{"type": "function", "function": {"name": "browser_click", "parameters": {"type": "object"}}}],
        response_format={"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": {"type": "object"}}},
    ))
    assert sent["input"] == [{"role": "user", "content": "do it"}]
    assert sent["options"]["instructions"] == "rules"
    assert sent["options"]["text"] == {"format": {"type": "json_schema", "name": "answer", "strict": True, "schema": {"type": "object"}}}
    message = response.choices[0].message
    assert [call.function.name for call in message.tool_calls] == ["browser_click", "finish_form"]
    assert message.tool_calls[1].function.arguments == "not-json"
