"""Private bot credentials for the Codex flyer-ingestion skill."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import secrets
import subprocess

import httpx

from core.config import settings
from core.database import get_supabase
from services.admin_seed import AdminSeed, seed_admin_user

KEYCHAIN_SERVICE = "girospesa-volantini"
KEYCHAIN_ACCOUNT = "credentials"


class FlyerAgentAuthError(RuntimeError):
    """Raised when the local flyer bot cannot authenticate safely."""


@dataclass(frozen=True)
class FlyerAgentCredentials:
    """The only private identity used by the agent API client."""

    email: str
    password: str


@dataclass(frozen=True)
class FlyerAgentAuthSettings:
    """Non-secret connection settings used to obtain a user bearer token."""

    api_url: str
    supabase_url: str
    publishable_key: str


class KeychainCredentialStore:
    """Store bot credentials only in the logged-in macOS Keychain."""

    def load(self) -> FlyerAgentCredentials | None:
        try:
            result = subprocess.run(_keychain_command("find"), check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return None
        return _credentials_from_json(result.stdout)

    def save(self, credentials: FlyerAgentCredentials) -> None:
        payload = json.dumps(credentials.__dict__, separators=(",", ":"))
        try:
            subprocess.run(_keychain_command("save", payload), check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise FlyerAgentAuthError("Cannot save flyer bot credentials in Keychain") from exc


class FlyerAgentProvisioner:
    """Create or repair the dedicated technical admin account once."""

    def __init__(self, store: KeychainCredentialStore) -> None:
        self._store = store

    def provision(self, email: str) -> str:
        credentials, is_new = _credentials_for_email(self._store, email)
        supabase = get_supabase()
        result = seed_admin_user(supabase, _admin_seed(credentials))
        if is_new:
            supabase.auth.admin.update_user_by_id(result.user_id, _password_update(credentials))
            self._store.save(credentials)
        return result.user_id


class SupabaseFlyerAgentAuthenticator:
    """Exchange the Keychain password for a short-lived ordinary user JWT."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def access_token(self, config: FlyerAgentAuthSettings, credentials: FlyerAgentCredentials) -> str:
        response = self._http.post(
            _token_url(config.supabase_url),
            headers=_auth_headers(config),
            json=_login_body(credentials),
        )
        if response.status_code != 200:
            raise FlyerAgentAuthError("Flyer bot login failed")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise FlyerAgentAuthError("Flyer bot login returned no access token")
        return token


def load_agent_credentials() -> FlyerAgentCredentials:
    credentials = KeychainCredentialStore().load()
    if credentials is None:
        raise FlyerAgentAuthError("Flyer bot is not provisioned in the macOS Keychain")
    return credentials


def load_agent_auth_settings() -> FlyerAgentAuthSettings:
    return FlyerAgentAuthSettings(
        api_url=settings.backend_url.rstrip("/"),
        supabase_url=settings.supabase_url.rstrip("/"),
        publishable_key=_publishable_key(),
    )


def _keychain_command(action: str, payload: str | None = None) -> list[str]:
    command = [
        "security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
        "-a", KEYCHAIN_ACCOUNT, "-w",
    ]
    if action == "save":
        return ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", payload or ""]
    return command


def _credentials_from_json(value: str) -> FlyerAgentCredentials:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise FlyerAgentAuthError("Flyer bot Keychain entry is invalid") from exc
    email = payload.get("email") if isinstance(payload, dict) else None
    password = payload.get("password") if isinstance(payload, dict) else None
    if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
        raise FlyerAgentAuthError("Flyer bot Keychain entry is incomplete")
    return FlyerAgentCredentials(email=email, password=password)


def _credentials_for_email(store: KeychainCredentialStore, email: str) -> tuple[FlyerAgentCredentials, bool]:
    current = store.load()
    if current is not None and current.email == email:
        return current, False
    if current is not None:
        raise FlyerAgentAuthError("A different flyer bot is already provisioned")
    return FlyerAgentCredentials(email=email, password=secrets.token_urlsafe(32)), True


def _admin_seed(credentials: FlyerAgentCredentials) -> AdminSeed:
    return AdminSeed(
        email=credentials.email,
        password=credentials.password,
        jwt_role="admin",
        profile_role="admin",
    )


def _password_update(credentials: FlyerAgentCredentials) -> dict[str, object]:
    return {
        "password": credentials.password,
        "email_confirm": True,
        "app_metadata": {"role": "admin"},
    }


def _token_url(supabase_url: str) -> str:
    return f"{supabase_url}/auth/v1/token?grant_type=password"


def _auth_headers(config: FlyerAgentAuthSettings) -> dict[str, str]:
    return {"apikey": config.publishable_key, "Content-Type": "application/json"}


def _login_body(credentials: FlyerAgentCredentials) -> dict[str, object]:
    return {"email": credentials.email, "password": credentials.password, "gotrue_meta_security": {}}


def _publishable_key() -> str:
    for name in ("GIROSPESA_FLYER_AGENT_PUBLISHABLE_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _local_publishable_key()


def _local_publishable_key() -> str:
    try:
        status = subprocess.check_output(["supabase", "status", "-o", "env"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FlyerAgentAuthError("Set GIROSPESA_FLYER_AGENT_PUBLISHABLE_KEY") from exc
    match = re.search(r'^(?:ANON_KEY|PUBLISHABLE_KEY)="([^"]+)"$', status, re.MULTILINE)
    if match is None:
        raise FlyerAgentAuthError("Local Supabase did not expose a publishable key")
    return match.group(1)
