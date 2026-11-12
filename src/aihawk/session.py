"""One person's search, with no screen in it: find, rank, apply. The terminal interface drives this; tests drive it too."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from openai import AsyncOpenAI

from aihawk import errors
from aihawk.apply import MAILBOXES, NOTE, Outcome, apply_to_job, signed_in, site_of
from aihawk.apply import sign_in as sign_in_browser
from aihawk.config import DEFAULT_BASE_URL, KEY_ENV, Config, ConfigError, load_config
from aihawk.database import load_manifest, search
from aihawk.library import Library
from aihawk.llm import ACCOUNT_CAUSES, SPEND, Mark, cause, make_client, no_credit, not_answering, refused
from aihawk.plugins import Plugin, always_plugins, load_plugins, plugin_for
from aihawk.profile import CV_NAME_FILE, ensure_account_password, import_answers_file, install_resume, save_setting
from aihawk.scoring import NOT_JUDGED, NotAnswering, fits, question, score
from aihawk.store import SUBMITTED, Store, Stored, write_applied
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
        errors.use(self.config.data_dir, self.config.api_key() or "")
        # How many jobs the whole database holds, once its manifest has been read.
        self.database_jobs = 0
        # The browser's engine coming down in the background, while the person looks at the jobs.
        self.engine: subprocess.Popen[bytes] | None = None

    def close(self) -> None:
        # The downloads are AIHawk's: they do not go on when AIHawk is closed.
        self.library.stopping.set()
        if self.engine is not None and self.engine.poll() is None:
            self.engine.terminate()
        self.store.close()

    @staticmethod
    def engine_here() -> bool:
        """Whether the browser's engine is on this computer. True when that cannot be told: nothing is then brought."""
        try:
            from invisible_core.download import engine_status

            return bool(engine_status()[0])
        except Exception:  # noqa: BLE001  The engine's own package says; if it cannot, the first application finds out.
            return True

    def bring_engine(self) -> None:
        """Starts bringing the browser's engine here, about 270 MB, silently and once, so that the first application does not wait
        for it. It is the engine's own `fetch`, in a process of its own that ends with AIHawk; a failure is no one's concern:
        the application that needs the engine asks for it again."""
        if self.engine is not None or self.engine_here():
            return
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.engine = subprocess.Popen(
                [sys.executable, "-m", "invisible_playwright", "fetch"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
            )
        except OSError:
            self.engine = None

    async def engine_coming(self, note: Callable[[str], None] | None) -> None:
        """Waits for the engine that is being brought here, saying so: an application that opened the browser now would start a
        second download of the same engine."""
        if self.engine is None or self.engine.poll() is not None:
            return
        while self.engine.poll() is None:
            if note:
                note("Getting the browser, only the first time: about 270 MB…")
            await asyncio.sleep(2)
        if note:
            note("")

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = make_client(self.config)
        return self._client

    def needs_key(self) -> bool:
        return not self.config.api_key()

    def save_key(self, key: str) -> None:
        self.config.save_key(key)
        errors.use(self.config.data_dir, key)
        self._client = None

    def refuse_key(self) -> str:
        """The AI service refused the key. One typed in is forgotten, so the next question asks for another; one from the environment is named."""
        self._client = None
        if self.config.key_from_environment():
            return f"The AI service refused the key in the {KEY_ENV} variable. Set a valid one there, then start AIHawk again."
        self.config.key_file.unlink(missing_ok=True)
        return "The AI service refused the key, so AIHawk forgot it. Paste a new one."

    def key_client(self, key: str) -> AsyncOpenAI:
        """A client for a key not yet kept, to try it: no retries, and no waiting long on a service that does not answer."""
        return AsyncOpenAI(base_url=DEFAULT_BASE_URL, api_key=key, max_retries=0, timeout=15.0)

    async def key_accepted(self, key: str) -> bool:
        """Whether the service takes this key: its list of models answers a key it does not know with 401 and nothing else is
        asked. A service that cannot be reached does not say no, so the key is kept: the check never blocks."""
        try:
            await self.key_client(key).get("/models", cast_to=dict)
        except Exception as error:  # noqa: BLE001  Only a refusal is an answer; anything else leaves the key to be tried for real.
            return not refused(error)
        return True

    async def credit_left(self, key: str) -> float | None:
        """The dollars this key can still spend at OrcaRouter, as the billing page of its API says; None when that cannot be told:
        no connection, an answer of another shape, a refusal. Only a number says anything, and the check never blocks."""
        try:
            answer = await self.key_client(key).get("/dashboard/billing/subscription", cast_to=dict)
        except Exception:  # noqa: BLE001  Not knowing is not a reason to stop anyone; the first call tells.
            return None
        left = answer.get("hard_limit_usd") if isinstance(answer, dict) else None
        return float(left) if isinstance(left, int | float) and not isinstance(left, bool) else None

    async def has_no_credit(self, key: str) -> bool:
        """Whether the service says this key's account has none left."""
        left = await self.credit_left(key)
        return left is not None and left <= 0

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

    async def sync(self, progress: Callable[[int, int], None], arrived: Callable[[], None], on_bytes: Callable[[int, int], None] = lambda done, total: None) -> None:
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
            lambda done, total: loop.call_soon_threadsafe(on_bytes, done, total),
        )

    async def find(self) -> int:
        """Bring the postings for this search, of those on this computer, into the store; how many are new."""
        jobs = await asyncio.to_thread(search, self.library, self.config.filters, self.config.max_age_days)
        new = self.store.add_all(jobs)
        self.telemetry.search(found=len(jobs), new=new)
        return new

    async def read(self, log: Log) -> int:
        """Jev reads the next WIDE postings the search ranked best and scores each one by how sure it is that the job is in
        line with the candidate. A posting Jev gave no answer for is set aside, not asked again and again. A request that got
        no answer at all leaves its postings as they were and raises NotAnswering once the others are kept. Returns how many
        were scored."""
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
            # Only the client can fail here; a batch that fails is a failed verdict inside fits. A key it refuses is for the
            # screen; one it cannot reach is asked about again, not set aside.
            if not not_answering(error):
                raise
            values = [NOT_JUDGED] * len(jobs)
        unjudged = [value is NOT_JUDGED for value in values]
        failed = [job.id for job, value in zip(jobs, values, strict=True) if value is None]
        if failed:
            why = " ".join(dict.fromkeys(reasons))
            log(f"{len(failed):,} of {len(jobs):,} jobs could not be read." + (f" {why}" if why else ""))
            self.store.set_statuses(failed, "passed_over")
        for job, value in zip(jobs, values, strict=True):
            if isinstance(value, float):
                self.store.set_score(job.id, score(value))
        answered = len(jobs) - len(failed) - sum(unjudged)
        self.telemetry.scored(postings=len(jobs), answered=answered, seconds=round(time.monotonic() - started, 1), scoring_model=self.config.decision_model)
        if any(unjudged):
            raise NotAnswering
        return answered

    def resume_name(self) -> str:
        """The name of the file the person gave as their resume; empty when it was not kept."""
        name = self.config.data_dir / "cvs" / CV_NAME_FILE
        return name.read_text(encoding="utf-8").strip() if name.is_file() else ""

    def _own_cv(self) -> Path:
        """The person's CV as they gave it, which goes with every form."""
        kept = sorted((self.config.data_dir / "cvs").glob("original-cv.*"))
        if not kept:
            raise ConfigError("your CV file is not kept: load your resume again")
        return kept[0]

    async def send(
        self, items: list[Stored], log: Log, begin: Callable[[int, int, Stored], None], done: Callable[[Stored, Outcome], None],
        note: Callable[[str], None] | None = None,
    ) -> list[tuple[Stored, Outcome]]:
        """Send each application once; each with what came of it. `begin` is told which one starts, of how many, and `done`
        what came of each when it ends. `note` is told what an application waits for: a line, and an empty one when it is working again."""
        results = []
        for number, item in enumerate(items, 1):
            begin(number, len(items), item)
            outcome = await self.apply(item, log, note)
            results.append((item, outcome))
            done(item, outcome)
            if outcome.reason in ACCOUNT_CAUSES:
                # The account or the key stopped it: every application after this one would fail the same way, after the
                # browser opened for nothing.
                break
        return results

    async def apply(self, item: Stored, log: Log, note: Callable[[str], None] | None = None) -> Outcome:
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
        told = NOTE.set(note)
        try:
            await self.engine_coming(note)
            cv_file = self._own_cv()
            outcome = await apply_to_job(
                self.client, self.config.model, job, cv, answers, cv_file, self.config.data_dir, self.config.browser_profile, guidance, account)
        except Exception as error:
            # One job's failure is reported and recorded; the next job still runs.
            if refused(error):
                raise
            outcome = Outcome("failed", errors.explained(error), reason="no_credit" if no_credit(error) else cause(error) or type(error).__name__)
        finally:
            NOTE.reset(told)
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
        except OSError:
            log("applied.csv could not be saved. Close it if another program has it open.")
        return outcome

    async def _record(self, item: Stored, host: str, outcome: Outcome, mark: Mark) -> dict[str, tuple[int, int]]:
        """What came of the application, kept with the job: its account, status, proof and cost. The tokens each model used."""
        if outcome.made_account and (email := self.store.answers().get("email")):
            # Written whatever the form did: the account exists on that site either way, and the next form there signs in.
            self.store.set_account(host, email)
        self.store.set_status(item.job.id, outcome.status, outcome.summary, outcome.reason)
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
