"""The job database AIHawk reads, as published in a release: read in place, so a search downloads only what it needs."""

from __future__ import annotations

import html
import json
import math
import re
import shutil
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from aihawk.config import Arc, Filters, Form, Node, Plan

# How many postings a search brings back to be judged. Past these, a title fits the search too little to be worth a look.
RECALL = 600
# What being in the person's own city is worth, against a node of the plan: about as much as a named skill.
CITY_WEIGHT = 1.5
# What a remote posting's location says when it does not narrow the job to a place.
NO_PLACE = ("", "remote", "anywhere", "worldwide", "global")


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
    # Where the database holds the description: "<file>#<row>".
    ref: str = ""
    # How well the title and the place fit the search, from 0 to 1 against the best one found.
    lexical: float = 0.0


class _PageText(HTMLParser):
    SKIPPED = frozenset({"script", "style", "noscript", "svg", "head", "nav", "footer"})
    BLOCKS = frozenset({"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "ul", "ol", "section"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def page_text(markup: str) -> str:
    parser = _PageText()
    parser.feed(html.unescape(markup))
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


def stored_descriptions(refs: list[str], done: Callable[[int], None] = lambda _: None) -> list[str]:
    """The descriptions the database holds for these postings, in order; empty where its sources published none.

    One connection, and one query per file: a file's footer is fetched once for the search, not once per posting, which
    downloads far less over HTTP.
    `done` is told how many have been read, as each file is.
    """
    texts = [""] * len(refs)
    by_file: dict[str, list[tuple[int, int]]] = {}
    count = 0
    for position, ref in enumerate(refs):
        if "#" not in ref:
            count += 1
            done(count)
            continue
        file, number = ref.rsplit("#", 1)
        by_file.setdefault(file, []).append((position, int(number)))
    if by_file:
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL httpfs; LOAD httpfs;")
            for file, places in by_file.items():
                marks = ", ".join(str(number) for _, number in places)
                try:
                    found = dict(connection.execute(
                        f"SELECT file_row_number, description FROM read_parquet(?, file_row_number = true) WHERE file_row_number IN ({marks})",
                        [file],
                    ).fetchall())
                except duckdb.Error:
                    found = {}
                for position, number in places:
                    texts[position] = found.get(number) or ""
                count += len(places)
                done(count)
        except duckdb.Error:
            pass
        finally:
            connection.close()
    return texts


def stored_description(ref: str) -> str:
    """The description the database holds for one posting; empty when its sources published none."""
    return stored_descriptions([ref])[0]


def with_description(job: Job, timeout: float = 30.0) -> Job:
    """The job with its description: the database's, or else the text of its page. One job, the one being judged."""
    if job.description.strip():
        return job
    stored = stored_description(job.ref)
    if stored.strip():
        return replace(job, description=stored)
    request = urllib.request.Request(job.url, headers={"User-Agent": "AIHawk (+https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return replace(job, description=page_text(response.read().decode("utf-8", "replace")))
    except OSError:
        return job


def load_manifest(location: str) -> dict[str, Any]:
    try:
        if location.startswith(("http://", "https://")):
            with urllib.request.urlopen(location, timeout=60) as response:
                return json.load(response)
        return json.loads(Path(location).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DatabaseError(f"the job database's manifest at {location} cannot be read: {error}") from error


def _base(manifest: dict[str, Any], location: str) -> str:
    """Where the database's files are: the release the manifest names, or beside the manifest."""
    return manifest.get("base_url") or location.rsplit("/", 1)[0].rsplit("\\", 1)[0]


def local_index(manifest: dict[str, Any], location: str, cache: Path, log: Callable[[str], None] = lambda _: None) -> Path:
    """Today's index on this computer: downloaded once per database, in one rename, and the older ones removed."""
    source = f"{_base(manifest, location)}/{manifest['index']['name']}"
    if not source.startswith(("http://", "https://")):
        return Path(source)
    cache.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", manifest["built_at"])
    path = cache / f"index-{stamp}.parquet"
    if path.is_file():
        return path
    log(f"downloading today's job index ({manifest['index']['bytes'] // 1_000_000} MB), once a day")
    partial = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(source, timeout=120) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise DatabaseError(f"the job index at {source} cannot be downloaded: {error}") from error
    partial.replace(path)
    for old in cache.glob("index-*.parquet"):
        if old != path:
            old.unlink()
    return path


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
    key: str
    company: str
    title: str
    location: str
    url: str
    part: str
    number: int
    posted: datetime | None


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
        places.append(f"(remote AND coalesce(country, '') = '' AND lower(trim(coalesce(location, ''))) IN ({unnamed}))")
        clauses.append("(" + " OR ".join(places) + ")")
    # A posting with no date is kept: many sources have none.
    clauses.append(f"(TRY_CAST(posted_at AS TIMESTAMP) IS NULL OR TRY_CAST(posted_at AS TIMESTAMP) >= now()::TIMESTAMP - INTERVAL {int(max_age_days)} DAY)")
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


def search(
    location: str, filters: Filters, max_age_days: int, cache: Path, log: Callable[[str], None] = lambda _: None, limit: int = RECALL
) -> tuple[dict[str, Any], list[Job]]:
    """The postings that best fit the search, by their titles, the best first, with the manifest they came from.

    The plan's nodes score a title by their best wording that it holds, weighted by how rare the wording is among the
    postings of this place: "Python" tells more than "Engineer". Arcs then scale the title, and the person's own city adds
    to it. The same title at the same company, posted once per place, is one job, in its best place. The description
    stays in the database until a job is judged.
    """
    plan = _plan_of(filters)
    nodes = tuple(node for node in plan.nodes if node.forms)
    if not nodes:
        raise DatabaseError("the search has no job titles: give AIHawk a resume")
    mean = sum(node.weight for node in nodes) / len(nodes)
    if mean <= 0:
        raise DatabaseError("the plan weighs nothing: give a node a weight above 0")
    manifest = load_manifest(location)
    index = str(local_index(manifest, location, cache, log))
    base = _base(manifest, location)
    texts = tuple(dict.fromkeys(form.text for node in nodes for form in node.forms))
    patterns = {text: _whole_words((text,)) for text in texts}
    cities = _whole_words(filters.cities) if filters.cities else None

    place_clauses, place_parameters = _place_and_age(filters, max_age_days)
    # A node of weight 0 is only a condition for an arc: it is no reason to bring a posting in.
    clause, words = _any_word("title", tuple(dict.fromkeys(form.text for node in nodes if node.weight > 0 for form in node.forms)))
    clauses, parameters = [*place_clauses, clause], [*place_parameters, *words]
    if filters.exclude:
        clause, words = _any_word("title", filters.exclude)
        clauses.append(f"NOT {clause}")
        parameters += words

    connection = duckdb.connect()
    try:
        (postings,) = connection.execute(
            f"SELECT count(*) FROM read_parquet(?) WHERE {' AND '.join(place_clauses)}", [index, *place_parameters]
        ).fetchone()
        rows = [
            _Posting(*row)
            for row in connection.execute(
                f"""SELECT key, coalesce(company, ''), title, coalesce(location, ''), url, part, row_number,
                        TRY_CAST(posted_at AS TIMESTAMP)
                    FROM read_parquet(?) WHERE {' AND '.join(clauses)}""",
                [index, *parameters],
            ).fetchall()
        ]
    except duckdb.Error as error:
        raise DatabaseError(f"the job index cannot be read: {error}") from error
    finally:
        connection.close()

    rarities = _rarities(texts, patterns, [row.title for row in rows], postings)
    scored = []
    for row in rows:
        evidence = _evidence(row.title, nodes, patterns, rarities)
        document = _apply_arcs(plan.arcs, row.title, evidence)
        in_city = cities is not None and cities.search(row.location) is not None
        scored.append((_score(nodes, evidence, document, in_city, mean), row))

    # One job per title and company: its best posting, then its newest.
    best_per_job: dict[tuple[str, str], tuple[float, _Posting]] = {}
    for score, row in scored:
        owner = (row.title.lower(), row.company.lower() or row.key)
        kept = best_per_job.get(owner)
        if kept is None or _rank(score, row) > _rank(*kept):
            best_per_job[owner] = (score, row)
    ranked = sorted(best_per_job.values(), key=lambda item: _rank(*item), reverse=True)[:limit]

    best = max((score for score, _ in ranked), default=1.0) or 1.0
    # Some sources publish their names with HTML entities still in them.
    return manifest, [
        Job(
            row.key,
            html.unescape(row.company),
            html.unescape(row.title),
            html.unescape(row.location),
            row.url,
            row.url,
            "",
            f"{base}/{row.part}#{row.number}",
            score / best,
        )
        for score, row in ranked
    ]
