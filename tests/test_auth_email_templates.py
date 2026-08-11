"""Guard the shared branding in Supabase Auth email templates."""

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOGO_URL = "https://www.girospesa.it/brand/girospesa-horizontal-logo-transparent.png"
TEMPLATE_NAMES = ("confirmation.html", "recovery.html")


def test_auth_email_templates_use_the_official_raster_logo():
    for template_name in TEMPLATE_NAMES:
        template = (BACKEND_ROOT / "supabase" / "templates" / template_name).read_text()

        assert LOGO_URL in template
        assert 'alt="GiroSpesa"' in template
        assert "Leaf icon" not in template
