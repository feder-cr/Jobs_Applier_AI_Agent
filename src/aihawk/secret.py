"""The account password sealed at rest: encrypted in the store with a key kept in its own file, in the AIHawk folder.

A sealed value starts with SEALED, so a plain one from an older store is told apart and sealed the next time it is read.
The key is made the first time one is needed and is readable by its owner alone where the system allows it.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SEALED = "sealed:"


class SecretError(Exception):
    """A sealed value that its key cannot open: the key file was replaced or the value was altered."""


def _key(path: Path) -> bytes:
    if path.is_file():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Created readable by its owner alone, so the key is never on disk for anyone else, even for a moment.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key)
    return key


def is_sealed(value: str) -> bool:
    return value.startswith(SEALED)


def seal(value: str, key_file: Path) -> str:
    return SEALED + Fernet(_key(key_file)).encrypt(value.encode("utf-8")).decode("ascii")


def unseal(value: str, key_file: Path) -> str:
    """The plain value: a sealed one opened with the key, a plain one as it is."""
    if not is_sealed(value):
        return value
    try:
        return Fernet(_key(key_file)).decrypt(value.removeprefix(SEALED).encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise SecretError(f"the account password cannot be opened with {key_file}: was the key file replaced?") from error
