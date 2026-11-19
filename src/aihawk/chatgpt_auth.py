"""Sign in with ChatGPT for a local, user-controlled AIHawk installation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from aihawk.credentials import SERVICE, CredentialStoreUnavailable, delete_secret, get_secret, set_secret

AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
OIDC_CONFIG = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
HOST_ID_KEY = "chatgpt_host_id"
EMAIL_KEY = "chatgpt_email"
CREDENTIAL_KEY = "active-account"
CALLBACK_PATH = "/auth/callback"


class AuthError(RuntimeError):
    """The ChatGPT sign-in could not be completed safely."""


class PlanUsageDisabled(AuthError):
    """The account did not grant ChatGPT plan usage permission."""


def host_id() -> str:
    """Return a new opaque host identifier in an OpenAI-supported format."""
    return f"urn:uuid:{uuid.uuid4()}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(redirect_uri: str, state: str, nonce: str, verifier: str, host: str, client_id: str | None = None, id_token_hint: str = "") -> str:
    params = {
        "client_id": client_id or "dynamic_agent_client",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": _challenge(verifier),
        "ext_agent_host_id": host,
    }
    if client_id is None:
        params["agent_name_hint"] = "AIHawk"
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return AUTHORIZATION_ENDPOINT + "?" + urlencode(params)


def callback_result(query: dict[str, list[str]], expected_state: str, expected_client_id: str | None = None) -> dict[str, str]:
    """Validate an OAuth loopback callback and return its code, issued client, and optional scopes."""
    state = (query.get("state") or [""])[0]
    if not state or not secrets.compare_digest(state, expected_state):
        raise AuthError("ChatGPT sign-in returned an invalid state value.")
    if (query.get("error") or [""])[0] == "access_denied":
        raise PlanUsageDisabled("ChatGPT plan usage was not approved. Connect again and approve plan usage to use it.")
    code = (query.get("code") or [""])[0]
    client = (query.get("client_id") or [expected_client_id or ""])[0]
    if not code:
        raise AuthError("ChatGPT sign-in did not return an authorization code.")
    if not client or client == "dynamic_agent_client":
        raise AuthError("ChatGPT sign-in did not return an issued client ID.")
    if expected_client_id and client != expected_client_id:
        raise AuthError("ChatGPT sign-in returned a different client ID than the selected account.")
    return {"code": code, "client_id": client, "scope": (query.get("scope") or [""])[0]}


def require_plan_scope(scopes: str | list[str]) -> None:
    granted = scopes.split() if isinstance(scopes, str) else scopes
    if PLAN_SCOPE not in granted:
        raise PlanUsageDisabled("ChatGPT plan usage permission was not granted. Enable plan usage in ChatGPT settings, then connect again.")


def validate_id_token(token: str, client_id: str, nonce: str, jwks: dict[str, Any], issuer: str = "https://auth.openai.com") -> dict[str, Any]:
    """Verify the identity token's signature and binding to this OAuth attempt."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        jwk = next(key for key in jwks.get("keys", []) if key.get("kid") == kid)
        claims = jwt.decode(token, jwt.PyJWK.from_dict(jwk).key, algorithms=["RS256"], audience=client_id, issuer=issuer)
    except Exception as error:
        raise AuthError("ChatGPT sign-in returned an invalid identity token.") from error
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AuthError("ChatGPT sign-in returned an identity token for a different sign-in attempt (nonce mismatch).")
    if not claims.get("sub"):
        raise AuthError("ChatGPT sign-in returned an identity token without an account ID.")
    return claims


def _config_values(workspace: Path) -> dict[str, Any]:
    import yaml

    path = workspace / "aihawk.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError) as error:
        raise AuthError("Could not read AIHawk settings for ChatGPT sign-in.") from error
    return raw if isinstance(raw, dict) else {}


def _save_config(workspace: Path, updates: dict[str, Any], removals: tuple[str, ...] = ()) -> None:
    import yaml

    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "aihawk.yaml"
    raw = _config_values(workspace)
    for key in removals:
        raw.pop(key, None)
    raw.update(updates)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _callback_listener() -> tuple[HTTPServer, str, threading.Event, dict[str, list[str]]]:
    finished = threading.Event()
    result: dict[str, list[str]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_error(404)
                return
            result.update(parse_qs(parsed.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>AIHawk connected. You can close this tab.</h2></body></html>")
            finished.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.5
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    return server, redirect_uri, finished, result


class ChatGPTAuth:
    """Own the selected local account's credentials and OAuth lifecycle."""

    def __init__(self, workspace: Path, *, keyring_module: Any = None, http: Any = httpx, open_browser: Any = webbrowser.open) -> None:
        self.workspace = workspace
        self.keyring_module = keyring_module
        self.http = http
        self.open_browser = open_browser

    def _credentials(self) -> dict[str, Any] | None:
        raw = get_secret(SERVICE, CREDENTIAL_KEY, keyring_module=self.keyring_module)
        return json.loads(raw) if raw else None

    @property
    def connected(self) -> bool:
        return bool(self._credentials())

    @property
    def email(self) -> str:
        return str(_config_values(self.workspace).get(EMAIL_KEY) or "")

    def connect(self, timeout: float = 300.0) -> str:
        """Open the system browser, validate the callback and save the token set securely."""
        credentials = self._credentials()
        host = str(_config_values(self.workspace).get(HOST_ID_KEY) or host_id())
        _save_config(self.workspace, {HOST_ID_KEY: host})
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        server, redirect_uri, finished, callback = _callback_listener()
        client_id = credentials.get("client_id") if credentials else None
        hint = credentials.get("id_token", "") if credentials else ""
        url = authorization_url(redirect_uri, state, nonce, verifier, host, client_id, hint)
        try:
            self.open_browser(url)
            deadline = time.monotonic() + timeout
            while not finished.is_set() and time.monotonic() < deadline:
                server.handle_request()
            if not finished.is_set():
                raise AuthError("Timed out waiting for ChatGPT sign-in in the browser.")
            result = callback_result(callback, state, client_id)
            token_response = self.http.post(
                TOKEN_ENDPOINT,
                data={"grant_type": "authorization_code", "client_id": result["client_id"], "code": result["code"],
                      "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE},
                timeout=20.0,
            )
            token_response.raise_for_status()
            data = token_response.json()
            scopes = data.get("scope") or result["scope"]
            require_plan_scope(scopes)
            oidc = self.http.get(OIDC_CONFIG, timeout=15.0).json()
            jwks = self.http.get(oidc["jwks_uri"], timeout=15.0).json()
            claims = validate_id_token(data["id_token"], result["client_id"], nonce, jwks, oidc["issuer"])
            if credentials and claims["sub"] != credentials.get("subject"):
                raise AuthError("ChatGPT sign-in resolved to a different account; existing credentials were kept.")
            token_set = {
                "client_id": result["client_id"], "subject": claims["sub"], "email": claims.get("email", ""),
                "id_token": data["id_token"], "access_token": data["access_token"], "refresh_token": data.get("refresh_token", ""),
                "expires_at": time.time() + int(data.get("expires_in", 3600)), "scopes": scopes.split() if isinstance(scopes, str) else scopes,
            }
            set_secret(SERVICE, CREDENTIAL_KEY, json.dumps(token_set), keyring_module=self.keyring_module)
            _save_config(self.workspace, {EMAIL_KEY: claims.get("email", "")})
            return str(claims.get("email") or "ChatGPT account")
        except CredentialStoreUnavailable:
            raise
        except AuthError:
            raise
        except Exception as error:
            raise AuthError("ChatGPT sign-in failed. Check the connection and try again.") from error
        finally:
            server.server_close()

    def access_token(self) -> str:
        credentials = self._credentials()
        if not credentials:
            raise AuthError("Connect a ChatGPT account before using its plan.")
        require_plan_scope(credentials.get("scopes", []))
        if float(credentials.get("expires_at", 0)) > time.time() + 60:
            return str(credentials["access_token"])
        try:
            response = self.http.post(TOKEN_ENDPOINT, data={
                "grant_type": "refresh_token", "client_id": credentials["client_id"],
                "refresh_token": credentials["refresh_token"], "resource": RESOURCE,
            }, timeout=20.0)
            response.raise_for_status()
            refreshed = response.json()
            require_plan_scope(refreshed.get("scope") or credentials["scopes"])
            credentials.update({"access_token": refreshed["access_token"], "expires_at": time.time() + int(refreshed.get("expires_in", 3600)),
                                "refresh_token": refreshed.get("refresh_token", credentials["refresh_token"]),
                                "scopes": (refreshed.get("scope") or credentials["scopes"]).split() if isinstance(refreshed.get("scope"), str) else (refreshed.get("scope") or credentials["scopes"])})
            set_secret(SERVICE, CREDENTIAL_KEY, json.dumps(credentials), keyring_module=self.keyring_module)
            return str(credentials["access_token"])
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "response", None)
            if code is not None and getattr(code, "status_code", 0) == 400:
                delete_secret(SERVICE, CREDENTIAL_KEY, keyring_module=self.keyring_module)
                _save_config(self.workspace, {}, removals=(EMAIL_KEY,))
                raise AuthError("ChatGPT sign-in expired. Connect the account again.") from None
            raise AuthError("Could not refresh the ChatGPT connection. Try again shortly.") from None

    def disconnect(self) -> None:
        credentials = self._credentials()
        if credentials and credentials.get("refresh_token"):
            try:
                discovery = self.http.get(OIDC_CONFIG, timeout=10.0).json()
                self.http.post(discovery["revocation_endpoint"], data={
                    "token": credentials["refresh_token"], "token_type_hint": "refresh_token", "client_id": credentials["client_id"],
                }, timeout=10.0)
            except Exception:  # noqa: BLE001, S110
                pass  # Local sign-out still clears access; the user can revoke it from ChatGPT Settings if unreachable.
        delete_secret(SERVICE, CREDENTIAL_KEY, keyring_module=self.keyring_module)
        _save_config(self.workspace, {}, removals=(EMAIL_KEY,))
