"""Jev's verdict: the request's exact shape, the one question, the score from its certainty, the batches and their limits, the client's retries and the failed verdicts. No network: a scripted endpoint stands in."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from aihawk.config import DEFAULT_BASE_URL, load_config
from aihawk.database import Job
from aihawk.scoring import DESCRIPTION_CHARS, MAX_CHARS, MAX_POSTINGS, NOT_JUDGED, fits, question, score

KEY = "sk-test-key"
JOB = Job("g:a:1", "Acme", "Python Engineer", "Rome", "u", "a", "Build Python services.")
ASKED = question("Senior Python work.", "Rome, Italy")


def client_with(handler: Any) -> AsyncOpenAI:
    """The client the session makes, with the endpoint behind it: the same retries and timeout as llm.make_client."""
    return AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=5, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def answering(certainty: float, seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": certainty} for name in body["questions"]}})

    return handler


def run(client: AsyncOpenAI, jobs: list[Job], cv: str = "cv", why: Callable[[str], None] = lambda reason: None) -> list[float | None]:
    return asyncio.run(fits(client, "typesafe/jev-1.14", cv, jobs, ASKED, why))


def postings(count: int, description: str = "") -> list[Job]:
    """`count` postings, the n-th titled Engineer n."""
    return [Job(f"j{number:03d}", "Firm", f"Engineer {number}", "Rome", "u", "a", description) for number in range(count)]


def test_the_request_goes_to_the_decision_endpoint_with_the_key_and_one_question() -> None:
    seen: list[httpx.Request] = []
    assert run(client_with(answering(0.8, seen)), [JOB]) == [0.8]
    (request,) = seen
    assert str(request.url) == "https://api.orcarouter.ai/v1/systemone"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer " + KEY
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"]["candidate_cv"] == "cv"
    assert body["state"]["job_postings"] == {"p00": "Python Engineer at Acme. Location: Rome\n\nBuild Python services."}
    assert body["questions"] == {"p00_fit": {"type": "noul", "instructions": f"Posting p00 (Python Engineer at Acme): {ASKED}"}}
    assert "Senior Python work." in ASKED and "Rome, Italy" in ASKED


def test_the_score_is_how_sure_the_yes_is_from_1_to_5() -> None:
    assert [score(value) for value in (0.0, 0.5, 0.85, 1.0)] == [1.0, 3.0, 4.4, 5.0]
    assert score(-0.2) == 1.0 and score(1.3) == 5.0


def test_a_retryable_failure_is_retried_by_the_client_and_then_answers() -> None:
    seen: list[httpx.Request] = []
    answer = answering(0.9, seen)

    def flaky(request: httpx.Request) -> httpx.Response:
        if not seen:
            seen.append(request)
            # A 503 that says to try again at once: the client retries it, as it does for every call the session makes.
            return httpx.Response(503, headers={"retry-after-ms": "1"}, json={"error": "busy"})
        return answer(request)

    assert run(client_with(flaky), [JOB]) == [0.9]
    assert len(seen) == 2


def test_a_refused_posting_alone_is_a_failed_verdict_and_not_a_no_opinion() -> None:
    seen: list[httpx.Request] = []

    def refused(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": "the request is wrong"})

    assert run(client_with(refused), [JOB]) == [None]
    assert len(seen) == 1


def test_a_failure_that_a_smaller_batch_cannot_cure_is_not_split_and_the_person_is_told_why() -> None:
    seen: list[httpx.Request] = []
    said: list[str] = []

    def refusing(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "the request is wrong", "code": "invalid_request_error"}})

    assert run(client_with(refusing), postings(8), why=said.append) == [None] * 8
    assert len(seen) == 1
    assert len(said) == 1 and said[0] == "The AI service did not accept the request."


@pytest.mark.parametrize("answer", [{"unexpected": 1}, {"answers": {"p00_fit": {"noul": "yes"}}}, {"answers": {"p00_fit": {"noul": 1.5}}}])
def test_an_answer_of_another_shape_is_a_failed_verdict(answer: dict[str, Any]) -> None:
    assert run(client_with(lambda request: httpx.Response(200, json=answer)), [JOB]) == [None]


def test_a_missing_answer_fails_only_that_posting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.9} for name in body["questions"] if name != "p01_fit"}})

    assert run(client_with(handler), postings(3)) == [0.9, None, 0.9]


def test_up_to_512_postings_go_in_one_request_and_more_make_another_in_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        # Each posting's answer differs, so a value in the wrong place shows.
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": int(re.search(r"Engineer (\d+) at", question["instructions"])[1]) / 1000} for name, question in body["questions"].items()}})

    values = run(client_with(handler), postings(600))
    sizes = sorted(len(json.loads(request.content)["state"]["job_postings"]) for request in seen)
    assert MAX_POSTINGS == 512 and sizes == [88, MAX_POSTINGS]
    assert values == pytest.approx([number / 1000 for number in range(600)])


def test_a_request_over_the_character_limit_is_split() -> None:
    seen: list[httpx.Request] = []
    values = run(client_with(answering(0.7, seen)), postings(40, "x" * DESCRIPTION_CHARS))
    assert values == [0.7] * 40
    assert len(seen) >= 2
    assert all(len(request.content) <= MAX_CHARS for request in seen)


def test_a_failing_batch_is_split_until_the_bad_posting_is_alone() -> None:
    seen: list[httpx.Request] = []
    answer = answering(0.9, seen)

    def refusing(request: httpx.Request) -> httpx.Response:
        if b"Engineer 7 at" in request.content:
            seen.append(request)
            return httpx.Response(400, json={"error": {"message": "This model's maximum context length is exceeded", "code": "context_length_exceeded"}})
        return answer(request)

    values = run(client_with(refusing), postings(100))
    assert values[7] is None
    assert values[:7] + values[8:] == [0.9] * 99
    # Every request that held posting 7 was refused, down to one that held it alone.
    assert min(len(json.loads(request.content)["state"]["job_postings"]) for request in seen if b"Engineer 7 at" in request.content) == 1


def test_the_decision_model_is_pinned_and_settable_in_the_settings(tmp_path: Path) -> None:
    assert load_config(tmp_path).decision_model == "typesafe/jev-1.14"
    (tmp_path / "aihawk.yaml").write_text("decision_model: other/model\n", encoding="utf-8")
    assert load_config(tmp_path).decision_model == "other/model"


@pytest.mark.parametrize("status", [429, 500, 503, 408])
def test_a_service_that_does_not_answer_leaves_the_postings_not_judged_and_says_nothing_of_them(status: int) -> None:
    said: list[str] = []
    client = AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "busy"}))))
    assert run(client, postings(3), why=said.append) == [NOT_JUDGED] * 3
    assert said == []


def test_a_lost_connection_leaves_the_postings_not_judged() -> None:
    def lost(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lost)))
    assert run(client, [JOB]) == [NOT_JUDGED]


def test_only_the_request_that_got_no_answer_is_not_judged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "Engineer 0 at" in request.content.decode():
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": 0.5} for name in body["questions"]}})

    client = AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    jobs = postings(MAX_POSTINGS + 1)
    values = run(client, jobs)
    assert values[:MAX_POSTINGS] == [NOT_JUDGED] * MAX_POSTINGS and values[MAX_POSTINGS] == 0.5


@pytest.mark.parametrize("status", [402, 403, 404])
def test_what_is_about_the_account_and_not_about_a_posting_is_raised_and_no_posting_is_set_aside(status: int) -> None:
    from openai import APIStatusError

    client = AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=KEY, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "no"}))))
    with pytest.raises(APIStatusError) as raised:
        run(client, postings(3))
    assert raised.value.status_code == status


def test_an_account_with_no_credit_is_told_in_plain_words_and_what_to_do() -> None:
    import httpx as http
    from openai import APIStatusError

    from aihawk.llm import explain

    error = APIStatusError("Error code: 402", response=http.Response(402, request=http.Request("POST", "https://x.example")), body=None)
    assert explain(error) == "Your OrcaRouter account has no credit yet. Add a few dollars at orcarouter.ai, then press → to go on."
