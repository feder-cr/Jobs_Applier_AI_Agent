"""aihawk init | scan | score | list | apply."""

from __future__ import annotations

import asyncio
import io
import re
import sys
from dataclasses import replace
from pathlib import Path

import click

from aihawk import __version__
from aihawk.apply import Outcome, apply_to_job
from aihawk.config import Config, ConfigError, load_config, write_templates
from aihawk.database import DatabaseError, search, with_description
from aihawk.documents import score_job, tailor_cv, write_cv_pdf
from aihawk.llm import make_client
from aihawk.plugins import Plugin, PluginError, load_plugins, plugin_for
from aihawk.store import SUBMITTED, Store, Stored


def _store(config: Config) -> Store:
    return Store(config.data_dir / "jobs.db")


def _read(path: Path, what: str) -> str:
    if not path.is_file():
        raise ConfigError(f"no {what} at {path}")
    return path.read_text(encoding="utf-8")


@click.group()
@click.version_option(__version__)
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), default=".", help="Folder of aihawk.yaml.")
@click.pass_context
def main(context: click.Context, workspace: Path) -> None:
    """Find the jobs that fit your CV and apply to them, on a model through OrcaRouter and an undetected browser."""
    context.obj = workspace.resolve()


@main.command()
@click.pass_obj
def init(workspace: Path) -> None:
    """Write aihawk.yaml, cv.md and answers.yaml; a file that exists is kept."""
    workspace.mkdir(parents=True, exist_ok=True)
    written = write_templates(workspace)
    for path in written:
        click.echo(f"wrote {path}")
    click.echo("Fill them in, set ORCAROUTER_API_KEY, then: aihawk scan && aihawk score && aihawk apply --top 5")


@main.command()
@click.pass_obj
def scan(workspace: Path) -> None:
    """Search the daily job database for your titles and locations. Uses no model and scrapes nothing."""
    config = load_config(workspace)
    store = _store(config)
    try:
        manifest, jobs = search(config.database, config.filters, config.max_age_days)
        new = sum(store.add(job) for job in jobs)
    finally:
        store.close()
    click.echo(f"{len(jobs)} postings kept of {manifest['rows_out']} (database of {manifest['built_at']}), {new} new")


async def _score(config: Config, store: Store, limit: int | None) -> int:
    client = make_client(config)
    cv = _read(config.cv, "CV")
    done = 0
    for item in store.unscored()[:limit]:
        # The page is read here for the jobs whose source has no description; a page that cannot be read is judged on its title.
        job = await asyncio.to_thread(with_description, item.job)
        report = await score_job(client, config.model, job, cv, config.goals)
        store.set_score(item.job.id, report["score"], report)
        click.echo(f"{report['score']:.1f}  {item.job.company}: {item.job.title}  [{report['legitimacy']}]")
        done += 1
    return done


@main.command()
@click.option("--limit", type=click.IntRange(min=1), help="Score at most this many, oldest first.")
@click.pass_obj
def score(workspace: Path, limit: int | None) -> None:
    """Judge the new jobs against your CV and goals, with the model."""
    config = load_config(workspace)
    store = _store(config)
    try:
        click.echo(f"{asyncio.run(_score(config, store, limit))} scored")
    finally:
        store.close()


@main.command(name="list")
@click.option("--status", help="Only jobs in this status.")
@click.pass_obj
def list_jobs(workspace: Path, status: str | None) -> None:
    """Show the jobs, the best first."""
    store = _store(load_config(workspace))
    try:
        for item in store.all():
            if status and item.status != status:
                continue
            shown = f"{item.score:.1f}" if item.score is not None else "  - "
            click.echo(f"{shown}  {item.status:<11} {item.job.id}  {item.job.company}: {item.job.title} ({item.job.location})")
    finally:
        store.close()


async def _confirm(summary: str) -> bool:
    click.echo(f"\nReady to send. Check the form in the browser window.\n{summary}\n")
    return await asyncio.to_thread(click.confirm, "Send this application?", default=False)


def _report(item: Stored, outcome: Outcome) -> None:
    click.echo(f"{outcome.status}: {item.job.company}: {item.job.title}\n{outcome.summary}")
    if outcome.questions:
        click.echo("Add these to your answers file and run apply again:")
        for question in outcome.questions:
            click.echo(f"  - {question}")


async def _apply(config: Config, store: Store, items: list[Stored], auto_submit: bool, plugins: tuple[Plugin, ...]) -> None:
    client = make_client(config)
    cv = _read(config.cv, "CV")
    answers = _read(config.answers, "answers file")
    for item in items:
        click.echo(f"\n{item.job.company}: {item.job.title}\n{item.job.apply_url}")
        cv_file = Path(item.cv_path) if item.cv_path and Path(item.cv_path).is_file() else None
        if cv_file is None:
            tailored = await tailor_cv(client, config.model, item.job, cv)
            name = re.sub(r"[^A-Za-z0-9]+", "-", item.job.id).strip("-")
            cv_file = config.data_dir / "cvs" / f"{name}.pdf"
            cv_file.parent.mkdir(parents=True, exist_ok=True)
            cv_file.with_suffix(".md").write_text(tailored, encoding="utf-8")
            write_cv_pdf(tailored, cv_file)
            store.set_cv(item.job.id, cv_file)
            click.echo(f"CV for this job: {cv_file}")
        try:
            plugin = plugin_for(item.job.url, plugins)
            job = replace(item.job, apply_url=plugin.form_url(item.job.url))
            outcome = await apply_to_job(client, config.model, job, cv, answers, cv_file, config.data_dir, None if auto_submit else _confirm, plugin.instructions)
        except Exception as error:  # noqa: BLE001  One job's failure is reported and recorded; the next job still runs.
            outcome = Outcome("failed", str(error) or type(error).__name__)
        store.set_status(item.job.id, outcome.status, outcome.summary)
        _report(item, outcome)


@main.command()
@click.argument("job_id", required=False)
@click.option("--top", type=click.IntRange(min=1), help="Apply to the best N jobs at or above the threshold.")
@click.option("--auto-submit", is_flag=True, help="Send without asking. Risks account bans and wrong applications in your name.")
@click.pass_obj
def apply(workspace: Path, job_id: str | None, top: int | None, auto_submit: bool) -> None:
    """Fill the application forms in a visible browser and send them after your yes."""
    if bool(job_id) == bool(top):
        raise click.UsageError("name one JOB_ID or use --top N")
    config = load_config(workspace)
    plugins = load_plugins(workspace)
    store = _store(config)
    try:
        if job_id:
            item = store.get(job_id)
            if item is None:
                raise click.ClickException(f"no job {job_id}: see `aihawk list`")
            if plugin_for(item.job.url, plugins) is None:
                raise click.ClickException(f"No plugin knows this site yet. Apply by hand: {item.job.url}")
            if item.status in SUBMITTED:
                raise click.ClickException(f"{job_id} was already sent ({item.status}): it is not sent twice")
            items = [item]
        else:
            # A site no plugin knows stays listed, and is applied to by hand.
            items = [item for item in store.to_apply(config.threshold, len(store.all())) if plugin_for(item.job.url, plugins)][: top or 0]
        if not items:
            click.echo("nothing to apply to")
            return
        asyncio.run(_apply(config, store, items, auto_submit, plugins))
    finally:
        store.close()


def run() -> None:
    # A Windows console's code page cannot print every company's name.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except (ConfigError, DatabaseError, PluginError) as error:
        raise SystemExit(f"aihawk: {error}") from error
