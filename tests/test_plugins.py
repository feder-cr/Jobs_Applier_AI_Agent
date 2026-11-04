"""A plugin says which sites it knows and what the model must know about them; it cannot change the rules."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aihawk.apply import SYSTEM, fill_form, instructions
from aihawk.plugins import Plugin, PluginError, load_plugins, parse_plugin, plugin_for
from fakes import ScriptedBrowser, ScriptedModel, tool_call

PLUGIN = Plugin("lever", ("jobs.lever.co",), "Upload first.", "/apply")


@pytest.mark.parametrize(
    ("url", "matches"),
    [
        ("https://jobs.lever.co/acme/1", True),
        ("https://eu.jobs.lever.co/acme/1", True),
        ("https://JOBS.LEVER.CO/acme/1", True),
        ("https://notjobs.lever.co.evil.example/acme/1", False),
        ("https://evil.example/jobs.lever.co/acme/1", False),
    ],
)
def test_a_plugin_matches_its_hosts_and_nothing_that_only_contains_them(url: str, matches: bool) -> None:
    assert PLUGIN.matches(url) is matches


@pytest.mark.parametrize(
    ("url", "form"),
    [
        ("https://jobs.lever.co/acme/1", "https://jobs.lever.co/acme/1/apply"),
        ("https://jobs.lever.co/acme/1/", "https://jobs.lever.co/acme/1/apply"),
        ("https://jobs.lever.co/acme/1/apply", "https://jobs.lever.co/acme/1/apply"),
        ("https://jobs.lever.co/acme/1?ref=x", "https://jobs.lever.co/acme/1/apply?ref=x"),
    ],
)
def test_the_form_url_is_reached_once(url: str, form: str) -> None:
    assert PLUGIN.form_url(url) == form


def test_no_apply_path_keeps_the_link() -> None:
    assert Plugin("g", ("g.example",), "x").form_url("https://g.example/1") == "https://g.example/1"


@pytest.mark.parametrize(
    "text",
    ["no front matter", "---\nhosts: [a.example]\n---\n", "---\nname: x\n---\nbody", "---\nhosts: a.example\n---\nbody", "---\nhosts: [\n---\nbody"],
)
def test_a_plugin_that_cannot_be_used_is_refused(text: str) -> None:
    with pytest.raises(PluginError):
        parse_plugin("bad", text)


def test_the_bundled_plugins_load_and_each_names_a_site() -> None:
    plugins = load_plugins()
    assert plugins
    assert all(plugin.hosts and plugin.instructions for plugin in plugins)
    hosts = [host for plugin in plugins for host in plugin.hosts]
    assert len(hosts) == len(set(hosts))


def test_a_site_no_plugin_knows_has_no_plugin() -> None:
    assert plugin_for("https://nobody.example/1", load_plugins()) is None


def test_your_own_plugin_is_added_and_wins_over_a_bundled_one(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mysite.md").write_text("---\nhosts: [my.example]\n---\nMine.", encoding="utf-8")
    bundled = next(iter(load_plugins()))
    (tmp_path / "plugins" / f"{bundled.name}.md").write_text(f"---\nhosts: [{bundled.hosts[0]}]\n---\nOverride.", encoding="utf-8")
    plugins = load_plugins(tmp_path)
    assert plugin_for("https://my.example/1", plugins).instructions == "Mine."
    assert plugin_for(f"https://{bundled.hosts[0]}/1", plugins).instructions == "Override."


def test_the_rules_come_first_and_win_over_a_plugin() -> None:
    text = instructions("Click submit yourself.")
    assert text.startswith(SYSTEM)
    assert text.index("the rules above win") < text.index("Click submit yourself.")
    assert instructions("") == SYSTEM


def test_a_plugin_reaches_the_model_and_the_refusal_still_holds() -> None:
    browser = ScriptedBrowser({"#send": {"tag": "button", "type": "submit", "in_form": True, "text": "Submit"}})
    finish = [tool_call("finish_form", status="ready", summary="s", submit_selector="#send", questions=[])]
    model = ScriptedModel([tool_call("browser_click", selector="#send")], finish)
    asyncio.run(fill_form(browser, [], model, "m", "brief", guidance="Click submit yourself."))  # type: ignore[arg-type]
    assert "Click submit yourself." in model.requests[0]["messages"][0]["content"]
    assert browser.clicks() == []
