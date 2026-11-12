"""What AIHawk tells its makers: one anonymous id, nothing about the person or the job, and only the events it declares."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aihawk import session as session_module
from aihawk import telemetry
from aihawk.apply import Outcome
from aihawk.database import Job
from aihawk.llm import Spend
from aihawk.session import Session
from aihawk.telemetry import EVENTS, KNOWN_SITES, Telemetry, install_id, vendor_of
from fakes import ROOT, load_script

SOURCE = ROOT / "src" / "aihawk"
SENDING = telemetry.send


def test_the_id_is_made_once_and_kept_with_the_data(tmp_path: Path) -> None:
    first = install_id(tmp_path / "data")
    assert install_id(tmp_path / "data") == first
    assert (tmp_path / "data" / "install_id").read_text(encoding="utf-8") == first


def test_an_id_that_cannot_be_kept_is_made_again_each_time(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("a file where the folder should be", encoding="utf-8")
    assert install_id(tmp_path / "data") != install_id(tmp_path / "data")


def test_every_event_has_the_one_id_the_version_and_no_person_profile(tmp_path: Path, tracked: list[dict[str, Any]]) -> None:
    run = Telemetry(tmp_path)
    run.started()
    run.search(found=3, new=2)
    assert [event["event"] for event in tracked] == ["app_started", "search"]
    assert {event["distinct_id"] for event in tracked} == {install_id(tmp_path)}
    for event in tracked:
        assert event["api_key"] == telemetry.PROJECT_KEY
        assert event["properties"]["$process_person_profile"] is False
        # PostHog must not look up where the sender's address is: that would give the city.
        assert event["properties"]["$geoip_disable"] is True
        assert event["properties"]["version"] and event["properties"]["os"]


def test_every_kind_of_event_asks_posthog_not_to_look_up_the_address(tmp_path: Path, tracked: list[dict[str, Any]]) -> None:
    run = Telemetry(tmp_path)
    run.started()
    run.step("key_asked")
    run.search(found=1, new=1)
    run.scored(postings=1, answered=1, seconds=0.5, scoring_model="m")
    run.application("submitted", "sent", 3, 1.0, None, "jobs.lever.co", "f", "s", {})
    run.closed()
    assert {event["event"] for event in tracked} == set(EVENTS)
    assert all(event["properties"]["$geoip_disable"] is True for event in tracked)


def test_the_start_carries_the_country_the_computer_is_set_to_and_no_other_place(
    tmp_path: Path, tracked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry, "computer_country", lambda: "GB")
    Telemetry(tmp_path).started()
    assert tracked[0]["properties"]["country"] == "GB"
    assert not {"city", "$geoip_city_name", "$ip"} & set(tracked[0]["properties"])


@pytest.mark.parametrize(
    ("name", "region"),
    [("en_GB.UTF-8", "GB"), ("it_IT", "IT"), ("C", ""), ("POSIX", ""), ("", ""), ("en", ""), ("pt-BR", "BR"), ("de_DE@euro", "DE"),
     ("zh-Hans-CN", "CN"), ("zh-Hans", ""), ("es-419", ""), ("en_gb", "GB"), ("C.UTF-8", "")],
)  # fmt: skip
def test_the_country_is_the_region_of_a_locale_name_when_it_has_one(name: str, region: str) -> None:
    assert telemetry.region_of(name) == region


def test_the_country_of_a_mac_or_linux_comes_from_the_first_locale_variable_that_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry.sys, "platform", "linux")
    for variable in ("LC_ALL", "LC_CTYPE", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(telemetry.locale, "getlocale", lambda *_: (None, None))
    assert telemetry.computer_country() == ""
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    assert telemetry.computer_country() == "IT"
    monkeypatch.setenv("LC_CTYPE", "en_GB.UTF-8")
    assert telemetry.computer_country() == "GB"
    monkeypatch.setenv("LC_ALL", "pt_BR.UTF-8")
    assert telemetry.computer_country() == "BR"
    # "C" says no country, and the program's own locale is asked next.
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setattr(telemetry.locale, "getlocale", lambda *_: ("fr_FR", "UTF-8"))
    assert telemetry.computer_country() == "FR"
    monkeypatch.setattr(telemetry.locale, "getlocale", lambda *_: (None, None))
    assert telemetry.computer_country() == ""


def test_the_country_is_empty_and_never_an_error_when_the_computer_cannot_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any) -> Any:
        raise ValueError("unknown locale")

    monkeypatch.setattr(telemetry.sys, "platform", "darwin")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setattr(telemetry.locale, "getlocale", broken)
    assert telemetry.computer_country() == ""
    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    monkeypatch.setattr(telemetry, "windows_country", broken)
    assert telemetry.computer_country() == ""


def test_windows_is_asked_for_its_country_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(telemetry, "windows_country", lambda: asked.append("windows") or "IE")
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    monkeypatch.setattr(telemetry.sys, "platform", "linux")
    assert telemetry.computer_country() == "IT" and asked == []
    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    assert telemetry.computer_country() == "IE" and asked == ["windows"]


def test_whatever_the_computer_is_the_country_is_two_capital_letters_or_nothing() -> None:
    assert re.fullmatch(r"[A-Z]{2}|", telemetry.computer_country())


def test_a_dry_run_sends_nothing_and_leaves_no_id(tmp_path: Path, tracked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIHAWK_DRY_RUN", "1")
    Telemetry(tmp_path).started()
    assert tracked == [] and not (tmp_path / "install_id").exists()


def test_a_request_that_fails_is_lost_and_nothing_else_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    SENDING({"event": "app_started"})


def test_the_step_a_start_reached_is_what_closing_reports(tmp_path: Path, tracked: list[dict[str, Any]]) -> None:
    run = Telemetry(tmp_path)
    run.step("key_asked")
    run.step("resume_failed", error="ConfigError", kind=".pdf")
    run.closed()
    steps = [event["properties"] for event in tracked if event["event"] == "start_step"]
    assert [(step["step"], step.get("error"), step.get("kind")) for step in steps] == [("key_asked", None, None), ("resume_failed", "ConfigError", ".pdf")]
    assert tracked[-1]["properties"]["last_step"] == "resume_failed" and tracked[-1]["properties"]["searched"] is False


def test_the_site_is_the_plugins_name_or_the_vendor_of_a_known_host_or_other_and_never_the_host(tmp_path: Path, tracked: list[dict[str, Any]]) -> None:
    run = Telemetry(tmp_path)
    tokens = {"m": (5, 2)}
    run.application("submitted", "", 3, 1.0, "lever", "jobs.lever.co", "f", "s", tokens)
    run.application("blocked", "captcha", 3, 1.0, None, "acme.wd5.myworkdayjobs.com", "f", "s", tokens)
    run.application("blocked", "closed", 3, 1.0, None, "careers.acme-corp.example", "f", "s", tokens)
    sites = [(event["properties"]["site"], event["properties"]["plugin"]) for event in tracked]
    assert sites == [("lever", True), ("workday", False), ("other", False)]
    assert "acme" not in json.dumps(tracked)


@pytest.mark.parametrize(
    ("host", "vendor"),
    [("boards.greenhouse.io", "greenhouse"), ("ACME.WD3.MYWORKDAYJOBS.COM", "workday"), ("jobs.example.org", "other"), ("www.linkedin.com", "other"), ("", "other")],
)
def test_a_vendor_is_named_by_a_fragment_of_the_host(host: str, vendor: str) -> None:
    assert vendor_of(host) == vendor


def test_every_known_site_is_a_form_vendor_of_the_pipeline_with_the_same_name() -> None:
    build = load_script("pipeline", "build")
    for fragment, vendor in KNOWN_SITES:
        assert (fragment, vendor) in build.VENDOR_HOSTS
        assert vendor not in build.AGGREGATORS


def test_every_event_and_property_the_code_sends_is_declared_and_every_declared_one_is_sent() -> None:
    sent: dict[str, set[str]] = {}
    for path in SOURCE.glob("*.py"):
        for call in (node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Attribute):
                continue
            named = {keyword.arg for keyword in call.keywords if keyword.arg}
            if call.func.attr == "track":
                event = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
                assert event in EVENTS, f"{path.name}:{call.lineno} sends an event that is not declared: {event!r}"
                sent.setdefault(event, set()).update(named)
            elif call.func.attr == "step" and getattr(call.func.value, "attr", getattr(call.func.value, "id", "")) == "telemetry":
                sent.setdefault("start_step", set()).update(named)
    assert set(sent) == set(EVENTS)
    for event, properties in sent.items():
        assert properties <= set(EVENTS[event]), f"{event} sends {sorted(properties - set(EVENTS[event]))}, which are not declared"
        assert set(EVENTS[event]) <= properties, f"{event} declares {sorted(set(EVENTS[event]) - properties)}, which nothing sends"


def test_each_application_reports_only_the_tokens_it_used_and_the_models_that_took_them(
    tmp_path: Path, tracked: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cv.md").write_text("# Ada Rossi\n", encoding="utf-8")
    (tmp_path / "data" / "cvs").mkdir(parents=True)
    (tmp_path / "data" / "cvs" / "original-cv.txt").write_text("Ada Rossi", encoding="utf-8")
    session = Session(tmp_path)
    session._client = SimpleNamespace()  # type: ignore[assignment]
    spend = Spend()
    spend.loaded = True
    monkeypatch.setattr(session_module, "SPEND", spend)
    # What each form's models took in and gave out, call by call.
    calls = iter([[("forms", 100, 10), ("check", 5, 1), ("forms", 50, 5)], [("forms", 7, 3)]])

    async def apply_to_job(*_: Any, **__: Any) -> Outcome:
        for model, taken, given in next(calls):
            spend.note(model, SimpleNamespace(prompt_tokens=taken, completion_tokens=given))
        return Outcome("submitted", "sent", steps=2)

    monkeypatch.setattr(session_module, "apply_to_job", apply_to_job)
    jobs = [Job(f"https://jobs.lever.co/acme/{number}", "Acme", "Engineer", "Rome", f"https://jobs.lever.co/acme/{number}", "a", "") for number in (1, 2)]
    session.store.add_all(jobs)
    for job in jobs:
        item = session.store.get(job.id)
        assert item is not None
        asyncio.run(session.apply(item, lambda line: None))
    first, second = [event["properties"] for event in tracked if event["event"] == "application"]
    assert (first["models"], first["input_tokens"], first["output_tokens"]) == (["check", "forms"], 155, 16)
    assert first["tokens_by_model"] == {"forms": {"input": 150, "output": 15}, "check": {"input": 5, "output": 1}}
    assert (second["models"], second["input_tokens"], second["output_tokens"]) == (["forms"], 7, 3)
    assert second["tokens_by_model"] == {"forms": {"input": 7, "output": 3}}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own settings")
def test_windows_tells_a_country_of_two_capital_letters() -> None:
    assert re.fullmatch(r"[A-Z]{2}", telemetry.windows_country())
