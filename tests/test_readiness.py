"""Whether the browser can open on this computer: each cause found, and what a person is told for it."""

from __future__ import annotations

from pathlib import Path

import pytest

from aihawk import readiness
from aihawk.readiness import APT_LINE, browser_blockers


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A Linux x86-64 computer where the browser can open: a desktop, the four libraries, no restriction, a free profile."""
    monkeypatch.setattr(readiness, "PLATFORM", "linux")
    monkeypatch.setattr(readiness, "MACHINE", "x86_64")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(readiness.ctypes.util, "find_library", lambda name: f"lib{name}.so")
    monkeypatch.setattr(readiness, "RESTRICTED_USERNS", tmp_path / "no-such-file")
    monkeypatch.setattr(readiness, "profile_is_held", lambda profile: False)
    return tmp_path


def names(profile: Path | None = None) -> list[str]:
    return [blocker.name for blocker in browser_blockers(profile)]


def test_a_linux_computer_that_has_everything_has_no_cause(linux: Path) -> None:
    assert browser_blockers(linux / "profile") == []


def test_no_desktop_is_a_cause_and_wayland_alone_is_not_a_desktop_the_engine_can_draw_on(linux: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY")
    assert names() == ["no_display"]
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert names() == ["no_display"]
    monkeypatch.setenv("DISPLAY", "")
    assert names() == ["no_display"]
    assert browser_blockers()[0].words == (
        "AIHawk needs a desktop to open its browser. Open it from a terminal on the computer's own desktop, not over a remote connection."
    )


@pytest.mark.parametrize("missing", ["gtk-3", "asound", "X11-xcb", "dbus-glib-1"])
def test_each_of_the_four_libraries_missing_is_a_cause_with_the_packages_to_install(linux: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setattr(readiness.ctypes.util, "find_library", lambda name: None if name == missing else "lib.so")
    assert names() == ["missing_libraries"]
    assert browser_blockers()[0].words == f"AIHawk's browser needs a few system packages. Run this once in a terminal, then try again: {APT_LINE}"


def test_the_packages_are_one_line_that_works_on_ubuntu_24_04_and_before_it() -> None:
    # The names with t64 of Ubuntu 24.04 first, the names before it after.
    assert APT_LINE == (
        "sudo apt-get install -y libgtk-3-0t64 libasound2t64 libx11-xcb1 libdbus-glib-1-2"
        " || sudo apt-get install -y libgtk-3-0 libasound2 libx11-xcb1 libdbus-glib-1-2"
    )


def test_the_restriction_on_user_namespaces_is_a_cause_only_when_it_reads_one(linux: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flag = linux / "restrict"
    monkeypatch.setattr(readiness, "RESTRICTED_USERNS", flag)
    assert names() == [], "not readable: nothing known to stop it"
    flag.write_text("0\n", encoding="ascii")
    assert names() == []
    flag.write_text("1\n", encoding="ascii")
    assert names() == ["sandbox_blocked"]
    words = browser_blockers()[0].words
    assert words.startswith("Your computer blocks something AIHawk's browser needs to open. Run this once in a terminal, then try again: ")
    assert "kernel.apparmor_restrict_unprivileged_userns=0" in words


def test_the_profile_held_by_a_live_process_is_a_cause_only_when_a_profile_is_given(linux: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "profile_is_held", lambda profile: True)
    assert names() == [], "the engine's download asks without a profile"
    assert names(linux / "profile") == ["profile_locked"]
    assert browser_blockers(linux / "profile")[0].words == "Close the other AIHawk window or browser that is still open, then try again."


def test_the_profile_is_asked_of_the_servers_own_reading_of_its_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from invisible_playwright_mcp.mcp import work

    asked: list[Path] = []
    monkeypatch.setattr(work, "profile_holder", lambda directory: asked.append(directory) or "another Firefox (process 7)")
    assert readiness.profile_is_held(tmp_path) is True
    monkeypatch.setattr(work, "profile_holder", lambda directory: None)
    assert readiness.profile_is_held(tmp_path) is False
    assert asked == [tmp_path]


def test_every_cause_found_is_listed_in_order(linux: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flag = linux / "restrict"
    flag.write_text("1", encoding="ascii")
    monkeypatch.setattr(readiness, "RESTRICTED_USERNS", flag)
    monkeypatch.setattr(readiness, "profile_is_held", lambda profile: True)
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setattr(readiness.ctypes.util, "find_library", lambda name: None)
    assert names(linux) == ["no_display", "missing_libraries", "sandbox_blocked", "profile_locked"]


def test_a_mac_or_a_linux_of_another_kind_has_the_one_cause_that_the_browser_has_no_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "PLATFORM", "darwin")
    found = browser_blockers()
    assert [blocker.name for blocker in found] == ["no_browser_here"]
    assert found[0].words == "On a Mac AIHawk finds and scores jobs, but cannot send applications yet: its browser has no Mac version."
    monkeypatch.setattr(readiness, "PLATFORM", "linux")
    monkeypatch.setattr(readiness, "MACHINE", "aarch64")
    monkeypatch.delenv("DISPLAY", raising=False)
    found = browser_blockers()
    assert [blocker.name for blocker in found] == ["no_browser_here"], "nothing else is asked of a computer with no version"
    assert found[0].words == "On this computer AIHawk finds and scores jobs, but cannot send applications yet: its browser has no version for it."


def test_on_windows_nothing_is_asked_of_the_desktop_the_libraries_or_the_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(readiness, "PLATFORM", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(readiness.ctypes.util, "find_library", lambda name: None)
    monkeypatch.setattr(readiness, "profile_is_held", lambda profile: True)
    assert browser_blockers(tmp_path) == []
