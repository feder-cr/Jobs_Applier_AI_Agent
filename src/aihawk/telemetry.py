"""What AIHawk tells its makers about how it is used, for all its users together: to PostHog, anonymously.

Each event carries an id made at random the first time AIHawk runs on a computer, so a person's events count once, and
nothing about the person: no name, email, CV, answer, job or link. What goes:

- `app_started`: AIHawk's version and the operating system.
- `first_run`: each step before the first search (the key, the resume, the mail, the jobs' download), and the seconds
  since AIHawk opened; `app_closed`: the last step reached, whether a search ran, how long AIHawk was open.
- `search`: how many jobs the search found, of how many in the database.
- `jobs_scored`: how many postings Jev read in a round, how many it answered for, how long the round took.
- `application`: how it ended (submitted, unconfirmed, refused, blocked, failed), why in one word, the kind of site
  (the plugin's name, or the form's host), the models, the gateway, the steps, the seconds, and the tokens each model took
  in and gave out. No price and no currency: the owner turns tokens into money himself, as of 2026-11-12.

The owner's decision of 2026-11-11: it cannot be turned off, and the README says so. A dry run is not counted. The project
key is PostHog's write-only key, made to be public: it can send events and read nothing.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from aihawk import __version__
from aihawk.apply import DRY_RUN_ENV

PROJECT_KEY = "phc_pfK4un6Vf6wppby8tpzexsWcVmwXBUHFZpbUSciZqkag"
CAPTURE = "https://us.i.posthog.com/i/v0/e/"
INSTALL_ID = "install_id"


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


def track(data_dir: Path, event: str, **properties: Any) -> None:
    """One event, sent in the background: AIHawk never waits for it. Not a daemon thread, so quitting right after an
    application still sends it, within the request's timeout. A dry run sends nothing."""
    if os.environ.get(DRY_RUN_ENV):
        return
    body = {
        "api_key": PROJECT_KEY,
        "event": event,
        "distinct_id": install_id(data_dir),
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
