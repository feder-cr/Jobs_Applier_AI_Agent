"""aihawk [your resume]: one command, the rest happens on the screen it opens."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import click

from aihawk import __version__
from aihawk.config import ConfigError, default_home
from aihawk.database import DatabaseError
from aihawk.plugins import PluginError


@click.command()
@click.version_option(__version__)
@click.argument("resume", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(resume: Path | None) -> None:
    """Give it your resume, get the jobs that fit, select them and apply."""
    # Imported here: the interface takes a moment to load, and --help should not wait for it.
    from aihawk.tui import AIHawk

    AIHawk(default_home().resolve(), resume.resolve() if resume else None).run()


def run() -> None:
    # A Windows console's code page cannot print every company's name.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except (ConfigError, DatabaseError, PluginError) as error:
        raise SystemExit(f"aihawk: {error}") from error
