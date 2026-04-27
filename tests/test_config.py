"""Tests for backend settings loading."""

import os
import sys
from pathlib import Path

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
        "POSTGRES_PASSWORD",
        "ANON_KEY",
    ):
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.supabase_url == "http://127.0.0.1:54321"
    assert settings.supabase_service_role_key == "test-service-role"
    assert settings.supabase_jwt_secret == "test-jwt-secret"
