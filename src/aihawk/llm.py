"""The one model gateway, OrcaRouter: OpenAI's chat API, and the decision model's /systemone call."""

from __future__ import annotations

import json
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from aihawk.config import DEFAULT_BASE_URL, KEY_ENV, KEY_URL, Config, ConfigError

# The SDK's own default is ten minutes a try, and a model that hangs would hold a judgment that long.
TIMEOUT = 180.0


class Spend:
    """What the model calls cost, in dollars: each call's tokens at the gateway's own price for its model, added up.

    The prices come from the gateway's /models list, read once, and give each call's cost the moment it ends. The gateway also keeps
    a record of what it charged for each request: `settle` reads it for the calls since the last settling and corrects the total,
    because the list and the charge differ (on 2026-11-07 OrcaRouter charged twice its listed price for DeepSeek flash). A model
    the list does not price is counted as a call of unknown cost until settled. The decision model's calls are not counted:
    they go through another endpoint and cost under a cent a hundred.
    """

    def __init__(self) -> None:
        self.prices: dict[str, tuple[float, float, float]] = {}  # per token: prompt, completion, cached prompt
        self.total = 0.0
        self.unpriced = 0
        self.loaded = False
        self.pending: list[tuple[str, float]] = []  # the gateway's id of each call not yet settled, and its estimate

    async def load_prices(self, client: AsyncOpenAI) -> None:
        """The price list, once. A gateway that does not give one leaves every call unpriced; nothing else stops."""
        if self.loaded:
            return
        self.loaded = True
        try:
            listed = await client.get("/models", cast_to=dict)
        except Exception:  # noqa: BLE001  The list is a convenience: a search or an application must not wait on it.
            return
        for model in (listed.get("data") or []) if isinstance(listed, dict) else []:
            pricing = model.get("pricing") or {}
            try:
                self.prices[model["id"]] = (float(pricing["prompt"]), float(pricing["completion"]), float(pricing.get("input_cache_read") or pricing["prompt"]))
            except (KeyError, TypeError, ValueError):
                continue

    def note(self, model: str, usage: Any, request_id: str = "") -> float:
        """One call's cost, estimated from its usage and added to the total; its gateway id kept for settling. Zero, and one more
        unpriced call, when the price is unknown."""
        estimate = 0.0
        if usage is None:
            # A client that says nothing about tokens: nothing to estimate, and nothing to call unpriced.
            if request_id:
                self.pending.append((request_id, 0.0))
            return 0.0
        if (price := self.prices.get(model)) is not None:
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            details = getattr(usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0)
            estimate = (prompt - cached) * price[0] + cached * price[2] + completion * price[1]
            self.total += estimate
        else:
            self.unpriced += 1
        if request_id:
            self.pending.append((request_id, estimate))
        return estimate

    async def settle(self, client: AsyncOpenAI) -> None:
        """What the gateway says it charged for each call not yet settled, in place of the estimate. A record it does not have yet
        stays an estimate: this is a correction, never a wait."""
        pending, self.pending = self.pending, []
        for request_id, estimate in pending:
            charged: float | None
            try:
                record = await client.get(f"/generation?id={request_id}", cast_to=dict)
                charged = float(record["data"]["total_cost"])
            except Exception:  # noqa: BLE001  A missing record, a gateway without one, a slow one: the estimate stands.
                charged = None
            if charged is None:
                continue
            if estimate == 0.0 and self.unpriced:
                self.unpriced -= 1
            self.total += charged - estimate


async def chat(client: AsyncOpenAI, **request: Any) -> tuple[Any, str]:
    """One chat completion, and the gateway's id of the request when the client gives the headers; an empty id otherwise."""
    raw = getattr(getattr(getattr(client, "chat", None), "completions", None), "with_raw_response", None)
    if raw is None:
        return await client.chat.completions.create(**request), ""
    response = await raw.create(**request)
    return response.parse(), str(response.headers.get("X-Orca-Request-Id", "") or "")


SPEND = Spend()


def money(cost: float) -> str:
    """A cost as a person says it: under a cent, 4 cents, 1.20 $."""
    if cost < 0.005:
        return "under a cent"
    if cost < 1:
        return f"{cost * 100:.0f} cents"
    return f"{cost:.2f} $"


def refused(error: BaseException) -> bool:
    """Whether OrcaRouter refused the key: the one failure that the person must fix before anything can work."""
    return isinstance(error, AuthenticationError)


def explain(error: Exception) -> str:
    """What a failed model call says in plain words. The detail stays after it, and the key is never in it."""
    if isinstance(error, APITimeoutError):
        return f"OrcaRouter did not answer in time. Try again in a moment. ({error})"
    if isinstance(error, APIConnectionError):
        return f"OrcaRouter cannot be reached: check your internet connection and try again. ({error})"
    if isinstance(error, RateLimitError):
        return f"OrcaRouter is asking AIHawk to slow down: wait a few minutes and try again. ({error})"
    if isinstance(error, InternalServerError):
        return f"OrcaRouter failed on its side: try again in a few minutes. ({error})"
    if isinstance(error, BadRequestError):
        return f"OrcaRouter rejected the request. ({error})"
    return f"Something failed: {error}"


def make_client(config: Config) -> AsyncOpenAI:
    key = config.api_key()
    if not key:
        raise ConfigError(f"no model key: set {KEY_ENV} (create one at {KEY_URL})")
    # The SDK retries 429s and 5xx itself and honours Retry-After, which is all OrcaRouter sends about its limits.
    return AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=key, max_retries=5, timeout=TIMEOUT)


async def structured(client: AsyncOpenAI, model: str, system: str, user: str, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """One answer that follows `schema`. A reply that is not that JSON raises: nothing is guessed from it."""
    response, request_id = await chat(
        client,
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    )
    SPEND.note(model, getattr(response, "usage", None), request_id)
    content = response.choices[0].message.content
    if not content:
        raise ValueError(f"the model gave no {name}")
    return json.loads(content)
