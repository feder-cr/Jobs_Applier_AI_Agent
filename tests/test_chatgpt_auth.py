from __future__ import annotations

from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from aihawk.chatgpt_auth import (
    CREDENTIAL_KEY,
    SERVICE,
    AuthError,
    ChatGPTAuth,
    PlanUsageDisabled,
    authorization_url,
    callback_result,
    require_plan_scope,
    validate_id_token,
)
from aihawk.config import load_config
from aihawk.credentials import CredentialStoreUnavailable, get_secret, set_secret


def test_authorization_url_uses_dynamic_registration_and_plan_permission() -> None:
    url = authorization_url(
        "http://127.0.0.1:54321/auth/callback", "state", "nonce", "verifier", "urn:uuid:host"
    )
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["scope"] == ["openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"]
    assert query["ext_agent_host_id"] == ["urn:uuid:host"]
    assert query["code_challenge_method"] == ["S256"]
    assert "verifier" not in url


def test_callback_rejects_wrong_state() -> None:
    with pytest.raises(AuthError, match="state"):
        callback_result({"code": ["code"], "state": ["wrong"], "client_id": ["oaiapp_test"]}, "state")


def test_callback_requires_issued_client_id_for_new_registration() -> None:
    with pytest.raises(AuthError, match="client ID"):
        callback_result({"code": ["code"], "state": ["state"]}, "state")


def test_callback_rejects_a_different_issued_client_id_for_existing_account() -> None:
    with pytest.raises(AuthError, match="client ID"):
        callback_result(
            {"code": ["code"], "state": ["state"], "client_id": ["oaiapp_other"]},
            "state",
            expected_client_id="oaiapp_selected",
        )


def test_callback_treats_denied_plan_consent_as_disabled() -> None:
    with pytest.raises(PlanUsageDisabled):
        callback_result({"error": ["access_denied"], "state": ["state"]}, "state")


def test_callback_accepts_a_valid_issued_client() -> None:
    assert callback_result({"code": ["code"], "state": ["state"], "client_id": ["oaiapp_1"]}, "state") == {
        "code": "code", "client_id": "oaiapp_1", "scope": ""
    }


def test_identity_only_grant_cannot_be_used_for_inference() -> None:
    with pytest.raises(PlanUsageDisabled, match="plan usage"):
        require_plan_scope("openid profile email")


def test_id_token_claims_must_match_issuer_audience_and_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.chatgpt_auth as auth

    calls: dict[str, object] = {}

    def decode(token: str, key: object, **kwargs: object) -> dict[str, str]:
        calls.update(kwargs)
        return {"iss": "https://auth.openai.com", "sub": "account-1", "aud": "oaiapp_1", "nonce": "nonce-1"}

    monkeypatch.setattr(auth.jwt, "decode", decode)
    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda token: {"kid": "key-1"})
    monkeypatch.setattr(auth.jwt.PyJWK, "from_dict", lambda key: type("Jwk", (), {"key": "public"})())
    claims = validate_id_token("signed", "oaiapp_1", "nonce-1", {"keys": [{"kid": "key-1"}]})
    assert claims["sub"] == "account-1"
    assert calls["audience"] == "oaiapp_1"
    assert calls["issuer"] == "https://auth.openai.com"


def test_wrong_nonce_is_rejected_after_signature_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.chatgpt_auth as auth

    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda *args, **kwargs: {"iss": "https://auth.openai.com", "sub": "account-1", "aud": "oaiapp_1", "nonce": "other"},
    )
    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda token: {"kid": "key-1"})
    monkeypatch.setattr(auth.jwt.PyJWK, "from_dict", lambda key: type("Jwk", (), {"key": "public"})())
    with pytest.raises(AuthError, match="nonce"):
        validate_id_token("signed", "oaiapp_1", "nonce-1", {"keys": [{"kid": "key-1"}]})


def test_credentials_fail_closed_when_no_secure_backend_is_available() -> None:
    class NoBackend:
        class Backend:
            priority = 0

        @staticmethod
        def get_keyring() -> Backend:
            return NoBackend.Backend()

    with pytest.raises(CredentialStoreUnavailable):
        get_secret("service", "token", keyring_module=NoBackend)


def test_credentials_are_written_through_keyring() -> None:
    class FakeKeyring:
        class Backend:
            priority = 1

        values: ClassVar[dict[tuple[str, str], str]] = {}

        @staticmethod
        def get_keyring() -> Backend:
            return FakeKeyring.Backend()

        @staticmethod
        def set_password(service: str, username: str, password: str) -> None:
            FakeKeyring.values[(service, username)] = password

        @staticmethod
        def get_password(service: str, username: str) -> str | None:
            return FakeKeyring.values.get((service, username))

    set_secret("service", "token", "opaque-token", keyring_module=FakeKeyring)
    assert get_secret("service", "token", keyring_module=FakeKeyring) == "opaque-token"


def test_config_reads_chatgpt_connection_metadata(tmp_path) -> None:
    (tmp_path / "aihawk.yaml").write_text("chatgpt_host_id: urn:uuid:test\nchatgpt_email: person@example.com\n")
    config = load_config(tmp_path)
    assert config.chatgpt_host_id == "urn:uuid:test"
    assert config.chatgpt_email == "person@example.com"


def test_access_token_refreshes_and_replaces_rotating_refresh_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import aihawk.chatgpt_auth as auth

    class FakeKeyring:
        values: ClassVar[dict[tuple[str, str], str]] = {}
        class Backend:
            priority = 1
        @staticmethod
        def get_keyring() -> Backend:
            return FakeKeyring.Backend()
        @staticmethod
        def get_password(service: str, username: str) -> str | None:
            return FakeKeyring.values.get((service, username))
        @staticmethod
        def set_password(service: str, username: str, password: str) -> None:
            FakeKeyring.values[(service, username)] = password
        @staticmethod
        def delete_password(service: str, username: str) -> None:
            FakeKeyring.values.pop((service, username), None)

    class Response:
        def raise_for_status(self) -> None:
            pass
        @staticmethod
        def json() -> dict[str, object]:
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

    class Http:
        @staticmethod
        def post(*args: object, **kwargs: object) -> Response:
            return Response()

    FakeKeyring.values[(SERVICE, CREDENTIAL_KEY)] = '{"client_id":"oaiapp_1","subject":"s","access_token":"old",' \
        '"refresh_token":"refresh","expires_at":0,"scopes":["chatgpt.tokens.use.direct"]}'
    monkeypatch.setattr(auth.time, "time", lambda: 1000.0)
    client = ChatGPTAuth(tmp_path, keyring_module=FakeKeyring, http=Http)
    assert client.access_token() == "new-access"
    assert '"refresh_token": "new-refresh"' in FakeKeyring.values[(SERVICE, CREDENTIAL_KEY)]


def test_disconnect_revokes_and_clears_credentials(tmp_path) -> None:
    from aihawk.chatgpt_auth import ChatGPTAuth

    class FakeKeyring:
        values: ClassVar[dict[tuple[str, str], str]] = {}
        class Backend:
            priority = 1
        @staticmethod
        def get_keyring() -> Backend:
            return FakeKeyring.Backend()
        @staticmethod
        def get_password(service: str, username: str) -> str | None:
            return FakeKeyring.values.get((service, username))
        @staticmethod
        def set_password(service: str, username: str, password: str) -> None:
            FakeKeyring.values[(service, username)] = password
        @staticmethod
        def delete_password(service: str, username: str) -> None:
            FakeKeyring.values.pop((service, username), None)

    class Response:
        @staticmethod
        def json() -> dict[str, str]:
            return {"revocation_endpoint": "https://auth.openai.com/revoke"}

    class Http:
        posts: ClassVar[list[tuple[object, object]]] = []
        @staticmethod
        def get(*args: object, **kwargs: object) -> Response:
            return Response()
        @staticmethod
        def post(url: object, **kwargs: object) -> None:
            Http.posts.append((url, kwargs["data"]))

    FakeKeyring.values[(SERVICE, CREDENTIAL_KEY)] = '{"client_id":"oaiapp_1","refresh_token":"refresh"}'
    client = ChatGPTAuth(tmp_path, keyring_module=FakeKeyring, http=Http)
    client.disconnect()
    assert Http.posts[0][0] == "https://auth.openai.com/revoke"
    assert (SERVICE, CREDENTIAL_KEY) not in FakeKeyring.values
