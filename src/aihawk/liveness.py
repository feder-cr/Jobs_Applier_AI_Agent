"""Whether a posting is still there, asked with one plain request before the browser is opened for it.

About one posting in six that the library lists has closed since it was published (measured on 2026-11-13, on about 650 postings
of the vendors where a plain request can tell, at every age). The browser would open it, spend its 90 seconds and its tokens, and
read "this job is no longer available". A plain request tells the same in under a second on the vendors below, each by the signal
the site itself gives, which was measured on real closed and open postings and, for Workday, confirmed in a real browser:

- Lever: the posting's address answers 404.
- Ashby: it answers 404 with a page titled "Jobs" (the board's own page); a 404 with another title is not read as closed.
- Greenhouse: it redirects to the board with `error=true`; else the board's public API answers 404 for the job.
- Workable: it redirects to `apply.workable.com/oops`.
- BambooHR: the job's own JSON, `/careers/<id>/detail`, answers 404 or redirects to bamboohr.com or to the list of jobs.
- Paylocity: it redirects to `/Recruiting/Jobs/JobNotFound`.
- Workday: the page is a script that draws itself, so the JSON the site reads is asked instead, and a closed job answers 403 with
  the error code S22.

Only a clear sign of "closed" counts. A site that blocks plain requests (iCIMS), one that draws everything with a script (Oracle,
ADP), every other vendor, a captcha, a 429, a timeout or any error means "unclear", and the application goes on as it always did.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx

from aihawk.apply import site_of
from aihawk.telemetry import vendor_of

OPEN, CLOSED, UNCLEAR = "open", "closed", "unclear"

# A browser's name: some of these sites answer a request with no name, or a library's, with a wall.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# The check must never hold an application up: the whole of it, both requests, ends within this many seconds.
TIMEOUT = 8.0
# How much of an answer is looked at: the title, an error code or the first keys of a JSON are all near its start.
LOOKED_AT = 20000

# The two boards whose jobs the public API at boards-api.greenhouse.io lists. The European boards have an API of their own,
# where this one answers 404 for a job that is open.
GREENHOUSE_BOARDS = ("boards.greenhouse.io", "job-boards.greenhouse.io")
# A Workday address is /<locale>/<site>/job/<path>, the locale (en-US, it-IT) being optional.
WORKDAY_PATH = re.compile(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+)$")
# What a person's "apply" button adds to a Workday job's address, which the job's own JSON does not have.
WORKDAY_APPLY = re.compile(r"/(?:apply|applyManually)/?$")


def title_of(text: str) -> str:
    """The text of the page's <title>, or empty."""
    found = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.DOTALL | re.IGNORECASE)
    return found.group(1).strip() if found else ""


def workday_api(url: str) -> str:
    """The address of the JSON the Workday page reads for this job, or empty when the address is not a Workday job's:
    `https://<tenant>.<...>.myworkdayjobs.com/[locale/]<site>/job/<path>` becomes `https://<host>/wday/cxs/<tenant>/<site>/job/<path>`,
    the tenant being the first label of the host."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host.endswith(".myworkdayjobs.com"):
        return ""
    found = WORKDAY_PATH.match(WORKDAY_APPLY.sub("", parts.path))
    if not found:
        return ""
    return f"https://{host}/wday/cxs/{host.split('.')[0]}/{found.group(1)}/job/{found.group(2)}"


def bamboohr_api(url: str) -> str:
    """The address of a BambooHR job's own JSON, on the same host, or empty when the address is not a job's."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    found = re.match(r"^/careers/(\d+)", parts.path)
    if not found or host == "www.bamboohr.com" or not host.endswith(".bamboohr.com"):
        return ""
    return f"https://{host}/careers/{found.group(1)}/detail"


def greenhouse_api(url: str) -> str:
    """The address of a Greenhouse job in the board's public API, or empty when the address is not a job of one of the boards."""
    parts = urlsplit(url)
    found = re.match(r"^/([^/]+)/jobs/(\d+)", parts.path)
    if not found or (parts.hostname or "").lower() not in GREENHOUSE_BOARDS:
        return ""
    return f"https://boards-api.greenhouse.io/v1/boards/{found.group(1)}/jobs/{found.group(2)}"


def classify(check: str, status: int, final_url: str, text: str = "") -> str:
    """What one answer says of the posting: "closed" for a clear sign of it, "open" for a clear sign that it is there, and
    "unclear" for anything else, which is never a reason to skip a job. `check` is which request was made: a vendor's name, or
    "greenhouse_api" for the second request to Greenhouse."""
    final = final_url.split("#")[0]
    path = urlsplit(final).path.rstrip("/")
    if check == "lever":
        return CLOSED if status == 404 else OPEN if status == 200 else UNCLEAR
    if check == "ashby":
        if status == 404:
            return CLOSED if title_of(text) == "Jobs" else UNCLEAR
        return OPEN if status == 200 else UNCLEAR
    if check == "greenhouse":
        if "error=true" in final:
            return CLOSED
        # A page that still holds its template's own placeholder was not drawn for a real job: the API is asked.
        return OPEN if status == 200 and "page_title" not in text else UNCLEAR
    if check == "greenhouse_api":
        return CLOSED if status == 404 else OPEN if status == 200 else UNCLEAR
    if check == "workable":
        if urlsplit(final).hostname == "apply.workable.com" and path == "/oops":
            return CLOSED
        return OPEN if status == 200 else UNCLEAR
    if check == "bamboohr":
        if status == 404 or urlsplit(final).hostname == "www.bamboohr.com" or path == "/careers":
            return CLOSED
        return OPEN if status == 200 and '"jobOpening"' in text else UNCLEAR
    if check == "paylocity":
        if "/recruiting/jobs/jobnotfound" in final.lower():
            return CLOSED
        return OPEN if status == 200 else UNCLEAR
    if check == "workday":
        if status == 200 and "jobPostingInfo" in text:
            return OPEN
        return CLOSED if status == 403 and _workday_error(text) == "S22" else UNCLEAR
    return UNCLEAR


def _workday_error(text: str) -> str:
    """The `errorCode` of Workday's JSON error, or empty."""
    try:
        body = json.loads(text)
    except ValueError:
        return ""
    return str(body.get("errorCode", "")) if isinstance(body, dict) else ""


def plan(url: str) -> tuple[str, str] | None:
    """The one request to make for a posting: which check it is, and the address to ask. None for a vendor with no sure sign."""
    host = site_of(url)
    if urlsplit(url).scheme not in ("http", "https") or not host:
        return None
    vendor = vendor_of(host)
    if vendor == "workday":
        return ("workday", api) if (api := workday_api(url)) else None
    if vendor == "bamboohr":
        return ("bamboohr", api) if (api := bamboohr_api(url)) else None
    if vendor in ("lever", "ashby", "greenhouse", "workable", "paylocity"):
        return vendor, url
    return None


async def _ask(client: httpx.AsyncClient, check: str, target: str) -> str:
    """One request, and what its answer says. The body is read whole by httpx, so only its start is looked at."""
    answer = await client.get(
        target, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5", "Accept-Language": "en-US,en;q=0.9"}
    )
    return classify(check, answer.status_code, str(answer.url), answer.text[:LOOKED_AT])


async def _verdict(url: str, client: httpx.AsyncClient) -> str:
    made = plan(url)
    if made is None:
        return UNCLEAR
    check, target = made
    verdict = await _ask(client, check, target)
    if check == "greenhouse" and verdict == UNCLEAR and (api := greenhouse_api(url)):
        verdict = await _ask(client, "greenhouse_api", api)
    return verdict


async def posting_is_closed(url: str, client: httpx.AsyncClient | None = None) -> bool:
    """True only when the posting is clearly closed. Anything else, an error, a timeout, a wall or a vendor with no sure sign,
    is False: the check must never stop an application, so it never raises. A client may be given (a test's); without one a
    fresh one is made, with no cookies kept past the check."""
    try:
        if client is not None:
            return await asyncio.wait_for(_verdict(url, client), TIMEOUT) == CLOSED
        if plan(url) is None:
            return False
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, max_redirects=5) as own:
            return await asyncio.wait_for(_verdict(url, own), TIMEOUT) == CLOSED
    except Exception:  # noqa: BLE001  A check that fails is not an answer: the application goes on as it always did.
        return False
