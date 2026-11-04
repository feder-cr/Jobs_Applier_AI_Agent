"""Jev's decision: the request's exact shape, its gate and signs, the client's retries and the failed verdicts. No network: a scripted endpoint stands in."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI, BadRequestError

from aihawk.config import DEFAULT_BASE_URL, load_config
from aihawk.database import Job
from aihawk.judge import evidence, logit, questions

KEY = "sk-test-key"
JOB = Job("g:a:1", "Acme", "Python Engineer", "Rome", "u", "a", "Build Python services.")
ASKED = questions("Senior Python work.", "Rome, Italy")
ANSWERS = {"role": 0.9, "must_haves": 0.6, "gap": 0.2, "level": 0.5, "location": 0.8, "elsewhere": 0.1}


def client_with(handler: Any) -> AsyncOpenAI:
    """The client the session makes, with the endpoint behind it: the same retries and timeout as llm.make_client."""
    return AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=5, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def answering(probabilities: dict[str, float], seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": probabilities[name]} for name in body["questions"]}})

    return handler


def test_the_request_goes_to_the_decision_endpoint_with_the_key_and_the_six_statements() -> None:
    seen: list[httpx.Request] = []
    asyncio.run(evidence(client_with(answering(ANSWERS, seen)), "typesafe/jev-1.14", "cv", JOB, ASKED))
    (request,) = seen
    assert str(request.url) == "https://api.orcarouter.ai/v1/systemone"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer " + KEY
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"]["candidate_cv"] == "cv"
    assert body["state"]["job_posting"].startswith("Python Engineer at Acme. Location: Rome\n\nBuild Python services.")
    assert body["questions"] == {name: {"type": "noul", "instructions": text} for name, (text, _) in ASKED.items()}
    assert len(body["questions"]) == 6


def test_the_answers_are_gated_and_signed_as_their_questions_say() -> None:
    # gap and elsewhere count against the job, so their low answers are support; 0.6 and 0.5 fall inside the gate and count for nothing.
    value = asyncio.run(evidence(client_with(answering(ANSWERS, [])), "typesafe/jev-1.14", "cv", JOB, ASKED))
    assert value == pytest.approx(2 * math.log(9) + 2 * math.log(4))


def test_the_log_odds_are_clamped_and_the_middle_is_no_evidence() -> None:
    assert logit(0.6) == 0.0 and logit(0.36) == 0.0 and logit(0.64) == 0.0
    assert logit(0.65) == pytest.approx(math.log(0.65 / 0.35))
    assert logit(1.0) == pytest.approx(math.log(9999))
    assert logit(0.0) == pytest.approx(-math.log(9999))


def test_a_retryable_failure_is_retried_by_the_client_and_then_answers() -> None:
    seen: list[httpx.Request] = []
    answer = answering({name: 0.9 for name in ASKED}, seen)

    def flaky(request: httpx.Request) -> httpx.Response:
        if not seen:
            seen.append(request)
            # A 503 that says to try again at once: the client retries it, as it does for every call the session makes.
            return httpx.Response(503, headers={"retry-after-ms": "1"}, json={"error": "busy"})
        return answer(request)

    value = asyncio.run(evidence(client_with(flaky), "typesafe/jev-1.14", "cv", JOB, ASKED))
    assert len(seen) == 2
    # Four answers add, two subtract: 0.9 is the same log-odds each time.
    assert value == pytest.approx(2 * math.log(9))


def test_an_error_the_client_does_not_retry_raises_and_never_reads_as_no_opinion() -> None:
    seen: list[httpx.Request] = []

    def refused(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": "the request is wrong"})

    with pytest.raises(BadRequestError) as caught:
        asyncio.run(evidence(client_with(refused), "typesafe/jev-1.14", "cv", JOB, ASKED))
    assert len(seen) == 1
    # The key is in the request's header only: no error message carries it.
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)


def test_an_answer_of_another_shape_raises_and_never_reads_as_no_opinion() -> None:
    with pytest.raises(KeyError):
        asyncio.run(evidence(client_with(lambda request: httpx.Response(200, json={"unexpected": 1})), "typesafe/jev-1.14", "cv", JOB, ASKED))


def test_the_decision_model_is_pinned_and_settable_in_the_settings(tmp_path: Path) -> None:
    assert load_config(tmp_path).decision_model == "typesafe/jev-1.14"
    (tmp_path / "aihawk.yaml").write_text("decision_model: other/model\n", encoding="utf-8")
    assert load_config(tmp_path).decision_model == "other/model"
