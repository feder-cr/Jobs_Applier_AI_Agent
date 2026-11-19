"""Small wrappers around the operating system's credential store."""

from __future__ import annotations

from typing import Any

SERVICE = "aihawk-chatgpt"


class CredentialStoreUnavailable(RuntimeError):
    """No protected operating-system credential store is available."""


def _keyring(module: Any = None) -> Any:
    try:
        if module is None:
            import keyring as module
        backend = module.get_keyring()
        if float(getattr(backend, "priority", 0)) <= 0:
            raise CredentialStoreUnavailable("No secure operating-system credential store is available.")
        return module
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        raise CredentialStoreUnavailable("The operating-system credential store is unavailable.") from error


def get_secret(service: str, username: str, *, keyring_module: Any = None) -> str | None:
    try:
        return _keyring(keyring_module).get_password(service, username)
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        raise CredentialStoreUnavailable("Could not read from the operating-system credential store.") from error


def set_secret(service: str, username: str, password: str, *, keyring_module: Any = None) -> None:
    try:
        _keyring(keyring_module).set_password(service, username, password)
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        raise CredentialStoreUnavailable("Could not write to the operating-system credential store.") from error


def delete_secret(service: str, username: str, *, keyring_module: Any = None) -> None:
    try:
        _keyring(keyring_module).delete_password(service, username)
    except CredentialStoreUnavailable:
        raise
    except Exception as error:
        # Deleting a missing credential is harmless; callers first read when that distinction matters.
        if type(error).__name__ != "PasswordDeleteError":
            raise CredentialStoreUnavailable("Could not remove credentials from the operating-system credential store.") from error
