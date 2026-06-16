"""Helpers for loading local test environment files."""

from pathlib import Path


def resolve_test_env_file(root: Path) -> Path:
    local_env = root / ".env.test"
    if local_env.exists():
        return local_env

    example_env = root / ".env.test.example"
    if example_env.exists():
        return example_env

    raise FileNotFoundError("Missing .env.test or .env.test.example.")
