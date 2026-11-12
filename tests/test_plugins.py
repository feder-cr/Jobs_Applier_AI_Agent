"""A plugin says which sites it knows and what the model must know about them; it cannot change the rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from aihawk.apply import SYSTEM, instructions
from aihawk.plugins import Plugin, PluginError, always_plugins, load_plugins, parse_plugin, plugin_for

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


@pytest.mark.parametrize(
    "url",
    [
        "https://zahneinsgmbh.jobs.personio.com/job/2013915",
        "https://tierarztpluspartner.jobs.personio.de/job/2873557",
        "https://roman-klis-design-gmbh.jobs.personio.de/job/1789583",
        "https://partou.jobs.personio.de/job/2873902",
    ],
)
def test_a_personio_posting_goes_straight_to_its_form(url: str) -> None:
    plugin = plugin_for(url, load_plugins())
    assert plugin is not None and plugin.name == "personio"
    assert plugin.form_url(url) == url + "/apply"


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
    sites = [plugin for plugin in plugins if not plugin.always]
    assert sites
    assert all(plugin.hosts and plugin.instructions for plugin in sites)
    hosts = [host for plugin in sites for host in plugin.hosts]
    assert len(hosts) == len(set(hosts))


def test_the_mail_and_menu_plugins_apply_to_every_form_and_name_no_site() -> None:
    plugins = load_plugins()
    always = {plugin.name: plugin for plugin in always_plugins(plugins)}
    assert set(always) == {"gmail", "outlook", "menus"}
    assert all(plugin.hosts == () and plugin.instructions for plugin in always.values())
    assert plugin_for("https://mail.google.com/mail/u/0/", plugins) is None
    assert plugin_for("https://outlook.live.com/mail/", plugins) is None
    assert plugin_for("https://jobs.lever.co/acme/1", plugins).name == "lever"


def test_a_plugin_for_every_form_lists_no_hosts_and_names_a_known_when() -> None:
    always = parse_plugin("mail", "---\nwhen: always\n---\nRead the mailbox.")
    assert always.always and always.hosts == () and always.instructions == "Read the mailbox."
    with pytest.raises(PluginError):
        parse_plugin("mail", "---\nwhen: always\nhosts: [mail.example]\n---\nRead.")
    with pytest.raises(PluginError):
        parse_plugin("mail", "---\nwhen: sometimes\nhosts: [mail.example]\n---\nRead.")


def test_your_own_mail_plugin_wins_over_the_bundled_one(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "gmail.md").write_text("---\nwhen: always\n---\nMine.", encoding="utf-8")
    always = {plugin.name: plugin.instructions for plugin in always_plugins(load_plugins(tmp_path))}
    assert always["gmail"] == "Mine."
    assert set(always) == {"gmail", "outlook", "menus"}


def test_a_plugin_saved_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mysite.md").write_bytes(b"\xef\xbb\xbf---\nhosts: [my.example]\n---\nMine.")
    assert plugin_for("https://my.example/1", load_plugins(tmp_path)).instructions == "Mine."


def test_a_plugin_that_is_not_utf8_is_refused_by_its_name(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mysite.md").write_bytes(b"---\nhosts: [my.example]\n---\nCaf\xe9 instructions.")
    with pytest.raises(PluginError, match="plugin mysite.md: it is not UTF-8 text"):
        load_plugins(tmp_path)


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


def test_a_lever_send_that_leaves_for_the_companys_own_site_is_a_received_application() -> None:
    # Some boards send the browser to the company's website after the send, with the application's id in the address.
    lever = plugin_for("https://jobs.lever.co/acme/1", load_plugins())
    assert lever is not None and "LeverAppId=" in lever.instructions and "received application" in lever.instructions
