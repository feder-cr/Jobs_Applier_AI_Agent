"""Responses API transport for ChatGPT plan usage through Sign in with ChatGPT."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from aihawk.config import ConfigError

API_BASE = "https://api.openai.com/v1"
LIMIT_CODE = "subscription_sharing_usage_limit_exceeded"
UNAVAILABLE_CODE = "subscription_sharing_usage_unavailable"


class ChatGPTAPIError(RuntimeError):
    """A plan-usage request did not complete; includes only a safe code and request ID."""

    def __init__(self, code: str = "", status_code: int = 0, request_id: str = "") -> None:
        self.code = code or "unknown_error"
        self.status_code = status_code
        self.request_id = request_id
        message = {
            LIMIT_CODE: "Your ChatGPT plan usage limit was reached. Pause AIHawk requests, then open Manage usage in ChatGPT Settings.",
            UNAVAILABLE_CODE: "ChatGPT plan usage is temporarily unavailable. Keep your connection and retry later.",
            "model_not_available": "That model is not visible to the connected ChatGPT account. Choose an available model in AIHawk settings.",
            "interrupted_stream": "The ChatGPT response stream ended before it completed. Try the request again.",
            "incomplete_response": "The ChatGPT response was incomplete. Try the request again.",
            "subscription_sharing_user_not_eligible": "This ChatGPT account or workspace is not eligible for plan usage in AIHawk.",
            "subscription_sharing_unsupported_capability": "The selected ChatGPT model does not support this AIHawk request.",
            "subscription_sharing_route_not_supported": "OpenAI rejected the ChatGPT plan request route. Check that AIHawk is up to date.",
            "subscription_sharing_invalid_user": "The ChatGPT connection is no longer valid. Connect the account again.",
            "connection_error": "Could not reach ChatGPT. Check your connection and try again.",
        }.get(self.code, f"The ChatGPT plan request failed ({self.code}).")
        if request_id:
            message += f" Request ID: {request_id}."
        super().__init__(message)


@dataclass(frozen=True)
class ResponseResult:
    id: str
    text: str
    output: list[Any]
    usage: Any


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str
    supports_images: bool = False


@dataclass(frozen=True)
class ChatFunctionCall:
    id: str
    type: str
    function: Any


@dataclass(frozen=True)
class ChatMessage:
    content: str | None
    tool_calls: list[ChatFunctionCall]
    role: str = "assistant"

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "tool_calls": [
            {"id": call.id, "type": call.type, "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in self.tool_calls
        ]}
        if self.content is not None or not exclude_none:
            result["content"] = self.content
        if exclude_none and not self.tool_calls:
            result.pop("tool_calls")
        return result


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def parse_models(response: Any) -> list[str]:
    """Only show models explicitly visible to this signed-in ChatGPT account."""
    return [model.slug for model in parse_model_catalog(response)]


def parse_model_catalog(response: Any) -> list[ModelInfo]:
    """Read the account-scoped catalog returned by the current SIWC models endpoint."""
    models = _get(response, "models", None)
    if models is None:
        models = _get(response, "data", []) or []
    visible: list[ModelInfo] = []
    for model in models:
        slug = str(_get(model, "slug", "") or _get(model, "id", ""))
        if not slug or _get(model, "visibility") != "list":
            continue
        capabilities = _get(model, "capabilities", {})
        modalities = _get(model, "input_modalities", None) or _get(capabilities, "input_modalities", [])
        visible.append(ModelInfo(slug, str(_get(model, "display_name", "") or slug), "image" in modalities))
    return visible


def response_input(messages: list[dict[str, Any]], *, supports_images: bool = False) -> tuple[str, list[Any]]:
    """Convert the application's Chat Completions history to Responses input items."""
    instructions: list[str] = []
    inputs: list[Any] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content")
        if role in {"system", "developer"}:
            if content:
                instructions.append(str(content))
            continue
        if role == "tool":
            inputs.append({"type": "function_call_output", "call_id": message.get("tool_call_id", ""), "output": str(content or "")})
            continue
        if content is not None:
            item_content = content
            if isinstance(content, list):
                item_content = []
                for part in content:
                    kind = part.get("type")
                    if kind == "text":
                        item_content.append({"type": "input_text", "text": part.get("text", "")})
                    elif kind == "image_url" and supports_images:
                        image = part.get("image_url", {})
                        url = image.get("url", "") if isinstance(image, dict) else image
                        item_content.append({"type": "input_image", "image_url": url})
                if not item_content:
                    item_content = ""
            if item_content:
                inputs.append({"role": role, "content": item_content})
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            inputs.append({"type": "function_call", "call_id": call.get("id", ""), "name": function.get("name", ""), "arguments": function.get("arguments", "{}")})
    return "\n\n".join(instructions), inputs


def response_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the browser's Chat Completions function declarations to Responses function tools."""
    converted = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function = tool.get("function", {})
        converted.append({
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            "strict": True,
        })
    return converted


def response_format(formatting: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map strict Chat Completions JSON schema settings to the Responses text format."""
    if not formatting or formatting.get("type") != "json_schema":
        return None
    schema = formatting.get("json_schema", {})
    return {"format": {
        "type": "json_schema", "name": schema.get("name", "result"),
        "strict": bool(schema.get("strict", True)), "schema": schema.get("schema", {"type": "object"}),
    }}


def chat_response(result: ResponseResult) -> Any:
    """Adapt completed text and function-call outputs to the existing application loop's small Chat Completions shape."""
    text_parts: list[str] = []
    calls: list[ChatFunctionCall] = []
    for item in result.output:
        kind = _get(item, "type", "")
        if kind == "function_call":
            calls.append(ChatFunctionCall(
                str(_get(item, "call_id", "")), "function",
                SimpleNamespace(name=str(_get(item, "name", "")), arguments=str(_get(item, "arguments", "{}"))),
            ))
        elif kind == "message":
            for part in _get(item, "content", []) or []:
                if _get(part, "type") == "output_text":
                    text_parts.append(str(_get(part, "text", "")))
    content = result.text or "".join(text_parts)
    usage = result.usage
    usage = SimpleNamespace(
        prompt_tokens=int(_get(usage, "input_tokens", 0) or 0),
        completion_tokens=int(_get(usage, "output_tokens", 0) or 0),
    ) if usage is not None else None
    message = ChatMessage(content or None, calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if calls else "stop")], usage=usage)


def _error_from_status(error: APIStatusError) -> ChatGPTAPIError:
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    code = detail.get("code", "") if isinstance(detail, dict) else ""
    request_id = str(getattr(error, "request_id", "") or "")
    return ChatGPTAPIError(str(code), int(error.status_code), request_id)


class ChatGPTProvider:
    """A per-account Responses client. The token callback must return an authorized, current OAuth access token."""

    def __init__(self, access_token: Callable[[], str | Any]) -> None:
        self.access_token = access_token
        self._model_catalog: list[ModelInfo] | None = None

    def clear_model_cache(self) -> None:
        self._model_catalog = None

    async def _client(self) -> AsyncOpenAI:
        token = self.access_token()
        if asyncio.iscoroutine(token):
            token = await token
        if not token:
            raise ConfigError("Connect ChatGPT before using its plan.")
        return AsyncOpenAI(api_key=str(token), base_url=API_BASE, max_retries=0)

    async def models(self, refresh: bool = False) -> list[str]:
        """Discover the account's visible model IDs; connection changes create a fresh provider/cache."""
        return [model.slug for model in await self.model_catalog(refresh=refresh)]

    async def model_catalog(self, refresh: bool = False) -> list[ModelInfo]:
        if self._model_catalog is not None and not refresh:
            return list(self._model_catalog)
        client = await self._client()
        try:
            answer = await client.get("/models", cast_to=dict)
            self._model_catalog = parse_model_catalog(answer)
            return list(self._model_catalog)
        except APIStatusError as error:
            raise _error_from_status(error) from None
        except APIConnectionError:
            raise ChatGPTAPIError("connection_error") from None
        finally:
            await client.close()

    async def request(self, model: str, input: list[Any], **options: Any) -> ResponseResult:
        """Stream one no-storage Responses request and accept it only after response.completed."""
        if model not in await self.models():
            raise ChatGPTAPIError("model_not_available", 403)
        client = await self._client()
        stream: Any = None
        deltas: list[str] = []
        completed: Any = None
        try:
            stream = await client.responses.create(
                model=model,
                input=input,
                store=False,
                stream=True,
                **options,
            )
            async for event in stream:
                event_type = _get(event, "type", "")
                if event_type == "response.output_text.delta":
                    deltas.append(str(_get(event, "delta", "")))
                elif event_type == "response.failed":
                    response = _get(event, "response", {})
                    error = _get(response, "error", {})
                    code = str(_get(error, "code", "unknown_error"))
                    raise ChatGPTAPIError(code, int(_get(error, "status_code", 0) or 0), str(_get(response, "_request_id", "") or ""))
                elif event_type == "response.incomplete":
                    response = _get(event, "response", {})
                    detail = _get(response, "incomplete_details", {})
                    code = str(_get(detail, "reason", "incomplete_response"))
                    raise ChatGPTAPIError(code)
                elif event_type == "response.completed":
                    completed = _get(event, "response")
            if completed is None:
                raise ChatGPTAPIError("interrupted_stream")
            text = _get(completed, "output_text", "") or "".join(deltas)
            return ResponseResult(
                id=str(_get(completed, "id", "")),
                text=str(text),
                output=list(_get(completed, "output", []) or []),
                usage=_get(completed, "usage"),
            )
        except ChatGPTAPIError:
            raise
        except APIStatusError as error:
            raise _error_from_status(error) from None
        except APIConnectionError:
            raise ChatGPTAPIError("connection_error") from None
        finally:
            if stream is not None:
                close = getattr(stream, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
            await client.close()

    async def chat_completion(self, **request: Any) -> Any:
        model = str(request["model"])
        catalog = await self.model_catalog()
        info = next((candidate for candidate in catalog if candidate.slug == model), None)
        if info is None:
            raise ChatGPTAPIError("model_not_available", 403)
        # Rebuild only the user image items after learning whether the chosen account model supports image input.
        instructions, inputs = response_input(request.get("messages", []), supports_images=info.supports_images)
        options: dict[str, Any] = {}
        if instructions:
            options["instructions"] = instructions
        if formatted := response_format(request.get("response_format")):
            options["text"] = formatted
        if tools := response_tools(request.get("tools", [])):
            options["tools"] = tools
        return chat_response(await self.request(model, inputs, **options))
