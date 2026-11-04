"""AIHawk's folder: the settings and everything it remembers about one person's search."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE = "aihawk.yaml"
KEY_FILE = "key"
DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
# The model invisible_dots gives a new Dot, for the long work on a form. It reads screenshots.
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
# For the plan and the other short calls of a search. Measured on OrcaRouter on five CVs: 10 s a plan, with valid graphs, where
# deepseek-v4.1-flash broke the nested schema and glm-5.3-flash took minutes.
DEFAULT_QUICK_MODEL = "google/gemini-3.5-flash"
# Judges the postings the funnel picks. Measured against the title's fit on five people's searches: see judge.py.
DEFAULT_JUDGE_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_THRESHOLD = 4.0
DEFAULT_DATABASE = "https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/releases/download/jobs-latest/manifest.json"
DEFAULT_MAX_AGE_DAYS = 60


class ConfigError(Exception):
    """aihawk.yaml or a file it needs says something that cannot be used."""


@dataclass(frozen=True)
class Form:
    """One wording a title may use for a node. The weight says how surely that exact wording means the job."""

    text: str
    weight: float = 1.0


@dataclass(frozen=True)
class Node:
    """One facet of the job, and the wordings that stand for it. The weight says how telling the facet is; 0 only conditions arcs."""

    id: str
    weight: float
    forms: tuple[Form, ...]


@dataclass(frozen=True)
class Arc:
    """When or unless a node (or a phrase) is in the title, scale the nodes named in `then`; "*" scales the whole title."""

    then: tuple[tuple[str, float], ...]
    when: str = ""
    unless: str = ""


@dataclass(frozen=True)
class Plan:
    nodes: tuple[Node, ...] = ()
    arcs: tuple[Arc, ...] = ()


@dataclass(frozen=True)
class Filters:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    plan: Plan = field(default_factory=Plan)

    def titles(self) -> str:
        """What a person reads as the job titles searched: the first wording of each of the plan's first nodes, not their ids."""
        nodes = [node for node in self.plan.nodes if node.forms][:3]
        return ", ".join(node.forms[0].text for node in nodes) or ", ".join(self.include[:3])


@dataclass(frozen=True)
class Config:
    workspace: Path
    name: str = ""
    goals: str = ""
    database: str = DEFAULT_DATABASE
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    model: str = DEFAULT_MODEL
    quick_model: str = DEFAULT_QUICK_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = "ORCAROUTER_API_KEY"
    threshold: float = DEFAULT_THRESHOLD
    filters: Filters = field(default_factory=Filters)

    @property
    def cv(self) -> Path:
        return self.workspace / "cv.md"

    @property
    def answers(self) -> Path:
        return self.workspace / "answers.yaml"

    @property
    def data_dir(self) -> Path:
        return self.workspace / "data"

    @property
    def key_file(self) -> Path:
        return self.workspace / KEY_FILE

    @property
    def browser_profile(self) -> Path:
        """The one Firefox profile for everything the browser does: the person's sign-ins and the applications."""
        return self.workspace / "browser-profile"

    def api_key(self) -> str:
        """The model's key: from the environment, or the one the person typed in."""
        from_environment = os.environ.get(self.api_key_env, "").strip()
        if from_environment:
            return from_environment
        return self.key_file.read_text(encoding="utf-8").strip() if self.key_file.is_file() else ""

    def save_key(self, key: str) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.key_file.touch(mode=0o600, exist_ok=True)
        self.key_file.write_text(key.strip(), encoding="utf-8")


def default_home() -> Path:
    return Path(os.environ.get("AIHAWK_HOME") or Path.home() / ".aihawk")


def _words(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of words")
    return tuple(value)


def _items(value: object, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a word or phrase, not {value!r}")
    return value


def _weight(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{name} must be a number, 0 or more, not {value!r}")
    return float(value)


def _node(entry: object) -> Node:
    if not isinstance(entry, dict):
        raise ConfigError(f"a node needs id, weight and forms: {entry!r}")
    forms = tuple(_form(item) for item in _items(entry.get("forms"), "a node's forms"))
    return Node(_text(entry.get("id"), "a node's id"), _weight(entry.get("weight", 1.0), "a node's weight"), forms)


def _form(entry: object) -> Form:
    if not isinstance(entry, dict):
        raise ConfigError(f"a form needs text and weight: {entry!r}")
    return Form(_text(entry.get("text"), "a form's text"), _weight(entry.get("weight", 1.0), "a form's weight"))


def _arc(entry: object) -> Arc:
    if not isinstance(entry, dict):
        raise ConfigError(f"an arc needs when or unless, and then: {entry!r}")
    when = str(entry.get("when") or "")
    unless = str(entry.get("unless") or "")
    # Exactly one condition: an arc with both, or with neither, would not say what it means.
    if bool(when) == bool(unless):
        raise ConfigError(f"an arc needs exactly one of when or unless: {entry!r}")
    then = tuple(_target(item) for item in _items(entry.get("then"), "an arc's then"))
    return Arc(then, when, unless)


def _target(entry: object) -> tuple[str, float]:
    if not isinstance(entry, dict):
        raise ConfigError(f"a target needs target and multiplier: {entry!r}")
    multiplier = entry.get("multiplier")
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise ConfigError(f"a multiplier must be a number, not {multiplier!r}")
    return _text(entry.get("target"), "a target"), float(multiplier)


def _plan(value: object) -> Plan:
    if value is None:
        return Plan()
    if not isinstance(value, dict):
        raise ConfigError("filters.plan must be a mapping with nodes and arcs")
    nodes = tuple(_node(entry) for entry in _items(value.get("nodes"), "filters.plan.nodes"))
    arcs = tuple(_arc(entry) for entry in _items(value.get("arcs"), "filters.plan.arcs"))
    ids = {node.id for node in nodes}
    for arc in arcs:
        for target, _ in arc.then:
            if target != "*" and target not in ids:
                raise ConfigError(f"an arc targets {target!r}, which is no node in filters.plan")
    return Plan(nodes, arcs)


def load_config(workspace: Path) -> Config:
    """The settings in the folder. Nothing there yet is a new search, not an error."""
    path = workspace / CONFIG_FILE
    if not path.is_file():
        return Config(workspace=workspace)
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
        name=str(raw.get("name") or ""),
        goals=str(raw.get("goals") or ""),
        database=str(raw.get("database") or DEFAULT_DATABASE),
        max_age_days=int(raw.get("max_age_days", DEFAULT_MAX_AGE_DAYS)),
        model=str(raw.get("model") or DEFAULT_MODEL),
        quick_model=str(raw.get("quick_model") or DEFAULT_QUICK_MODEL),
        judge_model=str(raw.get("judge_model") or DEFAULT_JUDGE_MODEL),
        base_url=str(raw.get("base_url") or DEFAULT_BASE_URL),
        api_key_env=str(raw.get("api_key_env") or "ORCAROUTER_API_KEY"),
        threshold=float(threshold),
        filters=Filters(
            _words(filters.get("include"), "filters.include"),
            _words(filters.get("exclude"), "filters.exclude"),
            _words(filters.get("locations"), "filters.locations"),
            _words(filters.get("cities"), "filters.cities"),
            _words(filters.get("countries"), "filters.countries"),
            _plan(filters.get("plan")),
        ),
    )
