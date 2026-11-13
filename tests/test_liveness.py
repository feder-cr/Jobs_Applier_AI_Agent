"""The check that a posting is still there: what each vendor's answer says, and that nothing it meets ever stops an application."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from aihawk import liveness
from aihawk.liveness import CLOSED, OPEN, UNCLEAR, bamboohr_api, classify, greenhouse_api, plan, posting_is_closed, title_of, workday_api

WORKDAY_CLOSED = '{"errorCode":"S22","httpStatus":403,"messages":["The page you are looking for doesn\'t exist"]}'
WORKDAY_OPEN = '{"jobPostingInfo":{"id":"abc","title":"Engineer"},"hiringOrganization":{"name":"Acme"}}'
ASHBY_BOARD = "<html><head><title>Jobs</title></head><body>Acme</body></html>"


@pytest.mark.parametrize(
    ("check", "status", "final", "text", "verdict"),
    [
        ("lever", 404, "https://jobs.lever.co/acme/1", "", CLOSED),
        ("lever", 200, "https://jobs.lever.co/acme/1", "<title>Engineer</title>", OPEN),
        ("lever", 429, "https://jobs.lever.co/acme/1", "", UNCLEAR),
        ("lever", 500, "https://jobs.lever.co/acme/1", "", UNCLEAR),
        ("ashby", 404, "https://jobs.ashbyhq.com/acme/1", ASHBY_BOARD, CLOSED),
        ("ashby", 404, "https://jobs.ashbyhq.com/acme/1", "<title>Not found</title>", UNCLEAR),
        ("ashby", 404, "https://jobs.ashbyhq.com/acme/1", "", UNCLEAR),
        ("ashby", 200, "https://jobs.ashbyhq.com/acme/1", "<title>Engineer @ Acme</title>", OPEN),
        ("ashby", 403, "https://jobs.ashbyhq.com/acme/1", "", UNCLEAR),
        ("greenhouse", 200, "https://job-boards.greenhouse.io/acme?error=true", "<title>Acme</title>", CLOSED),
        ("greenhouse", 200, "https://boards.greenhouse.io/acme/jobs/1#app", "<title>Engineer</title>", OPEN),
        ("greenhouse", 200, "https://boards.greenhouse.io/acme/jobs/1", "<title>page_title</title>", UNCLEAR),
        ("greenhouse", 403, "https://boards.greenhouse.io/acme/jobs/1", "", UNCLEAR),
        ("greenhouse_api", 404, "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1", '{"error":"Job not found"}', CLOSED),
        ("greenhouse_api", 200, "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1", '{"id":1}', OPEN),
        ("greenhouse_api", 429, "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1", "", UNCLEAR),
        ("workable", 200, "https://apply.workable.com/oops", "", CLOSED),
        ("workable", 200, "https://apply.workable.com/oops/", "", CLOSED),
        ("workable", 200, "https://apply.workable.com/acme/j/ABC123/", "", OPEN),
        ("workable", 200, "https://other.example/oops", "", OPEN),
        ("workable", 503, "https://apply.workable.com/acme/j/ABC123/", "", UNCLEAR),
        ("bamboohr", 404, "https://acme.bamboohr.com/careers/7/detail", "", CLOSED),
        ("bamboohr", 200, "https://www.bamboohr.com/", "<title>BambooHR</title>", CLOSED),
        ("bamboohr", 200, "https://acme.bamboohr.com/careers", "", CLOSED),
        ("bamboohr", 200, "https://acme.bamboohr.com/careers/7/detail", '{"result":{"jobOpening":{"id":7}}}', OPEN),
        ("bamboohr", 200, "https://acme.bamboohr.com/careers/7/detail", "<html>a wall</html>", UNCLEAR),
        ("bamboohr", 500, "https://acme.bamboohr.com/careers/7/detail", "", UNCLEAR),
        ("paylocity", 200, "https://recruiting.paylocity.com/Recruiting/Jobs/JobNotFound?x=1", "", CLOSED),
        ("paylocity", 200, "https://recruiting.paylocity.com/recruiting/jobs/jobnotfound", "", CLOSED),
        ("paylocity", 200, "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123", "", OPEN),
        ("paylocity", 403, "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123", "", UNCLEAR),
        ("workday", 403, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", WORKDAY_CLOSED, CLOSED),
        ("workday", 200, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", WORKDAY_OPEN, OPEN),
        ("workday", 200, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", "<html>a stub</html>", UNCLEAR),
        # A 403 of any other kind is a wall, not a closed job.
        ("workday", 403, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", '{"errorCode":"S99"}', UNCLEAR),
        ("workday", 403, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", "<html>Access denied</html>", UNCLEAR),
        ("workday", 404, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/ext/job/x", "", UNCLEAR),
        ("a vendor with no sure sign", 404, "https://x.example/1", "", UNCLEAR),
    ],
)
def test_what_an_answer_says_of_the_posting(check: str, status: int, final: str, text: str, verdict: str) -> None:
    assert classify(check, status, final, text) == verdict


def test_the_title_is_read_from_the_page() -> None:
    assert title_of("<html><head><TITLE lang='en'>\n Jobs \n</TITLE>") == "Jobs"
    assert title_of("no title here") == ""


@pytest.mark.parametrize(
    ("url", "api"),
    [
        (
            "https://acme.wd5.myworkdayjobs.com/External/job/Milan/Engineer_R123",
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Milan/Engineer_R123",
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Milan/Engineer_R123",
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Milan/Engineer_R123",
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/it-IT/Careers/job/Italy---Milan/Senior-Engineer_JR-9/Extra?source=x#top",
            "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/job/Italy---Milan/Senior-Engineer_JR-9/Extra",
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Milan/Engineer_R123/apply",
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Milan/Engineer_R123",
        ),
        # Not a job's address: a company's list of jobs, another host, another Workday product.
        ("https://acme.wd5.myworkdayjobs.com/External", ""),
        ("https://acme.example.com/External/job/Milan/Engineer_R123", ""),
        ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Milan/Engineer_R123", ""),
    ],
)
def test_a_workday_address_becomes_the_json_the_site_reads(url: str, api: str) -> None:
    assert workday_api(url) == api


def test_the_other_vendors_addresses_for_their_second_look() -> None:
    assert bamboohr_api("https://acme.bamboohr.com/careers/42?x=1") == "https://acme.bamboohr.com/careers/42/detail"
    assert bamboohr_api("https://acme.bamboohr.com/careers") == ""
    assert bamboohr_api("https://www.bamboohr.com/careers/42") == ""
    assert greenhouse_api("https://boards.greenhouse.io/acme/jobs/99?gh_src=x") == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/99"
    assert greenhouse_api("https://job-boards.greenhouse.io/acme/jobs/99") == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/99"
    # The European boards have an API of their own, where this one would call an open job missing.
    assert greenhouse_api("https://job-boards.eu.greenhouse.io/acme/jobs/99") == ""
    assert greenhouse_api("https://boards.greenhouse.io/embed/job_app?for=acme&token=99") == ""


def test_only_the_vendors_with_a_sure_sign_are_asked() -> None:
    assert plan("https://jobs.lever.co/acme/1") == ("lever", "https://jobs.lever.co/acme/1")
    assert plan("https://jobs.eu.lever.co/acme/1") == ("lever", "https://jobs.eu.lever.co/acme/1")
    assert plan("https://jobs.ashbyhq.com/acme/1")[0] == "ashby"  # type: ignore[index]
    assert plan("https://apply.workable.com/acme/j/ABC/")[0] == "workable"  # type: ignore[index]
    assert plan("https://recruiting.paylocity.com/Recruiting/Jobs/Details/1")[0] == "paylocity"  # type: ignore[index]
    assert plan("https://acme.bamboohr.com/careers/5") == ("bamboohr", "https://acme.bamboohr.com/careers/5/detail")
    assert plan("https://acme.wd5.myworkdayjobs.com/External/job/x/y_1") == (
        "workday", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/x/y_1"
    )
    for url in (
        "https://acme.icims.com/jobs/1/engineer/job",  # a wall
        "https://jobs.smartrecruiters.com/Acme/1-engineer",
        "https://acme.applytojob.com/apply/abc/engineer",
        "https://careers.nobody.example/job/1",
        "file:///etc/passwd",
        "ftp://jobs.lever.co/acme/1",
        "",
    ):
        assert plan(url) is None, url


def client_for(answer: Callable[[httpx.Request], httpx.Response], asked: list[str] | None = None) -> httpx.AsyncClient:
    """A client whose every request is answered by `answer`, never by a network."""

    def handler(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(str(request.url))
        return answer(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def closed(url: str, answer: Callable[[httpx.Request], httpx.Response], asked: list[str] | None = None) -> bool:
    async def run() -> bool:
        async with client_for(answer, asked) as client:
            return await posting_is_closed(url, client)

    return asyncio.run(run())


def test_a_lever_posting_that_answers_404_is_closed_and_one_that_answers_200_is_not() -> None:
    url = "https://jobs.lever.co/acme/abc"
    assert closed(url, lambda request: httpx.Response(404, text="Not found")) is True
    assert closed(url, lambda request: httpx.Response(200, text="<title>Engineer</title>")) is False


def test_a_closed_greenhouse_job_is_known_by_its_redirect_to_the_board() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/acme/jobs/5":
            return httpx.Response(302, headers={"location": "https://job-boards.greenhouse.io/acme?error=true"})
        return httpx.Response(200, text="<title>Acme jobs</title>")

    asked: list[str] = []
    assert closed("https://job-boards.greenhouse.io/acme/jobs/5", answer, asked) is True
    assert asked == ["https://job-boards.greenhouse.io/acme/jobs/5", "https://job-boards.greenhouse.io/acme?error=true"]


def test_greenhouse_asks_its_api_only_when_the_page_cannot_say_and_never_more_than_twice() -> None:
    asked: list[str] = []

    def stub_then_gone(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(404, json={"error": "Job not found"})
        return httpx.Response(200, text="<title>page_title</title>")

    assert closed("https://boards.greenhouse.io/acme/jobs/5", stub_then_gone, asked) is True
    assert asked == ["https://boards.greenhouse.io/acme/jobs/5", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/5"]

    asked.clear()
    assert closed("https://boards.greenhouse.io/acme/jobs/5", lambda request: httpx.Response(200, text="<title>Engineer</title>"), asked) is False
    assert asked == ["https://boards.greenhouse.io/acme/jobs/5"]

    asked.clear()

    def stub_then_open(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 5}) if request.url.host == "boards-api.greenhouse.io" else httpx.Response(200, text="<title>page_title</title>")

    assert closed("https://boards.greenhouse.io/acme/jobs/5", stub_then_open, asked) is False
    assert len(asked) == 2


def test_a_closed_workday_job_is_asked_through_its_json_and_not_its_page() -> None:
    asked: list[str] = []
    url = "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Milan/Engineer_R1"
    assert closed(url, lambda request: httpx.Response(403, text=WORKDAY_CLOSED), asked) is True
    assert asked == ["https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Milan/Engineer_R1"]
    assert closed(url, lambda request: httpx.Response(200, text=WORKDAY_OPEN)) is False
    assert closed(url, lambda request: httpx.Response(403, text="<html>blocked</html>")) is False


def test_a_vendor_with_no_sure_sign_is_not_asked_at_all() -> None:
    asked: list[str] = []
    assert closed("https://acme.icims.com/jobs/1/engineer/job", lambda request: httpx.Response(404), asked) is False
    assert closed("https://careers.nobody.example/job/1", lambda request: httpx.Response(404), asked) is False
    assert asked == []


def test_a_timeout_a_lost_connection_a_wall_and_a_server_error_are_not_closed_and_never_raise() -> None:
    url = "https://jobs.lever.co/acme/abc"

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    def breaks(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("anything at all")

    assert closed(url, times_out) is False
    assert closed(url, refuses) is False
    assert closed(url, breaks) is False
    assert closed(url, lambda request: httpx.Response(429, text="Too many requests")) is False
    assert closed(url, lambda request: httpx.Response(500, text="Oops")) is False
    assert closed(url, lambda request: httpx.Response(403, text="Just a moment...")) is False


def test_a_check_that_takes_too_long_ends_and_says_not_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(liveness, "TIMEOUT", 0.05)

    async def run() -> bool:
        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(404)

        # A handler that waits: the whole check is cut off by its own deadline, whatever the client's timeouts say.
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            return await posting_is_closed("https://jobs.lever.co/acme/abc", client)

    assert asyncio.run(run()) is False


def test_without_a_client_one_is_made_with_the_check_s_own_limits_and_closed_afterwards(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[dict[str, object]] = []
    real = httpx.AsyncClient

    def fake(**options: object) -> httpx.AsyncClient:
        made.append(options)
        return real(transport=httpx.MockTransport(lambda request: httpx.Response(404)), **options)  # type: ignore[arg-type]

    monkeypatch.setattr(liveness.httpx, "AsyncClient", fake)
    assert asyncio.run(posting_is_closed("https://jobs.lever.co/acme/abc")) is True
    assert made == [{"timeout": 8.0, "follow_redirects": True, "max_redirects": 5}]
    # A vendor with no sure sign opens no client.
    made.clear()
    assert asyncio.run(posting_is_closed("https://careers.nobody.example/job/1")) is False
    assert made == []


def test_the_request_carries_a_browsers_name() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200)

    closed("https://jobs.lever.co/acme/abc", answer)
    assert seen and seen[0].startswith("Mozilla/5.0")
