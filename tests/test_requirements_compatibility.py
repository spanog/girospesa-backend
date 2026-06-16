"""Guard dependency pins required for backend local boot."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _requirements_text() -> str:
    return (BACKEND_ROOT / "requirements.txt").read_text()


def test_httpx_pin_stays_compatible_with_supabase_pin():
    requirements = _requirements_text()

    assert "supabase==2.10.0" in requirements
    assert "httpx==0.27.2" in requirements
