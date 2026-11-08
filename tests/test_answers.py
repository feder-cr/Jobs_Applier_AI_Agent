"""The answers: in the store's SQLite table, the old file brought in once, and the account password made once."""

from __future__ import annotations

import string
from pathlib import Path

from aihawk.config import load_config
from aihawk.profile import ACCOUNT_PASSWORD, PASSWORD_SYMBOLS, ensure_account_password, import_answers_file, new_password
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
    store.add_answers({"phone": "+39 02"}, source="person")
    assert store.answers() == {"location": "Rome", "email": "ada@example.com", "phone": "+39 02"}
    store.close()


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


def test_the_account_password_is_sealed_on_disk_and_plain_when_read(tmp_path: Path) -> None:
    # Encrypted in the store with a key in its own file beside it; whoever reads jobs.db alone does not read the password.
    import sqlite3

    store = Store(tmp_path / "jobs.db")
    store.set_answer(ACCOUNT_PASSWORD, "Secret!9xWorkday")
    store.set_answer("email", "ada@example.com")
    assert store.answers()[ACCOUNT_PASSWORD] == "Secret!9xWorkday"
    assert "account_password: Secret!9xWorkday" in store.answers_text()
    store.close()
    raw = dict(sqlite3.connect(tmp_path / "jobs.db").execute("SELECT name, value FROM answers").fetchall())
    assert raw["email"] == "ada@example.com"
    assert raw[ACCOUNT_PASSWORD].startswith("sealed:") and "Secret!9x" not in raw[ACCOUNT_PASSWORD]
    assert (tmp_path / "secret.key").is_file()


def test_a_plain_password_left_by_an_older_store_is_sealed_on_its_first_read(tmp_path: Path) -> None:
    import sqlite3

    Store(tmp_path / "jobs.db").close()
    old = sqlite3.connect(tmp_path / "jobs.db")
    old.execute("INSERT INTO answers (name, value, source, updated) VALUES (?, ?, 'generated', 't')", (ACCOUNT_PASSWORD, "Plain!1pass"))
    old.commit()
    old.close()
    store = Store(tmp_path / "jobs.db")
    assert store.answers()[ACCOUNT_PASSWORD] == "Plain!1pass"
    store.close()
    raw = sqlite3.connect(tmp_path / "jobs.db").execute("SELECT value FROM answers WHERE name = ?", (ACCOUNT_PASSWORD,)).fetchone()[0]
    assert raw.startswith("sealed:")


def test_a_sealed_password_with_a_replaced_key_says_so(tmp_path: Path) -> None:
    import pytest

    from aihawk.secret import SecretError

    store = Store(tmp_path / "jobs.db")
    store.set_answer(ACCOUNT_PASSWORD, "Secret!9x")
    store.close()
    (tmp_path / "secret.key").write_bytes(b"ZmFrZWtleWZha2VrZXlmYWtla2V5ZmFrZWtleWZha2U=")
    with pytest.raises(SecretError, match="key file replaced"):
        Store(tmp_path / "jobs.db").answers()
