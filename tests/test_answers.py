"""The answers: in the store's SQLite table, the old file brought in once, and the account password made once."""

from __future__ import annotations

import string
from pathlib import Path

from aihawk.config import load_config
from aihawk.profile import ACCOUNT_PASSWORD, PASSWORD_SYMBOLS, add_answers, ensure_account_password, import_answers_file, new_password, read_answers
from aihawk.store import Store


def test_answers_are_kept_in_the_store_and_the_persons_answer_wins_over_the_cv(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    store.add_answers({"email": "ada@example.com", "phone": "", "location": "Milan"}, source="cv")
    assert store.answers() == {"email": "ada@example.com", "location": "Milan"}
    store.add_answers({"email": "other@example.com", "website": "ada.example"}, source="cv")
    assert store.answers()["email"] == "ada@example.com" and store.answers()["website"] == "ada.example"
    store.set_answer("email", "new@example.com")
    store.set_answer("website", "  ")
    assert store.answers() == {"email": "new@example.com", "location": "Milan"}
    assert store.answers_text() == "email: new@example.com\nlocation: Milan"
    store.close()


def test_the_old_answers_file_is_brought_in_once_and_renamed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "answers.yaml").write_bytes(b"\xef\xbb\xbfemail: ada@example.com\nlocation: Milan\n")
    config = load_config(home)
    store = Store(config.store_file)
    store.set_answer("location", "Rome")
    assert import_answers_file(config, store) == 2
    assert store.answers() == {"location": "Rome", "email": "ada@example.com"}
    assert not (home / "answers.yaml").exists() and (home / "answers.yaml.imported").is_file()
    assert import_answers_file(config, store) == 0
    store.close()
    add_answers(config, {"phone": "+39 02"})
    assert read_answers(config) == {"location": "Rome", "email": "ada@example.com", "phone": "+39 02"}


def test_the_account_password_is_made_once_and_has_every_class_of_character(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    assert ensure_account_password(store) is True
    password = store.answers()[ACCOUNT_PASSWORD]
    assert ensure_account_password(store) is False and store.answers()[ACCOUNT_PASSWORD] == password
    for _ in range(20):
        made = new_password()
        assert len(made) == 20
        assert any(c in string.ascii_uppercase for c in made) and any(c in string.ascii_lowercase for c in made)
        assert any(c in string.digits for c in made) and any(c in PASSWORD_SYMBOLS for c in made)
    assert len({new_password() for _ in range(10)}) == 10
    store.close()
