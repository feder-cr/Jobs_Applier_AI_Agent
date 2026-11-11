"""The search over the job database on this computer (library.py): the postings that best fit, by their titles."""

from __future__ import annotations

import html
import json
import math
import re
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from aihawk.config import Arc, Filters, Form, Node, Plan
from aihawk.library import Library

# How many postings a search brings back to be scored. Past these, a title fits the search too little to be worth a look.
RECALL = 600
# What being in the person's own city is worth, against a node of the plan: about as much as a named skill.
CITY_WEIGHT = 1.5
# What a posting with no description keeps of its score: ranked on its title alone, it needs a title twice as fitting to
# come before one that has a description.
UNDESCRIBED = 0.5
# What a remote posting's location says when it does not narrow the job to a place: the database writes "Remote" for one
# flagged remote that names no place.
NO_PLACE = ("remote", "anywhere", "worldwide", "global")


class DatabaseError(Exception):
    """The database cannot be read."""


@dataclass(frozen=True)
class Job:
    id: str
    company: str
    title: str
    location: str
    url: str
    apply_url: str
    description: str
    # How well the title and the place fit the search, from 0 to 1 against the best one found.
    lexical: float = 0.0


def posting_keys(urls: list[str]) -> list[str]:
    """The key each link is known by, as the job database computes it: one definition, in key.sql, for both."""
    if not urls:
        return []
    connection = duckdb.connect()
    try:
        connection.execute(resources.files("aihawk").joinpath("key.sql").read_text(encoding="utf-8"))
        return [row[0] for row in connection.execute("SELECT posting_key(url) FROM unnest(?) AS t(url)", [urls]).fetchall()]
    finally:
        connection.close()


def unreachable() -> str:
    """What a database that cannot be downloaded says: in plain words, and what to do. The error's own text is for the makers."""
    return "Cannot reach the job list. Check your internet connection, then start AIHawk again."


def load_manifest(location: str) -> dict[str, Any]:
    try:
        if location.startswith(("http://", "https://")):
            with urllib.request.urlopen(location, timeout=60) as response:
                return json.load(response)
        return json.loads(Path(location).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        if isinstance(error, OSError) and location.startswith(("http://", "https://")):
            raise DatabaseError(unreachable()) from error
        raise DatabaseError(f"The job list at {location} cannot be read.") from error


def _word_pattern(words: tuple[str, ...]) -> str:
    """Whole words, in any case: `java` is not `javascript`, and `roma` is not `Romania`."""
    # Not \\b: it finds no end after a symbol, as in `c++`, and takes accented letters for edges.
    return "(?i)(?:^|[^\\pL\\pN])(?:" + "|".join(re.escape(word) for word in words) + ")(?:$|[^\\pL\\pN])"


def _any_word(column: str, words: tuple[str, ...]) -> tuple[str, list[str]]:
    return f"regexp_matches({column}, ?)", [_word_pattern(words)]


def _place_pattern(words: tuple[str, ...]) -> str:
    """A place starts the location, or follows a separator, a dash or a number: `London` is not in "New London" or "Greater London"."""
    before = "(?:^|[,;/|()\\-–—]\\s*|\\d\\s+)"
    return "(?i)" + before + "(?:" + "|".join(re.escape(word) for word in words) + ")(?:$|[^\\pL\\pN])"


def rarity(postings: int, holding: int) -> float:
    """How telling a word is, from 0.05 to 1: a word in one posting of 50 or fewer counts in full."""
    return min(1.0, max(0.05, math.log(1 + postings / (1 + holding)) / math.log(51)))


class _Posting(NamedTuple):
    company: str
    title: str
    location: str
    url: str
    posted: datetime | None
    described: bool


def _whole_words(words: Iterable[str]) -> re.Pattern[str]:
    """The same whole-word rule as _word_pattern, for Python's re: no \\b, so `c++` keeps its symbols."""
    alternatives = "|".join(re.escape(word) for word in words)
    return re.compile(r"(?<![^\W_])(?:" + alternatives + r")(?![^\W_])", re.IGNORECASE)


def _plan_of(filters: Filters) -> Plan:
    """The plan the search runs. Words alone plan one node per word, of weight 1."""
    if filters.plan.nodes:
        return filters.plan
    return Plan(tuple(Node(word, 1.0, (Form(word),)) for word in filters.include))


def _place_and_age(filters: Filters, max_age_days: int) -> tuple[list[str], list[Any]]:
    """The SQL conditions on where and when a posting is, which decide which postings a search is over."""
    clauses, parameters = [], []
    places = []
    names = [*filters.countries, *(place.upper() for place in filters.locations)] if filters.countries else []
    marks = ", ".join("?" for _ in names)
    if filters.locations:
        clause, words = "regexp_matches(coalesce(location, ''), ?)", [_place_pattern(filters.locations)]
        # A town's name is not enough where the country says otherwise: Richmond, Virginia is not the one near London.
        if names:
            clause = f"({clause} AND (coalesce(country, '') = '' OR upper(country) IN ({marks})))"
            words = [*words, *names]
        places.append(clause)
        parameters += words
    if names:
        # Many sources write only a code, as in "IT": as a word in a location it would also be English's "it". Others
        # write the name there, as in "SPAIN".
        places.append(f"upper(coalesce(country, '')) IN ({marks})")
        parameters += names
    if places:
        # A remote posting that names no place could be open to anyone, and its description will say.
        unnamed = ", ".join(repr(word) for word in NO_PLACE)
        places.append(f"(coalesce(country, '') = '' AND lower(trim(coalesce(location, ''))) IN ({unnamed}))")
        clauses.append("(" + " OR ".join(places) + ")")
    # A posting with no date is kept: many sources have none.
    clauses.append(f"(posted_at IS NULL OR posted_at >= now()::TIMESTAMP - INTERVAL {int(max_age_days)} DAY)")
    return clauses, parameters


def _rarities(texts: tuple[str, ...], patterns: dict[str, re.Pattern[str]], titles: list[str], postings: int) -> dict[str, float]:
    """How rare each wording is among the fetched titles: "Python" tells more than "Engineer"."""
    holding = {text: sum(1 for title in titles if patterns[text].search(title)) for text in texts}
    return {text: rarity(postings, count) for text, count in holding.items()}


def _evidence(title: str, nodes: tuple[Node, ...], patterns: dict[str, re.Pattern[str]], rarities: dict[str, float]) -> dict[str, float]:
    """Per node, the best of its wordings that the title holds: the wording's weight times its rarity. Wordings never add up."""
    return {
        node.id: max((form.weight * rarities[form.text] for form in node.forms if patterns[form.text].search(title)), default=0.0)
        for node in nodes
    }


def _holds(condition: str, title: str, evidence: dict[str, float]) -> bool:
    """A node's id asks whether the node is in the title; any other text is a phrase the title must hold."""
    if condition in evidence:
        return evidence[condition] > 0
    return _whole_words((condition,)).search(title) is not None


def _apply_arcs(arcs: tuple[Arc, ...], title: str, evidence: dict[str, float]) -> float:
    """Each arc in order, each on the evidence the arcs before it left. Returns the multiplier of the whole title."""
    document = 1.0
    for arc in arcs:
        present = _holds(arc.when, title, evidence) if arc.when else not _holds(arc.unless, title, evidence)
        if not present:
            continue
        for target, multiplier in arc.then:
            # Clamped: one arc must not erase a node or flood the title.
            factor = min(3.0, max(0.05, multiplier))
            if target == "*":
                document *= factor
            elif target in evidence:
                evidence[target] *= factor
    return document


def _score(nodes: tuple[Node, ...], evidence: dict[str, float], document: float, in_city: bool, mean: float) -> float:
    """Each node's evidence on a log, so that one heavy node cannot dominate by size. A node of weight 0 scores nothing."""
    score = document * sum(math.log(1 + node.weight / mean * evidence[node.id]) for node in nodes if node.weight > 0)
    if in_city:
        score += math.log(1 + CITY_WEIGHT / mean)
    return score


def _rank(score: float, posting: _Posting) -> tuple[float, bool, datetime]:
    """Higher is better: the score, then the newest posting. A posting with no date ranks below one with a date."""
    return score, posting.posted is not None, posting.posted or datetime.min


def search(library: Library, filters: Filters, max_age_days: int, limit: int = RECALL) -> list[Job]:
    """The postings here that best fit the search, by their titles, the best first: none while nothing has come.

    The plan's nodes score a title by their best wording that it holds, weighted by how rare the wording is among the
    postings of this place: "Python" tells more than "Engineer". Arcs then scale the title, and the person's own city adds
    to it. A posting is known by its address. Its description is read when a job is scored, not here.
    """
    plan = _plan_of(filters)
    nodes = tuple(node for node in plan.nodes if node.forms)
    if not nodes:
        raise DatabaseError("There are no job titles to search for yet. Press r and give your resume.")
    mean = sum(node.weight for node in nodes) / len(nodes)
    if mean <= 0:
        raise DatabaseError("the plan weighs nothing: give a node a weight above 0")
    texts = tuple(dict.fromkeys(form.text for node in nodes for form in node.forms))
    patterns = {text: _whole_words((text,)) for text in texts}
    cities = _whole_words(filters.cities) if filters.cities else None

    place_clauses, place_parameters = _place_and_age(filters, max_age_days)
    # A node of weight 0 is only a condition for an arc: it is no reason to bring a posting in.
    clause, words = _any_word("title", tuple(dict.fromkeys(form.text for node in nodes if node.weight > 0 for form in node.forms)))
    # A posting's address comes from a source, and it is fetched and opened: only the web's, never a file on this computer.
    clauses, parameters = [*place_clauses, clause, "regexp_matches(url, '(?i)^https?://')"], [*place_parameters, *words]
    if filters.exclude:
        clause, words = _any_word("title", filters.exclude)
        clauses.append(f"NOT {clause}")
        parameters += words

    connection = duckdb.connect()
    try:
        if not library.view(connection):
            return []
        (postings,) = connection.execute(f"SELECT count(*) FROM postings WHERE {' AND '.join(place_clauses)}", place_parameters).fetchone()
        rows = [
            _Posting(*row)
            for row in connection.execute(
                f"""SELECT coalesce(company, ''), title, coalesce(location, ''), url, posted_at, described
                    FROM postings WHERE {' AND '.join(clauses)}""",
                parameters,
            ).fetchall()
        ]
    except duckdb.Error as error:
        raise DatabaseError("The jobs saved on this computer cannot be read. Start AIHawk again.") from error
    finally:
        connection.close()

    rarities = _rarities(texts, patterns, [row.title for row in rows], postings)
    scored = []
    for row in rows:
        evidence = _evidence(row.title, nodes, patterns, rarities)
        document = _apply_arcs(plan.arcs, row.title, evidence)
        in_city = cities is not None and cities.search(row.location) is not None
        scored.append((_score(nodes, evidence, document, in_city, mean) * (1.0 if row.described else UNDESCRIBED), row))

    ranked = sorted(scored, key=lambda item: _rank(*item), reverse=True)[:limit]
    best = max((score for score, _ in ranked), default=1.0) or 1.0
    # Some sources publish their names with HTML entities still in them.
    return [
        Job(row.url, html.unescape(row.company), html.unescape(row.title), html.unescape(row.location), row.url, row.url, "", score / best)
        for score, row in ranked
    ]
