"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI

from aihawk.apply import Confirm, Outcome, apply_to_job
from aihawk.apply import sign_in as sign_in_browser
from aihawk.config import Config, ConfigError, load_config
from aihawk.database import Job, search, stored_description, with_description
from aihawk.documents import score_job, tailor_cv, write_cv_pdf
from aihawk.judge import Judge, fuse, questions
from aihawk.llm import make_client
from aihawk.plugins import Plugin, always_plugins, load_plugins, plugin_for
from aihawk.profile import answer_question, install_resume
from aihawk.store import SUBMITTED, Store, Stored

# The funnel: the next WIDE postings by title are weighed by jevos, and the best JUDGED of them are scored by the judge model.
WIDE = 100
JUDGED = 25
JUDGE_AT_ONCE = 4
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
        self._judge: Judge | None = None

    def close(self) -> None:
        if self._judge is not None:
            self._judge.close()
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

    async def sign_in(self, wait: Callable[[], Awaitable[None]]) -> None:
        """Open the browser for the person to sign in, until `wait` returns: the same profile the applications use."""
        await sign_in_browser(self.config.browser_profile, self.config.data_dir / "browser", wait)

    async def find(self, log: Log) -> int:
        """Bring today's postings for this search into the store; how many are new."""
        manifest, jobs = await asyncio.to_thread(search, self.config.database, self.config.filters, self.config.max_age_days, self.config.data_dir / "index", log)
        new = sum(self.store.add(job) for job in jobs)
        log(f"{len(jobs)} postings fit your search, of {manifest['rows_out']:,} in today's database; {new} new")
        return new

    async def funnel(self, log: Log, status: Log) -> int:
        """Weigh the next WIDE postings by title with jevos, and shortlist the best JUDGED by the title's fit and that evidence.
        The others of the batch are passed over: kept, and listed below the judged ones. Returns how many are shortlisted."""
        batch = self.store.with_status("new")[:WIDE]
        if not batch:
            return 0
        texts = await asyncio.to_thread(_descriptions, [item.job.ref for item in batch])
        for item, text in zip(batch, texts, strict=True):
            self.store.set_description(item.job.id, text)
        jobs = [replace(item.job, description=text) for item, text in zip(batch, texts, strict=True)]
        judge = await self._judge_for(log, status)
        cv = self.config.cv.read_text(encoding="utf-8")
        asked = questions(self.config.goals, _places(self.config))
        limit = asyncio.Semaphore(JUDGE_AT_ONCE)

        async def weigh(job: Job) -> float:
            async with limit:
                return await judge.read(cv, job, asked)

        status(f"jevos reads {len(jobs)} postings")
        values = list(await asyncio.gather(*(weigh(job) for job in jobs)))
        for job, value in zip(jobs, values, strict=True):
            self.store.set_evidence(job.id, value)
        order = fuse([job.lexical for job in jobs], values)
        best = {jobs[index].id for index in sorted(range(len(jobs)), key=lambda index: -order[index])[:JUDGED]}
        self.store.set_statuses([job.id for job in jobs if job.id in best], "shortlisted")
        self.store.set_statuses([job.id for job in jobs if job.id not in best], "passed_over")
        return len(best)

    async def _judge_for(self, log: Log, status: Log) -> Judge:
        if self._judge is None:
            self._judge = Judge(self.config)
            await self._judge.start(log, status)
        return self._judge

    async def score(self, items: list[Stored], on_scored: Callable[[], None], log: Log) -> None:
        cv = self.config.cv.read_text(encoding="utf-8")
        limit = asyncio.Semaphore(SCORING_AT_ONCE)

        async def one(item: Stored) -> None:
            async with limit:
                try:
                    report = await score_job(self.client, self.config.judge_model, item.job, cv, self.config.goals)
                except Exception as error:  # noqa: BLE001  One posting that cannot be judged must not stop the others.
                    log(f"{item.job.company}: not judged: {error}")
                    return
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
            extras = always_plugins(self.plugins)
            guidance = "\n\n".join([plugin.instructions, *(extra.instructions for extra in extras)])
            reaches = frozenset(host for extra in extras for host in extra.reaches)
            try:
                outcome = await apply_to_job(self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, self.config.browser_profile, confirm, guidance, reaches)
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


def _descriptions(refs: list[str]) -> list[str]:
    """The descriptions the database holds, read eight at a time."""
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(stored_description, refs))


def _places(config: Config) -> str:
    return " or ".join(config.filters.cities[:3]) + ", " + (config.filters.locations or [""])[-1]
