from unittest.mock import MagicMock

from storage3.utils import StorageException

from scripts.refresh_supermarket_logo_cache import (
    content_type,
    refresh_logo,
    storage_path,
)


def test_logo_cache_migration_reads_public_storage_path() -> None:
    url = "https://project.supabase.co/storage/v1/object/public/logos/market/logo.png"

    assert storage_path(url) == "market/logo.png"


def test_logo_cache_migration_accepts_supported_image_types_only() -> None:
    assert content_type("market/logo.webp") == "image/webp"
    assert content_type("market/logo.svg") is None


def test_logo_cache_migration_skips_a_missing_storage_object() -> None:
    bucket = MagicMock()
    bucket.download.side_effect = StorageException({"statusCode": 404})

    assert refresh_logo(bucket, "missing.png", "image/png") is False
    bucket.update.assert_not_called()
