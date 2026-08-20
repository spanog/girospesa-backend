"""Give legacy public supermarket logos a one-year CDN cache policy.

Dry-run is the default. Existing paths remain valid because future logo uploads
use a content-addressed path, so cache entries can safely be immutable.
"""
from __future__ import annotations

import argparse
from typing import Iterable

from core.database import get_supabase


_PUBLIC_MARKER = "/storage/v1/object/public/logos/"
LOGO_CACHE_CONTROL = "31536000"
_CONTENT_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def storage_path(logo_url: str) -> str | None:
    if _PUBLIC_MARKER not in logo_url:
        return None
    return logo_url.split(_PUBLIC_MARKER, maxsplit=1)[1].split("?", maxsplit=1)[0]


def content_type(path: str) -> str | None:
    extension = path.rsplit(".", maxsplit=1)[-1].lower()
    return _CONTENT_TYPES.get(extension)


def supermarket_logos(sb) -> Iterable[dict]:
    return sb.table("supermarkets").select("id, logo_url").execute().data or []


def refresh_logo(bucket, path: str, mime_type: str) -> None:
    content = bytes(bucket.download(path))
    if not content:
        raise RuntimeError(f"Logo is empty: {path}")
    bucket.update(
        path=path,
        file=content,
        file_options={
            "content-type": mime_type,
            "cache-control": LOGO_CACHE_CONTROL,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="update Storage metadata")
    args = parser.parse_args()
    sb = get_supabase()
    bucket = sb.storage.from_("logos")
    for supermarket in supermarket_logos(sb):
        logo_url = supermarket.get("logo_url")
        path = storage_path(logo_url) if isinstance(logo_url, str) else None
        mime_type = content_type(path) if path else None
        if not path or not mime_type:
            continue
        print(f"{supermarket['id']}: {path}")
        if args.apply:
            refresh_logo(bucket, path, mime_type)


if __name__ == "__main__":
    main()
