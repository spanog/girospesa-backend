"""Guard local Supabase bootstrap wiring for dev persistence/admin seeding docs."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text()


def _canonical_migrations_dir() -> Path:
    frontend_dir = BACKEND_ROOT.parent / "girospesa-webapp/supabase/migrations"
    if any(frontend_dir.glob("*.sql")):
        return frontend_dir
    return BACKEND_ROOT / "supabase/migrations"


def test_compose_persists_database_and_storage_volumes():
    compose = _read("docker-compose.yml")

    assert "pgdata:/var/lib/postgresql/data" in compose
    assert "storage_data:/var/lib/storage" in compose


def test_integration_compose_uses_isolated_project_and_volumes():
    compose = _read("docker-compose.integration.yml")

    assert "name: girospesa-itest" in compose
    assert "itest_pgdata:/var/lib/postgresql/data" in compose
    assert "itest_storage_data:/var/lib/storage" in compose


def test_integration_compose_avoids_dev_ports():
    compose = _read("docker-compose.integration.yml")

    assert "54321" not in compose
    assert "54322" not in compose
    assert "54323" not in compose
    assert "55421:8000" in compose
    assert "55422:5432" in compose


def test_compose_mounts_local_bootstrap_inputs():
    compose = _read("docker-compose.yml")

    assert "./supabase/init/001-bootstrap-local-db.sh:/docker-entrypoint-initdb.d/zzz-bootstrap-local-db.sh:ro" in compose
    assert "../girospesa-webapp/supabase/migrations:/supabase-webapp-migrations:ro" in compose
    assert "./supabase/seed.sql:/supabase-backend/seed.sql:ro" in compose


def test_integration_compose_mounts_bootstrap_inputs():
    compose = _read("docker-compose.integration.yml")

    assert "./supabase/init/001-bootstrap-local-db.sh:/docker-entrypoint-initdb.d/zzz-bootstrap-local-db.sh:ro" in compose
    assert "../girospesa-webapp/supabase/migrations:/supabase-webapp-migrations:ro" in compose
    assert "./supabase/seed.sql:/supabase-backend/seed.sql:ro" in compose


def test_integration_fixture_does_not_shell_out_to_supabase_cli():
    conftest = _read("tests/conftest.py")
    integration_conftest = _read("tests/integration/conftest.py")

    assert "supabase status" not in conftest
    assert "supabase status" not in integration_conftest
    assert "ensure_integration_stack" in integration_conftest


def test_integration_fixture_scopes_env_to_pytest_session():
    integration_conftest = _read("tests/integration/conftest.py")

    assert "apply_integration_env()" not in integration_conftest
    assert "pytest.MonkeyPatch()" in integration_conftest
    assert "monkeypatch.undo()" in integration_conftest


def test_bootstrap_script_applies_schema_then_seed():
    bootstrap = _read("supabase/init/001-bootstrap-local-db.sh")

    assert "/supabase-webapp-migrations/*.sql" in bootstrap
    assert "/supabase-backend/seed.sql" in bootstrap


def test_bootstrap_adds_storage_public_compat_column():
    bootstrap = _read("supabase/init/001-bootstrap-local-db.sh")

    assert "ADD COLUMN IF NOT EXISTS public boolean" in bootstrap


def test_bootstrap_reapplies_offer_function_search_path():
    bootstrap = _read("supabase/init/001-bootstrap-local-db.sh")

    assert "ALTER FUNCTION public.offers_compute_fields() SET search_path = public" in bootstrap


def test_local_supabase_exposes_only_public_and_storage_schemas():
    compose = _read("docker-compose.yml")
    integration_compose = _read("docker-compose.integration.yml")
    backend_config = _read("supabase/config.toml")
    frontend_config = _read("../girospesa-webapp/supabase/config.toml")

    assert "graphql_public" not in compose
    assert "graphql_public" not in integration_compose
    assert 'schemas = ["public"]' in backend_config
    assert 'schemas = ["public"]' in frontend_config


def test_local_auth_allows_email_confirmation_callback_redirects():
    compose = _read("docker-compose.yml")
    backend_config = _read("supabase/config.toml")
    frontend_config = _read("../girospesa-webapp/supabase/config.toml")

    expected_redirects = [
        "http://127.0.0.1:3000/auth/callback?next=/email-verificata",
        "http://localhost:3000/auth/callback?next=/email-verificata",
    ]

    for redirect_url in expected_redirects:
        assert redirect_url in backend_config
        assert redirect_url in frontend_config
        assert redirect_url in compose


def test_shared_backend_migration_copies_match_frontend_canonical_files():
    migrations_dir = BACKEND_ROOT / "supabase/migrations"
    expected_root = BACKEND_ROOT.parent / "girospesa-webapp/supabase/migrations"

    mismatches = []
    for frontend_path in expected_root.glob("*.sql"):
        backend_path = migrations_dir / frontend_path.name
        if not backend_path.exists() or backend_path.is_symlink():
            mismatches.append(frontend_path.name)
            continue
        if backend_path.read_text() != frontend_path.read_text():
            mismatches.append(frontend_path.name)

    assert mismatches == []


def test_sql_seed_no_longer_owns_admin_user_bootstrap():
    seed = _read("supabase/seed.sql")

    assert "dev-admin@local.test" not in seed
    assert '"role":"admin"' not in seed


def test_auth_user_trigger_creates_default_empty_owner_list():
    migrations = "\n".join(
        path.read_text()
        for path in sorted(_canonical_migrations_dir().glob("*.sql"))
    )

    assert "INSERT INTO shopping_lists (user_id, name, items, is_active)" in migrations
    assert "INSERT INTO list_members (list_id, user_id, role)" in migrations
    assert "ON CONFLICT (list_id, user_id) DO NOTHING" in migrations


def test_pyproject_exposes_admin_seed_tasks():
    pyproject = _read("pyproject.toml")
    requirements = _read("requirements.txt")

    assert "seed-admin" in pyproject
    assert "check-admin-seed" in pyproject
    assert "dev-setup" in pyproject
    assert 'dev-setup = ".venv/bin/python -m scripts.seed_admin"' in pyproject
    assert "taskipy" in requirements


def test_main_has_no_admin_seed_startup_side_effect():
    main_py = _read("main.py")

    assert "ensure_local_admin_on_startup" not in main_py
