"""The decision model's view of a posting's fit: six closed questions answered by Jev through OrcaRouter, as log-odds of evidence."""

from __future__ import annotations

import math
import statistics
from typing import Any

from openai import AsyncOpenAI

from aihawk.database import Job

DESCRIPTION_CHARS = 24000
DECISION_ENDPOINT = "/systemone"


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


async def evidence(client: AsyncOpenAI, model: str, cv: str, job: Job, asked: dict[str, tuple[str, int]]) -> float:
    """The posting's evidence: each answer's log-odds, signed as its question says, added up. The client retries what can be retried and
    raises the rest; the caller counts a failed verdict, so it is never taken for no opinion."""
    state = {"candidate_cv": cv, "job_posting": f"{job.title} at {job.company}. Location: {job.location}\n\n{job.description[:DESCRIPTION_CHARS]}"}
    body: dict[str, Any] = {"model": model, "state": state, "questions": {name: {"type": "noul", "instructions": text} for name, (text, _) in asked.items()}}
    answers = (await client.post(DECISION_ENDPOINT, body=body, cast_to=dict))["answers"]
    return sum(sign * logit(answers[name]["noul"]) for name, (_, sign) in asked.items())


def fuse(lexical: list[float], evidence_of: list[float]) -> list[float]:
    """Each posting's title fit as a z-score over its batch, plus its evidence: the order the funnel and the list both use."""
    if not lexical:
        return []
    mean, spread = statistics.mean(lexical), statistics.pstdev(lexical) or 1.0
    return [(value - mean) / spread + added for value, added in zip(lexical, evidence_of, strict=True)]
