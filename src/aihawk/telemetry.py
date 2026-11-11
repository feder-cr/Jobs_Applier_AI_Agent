"""What AIHawk tells its makers about how it is used, for all its users together: to PostHog, anonymously.

Each event carries an id made at random the first time AIHawk runs on a computer, so a person's events count once, and
nothing about the person: no name, email, CV, answer, job, company or link. What goes, and EVENTS below is the same list:

- `app_started`: AIHawk's version and the operating system.
- `start_step`: each step of a start, not only the first one (the key, the resume, the mail, the jobs' download, the
  first job on the screen), and the seconds since AIHawk opened; `app_closed`: the last step reached, whether a search ran, how long AIHawk was open.
- `search`: how many jobs the search found, and how many of them were new to this computer.
- `jobs_scored`: how many postings Jev read in a round, how many it answered for, how long the round took.
- `application`: how it ended (submitted, unconfirmed, refused, blocked, failed), why in one word, the kind of site (the
  plugin's name, or the vendor's name when the form's host is a known application site, else "other": never the host),
  the models, the gateway, the steps, the seconds, and the tokens each model took in and gave out. No price and no
  currency: the owner turns tokens into money himself.

The owner's decision of 2026-11-11: it cannot be turned off, and the README says so. A dry run is not counted. The project
key is PostHog's write-only key, made to be public: it can send events and read nothing.
"""

from __future__ import annotations

import json
import platform
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aihawk import __version__
from aihawk.config import DEFAULT_BASE_URL, dry_run

PROJECT_KEY = "phc_pfK4un6Vf6wppby8tpzexsWcVmwXBUHFZpbUSciZqkag"
CAPTURE = "https://us.i.posthog.com/i/v0/e/"
INSTALL_ID = "install_id"
GATEWAY = urlsplit(DEFAULT_BASE_URL).hostname

# Every event and the properties it carries, besides the version and the operating system that all of them carry. The
# dashboard and the counters read only these, and a test holds every call in the code to them.
EVENTS: dict[str, tuple[str, ...]] = {
    "app_started": (),
    "start_step": ("step", "seconds", "error", "kind", "chunks"),
    "app_closed": ("last_step", "searched", "seconds"),
    "search": ("found", "new"),
    "jobs_scored": ("postings", "answered", "seconds", "scoring_model"),
    "application": (
        "outcome", "reason", "site", "plugin", "forms_model", "scoring_model", "gateway", "steps", "seconds", "models",
        "input_tokens", "output_tokens", "tokens_by_model",
    ),
}  # fmt: skip

# Hosts of the sites that take an employer's application, each with its vendor: the pipeline's VENDOR_HOSTS without the job
# boards and aggregators, matched the same way (the fragment is contained in the form's host). Only these names are ever
# sent, so a company's own host never is.
KNOWN_SITES = (
    ("myworkdayjobs.com", "workday"), ("myworkdaysite.com", "workday"), ("smartrecruiters.com", "smartrecruiters"),
    ("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"), ("icims.com", "icims"),
    ("paylocity.com", "paylocity"), ("adp.com", "adp"), ("oraclecloud.com", "oracle"), ("taleo.net", "taleo"),
    ("successfactors", "successfactors"), ("sapsf.", "successfactors"), ("jobvite.com", "jobvite"), ("bamboohr.com", "bamboohr"),
    ("applytojob.com", "jazzhr"), ("breezy.hr", "breezy"), ("recruitee.com", "recruitee"), ("teamtailor.com", "teamtailor"),
    ("personio.", "personio"), ("workable.com", "workable"), ("pinpointhq.com", "pinpoint"), ("rippling.com", "rippling"),
    ("ultipro.com", "ukg"), ("ukg.net", "ukg"), ("dayforcehcm.com", "dayforce"), ("paycomonline.", "paycom"),
    ("csod.com", "cornerstone"), ("careerplug.com", "careerplug"), ("recruiterflow.com", "recruiterflow"),
    ("zohorecruit.", "zoho"), ("jobs.gem.com", "gem"), ("applicantpro.com", "applicantpro"), ("isolvedhire.com", "isolved"),
    ("eightfold.ai", "eightfold"), ("avature.net", "avature"), ("phenompeople.com", "phenom"), ("join.com", "join"),
    ("hrmos.co", "hrmos"), ("gupy.io", "gupy"), ("softgarden.", "softgarden"), ("jobscore.com", "jobscore"),
    ("freshteam.com", "freshteam"), ("manatal.com", "manatal"), ("keka.com", "keka"), ("darwinbox.", "darwinbox"),
    ("recruiterbox.com", "recruiterbox"), ("betterteam.com", "betterteam"), ("brassring.com", "brassring"),
    ("governmentjobs.com", "neogov"), ("schooljobs.com", "neogov"), ("recruitingbypaycor.com", "paycor"),
    ("applitrack.com", "applitrack"), ("hrmdirect.com", "hrmdirect"), ("cadienttalent.com", "cadient"),
    ("hireology.com", "hireology"), ("workstream.us", "workstream"), ("paradox.ai", "paradox"), ("apploi.com", "apploi"),
    ("pageuppeople.com", "pageup"), ("comeet.com", "comeet"), ("dover.com", "dover"), ("silkroad.com", "silkroad"),
    ("hirebridge.com", "hirebridge"), ("careers-page.com", "manatal"), ("jobappnetwork.com", "jobappnetwork"),
)  # fmt: skip


def vendor_of(host: str) -> str:
    """The application-site vendor a form's host names, or "other": a company's own host is not sent."""
    host = host.lower()
    return next((vendor for fragment, vendor in KNOWN_SITES if fragment in host), "other")


def install_id(data_dir: Path) -> str:
    """This computer's random id, made once and kept with the data. One that cannot be kept is made again each time."""
    path = data_dir / INSTALL_ID
    try:
        if path.is_file() and (kept := path.read_text(encoding="utf-8").strip()):
            return kept
        made = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(made, encoding="utf-8")
        return made
    except OSError:
        return str(uuid.uuid4())


class Telemetry:
    """The events of one run of AIHawk, and how far its start came: where people stop before their first search."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.opened = time.monotonic()
        self.last_step = "opened"
        self.searched = False
        self._id: str | None = None

    def track(self, event: str, **properties: Any) -> None:
        """One event, sent in the background: AIHawk never waits for it. Not a daemon thread, so quitting right after an
        application still sends it, within the request's timeout. A dry run sends nothing."""
        if dry_run():
            return
        if self._id is None:
            self._id = install_id(self.data_dir)
        body = {
            "api_key": PROJECT_KEY,
            "event": event,
            "distinct_id": self._id,
            "properties": {
                **properties,
                "version": __version__,
                "os": platform.system(),
                # No person profile: the id only counts people, and an event with no profile costs PostHog less.
                "$process_person_profile": False,
                "$lib": "aihawk",
            },
        }
        threading.Thread(target=send, args=(body,), name="telemetry").start()

    def started(self) -> None:
        self.track("app_started")

    def step(self, name: str, **properties: Any) -> None:
        """How far a start has come."""
        self.last_step = name
        self.track("start_step", step=name, seconds=self.seconds(), **properties)

    def search(self, found: int, new: int) -> None:
        self.searched = True
        self.track("search", found=found, new=new)

    def scored(self, postings: int, answered: int, seconds: float, scoring_model: str) -> None:
        self.track("jobs_scored", postings=postings, answered=answered, seconds=seconds, scoring_model=scoring_model)

    def application(
        self, outcome: str, reason: str, steps: int, seconds: float, plugin: str | None, host: str,
        forms_model: str, scoring_model: str, tokens: dict[str, tuple[int, int]],
    ) -> None:
        """How an application went. `tokens` is what each model took in and gave out on it, as the gateway counted them. The
        site is the plugin's name, or the vendor of the form's host: the host itself stays here."""
        self.track(
            "application", outcome=outcome, reason=reason, site=plugin or vendor_of(host), plugin=plugin is not None,
            forms_model=forms_model, scoring_model=scoring_model, gateway=GATEWAY, steps=steps, seconds=seconds,
            models=sorted(tokens), input_tokens=sum(taken for taken, _ in tokens.values()),
            output_tokens=sum(given for _, given in tokens.values()),
            tokens_by_model={model: {"input": taken, "output": given} for model, (taken, given) in tokens.items()},
        )

    def closed(self) -> None:
        self.track("app_closed", last_step=self.last_step, searched=self.searched, seconds=self.seconds())

    def seconds(self) -> float:
        return round(time.monotonic() - self.opened, 1)


def send(body: dict[str, Any]) -> None:
    """The request, its answer read and dropped. An event that cannot be sent is lost, and nothing else happens."""
    request = urllib.request.Request(
        CAPTURE, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "AIHawk"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except Exception:  # noqa: BLE001, S110  Telemetry must never stop or slow AIHawk.
        pass
