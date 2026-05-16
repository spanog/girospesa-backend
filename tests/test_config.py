"""Tests for backend settings loading."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("APP_SESSION_SECRET", "test-app-session-secret")

if "core.config" in sys.modules and not hasattr(sys.modules["core.config"], "Settings"):
    sys.modules.pop("core.config")

from core.config import Settings


def test_settings_ignore_unknown_env_keys(
    tmp_path: Path,
    monkeypatch,
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SERVICE_ROLE_KEY=test-service-role",
                "SUPABASE_JWT_SECRET=test-jwt-secret",
                "APP_SESSION_SECRET=test-app-session-secret",
                "POSTGRES_PASSWORD=postgres",
                "ANON_KEY=test-anon-key",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "APP_SESSION_SECRET",
        "POSTGRES_PASSWORD",
        "ANON_KEY",
    ):
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.supabase_url == "http://127.0.0.1:54321"
    assert settings.supabase_service_role_key == "test-service-role"
    assert settings.supabase_jwt_secret == "test-jwt-secret"


def test_settings_require_app_session_secret(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SERVICE_ROLE_KEY=test-service-role",
                "SUPABASE_JWT_SECRET=test-jwt-secret",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "APP_SESSION_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


def test_settings_default_app_session_values(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("APP_SESSION_SECRET", "x" * 32)

    settings = Settings(_env_file=None)

    assert settings.app_session_secret == "x" * 32
    assert settings.app_session_cookie_name == "girospesa_session"
    assert settings.app_session_ttl_seconds == 60 * 60 * 24 * 7


@pytest.mark.parametrize("secret", ["", "   "])
def test_settings_reject_blank_app_session_secret(
    tmp_path: Path,
    monkeypatch,
    secret: str,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SERVICE_ROLE_KEY=test-service-role",
                "SUPABASE_JWT_SECRET=test-jwt-secret",
                f"APP_SESSION_SECRET={secret}",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "APP_SESSION_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


@pytest.mark.parametrize("ttl_seconds", [0, -1])
def test_settings_reject_non_positive_app_session_ttl_seconds(
    tmp_path: Path,
    monkeypatch,
    ttl_seconds: int,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SERVICE_ROLE_KEY=test-service-role",
                "SUPABASE_JWT_SECRET=test-jwt-secret",
                "APP_SESSION_SECRET=test-app-session-secret",
                f"APP_SESSION_TTL_SECONDS={ttl_seconds}",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "APP_SESSION_SECRET",
        "APP_SESSION_TTL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)
