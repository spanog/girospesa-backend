from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from jose import JWTError, jwt


class SessionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_session_secret: str
    app_session_cookie_name: str = "girospesa_session"
    app_session_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, gt=0)

    @field_validator("app_session_secret")
    @classmethod
    def validate_app_session_secret(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("app_session_secret must not be empty")
        return normalized


@lru_cache(maxsize=1)
def get_session_settings() -> SessionSettings:
    return SessionSettings()


def create_session_token(
    claims: dict[str, Any],
    *,
    lifetime_seconds: int | None = None,
) -> str:
    settings = get_session_settings()
    now = int(time.time())
    ttl = settings.app_session_ttl_seconds if lifetime_seconds is None else lifetime_seconds
    payload = {**claims, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.app_session_secret, algorithm="HS256")


def read_session_token(token: str) -> dict[str, Any] | None:
    settings = get_session_settings()
    try:
        return jwt.decode(
            token,
            settings.app_session_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError:
        return None
