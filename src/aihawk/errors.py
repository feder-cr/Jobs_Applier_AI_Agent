"""What went wrong that nobody foresaw, kept for the person to send to the makers: the screen says only that something did,
and the details, with the time and the traceback, are in `errors.log` in the workspace's data folder."""

from __future__ import annotations

import re
import traceback
from datetime import UTC, datetime
from pathlib import Path

from aihawk.llm import explain, known

LOG = "errors.log"
# The file is kept under this many bytes by dropping the oldest entries: it is something a person attaches to a message.
LIMIT = 200_000
# Anything that looks like a key is masked: the log is sent to other people.
KEY = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")

# Where the log is written, and the keys that are not shaped like one; set once by the session of this workspace.
folder: Path | None = None
secrets: list[str] = []


def use(data_dir: Path, *keys: str) -> None:
    """The folder the log is written to, and the keys to mask in it besides the ones that look like one."""
    global folder
    folder = data_dir
    secrets[:] = [key for key in keys if key]


def mask(text: str) -> str:
    for secret in secrets:
        text = text.replace(secret, "sk-…")
    return KEY.sub("sk-…", text)


def record(error: BaseException) -> None:
    """One entry at the end of the log: the time, and the error with its traceback. Never raises: a log that cannot be written
    must not be the next thing to go wrong."""
    if folder is None:
        return
    entry = f"== {datetime.now(UTC).isoformat(timespec='seconds')}\n{mask(''.join(traceback.format_exception(error)))}\n"
    path = folder / LOG
    try:
        folder.mkdir(parents=True, exist_ok=True)
        text = (path.read_text(encoding="utf-8") if path.is_file() else "") + entry
        while len(text.encode("utf-8")) > LIMIT:
            # The oldest entry goes; an entry alone that is too large keeps its end, where the error is.
            following = text.find("\n== ", 1)
            text = text[following + 1 :] if following != -1 else text[-LIMIT // 2 :]
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def explained(error: Exception) -> str:
    """What a failed call says in plain words. An error that is not one of the known kinds is written to the log, and the line
    says where."""
    text = explain(error)
    if known(error):
        return text
    record(error)
    return f"{text} Details are in {LOG}."
