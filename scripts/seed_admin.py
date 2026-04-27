"""CLI for env-driven admin seeding."""

from __future__ import annotations

import argparse
from dataclasses import asdict

from core.database import get_supabase
from services.admin_seed import (
    check_admin_seed_health,
    load_admin_seed_from_env,
    seed_admin_user,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed admin user via Supabase admin API.")
    parser.add_argument("--check", action="store_true", help="Only inspect current admin seed health.")
    args = parser.parse_args()

    supabase = get_supabase()
    seed = load_admin_seed_from_env()

    if not args.check:
        result = seed_admin_user(supabase, seed)
        print(f"seed={asdict(result)}")

    health = check_admin_seed_health(supabase, seed)
    print(f"health={asdict(health)}")
    return 0 if health.auth_user_exists and health.profile_role == "admin" and health.login_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
