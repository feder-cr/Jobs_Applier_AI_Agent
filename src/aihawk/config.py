"""The workspace's aihawk.yaml: who is looking, for what, at which companies, through which model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE = "aihawk.yaml"
DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
DEFAULT_MODEL = "orcarouter/auto"
DEFAULT_THRESHOLD = 4.0
DEFAULT_DATABASE = "https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/releases/download/jobs-latest/manifest.json"
DEFAULT_MAX_AGE_DAYS = 60

TEMPLATE = """\
# AIHawk's settings. The API key is read from the environment, never from this file.
model: orcarouter/auto
base_url: https://api.orcarouter.ai/v1
api_key_env: ORCAROUTER_API_KEY

# Your CV as text, and your answers to what application forms ask beyond it.
# AIHawk writes nothing on a form that is not in these two files.
cv: cv.md
answers: answers.yaml

# What you want, in your own words: it weighs in the score.
goals: Describe the role, seniority, location and pay you are looking for.

# A job scoring below this (1-5) is not applied to with `aihawk apply --top`.
threshold: 4.0

# Keep a job only if its title holds one of `include` (required) and none of `exclude`, and its
# location holds one of `locations`. Words are matched as written, so list every spelling you
# want: italy, italia, milan, milano. Older postings are left out.
filters:
  include: [Replace with words of the job titles you want]
  exclude: []
  locations: []
max_age_days: 60
"""

CV_TEMPLATE = "# Your Name\n\nReplace this with your real CV as plain text or Markdown.\n"

ANSWERS_TEMPLATE = """\
# Answers to questions application forms ask. Keep them true: they are written on the forms as they are.
# `aihawk apply` lists what a form asked that is missing here. Examples:
#
# email: you@example.com
# phone: "+39 000 000 0000"
# linkedin: https://www.linkedin.com/in/you
# work_authorization: I am authorized to work in the EU and need no sponsorship.
"""


class ConfigError(Exception):
    """aihawk.yaml is missing or says something that cannot be used."""


@dataclass(frozen=True)
class Filters:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    workspace: Path
    cv: Path
    answers: Path
    goals: str = ""
    database: str = DEFAULT_DATABASE
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = "ORCAROUTER_API_KEY"
    threshold: float = DEFAULT_THRESHOLD
    filters: Filters = field(default_factory=Filters)

    @property
    def data_dir(self) -> Path:
        return self.workspace / ".aihawk"


def _words(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of words")
    return tuple(value)


def load_config(workspace: Path) -> Config:
    path = workspace / CONFIG_FILE
    if not path.is_file():
        raise ConfigError(f"no {CONFIG_FILE} in {workspace}: run `aihawk init` first")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping of settings")

    filters = raw.get("filters") or {}
    threshold = raw.get("threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 1 <= threshold <= 5:
        raise ConfigError(f"threshold must be a number from 1 to 5, not {threshold!r}")

    return Config(
        workspace=workspace,
        cv=workspace / raw.get("cv", "cv.md"),
        answers=workspace / raw.get("answers", "answers.yaml"),
        goals=str(raw.get("goals") or ""),
        database=str(raw.get("database") or DEFAULT_DATABASE),
        max_age_days=int(raw.get("max_age_days", DEFAULT_MAX_AGE_DAYS)),
        model=str(raw.get("model") or DEFAULT_MODEL),
        base_url=str(raw.get("base_url") or DEFAULT_BASE_URL),
        api_key_env=str(raw.get("api_key_env") or "ORCAROUTER_API_KEY"),
        threshold=float(threshold),
        filters=Filters(
            _words(filters.get("include"), "filters.include"),
            _words(filters.get("exclude"), "filters.exclude"),
            _words(filters.get("locations"), "filters.locations"),
        ),
    )


def write_templates(workspace: Path) -> list[Path]:
    """Write the files of a new workspace; a file that exists is kept. Returns the ones written."""
    written = []
    for name, text in ((CONFIG_FILE, TEMPLATE), ("cv.md", CV_TEMPLATE), ("answers.yaml", ANSWERS_TEMPLATE)):
        path = workspace / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written
