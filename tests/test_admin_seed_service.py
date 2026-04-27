"""Unit tests for env-driven admin seed flow."""

from __future__ import annotations

import os

import pytest

from services import admin_seed


class _FakeUser:
    def __init__(self, user_id: str, email: str, app_metadata: dict | None = None):
        self.id = user_id
        self.email = email
        self.app_metadata = app_metadata or {}


class _FakeUserResponse:
    def __init__(self, user: _FakeUser):
        self.user = user


class _FakeAdminApi:
    def __init__(self, users: list[_FakeUser] | None = None):
        self.users = users or []
        self.create_calls: list[dict] = []
        self.update_calls: list[tuple[str, dict]] = []

    def list_users(self, page: int | None = None, per_page: int | None = None):
        return list(self.users)

    def create_user(self, attributes: dict):
        self.create_calls.append(attributes)
        user = _FakeUser("created-admin-id", attributes["email"], attributes.get("app_metadata"))
        self.users.append(user)
        return _FakeUserResponse(user)

    def update_user_by_id(self, user_id: str, attributes: dict):
        self.update_calls.append((user_id, attributes))
        current = next(user for user in self.users if user.id == user_id)
        current.app_metadata = attributes["app_metadata"]
        return _FakeUserResponse(current)


class _FakeAuth:
    def __init__(self, admin_api: _FakeAdminApi):
        self.admin = admin_api


class _FakeTable:
    def __init__(self):
        self.upsert_calls: list[tuple[dict, str]] = []

    def upsert(self, payload: dict, on_conflict: str):
        self.upsert_calls.append((payload, on_conflict))
        return self

    def execute(self):
        return None


class _FakeSupabase:
    def __init__(self, users: list[_FakeUser] | None = None):
        self.auth = _FakeAuth(_FakeAdminApi(users))
        self.user_profiles = _FakeTable()

    def table(self, name: str):
        assert name == "user_profiles"
        return self.user_profiles


def test_load_admin_seed_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-123")

    seed = admin_seed.load_admin_seed_from_env()

    assert seed.email == "admin@example.com"
    assert seed.password == "pw-123"
    assert seed.jwt_role == "admin"
    assert seed.profile_role == "admin"


def test_load_admin_seed_requires_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(admin_seed.settings, "admin_email", "")
    monkeypatch.setattr(admin_seed.settings, "admin_password", "")

    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        admin_seed.load_admin_seed_from_env()


def test_seed_admin_creates_missing_admin_user(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-123")
    supabase = _FakeSupabase()

    result = admin_seed.seed_admin_user(supabase, admin_seed.load_admin_seed_from_env())

    assert result.created_auth_user is True
    assert result.updated_auth_user is False
    assert supabase.auth.admin.create_calls == [
        {
            "email": "admin@example.com",
            "password": "pw-123",
            "email_confirm": True,
            "app_metadata": {"role": "admin"},
        }
    ]
    assert supabase.user_profiles.upsert_calls == [
        (
            {
                "id": "created-admin-id",
                "display_name": "admin",
                "role": "admin",
                "managed_supermarket_id": None,
            },
            "id",
        )
    ]


def test_seed_admin_skips_existing_admin_but_keeps_profile_in_sync(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-123")
    existing = _FakeUser("existing-admin-id", "admin@example.com", {"role": "admin"})
    supabase = _FakeSupabase([existing])

    result = admin_seed.seed_admin_user(supabase, admin_seed.load_admin_seed_from_env())

    assert result.created_auth_user is False
    assert result.updated_auth_user is False
    assert supabase.auth.admin.create_calls == []
    assert supabase.auth.admin.update_calls == []
    assert supabase.user_profiles.upsert_calls[0][0]["id"] == "existing-admin-id"


def test_seed_admin_updates_existing_user_missing_admin_role(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-123")
    existing = _FakeUser("existing-admin-id", "admin@example.com", {"role": "customer"})
    supabase = _FakeSupabase([existing])

    result = admin_seed.seed_admin_user(supabase, admin_seed.load_admin_seed_from_env())

    assert result.created_auth_user is False
    assert result.updated_auth_user is True
    assert supabase.auth.admin.update_calls == [
        ("existing-admin-id", {"app_metadata": {"role": "admin"}})
    ]


def test_find_user_by_email_returns_match():
    admin_api = _FakeAdminApi(
        [_FakeUser("u1", "one@example.com"), _FakeUser("u2", "two@example.com")]
    )

    user = admin_seed.find_user_by_email(admin_api, "two@example.com")

    assert user is not None
    assert user.id == "u2"


def test_module_uses_runtime_env_not_hardcoded_local_seed():
    assert "LOCAL_ADMIN_PASSWORD" not in admin_seed.__dict__
    assert os.path.basename(admin_seed.__file__) == "admin_seed.py"
