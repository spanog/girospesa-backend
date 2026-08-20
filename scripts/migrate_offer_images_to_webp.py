"""Migrate legacy offer PNGs to immutable, compact WebP assets.

Dry-run is the default. Originals are retained unless `--delete-originals` is
explicitly selected together with `--apply`, after every replacement downloads
and validates successfully.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from typing import Iterable

from PIL import Image

from core.database import get_supabase


_MAX_SIDE = 640
_WEBP_QUALITY = 82
_PUBLIC_MARKER = "/storage/v1/object/public/product-images/"


def storage_path(image_url: str) -> str | None:
    if _PUBLIC_MARKER not in image_url:
        return None
    return image_url.split(_PUBLIC_MARKER, maxsplit=1)[1].split("?", maxsplit=1)[0]


def webp_bytes(source: bytes) -> bytes:
    with Image.open(BytesIO(source)) as image:
        converted = image.convert("RGB")
        converted.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.Resampling.LANCZOS)
        output = BytesIO()
        converted.save(output, format="WEBP", quality=_WEBP_QUALITY, method=6)
        return output.getvalue()


def is_valid_webp(content: bytes) -> bool:
    with Image.open(BytesIO(content)) as image:
        return image.format == "WEBP" and max(image.size) <= _MAX_SIDE


def legacy_png_offers(sb) -> Iterable[dict]:
    result = (
        sb.table("offers")
        .select("id, image_url")
        .like("image_url", "%.png")
        .execute()
    )
    return result.data or []


def upload_verified_webp(bucket, path: str, content: bytes) -> None:
    bucket.upload(
        path=path,
        file=content,
        file_options={
            "content-type": "image/webp",
            "cache-control": "31536000",
            "upsert": "false",
        },
    )
    if not is_valid_webp(bytes(bucket.download(path))):
        raise RuntimeError(f"Verification failed for {path}")


def migrate_offer(sb, offer: dict, apply: bool) -> tuple[str | None, str | None]:
    old_url = offer.get("image_url")
    if not isinstance(old_url, str):
        return None, None
    old_path = storage_path(old_url)
    if old_path is None:
        return None, None
    source = bytes(sb.storage.from_("product-images").download(old_path))
    converted = webp_bytes(source)
    digest = hashlib.sha256(converted).hexdigest()
    new_path = f"migrated-offers/{offer['id']}/{digest}.webp"
    if not apply:
        return old_path, new_path
    bucket = sb.storage.from_("product-images")
    upload_verified_webp(bucket, new_path, converted)
    sb.table("offers").update({"image_url": bucket.get_public_url(new_path)}).eq(
        "id", offer["id"]
    ).execute()
    return old_path, new_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write new WebP objects and offer URLs"
    )
    parser.add_argument(
        "--delete-originals",
        action="store_true",
        help="remove verified PNGs after a successful apply",
    )
    args = parser.parse_args()
    if args.delete_originals and not args.apply:
        parser.error("--delete-originals requires --apply")
    sb = get_supabase()
    old_paths: set[str] = set()
    for offer in legacy_png_offers(sb):
        old_path, new_path = migrate_offer(sb, offer, args.apply)
        if old_path and new_path:
            old_paths.add(old_path)
            print(f"{offer['id']}: {old_path} -> {new_path}")
    if args.apply and args.delete_originals and old_paths:
        sb.storage.from_("product-images").remove(sorted(old_paths))


if __name__ == "__main__":
    main()
