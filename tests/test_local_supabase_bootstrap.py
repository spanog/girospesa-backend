"""Guard local Supabase bootstrap wiring for dev persistence/admin seeding docs."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text()


def test_compose_persists_database_and_storage_volumes():
    compose = _read("docker-compose.yml")

    assert "pgdata:/var/lib/postgresql/data" in compose
    assert "storage_data:/var/lib/storage" in compose


def test_compose_mounts_local_bootstrap_inputs():
    compose = _read("docker-compose.yml")

    assert "./supabase/init/001-bootstrap-local-db.sh:/docker-entrypoint-initdb.d/zzz-bootstrap-local-db.sh:ro" in compose
    assert "../lista-spesa-furba-webapp/supabase/migrations:/supabase-webapp-migrations:ro" in compose
    assert "./supabase/seed.sql:/supabase-backend/seed.sql:ro" in compose


def test_bootstrap_script_applies_schema_then_seed():
    bootstrap = _read("supabase/init/001-bootstrap-local-db.sh")

    assert "/supabase-webapp-migrations/*.sql" in bootstrap
    assert "/supabase-backend/seed.sql" in bootstrap


def test_sql_seed_no_longer_owns_admin_user_bootstrap():
    seed = _read("supabase/seed.sql")

    assert "dev-admin@local.test" not in seed
    assert '"role":"admin"' not in seed


def test_auth_user_trigger_creates_default_empty_owner_list():
    migrations = "\n".join(
        path.read_text()
        for path in sorted((BACKEND_ROOT.parent / "lista-spesa-furba-webapp/supabase/migrations").glob("*.sql"))
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
