"""What nobody foresaw goes to errors.log, with its traceback and the time; the screen says only that something went wrong."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError

from aihawk import errors


@pytest.fixture(autouse=True)
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(errors, "folder", tmp_path)
    monkeypatch.setattr(errors, "secrets", [])
    return tmp_path


def raised(text: str) -> Exception:
    try:
        raise KeyError(text)
    except KeyError as error:
        return error


def test_an_unexpected_error_is_written_with_the_time_and_its_traceback(folder: Path) -> None:
    errors.record(raised("the detail"))
    text = (folder / "errors.log").read_text(encoding="utf-8")
    assert text.startswith("== 20") and "KeyError: 'the detail'" in text and "Traceback" in text and "in raised" in text


def test_a_key_is_masked_whether_it_looks_like_one_or_is_known_to_be_one(folder: Path) -> None:
    errors.secrets[:] = ["plainly-the-key-123"]
    errors.record(raised("Authorization: Bearer sk-orca-abcdef123456 and plainly-the-key-123"))
    text = (folder / "errors.log").read_text(encoding="utf-8")
    assert "sk-orca-abcdef123456" not in text and "plainly-the-key-123" not in text and "sk-…" in text


def test_the_log_is_kept_under_its_limit_by_dropping_the_oldest(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(errors, "LIMIT", 5_000)
    for number in range(40):
        errors.record(raised(f"entry {number:02d} " + "x" * 400))
    text = (folder / "errors.log").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 5_000
    assert "entry 39" in text and "entry 00" not in text and text.startswith("== ")


def test_a_single_entry_larger_than_the_limit_keeps_its_end(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(errors, "LIMIT", 2_000)
    errors.record(raised("big " + "y" * 10_000))
    assert 0 < len((folder / "errors.log").read_bytes()) <= 2_000


def test_a_log_that_cannot_be_written_raises_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(errors, "folder", blocked)
    errors.record(raised("lost"))


def test_a_known_error_is_said_in_words_and_not_logged_and_an_unknown_one_says_where_the_details_are(folder: Path) -> None:
    request = httpx.Request("POST", "https://api.orcarouter.ai/v1/chat/completions")
    assert errors.explained(APIConnectionError(request=request)) == "Cannot reach the AI service. Check your internet connection."
    assert not (folder / "errors.log").exists()
    assert errors.explained(raised("secret detail")) == "Something went wrong. Try again in a moment. Details are in errors.log."
    assert "secret detail" in (folder / "errors.log").read_text(encoding="utf-8")
