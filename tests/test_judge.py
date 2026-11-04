"""Jev's decision: the request's exact shape, its gate and signs, the batches and their limits, the client's retries and the failed verdicts. No network: a scripted endpoint stands in."""

from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from aihawk.config import DEFAULT_BASE_URL, load_config
from aihawk.database import Job
from aihawk.judge import DESCRIPTION_CHARS, MAX_CHARS, MAX_POSTINGS, evidence, logit, questions

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
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": probabilities[name.partition("_")[2]]} for name in body["questions"]}})

    return handler


def run(client: AsyncOpenAI, jobs: list[Job], cv: str = "cv") -> list[float | None]:
    return asyncio.run(evidence(client, "typesafe/jev-1.14", cv, jobs, ASKED, lambda done: None))


def postings(count: int, description: str = "") -> list[Job]:
    """`count` postings, the n-th titled Engineer n."""
    return [Job(f"j{number:03d}", "Firm", f"Engineer {number}", "Rome", "u", "a", description) for number in range(count)]


def test_the_request_goes_to_the_decision_endpoint_with_the_key_and_the_six_statements() -> None:
    seen: list[httpx.Request] = []
    run(client_with(answering(ANSWERS, seen)), [JOB])
    (request,) = seen
    assert str(request.url) == "https://api.orcarouter.ai/v1/systemone"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer " + KEY
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"]["candidate_cv"] == "cv"
    assert body["state"]["job_postings"] == {"p00": "Python Engineer at Acme. Location: Rome\n\nBuild Python services."}
    assert body["questions"] == {f"p00_{name}": {"type": "noul", "instructions": f"Posting p00 (Python Engineer at Acme): {text}"} for name, (text, _) in ASKED.items()}
    assert len(body["questions"]) == 6


def test_the_answers_are_gated_and_signed_as_their_questions_say() -> None:
    # gap and elsewhere count against the job, so their low answers are support; 0.6 and 0.5 fall inside the gate and count for nothing.
    (value,) = run(client_with(answering(ANSWERS, [])), [JOB])
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

    (value,) = run(client_with(flaky), [JOB])
    assert len(seen) == 2
    # Four answers add, two subtract: 0.9 is the same log-odds each time.
    assert value == pytest.approx(2 * math.log(9))


def test_a_refused_posting_alone_is_a_failed_verdict_and_not_a_no_opinion() -> None:
    seen: list[httpx.Request] = []

    def refused(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": "the request is wrong"})

    assert run(client_with(refused), [JOB]) == [None]
    assert len(seen) == 1


def test_an_answer_of_another_shape_is_a_failed_verdict() -> None:
    assert run(client_with(lambda request: httpx.Response(200, json={"unexpected": 1})), [JOB]) == [None]


def test_a_missing_answer_fails_only_that_posting() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.9} for name in body["questions"] if name != "p01_gap"}})

    values = run(client_with(handler), postings(3))
    assert len(seen) == 1
    assert values[0] == pytest.approx(2 * math.log(9)) and values[2] == pytest.approx(2 * math.log(9))
    assert values[1] is None


def test_a_hundred_postings_make_two_requests_of_at_most_85_and_keep_their_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        # Each posting's answers differ, so a value in the wrong place shows.
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.8 + 0.002 * int(re.search(r"Engineer (\d+) at", question["instructions"])[1])} for name, question in body["questions"].items()}})

    values = run(client_with(handler), postings(100))
    sizes = sorted(len(json.loads(request.content)["state"]["job_postings"]) for request in seen)
    assert sizes == [15, MAX_POSTINGS]
    assert all(len(json.loads(request.content)["questions"]) <= 512 for request in seen)
    assert values == pytest.approx([2 * logit(0.8 + 0.002 * number) for number in range(100)])


def test_a_request_over_the_character_limit_is_split() -> None:
    seen: list[httpx.Request] = []
    values = run(client_with(answering(ANSWERS, seen)), postings(40, "x" * DESCRIPTION_CHARS))
    assert all(value is not None for value in values) and len(values) == 40
    assert len(seen) >= 2
    assert all(len(request.content) <= MAX_CHARS for request in seen)


def test_a_failing_batch_is_split_until_the_bad_posting_is_alone() -> None:
    seen: list[httpx.Request] = []
    answer = answering({name: 0.9 for name in ASKED}, seen)

    def refusing(request: httpx.Request) -> httpx.Response:
        if b"Engineer 7 at" in request.content:
            seen.append(request)
            return httpx.Response(400, json={"error": "the request is wrong"})
        return answer(request)

    values = run(client_with(refusing), postings(100))
    assert values[7] is None
    assert values[:7] + values[8:] == pytest.approx([2 * math.log(9)] * 99)
    # Every request that held posting 7 was refused, down to one that held it alone.
    assert min(len(json.loads(request.content)["state"]["job_postings"]) for request in seen if b"Engineer 7 at" in request.content) == 1


def test_the_decision_model_is_pinned_and_settable_in_the_settings(tmp_path: Path) -> None:
    assert load_config(tmp_path).decision_model == "typesafe/jev-1.14"
    (tmp_path / "aihawk.yaml").write_text("decision_model: other/model\n", encoding="utf-8")
    assert load_config(tmp_path).decision_model == "other/model"
