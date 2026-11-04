"""The decision model's view of a posting's fit: six closed questions answered by the decision model (Jev by default) through OrcaRouter, as log-odds of evidence."""

from __future__ import annotations

import asyncio
import json
import math
import statistics
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from aihawk.database import Job
from aihawk.llm import refused

DESCRIPTION_CHARS = 24000
DECISION_ENDPOINT = "/systemone"
# Batching is kept for its speed: the ranking with batches and with one posting a request differed by less than the noise of five
# people's grades. The endpoint refuses more than 512 questions a request (85 postings at six statements is 510), and the input
# is capped at 180k tokens, below the size the endpoint refused in testing.
MAX_POSTINGS = 85
MAX_CHARS = 180_000 * 4


def logit(p: float) -> float:
    """The log-odds of a probability; nothing near the middle, which is no evidence either way."""
    if abs(p - 0.5) < 0.15:
        return 0.0
    p = min(1 - 1e-4, max(1e-4, p))
    return math.log(p / (1 - p))


def questions(goals: str, places: str) -> dict[str, tuple[str, int]]:
    """The six questions, each with the sign its yes adds to the evidence."""
    return {
        "role": (f"The candidate is looking for this: {goals} This job is that kind of work, in the same field.", 1),
        "must_haves": ("Every requirement the posting states appears in the candidate's CV.", 1),
        "gap": ("The posting requires a skill, licence, language or experience that the candidate's CV does not show.", -1),
        "level": (f"The candidate is looking for this: {goals} The posting's seniority matches.", 1),
        "location": (f"The job is based in or near {places}, or it is remote and open to people living there.", 1),
        "elsewhere": (f"The job is based somewhere far from {places}, in a place the candidate could not commute to.", -1),
    }


async def evidence(client: AsyncOpenAI, model: str, cv: str, jobs: list[Job], asked: dict[str, tuple[str, int]], progress: Callable[[int], None]) -> list[float | None]:
    """One value per posting, in order; None where it failed alone. The groups go at once, and `progress` is told the postings done so far."""
    done = 0

    async def read(group: list[Job]) -> list[float | None]:
        nonlocal done
        values = await _read(client, model, cv, group, asked)
        done += len(group)
        progress(done)
        return values

    parts = await asyncio.gather(*(read(group) for group in groups(model, cv, jobs, asked)))
    return [value for part in parts for value in part]


def groups(model: str, cv: str, jobs: list[Job], asked: dict[str, tuple[str, int]]) -> list[list[Job]]:
    """In order, each group takes the next postings while it has fewer than MAX_POSTINGS and its request stays under MAX_CHARS."""
    out: list[list[Job]] = []
    for job in jobs:
        if out and len(out[-1]) < MAX_POSTINGS and len(json.dumps(request_body(model, cv, [*out[-1], job], asked), ensure_ascii=False)) <= MAX_CHARS:
            out[-1].append(job)
        else:
            out.append([job])
    return out


async def _read(client: AsyncOpenAI, model: str, cv: str, jobs: list[Job], asked: dict[str, tuple[str, int]]) -> list[float | None]:
    """One request's values. A request that fails is split in two and each half tried again; a posting that fails alone is a failed verdict."""
    try:
        reply = await client.post(DECISION_ENDPOINT, body=request_body(model, cv, jobs, asked), cast_to=dict)
    except Exception as error:
        # Whatever the failure, one bad posting must not cost the others their verdicts. A refused key fails every request
        # alike: splitting would only ask again, so it is raised for the screen.
        if refused(error):
            raise
        if len(jobs) == 1:
            return [None]
        middle = len(jobs) // 2
        return [*await _read(client, model, cv, jobs[:middle], asked), *await _read(client, model, cv, jobs[middle:], asked)]
    answers = reply.get("answers") or {}
    return [_verdict(answers, index, asked) for index in range(len(jobs))]


def request_body(model: str, cv: str, jobs: list[Job], asked: dict[str, tuple[str, int]]) -> dict[str, Any]:
    """The postings as p00, p01, ... in one state, each statement asked of each posting by name, and the posting named in its wording."""
    state = {"candidate_cv": cv, "job_postings": {_name(index): _posting(job) for index, job in enumerate(jobs)}}
    questions_of = {
        f"{_name(index)}_{name}": {"type": "noul", "instructions": f"Posting {_name(index)} ({job.title} at {job.company}): {text}"}
        for index, job in enumerate(jobs)
        for name, (text, _) in asked.items()
    }
    return {"model": model, "state": state, "questions": questions_of}


def _name(index: int) -> str:
    return f"p{index:02d}"


def _posting(job: Job) -> str:
    return f"{job.title} at {job.company}. Location: {job.location}\n\n{job.description[:DESCRIPTION_CHARS]}"


def _verdict(answers: dict[str, Any], index: int, asked: dict[str, tuple[str, int]]) -> float | None:
    """A posting's evidence: each answer's log-odds, signed as its question says, added up. A missing answer fails that posting alone."""
    try:
        return sum(sign * logit(answers[f"{_name(index)}_{name}"]["noul"]) for name, (_, sign) in asked.items())
    except (KeyError, TypeError):
        return None


def fuse(lexical: list[float], evidence_of: list[float]) -> list[float]:
    """Each posting's title fit as a z-score over its batch, plus its evidence: the order the funnel and the list both use."""
    if not lexical:
        return []
    mean, spread = statistics.mean(lexical), statistics.pstdev(lexical) or 1.0
    return [(value - mean) / spread + added for value, added in zip(lexical, evidence_of, strict=True)]
