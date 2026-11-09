"""Builds AIHawk's job database: every public job database we know of, in one schema, one row per posting.

    python pipeline/build.py --out work                 # everything
    python pipeline/build.py --out work --only riley    # only these sources

Scheduled every five hours in .github/workflows/jobs-database.yml, which publishes work/out to the Hugging Face dataset feder-cr/jobs. Each source is read into
work/sources/<name>.parquet in the columns of COMMON. A source that fails is left out of that build's database and named in
the manifest; publish.py refuses a database that lost too much of itself. Postings are the same posting when their links
are the same after `posting_key` (src/aihawk/key.sql), and of those the row with the longest description is kept. The
output is cut in chunks of CHUNK_ROWS complete postings, the newest first, which AIHawk downloads one after the other:
every column it reads and nothing else, the posting's address being its key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

HF = "https://huggingface.co/datasets"
HF_API = "https://huggingface.co/api/datasets"
RAW = "https://raw.githubusercontent.com"
JOBHIVE = "https://storage.stapply.ai/jobhive/v1/all.parquet"
# The private dataset that keeps each source as the last build read it, with the version it was read at.
SOURCES_DATASET = "feder-cr/jobs-sources"
# The database is a list of chunks, each CHUNK_ROWS complete postings, the newest first: AIHawk downloads them one after the
# other and searches what it has, so a person who has just come sees jobs arrive instead of waiting for all of them. A
# posting with a description of at least DESCRIBED characters is in a `jobs_N` chunk; the others, whose text says nothing a
# title does not, are in `titles_N` chunks, which come last and which a search puts below the rest.
CHUNK_ROWS = 50_000
# DuckDB cannot write row groups under 2048 rows: the parts it writes are rewritten by pyarrow into the chunks.
ROW_GROUP = 2048
# A chunk's row group. A chunk is downloaded whole, so nothing reads a single group over the network: groups of 10,000
# rows compress like one of 100,000 (1.85 GB for the whole database at zstd 12 against 2.20 GB in groups of 256, measured
# on 2026-11-09), and a search on this computer still skips the groups of postings older than it asks for.
CHUNK_GROUP = 10_000
# zstd 12: 1.85 GB against 2.04 at level 6 and 1.62 at 19, which took ten times as long to write. Measured on 2026-11-09.
CHUNK_LEVEL = 12
CHUNK_THREADS = 4
# The part number of the first `titles_N` chunk: the `jobs_N` ones count from 0.
TITLES_PART = 1_000_000
# A posting older than this is not published: nobody applies to it, and a search is for the last 60 days by default.
MAX_AGE_DAYS = 180
# A posting with no date sorts as if it were this old: many sources have none, and most of what they publish is current.
UNDATED_DAYS = 30
# What a remote posting's location says when it does not narrow the job to a place; the database says "Remote" instead.
NO_PLACE = ("", "remote", "anywhere", "worldwide", "global")
# What a chunk holds: nothing AIHawk does not read. The posting's address is its key.
CHUNK_COLUMNS = ("url", "title", "company", "location", "country", "posted_at", "description")
# A description shorter than this says nothing a title does not: it is not published, and the posting is a `titles_N` one.
DESCRIBED = 200

COMMON = (
    "url", "title", "company", "ats", "location", "country", "remote",
    "salary_min", "salary_max", "salary_currency", "description", "posted_at",
)  # fmt: skip

# The posting's identity in its link: no scheme, no www, no tracking, no /apply step, in lower case.
# The key a posting is known by, defined once for the build and for AIHawk, which stores every job under it.
KEY_SQL = Path(__file__).resolve().parents[1] / "src" / "aihawk" / "key.sql"
KEY = "posting_key(url)"

# The country of a posting as ISO 3166's two letters, whatever a source writes: its name, in English or in its own language,
# or its three letters. Anything else, a city or "Remote", says no country. Measured on 2026-11-09: 1,725 different values,
# 742,569 postings saying "UNITED STATES".
COUNTRIES = {"UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US", "U.S.": "US", "U.S.A.": "US", "AMERICA": "US", "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB", "SCOTLAND": "GB", "WALES": "GB", "NORTHERN IRELAND": "GB", "UK": "GB", "CANADA": "CA", "AUSTRALIA": "AU", "INDIA": "IN", "FRANCE": "FR", "KENYA": "KE", "NETHERLANDS": "NL", "THE NETHERLANDS": "NL", "NEDERLAND": "NL", "MEXICO": "MX", "MÉXICO": "MX", "UNITED ARAB EMIRATES": "AE", "UAE": "AE", "GERMANY": "DE", "DEUTSCHLAND": "DE", "SPAIN": "ES", "ESPAÑA": "ES", "MALAYSIA": "MY", "CHINA": "CN", "ITALY": "IT", "ITALIA": "IT", "AUSTRIA": "AT", "ÖSTERREICH": "AT", "PHILIPPINES": "PH", "BRAZIL": "BR", "BRASIL": "BR", "BELGIUM": "BE", "BELGIË": "BE", "BELGIQUE": "BE", "SOUTH AFRICA": "ZA", "NEW ZEALAND": "NZ", "POLAND": "PL", "POLSKA": "PL", "SINGAPORE": "SG", "IRELAND": "IE", "SWITZERLAND": "CH", "SCHWEIZ": "CH", "SUISSE": "CH", "SAUDI ARABIA": "SA", "TAIWAN": "TW", "PORTUGAL": "PT", "THAILAND": "TH", "JAPAN": "JP", "日本": "JP", "EGYPT": "EG", "HONG KONG": "HK", "NIGERIA": "NG", "VIETNAM": "VN", "VIET NAM": "VN", "INDONESIA": "ID", "SWEDEN": "SE", "SVERIGE": "SE", "CHILE": "CL", "PAKISTAN": "PK", "DENMARK": "DK", "DANMARK": "DK", "PERU": "PE", "PERÚ": "PE", "LUXEMBOURG": "LU", "COSTA RICA": "CR", "COLOMBIA": "CO", "CZECHIA": "CZ", "CZECH REPUBLIC": "CZ", "FINLAND": "FI", "SUOMI": "FI", "PUERTO RICO": "PR", "NORWAY": "NO", "NORGE": "NO", "ROMANIA": "RO", "UKRAINE": "UA", "УКРАЇНА": "UA", "ARGENTINA": "AR", "MALTA": "MT", "SERBIA": "RS", "CYPRUS": "CY", "ISRAEL": "IL", "KOREA, REPUBLIC OF": "KR", "SOUTH KOREA": "KR", "KOREA (SOUTH)": "KR", "KOREA": "KR", "REPUBLIC OF KOREA": "KR", "BULGARIA": "BG", "GREECE": "GR", "HUNGARY": "HU", "LEBANON": "LB", "GHANA": "GH", "QATAR": "QA", "TURKEY": "TR", "TÜRKIYE": "TR", "TURKIYE": "TR", "ARMENIA": "AM", "SRI LANKA": "LK", "JAMAICA": "JM", "LIBYA": "LY", "MOROCCO": "MA", "MAROC": "MA", "KUWAIT": "KW", "LATVIA": "LV", "JORDAN": "JO", "PANAMA": "PA", "PANAMÁ": "PA", "DOMINICAN REPUBLIC": "DO", "GUYANA": "GY", "LITHUANIA": "LT", "TANZANIA": "TZ", "TANZANIA, UNITED REPUBLIC OF": "TZ", "CAYMAN ISLANDS": "KY", "GUATEMALA": "GT", "BARBADOS": "BB", "BAHAMAS": "BS", "THE BAHAMAS": "BS", "BOLIVIA": "BO", "BERMUDA": "BM", "BELIZE": "BZ", "SLOVAKIA": "SK", "PAPUA NEW GUINEA": "PG", "MAURITIUS": "MU", "UGANDA": "UG", "CÔTE D'IVOIRE": "CI", "COTE D'IVOIRE": "CI", "IVORY COAST": "CI", "TUNISIA": "TN", "ECUADOR": "EC", "MACAO": "MO", "MACAU": "MO", "HONDURAS": "HN", "SLOVENIA": "SI", "ESTONIA": "EE", "NAMIBIA": "NA", "OMAN": "OM", "SULTANATE OF OMAN": "OM", "BOSNIA AND HERZEGOVINA": "BA", "MADAGASCAR": "MG", "ZAMBIA": "ZM", "BANGLADESH": "BD", "ZIMBABWE": "ZW", "ALBANIA": "AL", "TRINIDAD AND TOBAGO": "TT", "URUGUAY": "UY", "MOLDOVA": "MD", "MALDIVES": "MV", "SYRIA": "SY", "EL SALVADOR": "SV", "SENEGAL": "SN", "IRAQ": "IQ", "KOSOVO": "XK", "ICELAND": "IS", "TIMOR-LESTE": "TL", "GUINEA": "GN", "GUAM": "GU", "CROATIA": "HR", "HRVATSKA": "HR", "RUSSIA": "RU", "RUSSIAN FEDERATION": "RU", "IRAN": "IR", "ALGERIA": "DZ", "ETHIOPIA": "ET", "RWANDA": "RW", "BOTSWANA": "BW", "MOZAMBIQUE": "MZ", "ANGOLA": "AO", "CAMEROON": "CM", "NEPAL": "NP", "MONGOLIA": "MN", "KAZAKHSTAN": "KZ", "UZBEKISTAN": "UZ", "GEORGIA": "GE", "AZERBAIJAN": "AZ", "BELARUS": "BY", "NORTH MACEDONIA": "MK", "MONTENEGRO": "ME", "VENEZUELA": "VE", "PARAGUAY": "PY", "NICARAGUA": "NI", "CUBA": "CU", "HAITI": "HT", "BAHRAIN": "BH", "YEMEN": "YE", "AFGHANISTAN": "AF", "MYANMAR": "MM", "CAMBODIA": "KH", "LAOS": "LA", "BRUNEI": "BN", "FIJI": "FJ", "LIECHTENSTEIN": "LI", "MONACO": "MC", "ANDORRA": "AD", "GIBRALTAR": "GI", "ISLE OF MAN": "IM", "JERSEY": "JE", "GUERNSEY": "GG", "GREENLAND": "GL", "ARUBA": "AW", "CURAÇAO": "CW", "CURACAO": "CW", "MALAWI": "MW", "SUDAN": "SD", "SOMALIA": "SO", "DJIBOUTI": "DJ", "ERITREA": "ER", "MALI": "ML", "NIGER": "NE", "CHAD": "TD", "TOGO": "TG", "BENIN": "BJ", "BURKINA FASO": "BF", "SIERRA LEONE": "SL", "LIBERIA": "LR", "GAMBIA": "GM", "GABON": "GA", "CONGO": "CG", "DEMOCRATIC REPUBLIC OF THE CONGO": "CD", "SOUTH SUDAN": "SS", "LESOTHO": "LS", "ESWATINI": "SZ", "SEYCHELLES": "SC", "CAPE VERDE": "CV", "TURKS AND CAICOS ISLANDS": "TC", "VIRGIN ISLANDS": "VI", "SAINT LUCIA": "LC", "GRENADA": "GD", "DOMINICA": "DM", "ANTIGUA AND BARBUDA": "AG", "SAINT KITTS AND NEVIS": "KN", "SURINAME": "SR", "NEW CALEDONIA": "NC", "FRENCH POLYNESIA": "PF", "RÉUNION": "RE", "REUNION": "RE", "DEU": "DE", "SGP": "SG", "PHL": "PH", "MYS": "MY", "AUS": "AU", "CHN": "CN", "BRA": "BR", "JPN": "JP", "NLD": "NL", "IRL": "IE", "IRQ": "IQ", "KOR": "KR", "EGY": "EG", "VNM": "VN", "POL": "PL", "DJI": "DJ", "SYR": "SY", "TCA": "TC", "ITA": "IT", "KWT": "KW", "ISR": "IL", "FRA": "FR", "SWE": "SE", "PAK": "PK", "KEN": "KE", "ARE": "AE", "DOM": "DO", "SAU": "SA", "GBR": "GB", "CAN": "CA", "IND": "IN", "ESP": "ES", "MEX": "MX", "CHE": "CH", "AUT": "AT", "BEL": "BE", "PRT": "PT", "NZL": "NZ", "ZAF": "ZA", "ARG": "AR", "COL": "CO", "CHL": "CL", "PER": "PE", "IDN": "ID", "THA": "TH", "TWN": "TW", "HKG": "HK", "NGA": "NG", "DNK": "DK", "NOR": "NO", "FIN": "FI", "CZE": "CZ", "ROU": "RO", "HUN": "HU", "GRC": "GR", "TUR": "TR", "UKR": "UA", "QAT": "QA", "LUX": "LU", "CRI": "CR"}  # fmt: skip
NOT_COUNTRIES = ("XX", "EU", "UN", "ZZ")
# One name for each vendor, whatever a source calls it. None: a label that names no vendor. One source labels postings of
# every site "datastax", 34,477 of them LinkedIn's (measured on 2026-11-09).
VENDOR_ALIASES = {
    "ashbyhq": "ashby", "join_com": "join", "oracle_hcm": "oracle", "wttj": "welcometothejungle", "csod": "cornerstone",
    "ultipro": "ukg", "beisen_legacy": "beisen", "datastax": None,
}  # fmt: skip
# The vendor a link's host names, for the postings whose source does not say. The first that matches.
VENDOR_HOSTS = (
    ("myworkdayjobs.com", "workday"), ("myworkdaysite.com", "workday"), ("smartrecruiters.com", "smartrecruiters"),
    ("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"), ("icims.com", "icims"),
    ("paylocity.com", "paylocity"), ("adp.com", "adp"), ("oraclecloud.com", "oracle"), ("taleo.net", "taleo"),
    ("successfactors", "successfactors"), ("sapsf.", "successfactors"), ("jobvite.com", "jobvite"), ("bamboohr.com", "bamboohr"),
    ("applytojob.com", "jazzhr"), ("breezy.hr", "breezy"), ("recruitee.com", "recruitee"), ("teamtailor.com", "teamtailor"),
    ("personio.", "personio"), ("workable.com", "workable"), ("pinpointhq.com", "pinpoint"), ("rippling.com", "rippling"),
    ("ultipro.com", "ukg"), ("ukg.net", "ukg"), ("dayforcehcm.com", "dayforce"), ("paycomonline.", "paycom"),
    ("csod.com", "cornerstone"), ("careerplug.com", "careerplug"), ("recruiterflow.com", "recruiterflow"),
    ("zohorecruit.", "zoho"), ("jobs.gem.com", "gem"), ("applicantpro.com", "applicantpro"), ("isolvedhire.com", "isolved"),
    ("eightfold.ai", "eightfold"), ("avature.net", "avature"), ("phenompeople.com", "phenom"), ("join.com", "join"),
    ("hrmos.co", "hrmos"), ("gupy.io", "gupy"), ("softgarden.", "softgarden"), ("jobscore.com", "jobscore"),
    ("freshteam.com", "freshteam"), ("manatal.com", "manatal"), ("keka.com", "keka"), ("darwinbox.", "darwinbox"),
    ("recruiterbox.com", "recruiterbox"), ("betterteam.com", "betterteam"), ("linkedin.com", "linkedin"),
    ("indeed.", "indeed"), ("glassdoor.", "glassdoor"), ("europa.eu", "eures"), ("arbeitsagentur.de", "bundesagentur"),
    ("jobbank.gc.ca", "jobbankca"), ("jobicy.com", "jobicy"), ("welcometothejungle.com", "welcometothejungle"),
    # Read from the hosts of the postings no vendor was known for, on 2026-11-09: forms first, then boards.
    ("brassring.com", "brassring"), ("governmentjobs.com", "neogov"), ("schooljobs.com", "neogov"),
    ("recruitingbypaycor.com", "paycor"), ("applitrack.com", "applitrack"), ("hrmdirect.com", "hrmdirect"),
    ("cadienttalent.com", "cadient"), ("hireology.com", "hireology"), ("workstream.us", "workstream"),
    ("paradox.ai", "paradox"), ("apploi.com", "apploi"), ("pageuppeople.com", "pageup"), ("comeet.com", "comeet"),
    ("dover.com", "dover"), ("silkroad.com", "silkroad"), ("hirebridge.com", "hirebridge"), ("careers-page.com", "manatal"),
    ("jobappnetwork.com", "jobappnetwork"), ("arbeitnow.com", "arbeitnow"), ("builtin.com", "builtin"),
    ("trudvsem.ru", "trudvsem"), ("jobsyn.org", "directemployers"), ("usajobs.gov", "usajobs"), ("glints.com", "glints"),
    ("jobvision.ir", "jobvision"), ("jobinja.ir", "jobinja"), ("irantalent.com", "irantalent"), ("51job.com", "51job"),
    ("myjob.mu", "myjobmu"), ("hr.ge", "hrge"), ("jobmaster.co.il", "jobmaster"), ("reed.co.uk", "reed"),
)  # fmt: skip
# Vendors that list other companies' postings rather than take the application: a copy on one is never the one kept when
# the company's own form carries the same job.
AGGREGATORS = (
    "eures", "bundesagentur", "jobbankca", "linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow", "weworkremotely",
    "remoteok", "remotive", "himalayas", "workingnomads", "builtin", "themuse", "wellfound", "ycombinator", "getonbrd",
    "cryptojobs", "fashionjobs", "jobsch", "programathor", "jobtech", "arbetsformedlingen", "manfred", "welcometothejungle",
    "trudvsem", "directemployers", "usajobs", "glints", "jobvision", "jobinja", "irantalent", "51job", "myjobmu", "hrge",
    "jobmaster", "reed",
)  # fmt: skip
# Boards whose posting pages, opened on 2026-11-08, sent the applicant on to one of the companies' own forms AIHawk applies
# on: such a posting goes, as the job is the form's. EURES, the Bundesagentur, Welcome to the Jungle and Job Bank showed no
# such link in their pages, and are not opened.
LINKING_BOARDS = ("jobsch", "arbeitnow", "wellfound", "cryptojobs", "workingnomads", "arbetsformedlingen", "remotive")
BOARD_CHECKS = "board_checks.json"
# A site whose postings are mostly copies of jobs on companies' own forms is a front for them, a board or a company's
# careers page in front of its ATS (careers.oracle.com, apply.ford.com): when at least FRONT_SHARE of its postings, and
# it has at least FRONT_LEAST, merge into a form, the rest of its postings are copies too, and go. The owner's choice of
# 2026-11-08; measured then, it named 68 sites and took 12,443 more postings.
FRONT_SHARE = 0.3
FRONT_LEAST = 50
LEGAL_FORMS = (
    "inc", "llc", "ltd", "limited", "gmbh", "ag", "sa", "sas", "srl", "spa", "corp", "corporation", "co", "company", "plc",
    "bv", "nv", "oy", "ab", "as", "pty", "pvt", "lp", "llp", "kg", "group", "holdings",
)  # fmt: skip


def _macros() -> str:
    """The vendor a posting belongs to, and the print that knows one job under links of different sites."""
    aliases = " ".join(f"WHEN '{name}' THEN {f'{chr(39)}{to}{chr(39)}' if to else 'NULL'}" for name, to in VENDOR_ALIASES.items())
    hosts = " ".join(f"WHEN contains(regexp_extract(lower(url), '^https?://([^/]+)', 1), '{host}') THEN '{name}'" for host, name in VENDOR_HOSTS)
    forms = "|".join(LEGAL_FORMS)
    # Quoted for SQL: a name may hold an apostrophe, as Côte d'Ivoire's does.
    countries = ", ".join("'" + name.replace("'", "''") + "'" for name in COUNTRIES)
    codes = " ".join("WHEN '" + name.replace("'", "''") + f"' THEN '{code}'" for name, code in COUNTRIES.items())
    return f"""
        -- The link's host first: it is where the application goes, and a source's label can be wrong.
        CREATE OR REPLACE MACRO vendor(ats, url) AS coalesce(
            CASE {hosts} END,
            CASE lower(nullif(trim(ats), '')) {aliases} ELSE lower(nullif(trim(ats), '')) END);
        CREATE OR REPLACE MACRO country_code(country) AS CASE
            WHEN upper(trim(country)) IN {NOT_COUNTRIES!r} THEN NULL
            WHEN upper(trim(country)) IN ({countries}) THEN CASE upper(trim(country)) {codes} END
            WHEN regexp_matches(upper(trim(country)), '^[A-Z]{{2}}$') THEN upper(trim(country))
            END;
        CREATE OR REPLACE MACRO plain(text) AS trim(regexp_replace(regexp_replace(lower(coalesce(text, '')), '[^\\pL\\pN]+', ' ', 'g'), '\\s+', ' ', 'g'));
        CREATE OR REPLACE MACRO employer(company) AS trim(regexp_replace(regexp_replace(plain(company), '\\b({forms})\\b', ' ', 'g'), '\\s+', ' ', 'g'));
        CREATE OR REPLACE MACRO city(location) AS plain(split_part(coalesce(location, ''), ',', 1));
        -- No city, no print: one role with no place given would otherwise be one job across a whole country.
        CREATE OR REPLACE MACRO job_print(company, title, location) AS CASE WHEN employer(company) <> '' AND plain(title) <> ''
            AND city(location) NOT IN ('', 'not specified', 'n a', 'na', 'unknown', 'various', 'multiple locations')
            THEN employer(company) || '|' || plain(title) || '|' || city(location) END;
    """


def _text(value: str | None) -> str:
    return f"CAST({value} AS VARCHAR)" if value else "CAST(NULL AS VARCHAR)"


def _number(value: str | None) -> str:
    return f"TRY_CAST({value} AS DOUBLE)" if value else "CAST(NULL AS DOUBLE)"


def select(
    source: str,
    url: str,
    title: str = "title",
    company: str | None = "company",
    ats: str | None = None,
    location: str | None = "location",
    country: str | None = None,
    remote: str | None = None,
    salary_min: str | None = None,
    salary_max: str | None = None,
    salary_currency: str | None = None,
    description: str | None = None,
    posted_at: str | None = None,
    where: str = "TRUE",
) -> str:
    """The SELECT that turns one source's rows into COMMON."""
    return f"""SELECT
        {_text(url)} AS url, {_text(title)} AS title, {_text(company)} AS company, lower({_text(ats)}) AS ats,
        {_text(location)} AS location, upper({_text(country)}) AS country, TRY_CAST({remote or 'NULL'} AS BOOLEAN) AS remote,
        {_number(salary_min)} AS salary_min, {_number(salary_max)} AS salary_max, {_text(salary_currency)} AS salary_currency,
        {_text(description)} AS description, {_text(posted_at)} AS posted_at
        FROM {source} WHERE ({where})"""


def _hf_files(dataset: str, prefix: str, suffix: str = ".parquet") -> list[str]:
    """A Hugging Face dataset's files under `prefix` and ending in `suffix`, in name order, as addresses to read."""
    with urllib.request.urlopen(f"{HF_API}/{dataset}", timeout=60) as response:
        names = sorted(item["rfilename"] for item in json.load(response)["siblings"])
    return [f"{HF}/{dataset}/resolve/main/{name}" for name in names if name.startswith(prefix) and name.endswith(suffix)]


def _hf_newest(dataset: str, prefix: str, suffix: str, count: int = 1) -> list[str]:
    """The last `count` of those files by name: the newest, for a dataset that names its files by date."""
    return _hf_files(dataset, prefix, suffix)[-count:]


def _open_apply(_: Path) -> str:
    # One folder a day, data/date=YYYY-MM-DD/source=<board>/: the newest day is the jobs open now.
    files = _hf_files("edwarddgao/open-apply-jobs", "data/date=")
    newest = max(file.split("data/date=")[1].split("/")[0] for file in files)
    today = [file for file in files if f"data/date={newest}/" in file]
    return select(
        f"read_parquet({today!r}, union_by_name=true)", url="apply_url", company="source_slug",
        location="array_to_string(locations, '; ')", description="description_html", posted_at="posted_at",
    )  # fmt: skip


def _dtunkelang(work: Path, board: str) -> str:
    # Thousands of small files of one board's postings, under pipeline-spill/: fetched in one go, then read together.
    from huggingface_hub import snapshot_download  # imported here: only the published build reads this source

    folder = work / "clones" / f"dtunkelang-{board}"
    snapshot_download("dtunkelang/jobs-demo", repo_type="dataset", local_dir=str(folder), allow_patterns=[f"pipeline-spill/**/{board}-*.parquet"])
    pattern = (folder / "pipeline-spill" / "**" / f"{board}-*.parquet").as_posix()
    return select(
        f"read_parquet('{pattern}', union_by_name=true)", url="apply_url", company="source_slug", ats=f"'{board}'",
        location="array_to_string(locations, '; ')", description="description_html", posted_at="posted_at",
    )  # fmt: skip


def _nexus(work: Path) -> str:
    # One JSON file inside a zip, which DuckDB cannot open: it is unpacked first.
    folder = work / "clones" / "nexus"
    folder.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{HF}/Yash514311/nexus-jobs/resolve/main/jobs.zip", timeout=600) as response:
        (folder / "jobs.zip").write_bytes(response.read())
    with zipfile.ZipFile(folder / "jobs.zip") as archive:
        archive.extractall(folder)
    found = sorted(folder.rglob("*.json"))
    return select(f"read_json('{found[0].as_posix()}', maximum_object_size=500000000)", url="applyLink", location="location")


def _unnested(url: str, field: str) -> str:
    """The rows of a JSON file that holds them under one field, each as `u`."""
    return f"(SELECT unnest({field}) AS u FROM read_json('{url}', maximum_object_size=200000000))"


def _github_release_asset(repo: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/latest/download/{name}"


@dataclass(frozen=True)
class Source:
    name: str
    home: str
    license: str
    sql: Callable[[Path], str]


def _simplify(repo: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_json('{RAW}/{repo}/HEAD/.github/scripts/listings.json', maximum_object_size=200000000, format='array')",
        url="url", company="company_name", location="array_to_string(locations, '; ')",
        posted_at="make_timestamp(date_posted * 1000000)", where="active AND is_visible",
    )  # fmt: skip


def _open_jobs_data(repo: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_json('{RAW}/{repo}/HEAD/data/jobs.json', maximum_object_size=200000000, format='array')",
        url="applyUrl", ats="platform", location="array_to_string(locations, '; ')", remote="isRemote", posted_at="postedAt",
    )  # fmt: skip


def _headcount(name: str) -> Callable[[Path], str]:
    return lambda _: select(
        f"read_parquet('{HF}/HeadcountLabs/{name}/resolve/main/data/part-0.parquet')",
        url="coalesce(application_url, posting_url)", company="employer_name",
        location="concat_ws(', ', location_city, location_region, location_country)", country="location_country",
        remote="work_arrangement = 'remote'", salary_min="pay_min", salary_max="pay_max", salary_currency="pay_currency",
        posted_at="posted_at", where="currentness = 'current'",
    )  # fmt: skip


def _scholarweave(_: Path) -> str:
    files = _hf_files("scholarweave/Jobs-Dataset-v2", "data/active/full/current/")
    companies = max(_hf_files("scholarweave/Jobs-Dataset-v2", "data/companies/"))
    source = (
        f"read_parquet({files!r}) AS job LEFT JOIN read_parquet('{companies}') AS firm ON job.company_id = firm.id"
    )
    return select(
        source, url="job.apply_url", title="job.title", company="firm.name", ats="firm.ats",
        location="job_model_json->>'$.location.city'", country="job.country", remote="job.is_remote",
        description="job_model_json->>'$.description_plain'", posted_at="job.posted_at",
    )  # fmt: skip


def _eu_tech(_: Path) -> str:
    latest = sorted(_hf_files("Aramente/eu-tech-jobs", "snapshots/"))
    jobs = [name for name in latest if name.endswith("/jobs.parquet")][-1]
    return select(
        f"read_parquet('{jobs}')", url="url", company="company_slug", ats="source", location="location",
        remote="remote_policy = 'remote'", salary_min="salary_min", salary_max="salary_max",
        salary_currency="salary_currency", description="description_md", posted_at="posted_at",
    )  # fmt: skip


def _rolebase(work: Path) -> str:
    # Thousands of small files in the repository: a shallow clone is the one way to read them in a reasonable time.
    clones = work / "clones"
    clones.mkdir(parents=True, exist_ok=True)
    clone = Path(tempfile.mkdtemp(dir=clones)) / "roleBase"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", "https://github.com/chandranshB/roleBase", str(clone)],
        check=True, timeout=3600,
    )  # fmt: skip
    pattern = (clone / "data" / "jobs" / "*.jsonl").as_posix()
    source = (
        f"read_json('{pattern}', format='newline_delimited', union_by_name=true, ignore_errors=true, "
        "columns={id: 'VARCHAR', company: 'VARCHAR', title: 'VARCHAR', location: 'VARCHAR', remote: 'BOOLEAN', "
        "url: 'VARCHAR', posted: 'VARCHAR', closed_at: 'VARCHAR'})"
    )
    # A link is left out of the file when it follows the board's standard pattern: for these the pattern is known.
    url = r"""coalesce(url, CASE split_part(id, ':', 1)
        WHEN 'greenhouse' THEN 'https://job-boards.greenhouse.io/' || split_part(id, ':', 2) || '/jobs/' || split_part(id, ':', 3)
        WHEN 'lever' THEN 'https://jobs.lever.co/' || split_part(id, ':', 2) || '/' || split_part(id, ':', 3)
        WHEN 'ashby' THEN 'https://jobs.ashbyhq.com/' || split_part(id, ':', 2) || '/' || split_part(id, ':', 3)
        END)"""
    # Links ending in a 12-character hex code are not the board's own ids, and many of them answer 404 or 403 when checked.
    # A posting with no working link is of no use to anyone.
    usable = r"closed_at IS NULL AND NOT regexp_matches(coalesce(url, ''), '/job/[0-9a-f]{12}$')"
    return select(source, url=url, ats="split_part(id, ':', 1)", remote="remote", posted_at="posted", where=usable)


def _clean_up(work: Path) -> None:
    """The clone is read: its room is for the merge."""
    shutil.rmtree(work / "clones", ignore_errors=True)


SOURCES = (
    Source("jobhive", "https://github.com/kalil0321/ats-scrapers", "not stated", lambda _: select(
        f"read_parquet('{JOBHIVE}')",
        url="coalesce(apply_url, url)", ats="ats_type", country="country_iso", remote="is_remote",
        salary_min="salary_min", salary_max="salary_max", salary_currency="salary_currency",
        description="description", posted_at="posted_at",
    )),
    Source("riley", "https://github.com/Riley-Dorrington/job-board-aggregator", "MIT (code)", lambda _: select(
        f"read_json('{_github_release_asset('Riley-Dorrington/job-board-aggregator', 'all_jobs.json.gz')}', "
        "format='array', compression='gzip', maximum_object_size=100000000)",
        url="url", ats="ats", remote="remote",
    )),
    Source("rolebase", "https://github.com/chandranshB/roleBase", "AGPL-3.0", _rolebase),
    Source("scholarweave", "https://huggingface.co/datasets/scholarweave/Jobs-Dataset-v2", "Apache-2.0", _scholarweave),
    Source("headcount-pay", "https://huggingface.co/datasets/HeadcountLabs/pay-transparency-jobs", "ODbL", _headcount("pay-transparency-jobs")),
    Source("headcount-tech", "https://huggingface.co/datasets/HeadcountLabs/tech-jobs", "ODbL", _headcount("tech-jobs")),
    Source("headcount-remote", "https://huggingface.co/datasets/HeadcountLabs/remote-jobs", "ODbL", _headcount("remote-jobs")),
    Source("headcount-internships", "https://huggingface.co/datasets/HeadcountLabs/internships", "ODbL", _headcount("internships")),
    Source("headcount-healthcare", "https://huggingface.co/datasets/HeadcountLabs/healthcare-jobs", "ODbL", _headcount("healthcare-jobs")),
    Source("eu-tech-jobs", "https://huggingface.co/datasets/Aramente/eu-tech-jobs", "CC-BY-4.0", _eu_tech),
    Source("minmaxxxer", "https://huggingface.co/datasets/minmaxxxer/job-postings-with-salaries", "research use, no republishing as listings", lambda _: select(
        f"read_parquet('{HF}/minmaxxxer/job-postings-with-salaries/resolve/main/job_postings_with_salaries.parquet')",
        url="coalesce(apply_url, url)", ats="ats", country="country_code", remote="workplace_type = 'remote'",
        salary_min="salary_min", salary_max="salary_max", salary_currency="salary_currency",
        description="description", posted_at="posted_at",
    )),
    Source("lookin4jobs", "https://github.com/lookin4jobs/open-jobs-data", "MIT (code)", _open_jobs_data("lookin4jobs/open-jobs-data")),
    Source("conorscode", "https://github.com/ConorsCode/open-jobs-data", "MIT (code)", _open_jobs_data("ConorsCode/open-jobs-data")),
    Source("fecklesspleb", "https://github.com/fecklesspleb/ats-new-jobs-feed", "MIT (code)", lambda _: select(
        f"read_csv('{RAW}/fecklesspleb/ats-new-jobs-feed/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="url", ats="ats", remote="workplace = 'remote'", posted_at="posted_at",
    )),
    Source("simplify-new-grad", "https://github.com/SimplifyJobs/New-Grad-Positions", "not stated", _simplify("SimplifyJobs/New-Grad-Positions")),
    Source("simplify-internships", "https://github.com/SimplifyJobs/Summer2027-Internships", "not stated", _simplify("SimplifyJobs/Summer2027-Internships")),
    Source("vanshb03-new-grad", "https://github.com/vanshb03/New-Grad-2027", "MIT", _simplify("vanshb03/New-Grad-2027")),
    Source("vanshb03-internships", "https://github.com/vanshb03/Summer2026-Internships", "MIT", _simplify("vanshb03/Summer2026-Internships")),
    Source("simplify-trabaho", "https://github.com/yanicells/SimplifyTrabaho", "CC0-1.0 (data), MIT (code)", lambda _: select(
        _unnested(f"{RAW}/yanicells/SimplifyTrabaho/HEAD/data/listings.json", "listings"),
        url="u.url", title="u.title", company="u.company", ats="u.source", location="array_to_string(u.locations, '; ')",
        remote="u.workSetup = 'remote'",
    )),
    Source("headcount-engineering-interns", "https://github.com/HeadcountLabs/engineering-internships", "ODbL", lambda _: select(
        f"read_csv('{RAW}/HeadcountLabs/engineering-internships/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="apply_url", location="locations", remote="remote = 'true'", posted_at="posted",
    )),
    Source("headcount-software-interns", "https://github.com/HeadcountLabs/software-engineering-internships", "ODbL", lambda _: select(
        f"read_csv('{RAW}/HeadcountLabs/software-engineering-internships/HEAD/data/jobs.csv', header=true, all_varchar=true)",
        url="apply_url", location="locations", remote="remote = 'true'", posted_at="posted",
    )),
    Source("applyguy", "https://github.com/ApplyGuy/2027-New-Grad-Jobs", "not stated", lambda _: select(
        _unnested(f"{RAW}/ApplyGuy/2027-New-Grad-Jobs/HEAD/data/new-grad-jobs.json", "jobs"),
        url="u.url", title="u.title", company="u.company", location="u.location", posted_at="u.posted",
    )),
    Source("fde-jobs", "https://github.com/stephanelx88/forward-deployed-engineer-jobs", "CC-BY-4.0", lambda _: select(
        f"read_csv('{RAW}/stephanelx88/forward-deployed-engineer-jobs/HEAD/data/fde-jobs.csv', header=true, all_varchar=true)",
        url="fdetimes_url", country="country", salary_min="pay_low_usd", salary_max="pay_high_usd", posted_at="first_seen",
    )),
    Source("remote-india", "https://github.com/raghavendra81422-boop/remote-jobs-open-to-india", "MIT", lambda _: select(
        _unnested(f"{RAW}/raghavendra81422-boop/remote-jobs-open-to-india/HEAD/data.json", "jobs"),
        url="u.roliva_url", title="u.title", company="u.company", location="u.location", remote="TRUE", posted_at="u.posted_date",
    )),
    # Added on 2026-11-08 from Hugging Face, each dataset updated at least a few times a fortnight: see docs/data-sources.md.
    Source("open-apply", "https://huggingface.co/datasets/edwarddgao/open-apply-jobs", "MIT", _open_apply),
    Source("shifat", "https://huggingface.co/datasets/Shifat2110724169/job-market-intel-corpus", "CC-BY-4.0", lambda _: select(
        f"read_parquet({_hf_files('Shifat2110724169/job-market-intel-corpus', 'shards/')!r}, union_by_name=true)",
        url="coalesce(job_url, application_url)", title="title_original", company="company_name",
        location="coalesce(location_raw, city_name, country_name)", description="description_clean", posted_at="posting_date",
    )),
    Source("dtunkelang-paylocity", "https://huggingface.co/datasets/dtunkelang/jobs-demo", "CC-BY-4.0", lambda work: _dtunkelang(work, "paylocity")),
    Source("dtunkelang-adp", "https://huggingface.co/datasets/dtunkelang/jobs-demo", "CC-BY-4.0", lambda work: _dtunkelang(work, "adp")),
    Source("nexus", "https://huggingface.co/datasets/Yash514311/nexus-jobs", "MIT (tag, no card)", _nexus),
    Source("weio", "https://huggingface.co/datasets/weio-ops/career-jobs-index", "other", lambda _: select(
        f"read_json('{HF}/weio-ops/career-jobs-index/resolve/main/jobs-index.jsonl.gz', format='newline_delimited')",
        url="job_url", company="company_name", posted_at="posted_at",
    )),
    Source("veritahire", "https://huggingface.co/datasets/VeritaHire/verified-jobs-feed-open-sample", "CC-BY-NC-4.0", lambda _: select(
        f"read_parquet('{HF}/VeritaHire/verified-jobs-feed-open-sample/resolve/main/veritahire-jobs-open-sample.parquet')",
        url="apply_url", company="employer", posted_at="posted_at",
    )),
    Source("thejobsapi", "https://huggingface.co/datasets/thejobsapi/employer-career-site-jobs-us-canada-2026-11", "CC-BY-4.0", lambda _: select(
        f"read_json('{HF}/thejobsapi/employer-career-site-jobs-us-canada-2026-11/resolve/main/jobs.jsonl', format='newline_delimited')",
        url="source_url", company="employer", location=None, posted_at="posted_at",
    )),
    Source("cleanfeeds", "https://huggingface.co/datasets/cleanfeeds/tech-jobs-daily", "CC-BY-4.0", lambda _: select(
        f"read_csv('{_hf_newest('cleanfeeds/tech-jobs-daily', 'data/jobs-', '.csv')[0]}', all_varchar=true)", url="url", posted_at="posted_at",
    )),
    Source("jobicy", "https://huggingface.co/datasets/jobicy/remote-jobs", "jobicy terms: redistribution allowed", lambda _: select(
        f"read_json({_hf_newest('jobicy/remote-jobs', 'data/daily/', '.jsonl', 14)!r}, format='newline_delimited', union_by_name=true)",
        url="url", title="jobTitle", company="companyName", location="jobGeo", description="jobDescription", posted_at="pubDate",
    )),
    Source("mauritius", "https://huggingface.co/datasets/goated69/mauritius-it-jobs", "CC-BY-4.0", lambda _: select(
        f"read_csv('{HF}/goated69/mauritius-it-jobs/resolve/main/jobs.csv', all_varchar=true)",
        url="url", title="job_title", description="job_details", posted_at="date_posted",
    )),
    Source("bangladeshi", "https://huggingface.co/datasets/swadhinbiswas/bangladeshi-jobs", "CC-BY-4.0", lambda _: select(
        f"read_parquet('{HF}/swadhinbiswas/bangladeshi-jobs/resolve/main/parquet/fact_job.parquet')",
        url="apply_links[1]", company="company_name", location="location_text", description="description_md", posted_at="posted_at_raw",
    )),
    Source("pennydata", "https://huggingface.co/datasets/pennydata/greenhouse-jobs-snapshot-2026-10", "CC0-1.0", lambda _: select(
        f"read_csv('{_hf_newest('pennydata/greenhouse-jobs-snapshot-2026-10', 'snapshot-', '.csv')[0]}', all_varchar=true)", url="url", ats="'greenhouse'",
    )),
)  # fmt: skip


def connect(work: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    connection.execute(f"SET temp_directory = '{(work / 'tmp').as_posix()}'")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET max_temp_directory_size = '40GB'")
    # Below the runner's 16 GB, so DuckDB spills to the temp directory before the system runs out: a build that sorted
    # with the default limit used 12.4 GiB of 12.4 and stopped.
    connection.execute("SET memory_limit = '9GB'")
    connection.execute(KEY_SQL.read_text(encoding="utf-8"))
    connection.execute(_macros())
    return connection


def _git_head(repo: str) -> str:
    """The commit a GitHub repository's default branch is at, by git itself: it has no API limit."""
    done = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}", "HEAD"], check=True, capture_output=True, text=True, timeout=60)
    return done.stdout.split()[0]


def _hf_revision(dataset: str) -> str:
    """The commit a Hugging Face dataset is at."""
    with urllib.request.urlopen(f"{HF_API}/{dataset}/revision/main", timeout=60) as response:
        return json.load(response)["sha"]


def _file_version(url: str) -> str | None:
    """What a server says of a file without sending it: its ETag, or its date and size; None when it says neither."""
    # Named: Cloudflare, in front of jobhive's storage, refuses Python's own user agent.
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "AIHawk-pipeline (+https://github.com/feder-cr/Jobs_Applier_AI_Agent)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        etag, modified, size = (response.headers.get(name, "") for name in ("ETag", "Last-Modified", "Content-Length"))
    return etag or (f"{modified} {size}" if modified else None)


# Where a source's data is not its home's latest commit: the file's own headers, or None, read every build. Riley's file is
# a release asset, which a release changes without a commit; it takes seconds to read.
VERSIONS: dict[str, Callable[[], str | None]] = {"jobhive": lambda: _file_version(JOBHIVE), "riley": lambda: None}


def version_of(source: Source) -> str | None:
    """What the source's data is as of now, to tell whether it changed since the last build; None when that cannot be
    known, and the source is read."""
    try:
        if source.name in VERSIONS:
            return VERSIONS[source.name]()
        if source.home.startswith("https://github.com/"):
            return _git_head(source.home.removeprefix("https://github.com/"))
        if source.home.startswith(f"{HF}/"):
            return _hf_revision(source.home.removeprefix(f"{HF}/"))
    except Exception:  # noqa: BLE001  A version that cannot be had only means the source is read.
        return None
    return None


class SourceCache:
    """The sources as the last build read them, kept in the private dataset SOURCES_DATASET: a source whose version has
    not changed is taken from there instead of read again. A build that cannot reach it reads every source."""

    def __init__(self, api: object, versions: dict[str, dict]) -> None:
        self.api, self.versions = api, versions

    @classmethod
    def open(cls) -> SourceCache | None:
        token = os.environ.get("HF_TOKEN", "")
        if not token:
            return None
        try:
            from huggingface_hub import HfApi, hf_hub_download  # imported here: only the published build needs it

            api = HfApi(token=token)
            api.create_repo(SOURCES_DATASET, repo_type="dataset", private=True, exist_ok=True)
            try:
                versions = json.loads(Path(hf_hub_download(SOURCES_DATASET, "versions.json", repo_type="dataset", token=token)).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001  The first build with a cache: nothing kept yet.
                versions = {}
            return cls(api, versions)
        except Exception as error:  # noqa: BLE001  No cache is a slower build, not a failed one.
            print(f"source cache: not used: {error}", file=sys.stderr, flush=True)
            return None

    def take(self, name: str, version: str, path: Path) -> dict | None:
        """The source's report, its file put at `path`, when the cache holds it at this version; None otherwise."""
        kept = self.versions.get(name)
        if not kept or kept["version"] != version:
            return None
        try:
            from huggingface_hub import hf_hub_download

            shutil.copyfile(hf_hub_download(SOURCES_DATASET, f"sources/{name}.parquet", repo_type="dataset", token=self.api.token), path)
        except Exception:  # noqa: BLE001  A file that cannot be had is read from the source instead.
            return None
        return kept["report"]

    def checks(self) -> dict[str, bool]:
        """The boards' pages already opened: each posting's key, and whether it sent on to a form."""
        try:
            from huggingface_hub import hf_hub_download

            return json.loads(Path(hf_hub_download(SOURCES_DATASET, BOARD_CHECKS, repo_type="dataset", token=self.api.token)).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  None yet.
            return {}

    def keep_checks(self, checks: dict[str, bool], work: Path) -> None:
        from huggingface_hub import CommitOperationAdd

        (work / BOARD_CHECKS).write_text(json.dumps(checks), encoding="utf-8")
        try:
            self.api.create_commit(SOURCES_DATASET, repo_type="dataset", operations=[CommitOperationAdd(BOARD_CHECKS, str(work / BOARD_CHECKS))],
                                   commit_message=f"{len(checks)} board pages checked")
            self.api.super_squash_history(SOURCES_DATASET, repo_type="dataset")
        except Exception as error:  # noqa: BLE001  The next build opens these pages again.
            print(f"board checks: not kept: {error}", file=sys.stderr, flush=True)

    def keep(self, fresh: list[tuple[str, str, dict]], work: Path) -> None:
        """The sources read this build, at their versions, in one commit; the history squashed, so old copies do not pile up."""
        from huggingface_hub import CommitOperationAdd

        versions = {**self.versions, **{name: {"version": version, "report": report} for name, version, report in fresh}}
        (work / "versions.json").write_text(json.dumps(versions, indent=1), encoding="utf-8")
        operations = [CommitOperationAdd(f"sources/{name}.parquet", str(work / "sources" / f"{name}.parquet")) for name, _, _ in fresh]
        operations.append(CommitOperationAdd("versions.json", str(work / "versions.json")))
        try:
            self.api.create_commit(SOURCES_DATASET, repo_type="dataset", operations=operations, commit_message=f"{len(fresh)} sources read again")
            self.api.super_squash_history(SOURCES_DATASET, repo_type="dataset")
        except Exception as error:  # noqa: BLE001  The build is done; the next one reads these sources again.
            print(f"source cache: not kept: {error}", file=sys.stderr, flush=True)


def sends_on(url: str) -> bool:
    """Whether a board's posting page sends the applicant on to a company's own form: by its address after redirects, or a
    link in it to one of the forms AIHawk applies on. A page that cannot be read does not."""
    forms = [re.escape(host) for host, vendor in VENDOR_HOSTS if vendor not in AGGREGATORS]
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            seen = response.url + response.read(400_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001  A board that cannot be read keeps its posting.
        return False
    return re.search("|".join(forms), seen) is not None


def board_check(cache: SourceCache | None, work: Path) -> Callable[[list[tuple[str, str]]], set[str]]:
    """The postings, of (key, link), that send on to a form. Each page is opened once: the answers are kept in the cache."""

    def sent_on(postings: list[tuple[str, str]]) -> set[str]:
        known = cache.checks() if cache else {}
        new = [(key, url) for key, url in postings if key not in known]
        with ThreadPoolExecutor(16) as pool:
            known |= dict(zip((key for key, _ in new), pool.map(sends_on, (url for _, url in new)), strict=True))
        if cache and new:
            cache.keep_checks(known, work)
        return {key for key, _ in postings if known.get(key)}

    return sent_on


def read_all(connection: duckdb.DuckDBPyConnection, chosen: list[Source], work: Path, cache: SourceCache | None) -> list[dict]:
    """Every chosen source into work/sources, each one taken from the cache when its version has not changed since the
    last build, and read from its home otherwise. The reports, in the sources' order."""
    reports, fresh = [], []
    for source in chosen:
        version = version_of(source) if cache else None
        path = work / "sources" / f"{source.name}.parquet"
        started = time.monotonic()
        if cache and version and (kept := cache.take(source.name, version, path)) is not None:
            reports.append({**kept, "reused": True, "seconds": round(time.monotonic() - started)})
            print(f"{source.name}: unchanged since the last build, {kept.get('rows')} rows taken from the cache", flush=True)
            continue
        report = read_source(connection, source, work)
        reports.append(report)
        if cache and version and "error" not in report:
            fresh.append((source.name, version, report))
    if cache and fresh:
        cache.keep(fresh, work)
    return reports


def read_source(connection: duckdb.DuckDBPyConnection, source: Source, work: Path) -> dict[str, object]:
    """One source into work/sources/<name>.parquet. A failure is recorded, never raised: the others still run."""
    path = work / "sources" / f"{source.name}.parquet"
    started = time.monotonic()
    report: dict[str, object] = {"name": source.name, "home": source.home, "license": source.license}
    try:
        connection.execute(
            f"COPY ({source.sql(work)}) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"
        )
        report["rows"] = connection.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]
        report["seconds"] = round(time.monotonic() - started)
        print(f"{source.name}: {report['rows']} rows in {report['seconds']} s", flush=True)
    except Exception as error:  # noqa: BLE001  One source's failure must not lose the other sources.
        path.unlink(missing_ok=True)
        report["error"] = str(error)[:500]
        print(f"{source.name}: FAILED: {report['error']}", file=sys.stderr, flush=True)
    return report


def merge(connection: duckdb.DuckDBPyConnection, work: Path, sent_on: Callable[[list[tuple[str, str]]], set[str]] = lambda _: set()) -> dict[str, int]:
    """One row per posting, cut in files. Returns how many rows went in and came out.

    Which row wins is decided on the keys alone, and only then are the winners' rows, descriptions included, read and
    written: sorting 20 million descriptions would take more disk than a runner has.
    """
    inputs = sorted((work / "sources").glob("*.parquet"))
    if not inputs:
        raise SystemExit("no source could be read")
    files = [path.as_posix() for path in inputs]
    every_row = f"read_parquet({files!r}, filename = true, file_row_number = true)"
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE best AS
        SELECT key, count(*) AS copies, list_sort(list(DISTINCT source)) AS sources,
            -- The longest description wins. Copies that tie, most often two with none, are settled by the source's name
            -- and then the row: left to DuckDB, a tie went a different way in each build, and a third of the postings
            -- came out changed in a delta though nothing about them had.
            arg_max(
                struct_pack(filename := filename, row := file_row_number, length := length, source := source, print := print,
                    employer := vendor IS NOT NULL AND vendor NOT IN {AGGREGATORS!r}, host := host, vendor := vendor, url := url,
                    posted := posted),
                struct_pack(length := length, source := source, row := -file_row_number)
            ) AS winner
        FROM (
            SELECT {KEY} AS key, regexp_extract(filename, '([^/\\\\]+)\\.parquet$', 1) AS source, filename, file_row_number,
                length(coalesce(description, '')) AS length, vendor(ats, url) AS vendor, job_print(company, title, location) AS print,
                regexp_extract(posting_link(url), '^[^/]+') AS host, url,
                -- A date after the build is a source's mistake: the posting is as new as one posted now, and no newer.
                least(TRY_CAST(posted_at AS TIMESTAMP), now()::TIMESTAMP) AS posted
            FROM {every_row}
            -- No LinkedIn posting is kept, whatever source carried it: the owner's decision of 2026-11-08.
            WHERE url LIKE 'http%' AND coalesce(title, '') <> '' AND vendor(ats, url) IS DISTINCT FROM 'linkedin'
        ) GROUP BY key
    """)
    # One job under links of different sites, a company's form and a board's copy, has one print: the company, the title and
    # the city. The best of a print is kept, on a company's own form if there is one, then the longest description, then
    # the key; a copy on another site that is not a company's form is merged into it. Postings on the kept one's own site
    # stay: they are other openings (Accenture posts hundreds of one role in one city), and so do other companies' forms.
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE ranked AS
        SELECT key, winner.print AS print, winner.host AS host, winner.employer AS employer,
            row_number() OVER (PARTITION BY winner.print ORDER BY winner.employer DESC, winner.length DESC, winner.source, key) AS place
        FROM best WHERE winner.print IS NOT NULL
    """)
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE twins AS
        SELECT copy.key, first.key AS kept
        FROM ranked AS copy JOIN ranked AS first ON first.print = copy.print AND first.place = 1
        WHERE copy.place > 1 AND copy.host <> first.host AND NOT copy.employer
    """)
    # The sites that are fronts for companies' forms, a site being a board, or the host of a link with no vendor.
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE fronts AS
        SELECT site, postings, merged FROM (
            SELECT coalesce(posting.winner.vendor, posting.winner.host) AS site, count(*) AS postings,
                count(*) FILTER (WHERE kept.winner.employer) AS merged
            FROM best AS posting
            LEFT JOIN twins ON twins.key = posting.key LEFT JOIN best AS kept ON kept.key = twins.kept
            WHERE NOT posting.winner.employer GROUP BY 1
        ) WHERE postings >= {FRONT_LEAST} AND merged >= postings * {FRONT_SHARE}
    """)
    merged_away, lost = connection.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE kept.winner.length < {DESCRIBED} AND twin.winner.length >= {DESCRIBED})
        FROM twins JOIN best AS twin ON twin.key = twins.key JOIN best AS kept ON kept.key = twins.kept
    """).fetchone()
    print(f"one job under several sites: {merged_away} postings merged into another; {lost} of them described where the one kept is not", flush=True)
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE best AS
        SELECT best.key, best.copies + coalesce(merged.copies, 0) AS copies,
            list_sort(list_distinct(list_concat(best.sources, coalesce(merged.sources, [])))) AS sources, best.winner
        FROM best
        LEFT JOIN (
            SELECT twins.kept AS key, sum(other.copies) AS copies, flatten(list(other.sources)) AS sources
            FROM twins JOIN best AS other ON other.key = twins.key GROUP BY twins.kept
        ) AS merged ON merged.key = best.key
        WHERE best.key NOT IN (SELECT key FROM twins)
    """)
    fronts = connection.execute("SELECT site, postings, merged FROM fronts ORDER BY postings DESC").fetchall()
    connection.execute("DELETE FROM best WHERE NOT winner.employer AND coalesce(winner.vendor, winner.host) IN (SELECT site FROM fronts)")
    print(f"fronts for companies' forms: {len(fronts)} sites, {sum(row[1] - row[2] for row in fronts)} more postings go: "
          + ", ".join(f"{site} {merged}/{postings}" for site, postings, merged in fronts[:15]), flush=True)
    # A board's posting whose page sends the applicant on to a company's own form goes: `sent_on` names them.
    on_boards = connection.execute(f"SELECT key, winner.url FROM best WHERE winner.vendor IN {LINKING_BOARDS!r}").fetchall()
    sent = sent_on(on_boards)
    if sent:
        connection.execute("CREATE OR REPLACE TEMP TABLE sent AS SELECT unnest(?) AS key", [sorted(sent)])
        connection.execute("DELETE FROM best WHERE key IN (SELECT key FROM sent)")
    print(f"boards: {len(sent)} of {len(on_boards)} postings checked send the applicant on to a company's form, and go", flush=True)
    rows_in = connection.execute("SELECT sum(copies) FROM best").fetchone()[0]
    out = work / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # A remote posting that names no place says "Remote": the database has no column for it, and a search takes it for
    # one open to anyone, as it took one flagged remote with no place.
    unnamed = ", ".join(repr(word) for word in NO_PLACE)
    location = f"CASE WHEN row.remote AND coalesce(row.country, '') = '' AND lower(trim(coalesce(row.location, ''))) IN ({unnamed}) THEN 'Remote' ELSE row.location END"
    # Sorting ten million rows with their descriptions at once ran a runner out of memory, so only the keys are sorted: the
    # newest first, into chunks of CHUNK_ROWS, the described ones apart from the others; the rows go to their chunk's part in
    # one pass; and each part is sorted alone. A posting older than MAX_AGE_DAYS is not kept.
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE ranges AS
        SELECT key, CASE WHEN winner.length >= {DESCRIBED} THEN 0 ELSE {TITLES_PART} END
            + (row_number() OVER (
                PARTITION BY winner.length >= {DESCRIBED}
                ORDER BY coalesce(winner.posted, now()::TIMESTAMP - INTERVAL {UNDATED_DAYS} DAY) DESC, key
            ) - 1) // {CHUNK_ROWS} AS part
        FROM best
        WHERE winner.posted IS NULL OR winner.posted >= now()::TIMESTAMP - INTERVAL {MAX_AGE_DAYS} DAY
    """)
    parts = work / "tmp" / "parts"
    shutil.rmtree(parts, ignore_errors=True)
    connection.execute(f"""
        COPY (
            SELECT row.url, row.title, row.company, {location} AS location, country_code(row.country) AS country, best.winner.posted AS posted_at,
                CASE WHEN best.winner.length >= {DESCRIBED} THEN row.description END AS description, ranges.part
            FROM {every_row} AS row
            JOIN best ON row.filename = best.winner.filename AND row.file_row_number = best.winner.row
            JOIN ranges ON ranges.key = best.key
        ) TO '{parts.as_posix()}' (
            -- One open file a part, each holding a row group in memory: small row groups keep a hundred of them light.
            FORMAT parquet, COMPRESSION zstd, PARTITION_BY (part), ROW_GROUP_SIZE {ROW_GROUP}
        )
    """)
    folders = sorted(parts.glob("part=*"), key=lambda folder: int(folder.name.split("=")[1]))
    with ThreadPoolExecutor(CHUNK_THREADS) as pool:
        list(pool.map(lambda folder: write_chunk(folder, out), folders))
    rows_out = connection.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}/*.parquet')").fetchone()[0]
    expected = connection.execute("SELECT count(*) FROM ranges").fetchone()[0]
    if rows_out != expected:
        raise SystemExit(f"{expected} postings chosen and {rows_out} written")
    return {"rows_in": int(rows_in), "rows_out": rows_out}


def write_chunk(folder: Path, out: Path) -> None:
    """A part as a chunk: its postings newest first, in row groups of CHUNK_GROUP, and the part's folder gone."""
    import pyarrow.parquet as pq  # imported here: only the build writes these files

    part = int(folder.name.split("=")[1])
    name = f"titles_{part - TITLES_PART}.parquet" if part >= TITLES_PART else f"jobs_{part}.parquet"
    table = pq.read_table([path.as_posix() for path in sorted(folder.glob("*.parquet"))], columns=list(CHUNK_COLUMNS))
    table = table.sort_by([("posted_at", "descending"), ("url", "ascending")])
    # Statistics on the date alone: a search for the last 60 days skips the groups of older postings. Those of the other
    # columns made the footer ten times larger.
    pq.write_table(table, out / name, row_group_size=CHUNK_GROUP, compression="zstd", compression_level=CHUNK_LEVEL, write_statistics=["posted_at"])
    shutil.rmtree(folder)


def chunk_entry(connection: duckdb.DuckDBPyConnection, path: Path) -> dict[str, object]:
    """A chunk as the manifest names it: its size, its postings, the dates it spans, and whether its postings have no text."""
    rows, newest, oldest = connection.execute("SELECT count(*), max(posted_at), min(posted_at) FROM read_parquet(?)", [path.as_posix()]).fetchone()
    return {
        "name": path.name, "bytes": path.stat().st_size, "rows": rows, "titles_only": path.name.startswith("titles_"),
        "newest": newest.isoformat() if newest else None, "oldest": oldest.isoformat() if oldest else None,
    }


def chunk_order(path: Path) -> tuple[int, int]:
    """The order AIHawk downloads them in: the `jobs_N` chunks, then the `titles_N` ones, each the newest first."""
    kind, _, number = path.stem.partition("_")
    return (kind == "titles", int(number))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("work"))
    parser.add_argument("--only", nargs="*", help="Read only these sources.")
    parser.add_argument("--reuse", action="store_true", help="Keep a source already read in this work folder.")
    arguments = parser.parse_args()

    work: Path = arguments.out.resolve()
    (work / "sources").mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    connection = connect(work)
    started = time.monotonic()
    chosen = [source for source in SOURCES if not arguments.only or source.name in arguments.only]
    if arguments.reuse:
        kept = [source for source in chosen if (work / "sources" / f"{source.name}.parquet").exists()]
        reports = [{"name": source.name, "home": source.home, "license": source.license, "reused": True} for source in kept]
        reports += read_all(connection, [source for source in chosen if source not in kept], work, None)
    cache = None if arguments.reuse else SourceCache.open()
    if not arguments.reuse:
        reports = read_all(connection, chosen, work, cache)
    _clean_up(work)
    counts = merge(connection, work, board_check(cache, work))
    out = work / "out"
    manifest = {
        # 3: the database is a list of chunks of complete postings, the newest first, which AIHawk downloads one after the
        # other, and a delta of the postings added, changed or gone for each build.
        "schema": 3,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **counts,
        "seconds": round(time.monotonic() - started),
        "columns": list(CHUNK_COLUMNS),
        "chunks": [chunk_entry(connection, path) for path in sorted(out.glob("*.parquet"), key=chunk_order)],
        "sources": reports,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{counts['rows_in']} rows in, {counts['rows_out']} postings out, {len(manifest['chunks'])} chunks, {manifest['seconds']} s")


if __name__ == "__main__":
    main()
