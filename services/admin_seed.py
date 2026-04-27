"""Env-driven Supabase admin seed flow."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from core.config import settings


@dataclass(frozen=True)
class AdminSeed:
    email: str
    password: str
    jwt_role: str
    profile_role: str


@dataclass(frozen=True)
class AdminSeedResult:
    user_id: str
    created_auth_user: bool
    updated_auth_user: bool
    profile_upserted: bool


@dataclass(frozen=True)
class AdminSeedHealth:
    auth_user_exists: bool
    user_id: str | None
    profile_role: str | None
    login_ok: bool


def load_admin_seed_from_env() -> AdminSeed:
    email = os.environ.get("ADMIN_EMAIL", "").strip() or settings.admin_email.strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip() or settings.admin_password.strip()
    if not email:
        raise RuntimeError("ADMIN_EMAIL is required for admin seeding.")
    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required for admin seeding.")
    return AdminSeed(
        email=email,
        password=password,
        jwt_role="admin",
        profile_role="admin",
    )


def seed_admin_user(supabase_client, seed: AdminSeed) -> AdminSeedResult:
    existing = find_user_by_email(supabase_client.auth.admin, seed.email)
    created = existing is None
    updated = False
    if created:
        user = supabase_client.auth.admin.create_user(
            {
                "email": seed.email,
                "password": seed.password,
                "email_confirm": True,
                "app_metadata": {"role": seed.jwt_role},
            }
        ).user
        user_id = user.id
        app_metadata = {"role": seed.jwt_role}
    else:
        user_id = existing.id
        app_metadata = getattr(existing, "app_metadata", {}) or {}
        if app_metadata.get("role") != seed.jwt_role:
            supabase_client.auth.admin.update_user_by_id(
                user_id,
                {"app_metadata": {"role": seed.jwt_role}},
            )
            updated = True
            app_metadata = {"role": seed.jwt_role}
    supabase_client.table("user_profiles").upsert(
        {
            "id": user_id,
            "display_name": _display_name_from_email(seed.email),
            "role": seed.profile_role,
            "managed_supermarket_id": None,
        },
        on_conflict="id",
    ).execute()
    return AdminSeedResult(
        user_id=user_id,
        created_auth_user=created,
        updated_auth_user=updated,
        profile_upserted=True,
    )


def check_admin_seed_health(supabase_client, seed: AdminSeed) -> AdminSeedHealth:
    user = find_user_by_email(supabase_client.auth.admin, seed.email)
    profile_role = None
    if user is not None:
        result = (
            supabase_client.table("user_profiles")
            .select("role")
            .eq("id", user.id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows:
            profile_role = rows[0]["role"]
    return AdminSeedHealth(
        auth_user_exists=user is not None,
        user_id=None if user is None else user.id,
        profile_role=profile_role,
        login_ok=_login_smoke_check(seed),
    )


def find_user_by_email(admin_api, email: str):
    page = 1
    per_page = 200
    while True:
        users = admin_api.list_users(page=page, per_page=per_page)
        if not users:
            return None
        for user in users:
            if getattr(user, "email", None) == email:
                return user
        if len(users) < per_page:
            return None
        page += 1


def _login_smoke_check(seed: AdminSeed) -> bool:
    supabase_url = _required_env("SUPABASE_URL", settings.supabase_url)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "apikey": _resolve_anon_key(),
    }
    response = httpx.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers=headers,
        json={"email": seed.email, "password": seed.password, "gotrue_meta_security": {}},
        timeout=5,
    )
    return response.status_code == 200


def _resolve_anon_key() -> str:
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if anon_key:
        return anon_key
    if settings.supabase_url:
        status_env = _read_supabase_status_env()
    else:
        status_env = ""
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if publishable_key:
        return publishable_key
    for key_name in ("ANON_KEY", "PUBLISHABLE_KEY"):
        match = re.search(rf'^{key_name}="([^"]+)"$', status_env, re.MULTILINE)
        if match is not None:
            return match.group(1)
    raise RuntimeError("SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY is required for login smoke check.")


def _read_supabase_status_env() -> str:
    return subprocess.check_output(
        ["supabase", "status", "-o", "env"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )


def _display_name_from_email(email: str) -> str:
    return email.split("@", 1)[0]


def _required_env(name: str, fallback: str = "") -> str:
    value = os.environ.get(name, "").strip() or fallback.strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def _run_local_psql(sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            _resolve_db_container_name(),
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-tA",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        input=sql,
        text=True,
        capture_output=True,
    )


def _resolve_db_container_name() -> str:
    config_toml = (Path(__file__).resolve().parents[1] / "supabase" / "config.toml").read_text(
        encoding="utf-8"
    )
    project_id_match = re.search(r'project_id = "([^"]+)"', config_toml)
    if project_id_match is None:
        raise RuntimeError("Cannot resolve Supabase project_id from supabase/config.toml.")
    return f"supabase_db_{project_id_match.group(1)}"
