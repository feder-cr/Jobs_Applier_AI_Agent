from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from aihawk.chatgpt_provider import ChatGPTAPIError, ChatGPTProvider, parse_models
from aihawk.config import Config


def test_model_catalog_exposes_only_visible_models() -> None:
    assert parse_models({"data": [
        {"id": "gpt-visible", "visibility": "list"},
        {"id": "internal", "visibility": "hidden"},
        {"id": "unknown-visibility"},
    ]}) == ["gpt-visible"]


def test_model_catalog_uses_documented_models_slug_and_display_name() -> None:
    from aihawk.chatgpt_provider import parse_model_catalog

    models = parse_model_catalog({"models": [
        {"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"},
        {"slug": "internal", "display_name": "Internal", "visibility": "hidden"},
    ]})
    assert [(model.slug, model.display_name) for model in models] == [("gpt-visible", "GPT Visible")]


def test_models_are_fetched_once_per_provider_and_refresh_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.chatgpt_provider as provider

    calls = 0

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.models = self
        async def get(self, path: str, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return {"models": [{"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"}]}
        async def close(self) -> None:
            pass

    monkeypatch.setattr(provider, "AsyncOpenAI", Client)
    instance = ChatGPTProvider(lambda: "oauth-token")
    assert asyncio.run(instance.models()) == ["gpt-visible"]
    assert asyncio.run(instance.models()) == ["gpt-visible"]
    assert calls == 1
    assert asyncio.run(instance.models(refresh=True)) == ["gpt-visible"]
    assert calls == 2


def test_responses_request_streams_with_plan_constraints_and_waits_for_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Stream:
        def __init__(self) -> None:
            self.events = [
                NS(type="response.output_text.delta", delta="Hello"),
                NS(type="response.completed", response=NS(id="resp_1", output_text="Hello", output=[], usage=NS(input_tokens=2, output_tokens=1))),
            ]
        def __aiter__(self):
            self.iterator = iter(self.events)
            return self
        async def __anext__(self):
            try:
                return next(self.iterator)
            except StopIteration:
                raise StopAsyncIteration
        async def close(self) -> None:
            pass

    class Client:
        def __init__(self, **kwargs: object) -> None:
            calls.append({"client": kwargs})
            self.responses = self
        async def get(self, path: str, **kwargs: object) -> dict[str, object]:
            return {"models": [{"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"}]}
        async def create(self, **kwargs: object) -> Stream:
            calls.append(kwargs)
            return Stream()
        async def close(self) -> None:
            pass

    import aihawk.chatgpt_provider as provider

    monkeypatch.setattr(provider, "AsyncOpenAI", Client)
    result = asyncio.run(ChatGPTProvider(lambda: "oauth-token").request("gpt-visible", [{"role": "user", "content": "Hello"}]))
    assert result.text == "Hello"
    assert result.id == "resp_1"
    assert calls[0]["client"] == {"api_key": "oauth-token", "base_url": "https://api.openai.com/v1", "max_retries": 0}
    assert calls[2]["store"] is False
    assert calls[2]["stream"] is True


def test_stream_failure_preserves_subscription_limit_code_and_does_not_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        async def __aiter__(self):
            yield NS(type="response.output_text.delta", delta="partial")
            yield NS(type="response.failed", response=NS(error=NS(code="subscription_sharing_usage_limit_exceeded", message="limit")))
        async def close(self) -> None:
            pass

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.responses = self
        async def get(self, path: str, **kwargs: object) -> dict[str, object]:
            return {"models": [{"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"}]}
        async def create(self, **kwargs: object) -> Stream:
            return Stream()
        async def close(self) -> None:
            pass

    import aihawk.chatgpt_provider as provider

    monkeypatch.setattr(provider, "AsyncOpenAI", Client)
    with pytest.raises(ChatGPTAPIError) as caught:
        asyncio.run(ChatGPTProvider(lambda: "oauth-token").request("gpt-visible", [{"role": "user", "content": "Hello"}]))
    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"
    assert "Manage usage" in str(caught.value)


def test_stream_without_terminal_completion_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        async def __aiter__(self):
            yield NS(type="response.output_text.delta", delta="partial")
        async def close(self) -> None:
            pass

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.responses = self
        async def get(self, path: str, **kwargs: object) -> dict[str, object]:
            return {"models": [{"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"}]}
        async def create(self, **kwargs: object) -> Stream:
            return Stream()
        async def close(self) -> None:
            pass

    import aihawk.chatgpt_provider as provider

    monkeypatch.setattr(provider, "AsyncOpenAI", Client)
    with pytest.raises(ChatGPTAPIError, match="ended before it completed"):
        asyncio.run(ChatGPTProvider(lambda: "oauth-token").request("gpt-visible", [{"role": "user", "content": "Hello"}]))


def test_incomplete_response_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        async def __aiter__(self):
            yield NS(type="response.incomplete", response=NS(incomplete_details=NS(reason="max_output_tokens")))
        async def close(self) -> None:
            pass

    class Client:
        def __init__(self, **kwargs: object) -> None:
            self.responses = self
        async def get(self, path: str, **kwargs: object) -> dict[str, object]:
            return {"models": [{"slug": "gpt-visible", "display_name": "GPT Visible", "visibility": "list"}]}
        async def create(self, **kwargs: object) -> Stream:
            return Stream()
        async def close(self) -> None:
            pass

    import aihawk.chatgpt_provider as provider

    monkeypatch.setattr(provider, "AsyncOpenAI", Client)
    with pytest.raises(ChatGPTAPIError) as caught:
        asyncio.run(ChatGPTProvider(lambda: "oauth-token").request("gpt-visible", []))
    assert caught.value.code == "max_output_tokens"


def test_usage_unavailable_has_retry_later_message() -> None:
    error = ChatGPTAPIError("subscription_sharing_usage_unavailable", 503)
    assert "retry later" in str(error).lower()


def test_chatgpt_provider_selection_does_not_construct_orcarouter_client(tmp_path) -> None:
    from aihawk.llm import make_client

    client = make_client(Config(workspace=tmp_path, provider="chatgpt"))
    assert isinstance(client, ChatGPTProvider)


def test_selected_chatgpt_model_routes_every_pipeline_stage_and_requires_selection(tmp_path) -> None:
    from aihawk.config import ConfigError

    config = Config(workspace=tmp_path, provider="chatgpt", chatgpt_model="visible-model")
    assert [config.model_for(stage) for stage in ("quick_model", "decision_model", "model")] == ["visible-model"] * 3
    with pytest.raises(ConfigError, match="Choose a ChatGPT model"):
        Config(workspace=tmp_path, provider="chatgpt").model_for("model")


def test_changing_search_filters_keeps_the_current_model_client(tmp_path) -> None:
    from aihawk.session import Session

    session = Session.__new__(Session)
    session.config = Config(workspace=tmp_path)
    session.workspace = tmp_path
    session._client = object()
    existing = session._client
    session.change_setting("exclude", "Python")
    assert session._client is existing


def test_switching_provider_rebuilds_the_model_client(tmp_path) -> None:
    from aihawk.session import Session

    session = Session.__new__(Session)
    session.config = Config(workspace=tmp_path)
    session.workspace = tmp_path
    session._client = object()
    session.change_setting("provider", "chatgpt")
    assert session.config.provider == "chatgpt"
    assert session._client is None


def test_chatgpt_application_record_does_not_call_orcarouter_spend_endpoints(monkeypatch) -> None:
    from types import SimpleNamespace as NS

    from aihawk import session as session_module
    from aihawk.apply import Outcome

    client = ChatGPTProvider(lambda: "token")
    session = session_module.Session.__new__(session_module.Session)
    session._client = client
    session.store = NS(set_cost=lambda job_id, cost: None)
    session._keep = lambda *args: None

    async def unexpected(*args, **kwargs):
        raise AssertionError("ChatGPT plan use must not call OrcaRouter spend endpoints")

    monkeypatch.setattr(session_module.SPEND, "settle", unexpected)
    item = NS(job=NS(id="job-1"))
    result = asyncio.run(session._record(item, "example.com", Outcome("ready", "done"), session_module.SPEND.mark()))
    assert result == {}
