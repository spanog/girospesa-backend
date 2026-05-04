"""Manage isolated Docker Compose stack for integration tests."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = BACKEND_ROOT / "docker-compose.integration.yml"
PROJECT_NAME = "lista-spesa-furba-itest"
JWT_SECRET = "integration-test-jwt-secret-with-at-least-32-chars"
SUPABASE_URL = "http://127.0.0.1:55421"
DB_DSN = "postgresql://postgres:postgres@127.0.0.1:55422/postgres"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imxpc3RhLXNwZXNhLWZ1cmJhLWl0ZXN0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MTQwMDAwMDAsImV4cCI6MjA1OTU3NjAwMH0."
    "niOpzk45dAkzvpGeOCVJ3cJzyRNPcFxLXokL9FERjVY"
)
SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imxpc3RhLXNwZXNhLWZ1cmJhLWl0ZXN0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTcxNDAwMDAwMCwiZXhwIjoyMDU5NTc2MDAwfQ."
    "iVUfCaRH6Wmq5WBZZp0Cy4PkOhL-qCB8OeoAYwDh0jg"
)


def integration_env() -> dict[str, str]:
    return {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": ANON_KEY,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_ROLE_KEY,
        "SUPABASE_JWT_SECRET": JWT_SECRET,
        "DB_DSN": DB_DSN,
        "ADMIN_EMAIL": "test-admin@local.test",
        "ADMIN_PASSWORD": "TestAdmin123!",
        "LLM_PROVIDER": "gemini",
        "GOOGLE_API_KEY": "",
        "GEMINI_MODEL": "gemini-2.5-flash",
        "GEOCODING_PROVIDER": "disabled",
        "FRONTEND_URL": "http://127.0.0.1:3000",
    }


def apply_integration_env() -> None:
    os.environ.update(integration_env())


def compose_command(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "-p",
        PROJECT_NAME,
        *args,
    ]


def run_compose(*args: str) -> None:
    env = os.environ.copy()
    env.update(integration_env())
    subprocess.run(
        compose_command(*args),
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
    )


def print_env() -> None:
    for key, value in integration_env().items():
        print(f"{key}={value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["up", "down", "status", "env"])
    args = parser.parse_args()

    if args.command == "up":
        run_compose("up", "-d", "--wait")
    elif args.command == "down":
        run_compose("down", "-v", "--remove-orphans")
    elif args.command == "status":
        run_compose("ps")
    else:
        print_env()


if __name__ == "__main__":
    main()
