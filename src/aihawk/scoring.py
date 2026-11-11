"""Jev's verdict on each posting: one closed question, whether the job is in line with the candidate, answered through
OrcaRouter with how sure the yes is. That certainty is the posting's score, from 1 to 5."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from openai import APIStatusError, AsyncOpenAI

from aihawk import errors
from aihawk.database import Job
from aihawk.llm import account_problem, not_answering, refused

DESCRIPTION_CHARS = 24000
DECISION_ENDPOINT = "/systemone"
# Postings are sent in batches to save requests. The endpoint refuses more than 512 questions in one request, one per
# posting here. A batch is also kept to MAX_CHARS, a rough cap on its size at about four characters a token.
MAX_POSTINGS = 512
MAX_CHARS = 180_000 * 4
# The words an endpoint's refusal uses for a request or context that is too big, which a smaller batch can cure.
TOO_LARGE = re.compile(r"context|too (?:large|long|many)")


class NotJudged:
    """What a posting's value is when the request about it got no answer: not that Jev had none for it. It is asked about
    again; a posting Jev gave no verdict for (None) is set aside."""


NOT_JUDGED = NotJudged()
Value = float | None | NotJudged


class NotAnswering(Exception):
    """A round's request got no answer, so its postings were left to be read again."""


def question(goals: str, places: str) -> str:
    """The one question, asked of every posting: in or out."""
    return (
        f"The candidate is looking for this: {goals} This job is in line with it: the same kind of work at their level, "
        f"their CV shows what the posting requires, and it is based in or near {places}, or remote and open to people living there."
    )


def score(certainty: float) -> float:
    """The score of a yes this sure, from 1 for a sure no to 5 for a sure yes."""
    return round(1 + 4 * min(1.0, max(0.0, certainty)), 1)


async def fits(client: AsyncOpenAI, model: str, cv: str, jobs: list[Job], asked: str, why: Callable[[str], None]) -> list[Value]:
    """How sure Jev is that each posting is in line, in order; None where it gave no verdict, NOT_JUDGED where the request
    got no answer at all. The groups go at once. `why` is told, once per request that cannot work as it is, in plain words why."""
    parts = await asyncio.gather(*(_read(client, model, cv, group, asked, why) for group in groups(model, cv, jobs, asked)))
    return [value for part in parts for value in part]


def groups(model: str, cv: str, jobs: list[Job], asked: str) -> list[list[Job]]:
    """In order, each group takes the next postings while it has fewer than MAX_POSTINGS and its request stays under
    MAX_CHARS. A posting's size is counted once, not the whole request again for each one."""
    base = len(json.dumps(request_body(model, cv, [], asked), ensure_ascii=False))
    out: list[list[Job]] = []
    size = base
    for job in jobs:
        index = len(out[-1]) if out else 0
        added = len(json.dumps({_name(index): _posting(job), **_question(index, job, asked)}, ensure_ascii=False)) + 8
        if out and len(out[-1]) < MAX_POSTINGS and size + added <= MAX_CHARS:
            out[-1].append(job)
            size += added
        else:
            out.append([job])
            size = base + len(json.dumps({_name(0): _posting(job), **_question(0, job, asked)}, ensure_ascii=False)) + 8
    return out


def too_large(error: BaseException) -> bool:
    """Whether a smaller batch can cure the failure: the request or its context is too large for the endpoint."""
    if not isinstance(error, APIStatusError):
        return False
    return error.status_code == 413 or (error.status_code == 400 and TOO_LARGE.search(str(error).lower()) is not None)


async def _read(client: AsyncOpenAI, model: str, cv: str, jobs: list[Job], asked: str, why: Callable[[str], None]) -> list[Value]:
    """One request's values. A request too large for the endpoint is split in two and each half tried again. A service that does not
    answer leaves its postings not judged. Any other failure ends the request at once, without a split: its postings have no
    verdict, and `why` says why."""
    try:
        reply = await client.post(DECISION_ENDPOINT, body=request_body(model, cv, jobs, asked), cast_to=dict)
    except Exception as error:
        # A refused key, or an account with no credit, fails every request alike: splitting would only ask again, so it is raised
        # for the screen, and no posting is set aside for it.
        if refused(error) or account_problem(error):
            raise
        if too_large(error) and len(jobs) > 1:
            middle = len(jobs) // 2
            return [*await _read(client, model, cv, jobs[:middle], asked, why), *await _read(client, model, cv, jobs[middle:], asked, why)]
        if not_answering(error):
            return [NOT_JUDGED] * len(jobs)
        why(errors.explained(error))
        return [None] * len(jobs)
    answers = reply.get("answers") or {}
    values = [_verdict(answers, index) for index in range(len(jobs))]
    if any(value is None for value in values):
        why("The AI service left some out.")
    return values


def request_body(model: str, cv: str, jobs: list[Job], asked: str) -> dict[str, Any]:
    """The postings as p00, p01, ... in one state, the question asked of each posting by name, the posting named in its wording."""
    state = {"candidate_cv": cv, "job_postings": {_name(index): _posting(job) for index, job in enumerate(jobs)}}
    questions = {name: entry for index, job in enumerate(jobs) for name, entry in _question(index, job, asked).items()}
    return {"model": model, "state": state, "questions": questions}


def _question(index: int, job: Job, asked: str) -> dict[str, Any]:
    return {f"{_name(index)}_fit": {"type": "noul", "instructions": f"Posting {_name(index)} ({job.title} at {job.company}): {asked}"}}


def _name(index: int) -> str:
    return f"p{index:02d}"


def _posting(job: Job) -> str:
    return f"{job.title} at {job.company}. Location: {job.location}\n\n{job.description[:DESCRIPTION_CHARS]}"


def _verdict(answers: dict[str, Any], index: int) -> float | None:
    """How sure the yes is, from 0 to 1. A missing answer, or one of another shape, fails that posting alone."""
    try:
        value = float(answers[f"{_name(index)}_fit"]["noul"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None
