"""Tests for backend settings loading."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("APP_SESSION_SECRET", "test-app-session-secret")
os.environ.setdefault("SUPABASE_SECRET_KEY", "test-secret-key")

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
                "SUPABASE_SECRET_KEY=test-secret-key",
                "APP_SESSION_SECRET=test-app-session-secret",
                "POSTGRES_PASSWORD=postgres",
                "ANON_KEY=test-anon-key",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "APP_SESSION_SECRET",
        "POSTGRES_PASSWORD",
        "ANON_KEY",
    ):
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.supabase_url == "http://127.0.0.1:54321"
    assert settings.supabase_secret_key == "test-secret-key"


def test_settings_require_app_session_secret(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SECRET_KEY=test-secret-key",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "APP_SESSION_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


def test_settings_default_app_session_values(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("APP_SESSION_SECRET", "x" * 32)

    settings = Settings(_env_file=None)

    assert settings.app_session_secret == "x" * 32
    assert settings.app_session_ttl_seconds == 60 * 60 * 24 * 7


def test_settings_accept_supabase_service_role_key_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SUPABASE_URL=http://127.0.0.1:54321",
                "SUPABASE_SERVICE_ROLE_KEY=test-service-role-key",
                "APP_SESSION_SECRET=test-app-session-secret",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "APP_SESSION_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.supabase_secret_key == "test-service-role-key"


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
                "SUPABASE_SECRET_KEY=test-secret-key",
                f"APP_SESSION_SECRET={secret}",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
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
                "SUPABASE_SECRET_KEY=test-secret-key",
                "APP_SESSION_SECRET=test-app-session-secret",
                f"APP_SESSION_TTL_SECONDS={ttl_seconds}",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "APP_SESSION_SECRET",
        "APP_SESSION_TTL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


@pytest.mark.parametrize("model", ["qwen3.8:27b-nvfp4", "gemma4:26b-mlx"])
def test_local_ollama_settings_load_without_google_key(monkeypatch, model):
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("APP_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", model)
    monkeypatch.delenv("OLLAMA_CONTEXT_TOKENS", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    config = Settings(_env_file=None)
    assert config.llm_provider == "ollama"
    assert config.ollama_model == model
    assert config.google_api_key == ""
    assert config.ollama_context_tokens == 16384


@pytest.mark.parametrize("field,value", [("ollama_timeout_seconds", 0), ("ollama_context_tokens", 0),
                                         ("ollama_max_output_tokens", 0), ("ollama_image_dpi", 301)])
def test_invalid_ollama_runtime_limits_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url="http://localhost", supabase_secret_key="test",
                 app_session_secret="test", **{field: value})
