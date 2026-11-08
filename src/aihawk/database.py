"""The job database AIHawk reads, as published on Hugging Face: read in place, so a search downloads only what it needs."""

from __future__ import annotations

import errno
import html
import json
import math
import re
import urllib.request
from bisect import bisect_right
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from aihawk.config import Arc, Filters, Form, Node, Plan

# How many postings a search brings back to be judged. Past these, a title fits the search too little to be worth a look.
RECALL = 600
# Description files read at once: each read waits on the network, not on this computer.
FILES_AT_ONCE = 8
# What being in the person's own city is worth, against a node of the plan: about as much as a named skill.
CITY_WEIGHT = 1.5
# What a posting with no description keeps of its score: judged on its title alone, it needs a title twice as fitting to
# come before one that has a description. The pipeline marks which have one.
UNDESCRIBED = 0.5
# The columns of the index that a delta carries and an index kept on this computer is read by. A description is found by
# the posting's key, in the description file whose range holds it.
INDEX_COLUMNS = ("key", "url", "title", "company", "ats", "location", "country", "remote", "salary_min", "salary_max", "salary_currency", "posted_at", "described")
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
    # How well the title and the place fit the search, from 0 to 1 against the best one found.
    lexical: float = 0.0


def description_files(manifest: dict[str, Any], location: str) -> tuple[tuple[str, str, str], ...]:
    """Where a description is found by its posting's key: each file's first key, last key and address, in key order."""
    base = _base(manifest, location)
    found = [(item["first_key"], item["last_key"], f"{base}/{item['name']}") for item in manifest.get("files", []) if "first_key" in item]
    return tuple(sorted(found))


def _holder(key: str, files: tuple[tuple[str, str, str], ...]) -> str | None:
    """The file whose range of keys holds this one, or None."""
    at = bisect_right([first for first, _, _ in files], key) - 1
    return files[at][2] if at >= 0 and key <= files[at][1] else None


def stored_descriptions(
    keys: list[str],
    files: tuple[tuple[str, str, str], ...],
    done: Callable[[int], None] = lambda _: None,
    problem: Callable[[str], None] = lambda _: None,
) -> list[str]:
    """The descriptions the database holds for these postings, by their keys, in order; empty where its sources published
    none. One query per file, FILES_AT_ONCE files at a time, each on its own connection: a key read in a file in key order
    reads only the row group that can hold it, and every read waits on the network. On Hugging Face's servers one file
    took 3 to 4 seconds, and 25 of them one after the other 249.
    `done` is told how many have been read, as each file is. A file that cannot be read is told once to `problem`, and
    the others are still read: the search goes on with what it has.
    """
    texts = [""] * len(keys)
    by_file: dict[str, list[tuple[int, str]]] = {}
    count = 0
    for position, key in enumerate(keys):
        if key and (file := _holder(key, files)):
            by_file.setdefault(file, []).append((position, key))
        else:
            count += 1
            done(count)

    def read(file: str, held: list[tuple[int, str]]) -> dict[str, str] | None:
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL httpfs; LOAD httpfs;")
            # Constants, not a subquery: that is what lets the reader skip every row group whose range cannot hold them.
            marks = ", ".join("?" for _ in held)
            return dict(connection.execute(f"SELECT key, description FROM read_parquet(?) WHERE key IN ({marks})", [file, *(key for _, key in held)]).fetchall())
        except duckdb.Error:
            return None
        finally:
            connection.close()

    failed = False
    with ThreadPoolExecutor(FILES_AT_ONCE) as pool:
        reads = {pool.submit(read, file, held): held for file, held in by_file.items()}
        for finished in as_completed(reads):
            found, held = finished.result(), reads[finished]
            failed = failed or found is None
            for position, key in held:
                texts[position] = (found or {}).get(key) or ""
            count += len(held)
            done(count)
    if failed:
        problem("Some job descriptions could not be read from the job database, so those postings are judged without them.")
    return texts


def unreachable(error: BaseException) -> str:
    """What a database that cannot be downloaded says: in plain words, the detail kept after them."""
    return f"The job database cannot be reached: check your internet connection, then start AIHawk again. ({error})"


def load_manifest(location: str) -> dict[str, Any]:
    try:
        if location.startswith(("http://", "https://")):
            with urllib.request.urlopen(location, timeout=60) as response:
                return json.load(response)
        return json.loads(Path(location).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        if isinstance(error, OSError) and location.startswith(("http://", "https://")):
            raise DatabaseError(unreachable(error)) from error
        raise DatabaseError(f"the job database's manifest at {location} cannot be read: {error}") from error


def built_at(manifest: dict[str, Any]) -> datetime:
    """When the database was built, as the manifest says: a time with no zone is UTC, as the pipeline's is."""
    built = datetime.fromisoformat(manifest["built_at"])
    return built if built.tzinfo else built.replace(tzinfo=UTC)


def _base(manifest: dict[str, Any], location: str) -> str:
    """Where the database's files are: the release the manifest names, or beside the manifest."""
    return manifest.get("base_url") or location.rsplit("/", 1)[0].rsplit("\\", 1)[0]


def local_index(manifest: dict[str, Any], location: str, cache: Path, log: Callable[[str], None] = lambda _: None) -> Path:
    """The index on this computer, as of the manifest's build: downloaded whole once, then kept up to date by applying the
    deltas it has not seen, in order. A database on this computer is read where it is."""
    source = f"{_base(manifest, location)}/{manifest['index']['name']}"
    if not source.startswith(("http://", "https://")):
        return Path(source)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "index.parquet"
    state = cache / "index.build"
    build = manifest.get("build") or manifest["built_at"]
    have = state.read_text(encoding="utf-8").strip() if path.is_file() and state.is_file() else ""
    if have == build:
        return path
    chain = _chain(manifest.get("deltas", []), have) if have else None
    if chain is not None:
        log(f"updating the job index: {sum(delta['adds'] for delta in chain):,} postings new or changed, {sum(delta['drops'] for delta in chain):,} gone")
        _apply(path, chain)
    else:
        # The first time, or a gap no delta covers: the whole index, once.
        _download(manifest, source, path, log)
    state.write_text(build, encoding="utf-8")
    return path


def _chain(deltas: list[dict[str, Any]], have: str) -> list[dict[str, Any]] | None:
    """The deltas that lead from the build this computer has to the newest, in order: none when it has the newest already,
    None when they cannot be followed."""
    if deltas and deltas[-1]["build"] == have:
        return []
    start = next((at for at, delta in enumerate(deltas) if delta["after"] == have), None)
    if start is None:
        return None
    chain = deltas[start:]
    if any(later["after"] != earlier["build"] for earlier, later in pairwise(chain)):
        return None
    return chain


def _apply(path: Path, chain: list[dict[str, Any]]) -> None:
    """Apply the deltas to the index in one rewrite. A key in several deltas takes its newest row, as Delta Lake's log does:
    the last action on a key is its state, so a posting added, then changed, then gone, ends gone."""
    columns = ", ".join(INDEX_COLUMNS)
    partial = path.with_suffix(".applying")
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        # A column added since this index was kept is NULL in it, until a delta brings the posting with it.
        held = {row[0] for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path.as_posix()]).fetchall()}
        kept = ", ".join(column if column in held else f"NULL AS {column}" for column in INDEX_COLUMNS)
        connection.execute(
            """CREATE TABLE changes AS
               SELECT * EXCLUDE (filename, newest) FROM (
                   SELECT *, row_number() OVER (PARTITION BY key ORDER BY filename DESC) AS newest
                   FROM read_parquet(?, filename = true, union_by_name = true)
               ) WHERE newest = 1""",
            [[delta["url"] for delta in chain]],
        )
        connection.execute(f"""
            COPY (
                SELECT {kept} FROM read_parquet(?) WHERE key NOT IN (SELECT key FROM changes)
                UNION ALL
                SELECT {columns} FROM changes WHERE op = 'add'
            ) TO '{partial.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
        """, [path.as_posix()])
    except duckdb.Error as error:
        partial.unlink(missing_ok=True)
        raise DatabaseError(f"the job index could not be brought up to date: {error}") from error
    finally:
        connection.close()
    partial.replace(path)


def _download(manifest: dict[str, Any], source: str, path: Path, log: Callable[[str], None]) -> None:
    """The release's whole index into `path`, in one rename at the end, saying how far it has come."""
    log(f"downloading the job index built {built_at(manifest):%Y-%m-%d} ({manifest['index']['bytes'] // 1_000_000} MB), once")
    partial = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(source, timeout=120) as response, partial.open("wb") as out:
            copied = told = 0
            while chunk := response.read(1 << 20):
                out.write(chunk)
                copied += len(chunk)
                # A quarter at a time: this takes minutes on a slow line, and must not look stuck.
                if copied * 4 // manifest["index"]["bytes"] > told and copied < manifest["index"]["bytes"]:
                    told = copied * 4 // manifest["index"]["bytes"]
                    log(f"  {told * 25}%")
    except OSError as error:
        partial.unlink(missing_ok=True)
        if error.errno == errno.ENOSPC:
            raise DatabaseError(f"The disk is full: the job index needs {manifest['index']['bytes'] // 1_000_000} MB free in {path.parent}. Free some space, then start AIHawk again.") from error
        raise DatabaseError(unreachable(error)) from error
    partial.replace(path)


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
        (postings,) = connection.execute(
            f"SELECT count(*) FROM read_parquet(?) WHERE {' AND '.join(place_clauses)}", [index, *place_parameters]
        ).fetchone()
        rows = [
            _Posting(*row)
            for row in connection.execute(
                f"""SELECT key, coalesce(company, ''), title, coalesce(location, ''), url,
                        TRY_CAST(posted_at AS TIMESTAMP), coalesce(described, true)
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
        scored.append((_score(nodes, evidence, document, in_city, mean) * (1.0 if row.described else UNDESCRIBED), row))

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
            score / best,
        )
        for score, row in ranked
    ]
