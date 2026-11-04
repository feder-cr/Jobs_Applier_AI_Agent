"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI

from aihawk.apply import Confirm, Outcome, apply_to_job
from aihawk.config import ConfigError, load_config
from aihawk.database import search, with_description
from aihawk.documents import score_job, tailor_cv, write_cv_pdf
from aihawk.llm import make_client
from aihawk.plugins import Plugin, load_plugins, plugin_for
from aihawk.profile import answer_question, install_resume
from aihawk.store import SUBMITTED, Store, Stored

BATCH = 40
SCORING_AT_ONCE = 5
MAX_ROUNDS = 3

Log = Callable[[str], None]
Ask = Callable[[str], Awaitable[str | None]]


class Session:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.config = load_config(workspace)
        self.plugins: tuple[Plugin, ...] = load_plugins(workspace)
        self.store = Store(self.config.data_dir / "jobs.db")
        self._client: AsyncOpenAI | None = None

    def close(self) -> None:
        self.store.close()

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = make_client(self.config)
        return self._client

    def needs_key(self) -> bool:
        return not self.config.api_key()

    def save_key(self, key: str) -> None:
        self.config.save_key(key)
        self._client = None

    def needs_resume(self) -> bool:
        return not self.config.cv.is_file() or not self.config.filters.include

    async def load_resume(self, path: Path) -> None:
        await install_resume(self.client, self.config, path)
        self.config = load_config(self.workspace)

    def plugin(self, item: Stored) -> Plugin | None:
        return plugin_for(item.job.url, self.plugins)

    async def find(self, log: Log) -> int:
        """Bring today's postings for this search into the store; how many are new."""
        manifest, jobs = await asyncio.to_thread(search, self.config.database, self.config.filters, self.config.max_age_days, self.config.data_dir / "index", log)
        new = sum(self.store.add(job) for job in jobs)
        log(f"{len(jobs)} postings fit your search, of {manifest['rows_out']:,} in today's database; {new} new")
        return new

    def next_to_score(self, count: int = BATCH) -> list[Stored]:
        """The postings to judge next: where AIHawk can apply first, the newest first. Judging costs model calls."""
        unscored = self.store.unscored()
        return sorted(unscored, key=lambda item: self.plugin(item) is None)[:count]

    async def score(self, items: list[Stored], on_scored: Callable[[], None], log: Log) -> None:
        cv = self.config.cv.read_text(encoding="utf-8")
        limit = asyncio.Semaphore(SCORING_AT_ONCE)

        async def one(item: Stored) -> None:
            async with limit:
                try:
                    job = await asyncio.to_thread(with_description, item.job)
                    report = await score_job(self.client, self.config.model, job, cv, self.config.goals)
                except Exception as error:  # noqa: BLE001  One posting that cannot be judged must not stop the others.
                    log(f"{item.job.company}: not judged: {error}")
                    return
            self.store.set_description(item.job.id, job.description)
            self.store.set_score(item.job.id, report["score"], report)
            on_scored()

        await asyncio.gather(*(one(item) for item in items))

    async def _cv_for(self, item: Stored, cv: str, log: Log) -> Path:
        if item.cv_path and Path(item.cv_path).is_file():
            return Path(item.cv_path)
        log("writing a CV for this job")
        job = await asyncio.to_thread(with_description, item.job)
        tailored = await tailor_cv(self.client, self.config.model, job, cv)
        name = re.sub(r"[^A-Za-z0-9]+", "-", item.job.id).strip("-")[:120]
        path = self.config.data_dir / "cvs" / f"{name}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".md").write_text(tailored, encoding="utf-8")
        write_cv_pdf(tailored, path)
        self.store.set_cv(item.job.id, path)
        return path

    async def apply(self, item: Stored, confirm: Confirm | None, ask: Ask, log: Log) -> Outcome:
        """Fill and send one application. What a form asks and the files do not say is asked of the person, then tried again."""
        if item.status in SUBMITTED:
            raise ConfigError(f"{item.job.company}: already sent, and not sent twice")
        plugin = self.plugin(item)
        if plugin is None:
            raise ConfigError(f"no plugin knows this site. Apply by hand: {item.job.url}")
        cv = self.config.cv.read_text(encoding="utf-8")
        cv_file = await self._cv_for(item, cv, log)
        job = replace(item.job, apply_url=plugin.form_url(item.job.url))
        outcome = Outcome("failed", "not tried")
        for _ in range(MAX_ROUNDS):
            answers = self.config.answers.read_text(encoding="utf-8") if self.config.answers.is_file() else ""
            log(f"filling the form on {plugin.name}")
            try:
                outcome = await apply_to_job(self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, confirm, plugin.instructions)
            except Exception as error:  # noqa: BLE001  One job's failure is reported and recorded; the next job still runs.
                outcome = Outcome("failed", str(error) or type(error).__name__)
            if outcome.status != "needs_input":
                break
            answered = False
            for question in outcome.questions:
                answer = await ask(question)
                if answer:
                    answer_question(self.config, question, answer)
                    answered = True
            if not answered:
                break
        self.store.set_status(item.job.id, outcome.status, outcome.summary)
        return outcome
