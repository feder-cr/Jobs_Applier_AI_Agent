"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI

from aihawk.apply import Outcome, apply_to_job
from aihawk.apply import sign_in as sign_in_browser
from aihawk.config import Config, ConfigError, load_config
from aihawk.database import built_at, search, stored_descriptions, with_description
from aihawk.documents import MissingGlyphs, score_job, tailor_cv, write_cv_pdf
from aihawk.judge import evidence, fuse, questions
from aihawk.llm import explain, make_client, refused
from aihawk.plugins import Plugin, always_plugins, load_plugins, plugin_for
from aihawk.profile import answer_question, install_resume
from aihawk.store import SUBMITTED, Store, Stored, safe_name, write_applied

# The funnel: the next WIDE postings by title are weighed by the decision model, and the best JUDGED of them are scored by the judge model.
WIDE = 100
JUDGED = 25
SCORING_AT_ONCE = 5
# A form that asks something is tried again with the answers, this many rounds at most.
MAX_ROUNDS = 3
ASKED = ("needs_input", "refused")
# A job database built longer ago than this is said to be stale: the person is told no newer one has been published.
STALE_DAYS = 7
# An age limit no posting can pass: the search with it shows what the age limit alone leaves out.
NO_AGE_LIMIT = 36500

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

    def refuse_key(self) -> str:
        """OrcaRouter refused the key. One typed in is forgotten, so the next question asks for another; one from the environment is named."""
        self._client = None
        if self.config.key_from_environment():
            return f"OrcaRouter refused the key in the {self.config.api_key_env} variable. Set it to a key from https://orcarouter.ai, then start AIHawk again."
        self.config.key_file.unlink(missing_ok=True)
        return "OrcaRouter refused the key, so AIHawk forgot it. Get one at https://orcarouter.ai and type it in again."

    def needs_resume(self) -> bool:
        return not self.config.cv.is_file() or not self.config.filters.include

    async def load_resume(self, path: Path) -> None:
        await install_resume(self.client, self.config, path)
        self.config = load_config(self.workspace)

    def plugin(self, item: Stored) -> Plugin | None:
        return plugin_for(item.job.url, self.plugins)

    async def sign_in(self, wait: Callable[[], Awaitable[None]]) -> None:
        """Open the browser for the person to sign in, until `wait` returns: the same profile the applications use."""
        await sign_in_browser(self.config.browser_profile, self.config.data_dir / "browser", wait, self.config.api_key_env)

    async def find(self, log: Log) -> int:
        """Bring the postings for this search from the job database into the store; how many are new."""
        loop = asyncio.get_running_loop()
        index = self.config.data_dir / "index"
        # search runs in a thread, and the screen may be touched only from the loop: its lines are handed over to it.
        manifest, jobs = await asyncio.to_thread(
            search, self.config.database, self.config.filters, self.config.max_age_days, index, lambda line: loop.call_soon_threadsafe(log, line)
        )
        new = sum(self.store.add(job) for job in jobs)
        built = built_at(manifest)
        age = (datetime.now(UTC) - built).days
        log(f"{len(jobs)} postings fit your search, of {manifest['rows_out']:,} in the job database built {built:%Y-%m-%d}; {new} new")
        if age > STALE_DAYS:
            log(f"The job database is {age} days old, and no newer one has been published.")
        if not jobs:
            # The same search with no age limit: if postings fit it, the age limit is why none are shown.
            _, unaged = await asyncio.to_thread(search, self.config.database, self.config.filters, NO_AGE_LIMIT, index)
            if unaged:
                log(f"{len(unaged)} postings fit your search but were posted more than {self.config.max_age_days} days ago, so none are shown.")
        return new

    async def funnel(self, log: Log, status: Log) -> int:
        """Weigh the next WIDE postings by title with the decision model, and shortlist the best JUDGED by the title's fit and that evidence.
        The others of the batch are passed over: kept, and listed below the judged ones. Returns how many are shortlisted."""
        batch = self.store.with_status("new")[:WIDE]
        if not batch:
            return 0
        texts = await _read_descriptions([item.job.ref for item in batch], status, log)
        for item, text in zip(batch, texts, strict=True):
            self.store.set_description(item.job.id, text)
        jobs = [replace(item.job, description=text) for item, text in zip(batch, texts, strict=True)]
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        asked = questions(self.config.goals, _places(self.config))
        def shown(done: int) -> None:
            status(f"Jev reads {done}/{len(jobs)}")

        reasons: list[str] = []
        try:
            # The client is read here: with no key every verdict fails, the title alone decides, and the log says so once.
            values = await evidence(self.client, self.config.decision_model, cv, jobs, asked, shown, reasons.append)
        except Exception as error:
            # Only the client can fail here; a batch that fails is a failed verdict inside evidence.
            if refused(error):
                raise
            values = [None] * len(jobs)
            reasons.append(explain(error))
        failed = sum(value is None for value in values)
        if failed:
            why = "; ".join(dict.fromkeys(reasons))
            log(f"Jev could not read {failed} of {len(jobs)} postings: {why}; they are ranked by their title alone")
        for job, value in zip(jobs, values, strict=True):
            if value is not None:
                self.store.set_evidence(job.id, value)
        order = fuse([job.lexical for job in jobs], [value or 0.0 for value in values])
        best = {jobs[index].id for index in sorted(range(len(jobs)), key=lambda index: -order[index])[:JUDGED]}
        self.store.set_statuses([job.id for job in jobs if job.id in best], "shortlisted")
        self.store.set_statuses([job.id for job in jobs if job.id not in best], "passed_over")
        return len(best)

    async def score(self, items: list[Stored], on_scored: Callable[[], None], log: Log) -> None:
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        limit = asyncio.Semaphore(SCORING_AT_ONCE)

        async def one(item: Stored) -> None:
            async with limit:
                try:
                    report = await score_job(self.client, self.config.judge_model, item.job, cv, self.config.goals)
                except Exception as error:
                    # One posting that cannot be judged must not stop the others.
                    if refused(error):
                        raise
                    log(f"{item.job.company}: not judged: {explain(error)}")
                    return
            self.store.set_score(item.job.id, report["score"], report)
            on_scored()

        # A refused key is raised after the others, so the screen can ask for another one.
        for result in await asyncio.gather(*(one(item) for item in items), return_exceptions=True):
            if isinstance(result, Exception):
                raise result

    async def _cv_for(self, item: Stored, cv: str, log: Log) -> Path:
        if item.cv_path and Path(item.cv_path).is_file():
            return Path(item.cv_path)
        log("writing a CV for this job")
        job = await asyncio.to_thread(with_description, item.job)
        tailored = await tailor_cv(self.client, self.config.model, job, cv)
        path = self.config.data_dir / "cvs" / f"{safe_name(item.job.id)}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_cv_pdf(tailored, path)
        except MissingGlyphs as error:
            # The font cannot draw this posting's language, and a PDF with blanks where the text is must not be uploaded.
            own = self._own_cv()
            log(f"the CV written for this job cannot show {str(error)[:12]!r}, which the PDF font does not have: your own CV is sent with this application")
            self.store.set_cv(item.job.id, own)
            return own
        path.with_suffix(".md").write_text(tailored, encoding="utf-8")
        self.store.set_cv(item.job.id, path)
        return path

    def _own_cv(self) -> Path:
        """The person's CV as they gave it, kept by install_resume beside the tailored ones."""
        kept = sorted((self.config.data_dir / "cvs").glob("original-cv.*"))
        if not kept:
            raise ConfigError("the CV for this posting cannot be written in its language, and your own CV file is not kept: load your resume again")
        return kept[0]

    async def send(self, items: list[Stored], ask: Ask, log: Log, done: Callable[[], None]) -> None:
        """Send each application once. A form's questions are put to the person after the others are sent, so a form that
        waits does not hold them; a form that is answered is tried again."""
        pending = items
        for _ in range(MAX_ROUNDS):
            asking: list[tuple[Stored, list[str]]] = []
            for item in pending:
                outcome = await self.apply(item, log)
                done()
                if outcome.status in ASKED and outcome.questions:
                    asking.append((item, outcome.questions))
            pending = []
            for item, asked in asking:
                if await self._answer(item, asked, ask):
                    pending.append(item)
            if not pending:
                return

    async def _answer(self, item: Stored, asked: list[str], ask: Ask) -> bool:
        """Puts each question to the person; True when one was answered, so the form is tried again."""
        answered = False
        for question in asked:
            answer = await ask(f"{item.job.company} asks: {question}")
            if answer:
                answer_question(self.config, question, answer)
                answered = True
        return answered

    async def apply(self, item: Stored, log: Log) -> Outcome:
        """Fill and send one application, once. What the page said is kept with it, and applied.csv is written."""
        if item.status in SUBMITTED:
            raise ConfigError(f"{item.job.company}: already sent, and not sent twice")
        plugin = self.plugin(item)
        if plugin is None:
            raise ConfigError(f"no plugin knows this site. Apply by hand: {item.job.url}")
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        cv_file = await self._cv_for(item, cv, log)
        job = replace(item.job, apply_url=plugin.form_url(item.job.url))
        answers = self.config.answers.read_text(encoding="utf-8-sig") if self.config.answers.is_file() else ""
        log(f"filling the form on {plugin.name}")
        extras = always_plugins(self.plugins)
        guidance = "\n\n".join([plugin.instructions, *(extra.instructions for extra in extras)])
        reaches = frozenset(host for extra in extras for host in extra.reaches)
        try:
            outcome = await apply_to_job(
                self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, self.config.browser_profile, guidance, reaches, self.config.api_key_env
            )
        except Exception as error:
            # One job's failure is reported and recorded; the next job still runs.
            if refused(error):
                raise
            outcome = Outcome("failed", explain(error))
        self.store.set_status(item.job.id, outcome.status, outcome.summary)
        self.store.set_proof(item.job.id, outcome.proof)
        # The file for the person outside the app. One that another program holds open must not stop the next send.
        try:
            write_applied(self.config.workspace / "applied.csv", self.store.sent())
        except OSError as error:
            log(f"applied.csv was not written: {error}")
        return outcome


async def _read_descriptions(refs: list[str], status: Log, log: Log) -> list[str]:
    """The descriptions the database holds; the count of those read is said as each file is read, and a failure is logged."""
    loop = asyncio.get_running_loop()

    def shown(done: int) -> None:
        # The read runs in a thread: its count is handed to the loop, which alone touches the screen.
        loop.call_soon_threadsafe(status, f"reading descriptions {done}/{len(refs)}")

    def problem(text: str) -> None:
        loop.call_soon_threadsafe(log, text)

    return await asyncio.to_thread(stored_descriptions, refs, shown, problem)


def _places(config: Config) -> str:
    """The places a posting would name for this search: its cities, then its country, each one only when there is one."""
    parts = [" or ".join(config.filters.cities[:3]), (config.filters.locations or [""])[-1]]
    return ", ".join(part for part in parts if part)
