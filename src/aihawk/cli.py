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
@click.option("--home", type=click.Path(file_okay=False, path_type=Path), help="Folder for this search. Default: ~/.aihawk")
@click.option("--auto-submit", is_flag=True, help="Send without asking. Risks account bans and wrong applications in your name.")
def main(resume: Path | None, home: Path | None, auto_submit: bool) -> None:
    """Find the jobs that fit your resume and apply to them."""
    # Imported here: the interface takes a moment to load, and --help should not wait for it.
    from aihawk.tui import AIHawk  # noqa: PLC0415

    AIHawk((home or default_home()).resolve(), resume.resolve() if resume else None, auto_submit).run()


def run() -> None:
    # A Windows console's code page cannot print every company's name.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except (ConfigError, DatabaseError, PluginError) as error:
        raise SystemExit(f"aihawk: {error}") from error
