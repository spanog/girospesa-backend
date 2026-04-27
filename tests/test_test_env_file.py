"""Tests for test environment file resolution."""

from pathlib import Path

from tests.env import resolve_test_env_file


def test_resolve_test_env_file_prefers_local_file(tmp_path: Path):
    local_env = tmp_path / ".env.test"
    example_env = tmp_path / ".env.test.example"
    local_env.write_text("SUPABASE_URL=http://127.0.0.1:54321\n", encoding="utf-8")
    example_env.write_text("SUPABASE_URL=http://127.0.0.1:54321\n", encoding="utf-8")

    assert resolve_test_env_file(tmp_path) == local_env


def test_resolve_test_env_file_falls_back_to_example(tmp_path: Path):
    example_env = tmp_path / ".env.test.example"
    example_env.write_text("SUPABASE_URL=http://127.0.0.1:54321\n", encoding="utf-8")

    assert resolve_test_env_file(tmp_path) == example_env
