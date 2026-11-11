"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI

from aihawk.apply import MAILBOXES, Outcome, apply_to_job, signed_in, site_of
from aihawk.apply import sign_in as sign_in_browser
from aihawk.config import KEY_ENV, KEY_URL, Config, ConfigError, load_config
from aihawk.database import load_manifest, search
from aihawk.library import Library
from aihawk.llm import SPEND, Mark, explain, make_client, money, refused
from aihawk.plugins import Plugin, always_plugins, load_plugins, plugin_for
from aihawk.profile import ensure_account_password, import_answers_file, install_resume, save_setting
from aihawk.scoring import fits, question, score
from aihawk.store import SENT_WORDS, SUBMITTED, Store, Stored, write_applied
from aihawk.telemetry import Telemetry

# Jev reads the next WIDE postings the search ranked best and scores each of them: about what one of its requests holds,
# 215 to 230 postings with their descriptions on 2026-11-08. What a round waits on is not Jev, three seconds for 512, but
# their descriptions, each in its own block of a file on Hugging Face: 17 seconds for 100, 20 to 25 for 200, 40 to 90 for
# 512. A round of 200 shows the first page in half a minute, and the next rounds are read while the person reads.
WIDE = 200

Log = Callable[[str], None]


class Session:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.config = load_config(workspace)
        self.plugins: tuple[Plugin, ...] = load_plugins(workspace)
        self.store = Store(self.config.store_file)
        self.telemetry = Telemetry(self.config.data_dir)
        import_answers_file(self.config, self.store)
        ensure_account_password(self.store)
        self._client: AsyncOpenAI | None = None
        # The job database on this computer, which a search reads and the chunks still coming add to.
        self.library = Library(self.config.data_dir / "library")
        self.library.days = self.config.max_age_days
        # How many jobs the whole database holds, once its manifest has been read.
        self.database_jobs = 0

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
        await install_resume(self.client, self.config, path, self.store)
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

    async def sync(self, progress: Callable[[int, int], None], arrived: Callable[[], None]) -> None:
        """The job database on this computer brought to the newest build: what is new since the last time, then the chunks
        still missing, one by one. `arrived` is told after each that changes what a search finds, `progress` how many chunks
        are here of how many. Both are told on the loop, which alone touches the screen."""
        loop = asyncio.get_running_loop()
        # The days of this search, which may have changed in the settings since the last time.
        self.library.days = self.config.max_age_days
        manifest = await asyncio.to_thread(load_manifest, self.config.database)
        self.database_jobs = int(manifest.get("rows_out") or 0)
        # The index and the description files read in place, before 2026-11-09: nothing reads them any more.
        await asyncio.to_thread(shutil.rmtree, self.config.data_dir / "index", True)
        await asyncio.to_thread(
            self.library.sync, manifest, self.config.database,
            lambda done, size: loop.call_soon_threadsafe(progress, done, size), lambda: loop.call_soon_threadsafe(arrived),
        )

    async def find(self) -> int:
        """Bring the postings for this search, of those on this computer, into the store; how many are new."""
        jobs = await asyncio.to_thread(search, self.library, self.config.filters, self.config.max_age_days)
        new = self.store.add_all(jobs)
        self.telemetry.search(found=len(jobs), new=new)
        return new

    async def read(self, log: Log) -> int:
        """Jev reads the next WIDE postings the search ranked best and scores each one by how sure it is that the job is in
        line with the candidate. A posting Jev gave no answer for is set aside, not asked again and again. Returns how
        many were scored."""
        batch = self.store.with_status("new")[:WIDE]
        if not batch:
            return 0
        started = time.monotonic()
        # From this computer, by the posting's address: a job kept from an earlier build has its text there too.
        texts = await asyncio.to_thread(self.library.descriptions, [item.job.url for item in batch])
        for item, text in zip(batch, texts, strict=True):
            self.store.set_description(item.job.id, text)
        jobs = [replace(item.job, description=text) for item, text in zip(batch, texts, strict=True)]
        cv = self.config.cv.read_text(encoding="utf-8-sig")
        asked = question(self.config.goals, _places(self.config))
        reasons: list[str] = []
        try:
            values = await fits(self.client, self.config.decision_model, cv, jobs, asked, reasons.append)
        except Exception as error:
            # Only the client can fail here; a batch that fails is a failed verdict inside fits.
            if refused(error):
                raise
            values = [None] * len(jobs)
            reasons.append(explain(error))
        failed = [job.id for job, value in zip(jobs, values, strict=True) if value is None]
        if failed:
            why = "; ".join(dict.fromkeys(reasons))
            log(f"{len(failed):,} of {len(jobs):,} jobs could not be read" + (f" ({why})." if why else "."))
            self.store.set_statuses(failed, "passed_over")
        for job, value in zip(jobs, values, strict=True):
            if value is not None:
                self.store.set_score(job.id, score(value))
        self.telemetry.scored(
            postings=len(jobs), answered=len(jobs) - len(failed), seconds=round(time.monotonic() - started, 1),
            scoring_model=self.config.decision_model,
        )
        return len(jobs) - len(failed)

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
            mark = SPEND.mark()
            outcome = await self.apply(item, log)
            results.append((item, outcome))
            done()
            log(f"{item.job.company}: {SENT_WORDS.get(outcome.status, outcome.status)}. {outcome.proof.evidence} ({money(SPEND.since(mark)[0])})".replace("  (", " ("))
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
        guidance = _guidance(plugin, always_plugins(self.plugins))
        # The gateway's price list, read once, so each call's cost can be counted.
        await SPEND.load_prices(self.client)
        mark = SPEND.mark()
        started = time.monotonic()
        try:
            cv_file = self._own_cv()
            log(f"{item.job.company}: filling the form")
            outcome = await apply_to_job(
                self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, self.config.browser_profile, guidance, account)
        except Exception as error:
            # One job's failure is reported and recorded; the next job still runs.
            if refused(error):
                raise
            outcome = Outcome("failed", explain(error), reason=type(error).__name__)
        used = await self._record(item, host, outcome, mark)
        # How it went, for AIHawk's makers, with nothing about the person or the job: see telemetry.py. The tokens each
        # model took in and gave out on this application, as the gateway counted them: no price, no currency.
        self.telemetry.application(
            outcome.status, outcome.reason, outcome.steps, round(time.monotonic() - started, 1), plugin.name if plugin else None, host,
            self.config.model, self.config.decision_model, used,
        )
        # The file for the person outside the app. One that another program holds open must not stop the next send.
        try:
            write_applied(self.config.workspace / "applied.csv", self.store.sent())
        except OSError as error:
            log(f"applied.csv was not written: {error}")
        return outcome

    async def _record(self, item: Stored, host: str, outcome: Outcome, mark: Mark) -> dict[str, tuple[int, int]]:
        """What came of the application, kept with the job: its account, status, proof and cost. The tokens each model used."""
        if outcome.made_account and (email := self.store.answers().get("email")):
            # Written whatever the form did: the account exists on that site either way, and the next form there signs in.
            self.store.set_account(host, email)
        self.store.set_status(item.job.id, outcome.status, outcome.summary)
        self.store.set_proof(item.job.id, outcome.proof)
        await SPEND.settle(self.client)
        cost, used = SPEND.since(mark)
        self.store.set_cost(item.job.id, cost)
        return used


def _guidance(plugin: Plugin | None, everywhere: tuple[Plugin, ...]) -> str:
    """What the model is told about the site: its own plugin first, then the ones that hold on every site, under a line that
    says which is which."""
    held = "\n\n".join(extra.instructions for extra in everywhere)
    return "\n\n".join([*([plugin.instructions] if plugin else []), *([f"On every site, not only this one:\n\n{held}"] if held else [])])


def _places(config: Config) -> str:
    """The places a posting would name for this search: its cities, then its country, each one only when there is one."""
    parts = [" or ".join(config.filters.cities[:3]), (config.filters.locations or [""])[-1]]
    return ", ".join(part for part in parts if part)
