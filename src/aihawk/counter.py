"""The count of the applications AIHawk sends, for all its users together.

Every application that ends makes one download of a small file from the `usage-counter` release of AIHawk's repository,
the file named after how it ended: submitted, unconfirmed, refused, blocked or failed. GitHub counts each file's
downloads, and those counts are how many applications AIHawk has sent, and how they went. Nothing about the person or
the job is sent: the download is the same for everyone. The way invisible_playwright counts its browser launches.

The owner's decision of 2026-11-09: the count cannot be turned off, and the README says so. A dry run is not counted.
"""

from __future__ import annotations

import os
import threading
import urllib.request

from aihawk.apply import DRY_RUN_ENV

COUNTER = "https://github.com/feder-cr/Jobs_Applier_AI_Agent/releases/download/usage-counter/{outcome}.txt"
OUTCOMES = ("submitted", "unconfirmed", "refused", "blocked", "failed")


def count(outcome: str) -> None:
    """One download of the outcome's file, in the background: the next application does not wait for it. Not a daemon
    thread, so quitting right after a send still counts it, within the download's timeout."""
    if outcome not in OUTCOMES or os.environ.get(DRY_RUN_ENV):
        return
    threading.Thread(target=fetch, args=(COUNTER.format(outcome=outcome),), name="usage-counter").start()


def fetch(url: str) -> None:
    """The download, read and dropped. A count that cannot be made is lost, and nothing else happens."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AIHawk"}), timeout=15) as response:
            response.read()
    except Exception:  # noqa: BLE001, S110  The count must never stop or slow an application.
        pass
