"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI

from aihawk.apply import MAILBOXES, Outcome, apply_to_job, signed_in, site_of
from aihawk.apply import sign_in as sign_in_browser
from aihawk.config import KEY_ENV, KEY_URL, Config, ConfigError, load_config
from aihawk.database import built_at, search, stored_descriptions, with_description
from aihawk.documents import score_job
from aihawk.judge import evidence, fuse, questions
from aihawk.llm import SPEND, explain, make_client, money, refused
from aihawk.plugins import Plugin, always_plugins, load_plugins, plugin_for
from aihawk.profile import ensure_account_password, import_answers_file, install_resume, save_setting
from aihawk.store import SENT_WORDS, SUBMITTED, Store, Stored, write_applied

# The funnel: the next WIDE postings by title are weighed by the decision model, and the best JUDGED of them are scored by the judge model.
WIDE = 100
JUDGED = 25
SCORING_AT_ONCE = 5

Log = Callable[[str], None]


class Session:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.config = load_config(workspace)
        self.plugins: tuple[Plugin, ...] = load_plugins(workspace)
        self.store = Store(self.config.store_file)
        import_answers_file(self.config, self.store)
        ensure_account_password(self.store)
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
            return f"OrcaRouter refused the key in the {KEY_ENV} variable. Set it to a key from {KEY_URL}, then start AIHawk again."
        self.config.key_file.unlink(missing_ok=True)
        return f"OrcaRouter refused the key, so AIHawk forgot it. Get one at {KEY_URL} and type it in again."

    def needs_resume(self) -> bool:
        return not self.config.cv.is_file() or not self.config.filters.include

    async def load_resume(self, path: Path) -> None:
        await install_resume(self.client, self.config, path)
        self.config = load_config(self.workspace)

    def change_setting(self, name: str, value: str) -> None:
        save_setting(self.config, name, value)
        self.config = load_config(self.workspace)

    def mail(self) -> tuple[str, str]:
        """The address the person applies with, and its mailbox: gmail, outlook, later (left for later) or empty (never asked)."""
        answers = self.store.answers()
        return answers.get("email", ""), answers.get("mail_provider", "")

    def set_mail(self, email: str, provider: str) -> None:
        if email:
            self.store.set_answer("email", email)
        self.store.set_answer("mail_provider", provider)

    async def sign_in_mail(self, provider: str, wait: Callable[[], Awaitable[None]]) -> bool:
        """The mailbox open in the browser for the person to sign in, until `wait` returns; whether the inbox was reached."""
        url, _ = MAILBOXES[provider]
        where = await sign_in_browser(self.config.browser_profile, self.config.data_dir / "browser", wait, url)
        return signed_in(provider, where)

    async def priced(self) -> None:
        """The gateway's price list, read once, so each call's cost can be counted."""
        await SPEND.load_prices(self.client)

    async def settled(self) -> float:
        """The money spent so far, with every call the gateway has a record for at what it charged."""
        await SPEND.settle(self.client)
        return SPEND.total

    async def find(self, log: Log) -> int:
        """Bring the postings for this search from the job database into the store; how many are new."""
        loop = asyncio.get_running_loop()
        index = self.config.data_dir / "index"
        # search runs in a thread, and the screen may be touched only from the loop: its lines are handed over to it.
        manifest, jobs = await asyncio.to_thread(
            search, self.config.database, self.config.filters, self.config.max_age_days, index, lambda line: loop.call_soon_threadsafe(log, line)
        )
        new = sum(self.store.add(job) for job in jobs)
        log(f"{len(jobs)} postings fit your search, of {manifest['rows_out']:,} in the job database built {built_at(manifest):%Y-%m-%d %H:%M} UTC; {new} new")
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
        # A posting the judge did not read is not passed over for good: it stays new, so a later search can judge it.
        judged_out = [job.id for job, value in zip(jobs, values, strict=True) if value is not None and job.id not in best]
        self.store.set_statuses(judged_out, "passed_over")
        return len(best)

    async def score(self, items: list[Stored], on_scored: Callable[[], None], log: Log) -> None:
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        limit = asyncio.Semaphore(SCORING_AT_ONCE)

        async def one(item: Stored) -> None:
            async with limit:
                try:
                    job = item.job
                    if not job.description.strip():
                        # Many sources publish no text, and a score from the title alone says little: the posting's own page often has it.
                        job = await asyncio.to_thread(with_description, job)
                        self.store.set_description(job.id, job.description)
                    report = await score_job(self.client, self.config.judge_model, job, cv, self.config.goals)
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

    def _own_cv(self) -> Path:
        """The person's CV as they gave it, which goes with every form."""
        kept = sorted((self.config.data_dir / "cvs").glob("original-cv.*"))
        if not kept:
            raise ConfigError("your CV file is not kept: load your resume again")
        return kept[0]

    async def send(self, items: list[Stored], log: Log, done: Callable[[], None]) -> list[tuple[Stored, Outcome]]:
        """Send each application once; each with what came of it."""
        results = []
        for item in items:
            before = SPEND.total
            outcome = await self.apply(item, log)
            results.append((item, outcome))
            done()
            log(f"{item.job.company}: {SENT_WORDS.get(outcome.status, outcome.status)}. {outcome.proof.evidence} ({money(SPEND.total - before)})".replace("  (", " ("))
        return results

    async def apply(self, item: Stored, log: Log) -> Outcome:
        """Fill and send one application, once. What the page said is kept with it, and applied.csv is written."""
        if item.status in SUBMITTED:
            raise ConfigError(f"{item.job.company}: already sent, and not sent twice")
        # A plugin helps where there is one. Every posting is tried.
        plugin = plugin_for(item.job.url, self.plugins)
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        job = replace(item.job, apply_url=plugin.form_url(item.job.url) if plugin else item.job.url)
        answers = self.store.answers_text()
        host = site_of(job.apply_url)
        account = self.store.account(host)
        extras = always_plugins(self.plugins)
        # The site's own plugin first, then the ones that hold on every site, under a line that says which is which.
        everywhere = "\n\n".join(extra.instructions for extra in extras)
        guidance = "\n\n".join(
            [*([plugin.instructions] if plugin else []), *([f"On every site, not only this one:\n\n{everywhere}"] if everywhere else [])]
        )
        await self.priced()
        before = SPEND.total
        try:
            cv_file = self._own_cv()
            log(f"{item.job.company}: filling the form")
            outcome = await apply_to_job(
                self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, self.config.browser_profile, guidance, account)
        except Exception as error:
            # One job's failure is reported and recorded; the next job still runs.
            if refused(error):
                raise
            outcome = Outcome("failed", explain(error))
        if outcome.made_account and (email := self.store.answers().get("email")):
            # Written whatever the form did: the account exists on that site either way, and the next form there signs in.
            self.store.set_account(host, email)
        self.store.set_status(item.job.id, outcome.status, outcome.summary)
        self.store.set_proof(item.job.id, outcome.proof)
        await SPEND.settle(self.client)
        self.store.set_cost(item.job.id, SPEND.total - before)
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
