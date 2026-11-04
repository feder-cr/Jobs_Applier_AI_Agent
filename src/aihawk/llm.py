"""The one model gateway: OrcaRouter, or any endpoint compatible with OpenAI's API that the settings name."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from aihawk.config import Config, ConfigError


def make_client(config: Config) -> AsyncOpenAI:
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ConfigError(f"no model key: set {config.api_key_env} (create one at https://orcarouter.ai)")
    # The SDK retries 429s and 5xx itself and honours Retry-After, which is all OrcaRouter sends about its limits.
    return AsyncOpenAI(base_url=config.base_url, api_key=key, max_retries=5)


async def structured(client: AsyncOpenAI, model: str, system: str, user: str, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """One answer that follows `schema`. A reply that is not that JSON raises: nothing is guessed from it."""
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError(f"the model gave no {name}")
    return json.loads(content)
