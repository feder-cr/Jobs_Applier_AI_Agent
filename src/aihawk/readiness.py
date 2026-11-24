"""Whether this computer can run AIHawk's browser, and if not, the one thing to do about each cause.

One place answers it, for the start (before the person spends an evening choosing jobs), for the moment before a batch is sent,
and for each application. The causes are named: the name goes to telemetry as the reason an application was not sent and as
the step at the start, and never a path or a sentence. What the person is told is in plain words, without the names of the
parts: the instruction is the thing to do.

The rule: a precondition, found before the browser is tried, is only what certainly prevents the launch: no browser for this
computer (a Mac, or Linux that is not x86-64), no DISPLAY, libgtk-3 missing, the profile held by a live process. Everything else
(the other system packages, the system's security settings) is known only from a failed launch, through the cause the browser's
server names (`apply.server_cause`), because a check that guesses would stop computers that send today: Ubuntu 24.04 restricts
user namespaces by default and the engine works there, and the Firefox it downloads may not need every package APT_LINE lists.
"""

from __future__ import annotations

import ctypes.util
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

PLATFORM = sys.platform
MACHINE = platform.machine()

NO_BROWSER_HERE = "no_browser_here"
NO_DISPLAY = "no_display"
MISSING_LIBRARIES = "missing_libraries"
SANDBOX_BLOCKED = "sandbox_blocked"
PROFILE_LOCKED = "profile_locked"

# The packages to install when the browser's server says some are missing: the first line is for Ubuntu 24.04, whose libraries have
# a t64 in their names, and the second for Debian and the Ubuntus before it. Both checked with apt on 2026-11-12.
APT_LINE = (
    "sudo apt-get install -y libgtk-3-0t64 libasound2t64 libx11-xcb1 libdbus-glib-1-2"
    " || sudo apt-get install -y libgtk-3-0 libasound2 libx11-xcb1 libdbus-glib-1-2"
)
# The one library that certainly prevents the launch when it is missing (the old check tested it too): the window is drawn with it.
CERTAIN_LIBRARY = "gtk-3"

# What to tell the person for each cause that does not depend on the computer, and the short phrase for "not sent, <phrase>".
INSTRUCTIONS = {
    NO_DISPLAY: "AIHawk needs a desktop to open its browser. Open it from a terminal on the computer's own desktop, not over a remote connection.",
    MISSING_LIBRARIES: f"AIHawk's browser needs a few system packages. Run this once in a terminal, then try again: {APT_LINE}",
    SANDBOX_BLOCKED: "Your computer's security settings stopped AIHawk's browser from opening.",
    PROFILE_LOCKED: "Close the other AIHawk window or browser that is still open, then try again.",
}
WHY_NOT_SENT = {
    NO_BROWSER_HERE: "the browser has no version for this computer",
    NO_DISPLAY: "the browser needs a desktop",
    MISSING_LIBRARIES: "the browser needs a few system packages",
    SANDBOX_BLOCKED: "your computer blocks the browser from opening",
    PROFILE_LOCKED: "another window still has the browser open",
}
CAUSES = tuple(WHY_NOT_SENT)


@dataclass(frozen=True)
class Blocker:
    """One thing that stops the browser: its name, and what to tell the person to do about it."""

    name: str
    words: str


def no_version_words() -> str:
    """What to tell a person whose computer the browser has no version for; empty when it has one. The engine is built for
    Windows and for Linux on x86-64: on a Mac, or a Linux of another kind, AIHawk finds and scores the jobs and cannot send."""
    if PLATFORM == "darwin":
        return "On a Mac AIHawk finds and scores jobs, but cannot send applications yet: its browser has no Mac version."
    if PLATFORM.startswith("linux") and MACHINE.lower() not in ("x86_64", "amd64"):
        return "On this computer AIHawk finds and scores jobs, but cannot send applications yet: its browser has no version for it."
    return ""


def profile_is_held(profile: Path) -> bool:
    """Whether another Firefox has the profile open right now, read the way the browser's own server reads it, so that the two
    never disagree. If the server's package cannot be read, it is not held as far as this knows: the server says it when it opens."""
    try:
        from invisible_playwright_mcp.mcp.work import profile_holder

        return profile_holder(profile) is not None
    except Exception:  # noqa: BLE001  A check that cannot run never stops anyone.
        return False


def browser_blockers(profile: Path | None = None) -> list[Blocker]:
    """Every reason the browser cannot open here, each with the one thing to do; empty when it can. Windows has none. A computer
    the browser has no version for has that one alone. On Linux it opens a window, drawn through X (the engine asks for it, so
    a desktop that only speaks Wayland, with no DISPLAY, cannot show it), and needs libgtk-3 and the profile free when `profile` is given: a leftover Firefox holding it was how one computer failed six
    times in a row."""
    if words := no_version_words():
        return [Blocker(NO_BROWSER_HERE, words)]
    if not PLATFORM.startswith("linux"):
        return []
    found = []
    if not os.environ.get("DISPLAY"):
        found.append(NO_DISPLAY)
    if ctypes.util.find_library(CERTAIN_LIBRARY) is None:
        found.append(MISSING_LIBRARIES)
    if profile is not None and profile_is_held(profile):
        found.append(PROFILE_LOCKED)
    return [Blocker(name, INSTRUCTIONS[name]) for name in found]


def cause_named(name: object) -> str:
    """`name` when it is one of the causes above, else empty: the only thing read from outside, never a sentence."""
    return name if isinstance(name, str) and name in CAUSES else ""
