"""Remove product-images objects no longer referenced by an offer.

The default mode only reports candidates. Pass --apply to remove them through
the Supabase Storage API, then verify that no orphan object remains.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

from dotenv import dotenv_values
from core.database import get_supabase
from core.supabase_client import create_supabase_client


_BUCKET = "product-images"
_BATCH_SIZE = 1_000
_PUBLIC_MARKER = "/storage/v1/object/public/product-images/"


def _storage_paths(client: object, prefix: str = "") -> set[str]:
    bucket = client.storage.from_(_BUCKET)
    paths: set[str] = set()
    for rows in _storage_pages(bucket, prefix):
        paths.update(_file_paths(prefix, rows))
        for folder in _folder_paths(prefix, rows):
            paths.update(_storage_paths(client, folder))
    return paths


def _storage_pages(bucket: object, prefix: str) -> Iterable[list[dict]]:
    offset = 0
    while True:
        rows = bucket.list(prefix, {"limit": _BATCH_SIZE, "offset": offset})
        yield rows
        if len(rows) < _BATCH_SIZE:
            return
        offset += _BATCH_SIZE


def _file_paths(prefix: str, rows: list[dict]) -> set[str]:
    return {
        _full_path(prefix, row["name"])
        for row in rows
        if row.get("id") is not None and row.get("name")
    }


def _folder_paths(prefix: str, rows: list[dict]) -> set[str]:
    return {
        _full_path(prefix, row["name"])
        for row in rows
        if row.get("id") is None and row.get("name")
    }


def _full_path(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _referenced_paths(client: object) -> set[str]:
    paths: set[str] = set()
    for offer in _offer_rows(client):
        path = _product_image_path(offer.get("image_url"))
        if path:
            paths.add(path)
    return paths


def _offer_rows(client: object) -> Iterable[dict]:
    offset = 0
    while True:
        result = client.table("offers").select("image_url").range(
            offset, offset + _BATCH_SIZE - 1
        ).execute()
        rows = result.data or []
        yield from rows
        if len(rows) < _BATCH_SIZE:
            return
        offset += _BATCH_SIZE


def _product_image_path(image_url: object) -> str | None:
    if not isinstance(image_url, str) or _PUBLIC_MARKER not in image_url:
        return None
    path = image_url.split(_PUBLIC_MARKER, maxsplit=1)[1]
    return path.split("?", maxsplit=1)[0] or None


def orphan_paths(client: object) -> list[str]:
    return sorted(_storage_paths(client) - _referenced_paths(client))


def remove_paths(client: object, paths: list[str]) -> None:
    bucket = client.storage.from_(_BUCKET)
    for start in range(0, len(paths), _BATCH_SIZE):
        bucket.remove(paths[start : start + _BATCH_SIZE])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="remove reported objects")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="dotenv file containing production Supabase credentials",
    )
    return parser.parse_args()


def _supabase_client(env_file: Path | None) -> object:
    if env_file is None:
        return get_supabase()
    values = dotenv_values(env_file)
    url = values.get("SUPABASE_URL")
    key = values.get("SUPABASE_SECRET_KEY") or values.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase URL and service key are required")
    return create_supabase_client(url, key)


def main() -> None:
    args = _parse_args()
    client = _supabase_client(args.env_file)
    candidates = orphan_paths(client)
    print(f"orphan product-images: {len(candidates)}")
    if not args.apply:
        return
    remove_paths(client, candidates)
    remaining = orphan_paths(client)
    if remaining:
        raise RuntimeError(f"{len(remaining)} orphan product-images still remain")
    print(f"removed product-images: {len(candidates)}")


if __name__ == "__main__":
    main()
