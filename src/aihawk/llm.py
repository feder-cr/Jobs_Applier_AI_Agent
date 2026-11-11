"""The one model gateway, OrcaRouter: OpenAI's chat API, the cost of its calls and how a failed call is told."""

from __future__ import annotations

import json
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from aihawk.config import DEFAULT_BASE_URL, KEY_ENV, KEY_URL, Config, ConfigError

# The SDK's own default is ten minutes a try, and a model that hangs would hold a request that long.
TIMEOUT = 180.0


# A spend's total and tokens at one moment: see Spend.mark.
Mark = tuple[float, dict[str, tuple[int, int]]]


class Spend:
    """What the model calls cost, in dollars: each call's tokens at the gateway's own price for its model, added up. The
    tokens each model took in and gave out are counted too, every call, priced or not.

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
        # The tokens each model took in and gave out.
        self.tokens: dict[str, tuple[int, int]] = {}

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
        if usage is None:
            # A client that says nothing about tokens: nothing to count or estimate, and nothing to call unpriced.
            if request_id:
                self.pending.append((request_id, 0.0))
            return 0.0
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        taken, given = self.tokens.get(model, (0, 0))
        self.tokens[model] = (taken + prompt, given + completion)
        estimate = 0.0
        if (price := self.prices.get(model)) is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0)
            estimate = (prompt - cached) * price[0] + cached * price[2] + completion * price[1]
            self.total += estimate
        else:
            self.unpriced += 1
        if request_id:
            self.pending.append((request_id, estimate))
        return estimate

    def mark(self) -> Mark:
        """Where the count stands now, to ask later what came after it."""
        return self.total, dict(self.tokens)

    def since(self, mark: Mark) -> tuple[float, dict[str, tuple[int, int]]]:
        """What the calls after `mark` cost, and the tokens each model took in and gave out in them; a model that took none is left out."""
        total, before = mark
        used = {}
        for model, (taken, given) in self.tokens.items():
            taken_before, given_before = before.get(model, (0, 0))
            if (taken, given) != (taken_before, given_before):
                used[model] = (taken - taken_before, given - given_before)
        return self.total - total, used

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


def not_answering(error: BaseException) -> bool:
    """Whether the service failed to answer, not that it answered no: no connection, too slow, too busy, or broken on its side.
    The same request can work a moment later, so the postings in it are asked about again, not set aside."""
    if isinstance(error, APIConnectionError | RateLimitError):
        return True
    return isinstance(error, APIStatusError) and (error.status_code == 408 or error.status_code >= 500)


def account_problem(error: BaseException) -> bool:
    """Whether the service refused for what is about the account and not about one posting: no credit left (402), no right to
    ask (403), or nothing there to ask (404). Asking again changes nothing, and nothing is set aside for it."""
    return isinstance(error, APIStatusError) and error.status_code in (402, 403, 404)


def no_credit(error: BaseException) -> bool:
    """Whether the account's credit is finished: the service answers 402."""
    return isinstance(error, APIStatusError) and error.status_code == 402


def known(error: BaseException) -> bool:
    """Whether `explain` has words for this kind of error. One it has none for is unexpected, and goes to the error log."""
    return isinstance(error, APIConnectionError | RateLimitError | InternalServerError | BadRequestError) or account_problem(error)


def explain(error: Exception) -> str:
    """What a failed model call says in plain words and what to do about it. The error's own text stays out: it is for the
    makers, and the key can be in it."""
    if no_credit(error):
        return "Your OrcaRouter credit is finished. Add credit at orcarouter.ai, then press → to go on."
    if account_problem(error):
        return "The AI service refused the request. Check your OrcaRouter account at orcarouter.ai, then press → to go on."
    if isinstance(error, APITimeoutError):
        return "The AI service did not answer in time. Try again in a moment."
    if isinstance(error, APIConnectionError):
        return "Cannot reach the AI service. Check your internet connection."
    if isinstance(error, RateLimitError):
        return "The AI service is busy. Wait a minute."
    if isinstance(error, InternalServerError):
        return "The AI service has a problem on its side. Try again in a few minutes."
    if isinstance(error, BadRequestError):
        return "The AI service did not accept the request."
    return "Something went wrong. Try again in a moment."


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
