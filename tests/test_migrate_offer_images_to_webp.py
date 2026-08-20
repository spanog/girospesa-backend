from __future__ import annotations

from io import BytesIO

from PIL import Image

from scripts.migrate_offer_images_to_webp import is_valid_webp, storage_path, webp_bytes


def test_legacy_image_migration_creates_a_small_webp() -> None:
    source = BytesIO()
    Image.new("RGB", (1_600, 800), color="orange").save(source, format="PNG")

    converted = webp_bytes(source.getvalue())

    assert is_valid_webp(converted)
    with Image.open(BytesIO(converted)) as image:
        assert max(image.size) == 640


def test_legacy_image_migration_reads_public_storage_path() -> None:
    url = "https://project.supabase.co/storage/v1/object/public/product-images/draft/a.png"

    assert storage_path(url) == "draft/a.png"
