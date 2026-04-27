"""Integration smoke test for env-driven admin seed flow."""

from __future__ import annotations

import os
import time

from services import admin_seed


def test_admin_seed_creates_loginable_admin_and_profile(supabase_client):
    email = f"seed-admin-{int(time.time())}@local.test"
    password = "SeedAdmin123!"

    admin_seed._run_local_psql(
        f"""
        DELETE FROM auth.identities WHERE user_id IN (
          SELECT id FROM auth.users WHERE email = '{email}'
        );
        DELETE FROM auth.users WHERE email = '{email}';
        DELETE FROM public.user_profiles WHERE id IN (
          SELECT id FROM auth.users WHERE email = '{email}'
        );
        DELETE FROM public.user_profiles WHERE display_name = 'seed-admin';
        """
    )

    old_email = os.environ.get("ADMIN_EMAIL")
    old_password = os.environ.get("ADMIN_PASSWORD")
    os.environ["ADMIN_EMAIL"] = email
    os.environ["ADMIN_PASSWORD"] = password
    try:
        seed = admin_seed.load_admin_seed_from_env()
        result = admin_seed.seed_admin_user(supabase_client, seed)
        smoke = admin_seed.check_admin_seed_health(supabase_client=supabase_client, seed=seed)
    finally:
        if old_email is None:
            os.environ.pop("ADMIN_EMAIL", None)
        else:
            os.environ["ADMIN_EMAIL"] = old_email
        if old_password is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = old_password

    assert result.created_auth_user is True
    assert smoke.auth_user_exists is True
    assert smoke.profile_role == "admin"
    assert smoke.login_ok is True
