"""Irreversibly empty flyer assets before the offer-only database migration."""

from __future__ import annotations

import argparse

from core.database import get_supabase

BUCKETS = ("flyers", "product-images")
BATCH_SIZE = 1000


def _paths(client: object, bucket: str, prefix: str = "") -> list[str]:
    paths: list[str] = []
    offset = 0
    while True:
        rows = client.storage.from_(bucket).list(
            prefix, {"limit": BATCH_SIZE, "offset": offset}
        )
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            path = f"{prefix}/{name}" if prefix else name
            if row.get("id") is None:
                paths.extend(_paths(client, bucket, path))
            else:
                paths.append(path)
        if len(rows) < BATCH_SIZE:
            return paths
        offset += BATCH_SIZE


def empty_bucket(client: object, bucket: str) -> int:
    paths = _paths(client, bucket)
    for start in range(0, len(paths), BATCH_SIZE):
        client.storage.from_(bucket).remove(paths[start : start + BATCH_SIZE])
    if _paths(client, bucket):
        raise RuntimeError(f"Bucket {bucket} still contains objects")
    return len(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-offer-only-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_offer_only_reset:
        raise SystemExit("Pass --confirm-offer-only-reset to delete flyer assets")
    client = get_supabase()
    for bucket in BUCKETS:
        print(f"{bucket}: removed {empty_bucket(client, bucket)} objects")


if __name__ == "__main__":
    main()
