"""Guard GitHub workflows that form part of the production contract."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text()


def test_ci_workflow_runs_backend_tests_on_pull_requests():
    workflow = _read(".github/workflows/ci.yml")

    assert "pull_request:" in workflow
    assert "python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance" in workflow


def test_daily_maintenance_workflow_calls_production_cleanup_endpoint():
    workflow = _read(".github/workflows/daily-maintenance.yml")

    assert 'cron: "5 4 * * *"' in workflow
    assert "BACKEND_DAILY_MAINTENANCE_URL" in workflow
    assert "OPS_CRON_SECRET" in workflow
    assert "--fail-with-body" in workflow


def test_render_keepalive_workflow_pings_healthcheck_every_ten_minutes():
    workflow = _read(".github/workflows/render-keepalive.yml")

    assert 'cron: "*/10 * * * *"' in workflow
    assert "BACKEND_HEALTHCHECK_URL" in workflow
    assert 'curl --fail --silent --show-error "$BACKEND_HEALTHCHECK_URL"' in workflow


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
