"""Guard GitHub workflows that form part of the production contract."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text()


def test_ci_workflow_runs_backend_tests_manually_only():
    workflow = _read(".github/workflows/ci.yml")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance" in workflow
    assert "integration-test:" in workflow
    assert "docker compose version" in workflow
    assert "python -m pytest tests/integration -v --tb=short" in workflow
    assert "Dump integration Docker logs" in workflow
    assert "docker compose -f docker-compose.integration.yml -p girospesa-itest ps -a" in workflow
    assert "docker compose -f docker-compose.integration.yml -p girospesa-itest logs --no-color --tail=200" in workflow


def test_daily_maintenance_workflow_is_not_configured():
    assert not (BACKEND_ROOT / ".github/workflows/daily-maintenance.yml").exists()


def test_render_keepalive_workflow_is_not_configured():
    assert not (BACKEND_ROOT / ".github/workflows/render-keepalive.yml").exists()


def test_supabase_production_workflow_pushes_migrations_from_main():
    workflow = _read(".github/workflows/supabase-db-production.yml")

    assert "branches:" in workflow
    assert "- main" in workflow
    assert '"supabase/**"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "supabase/setup-cli@v1" in workflow
    assert 'supabase link --project-ref "$SUPABASE_PROJECT_ID"' in workflow
    assert "supabase db push --include-all" in workflow
    assert "SUPABASE_ACCESS_TOKEN" in workflow
    assert "SUPABASE_DB_PASSWORD" in workflow
    assert "SUPABASE_PROJECT_ID" in workflow
