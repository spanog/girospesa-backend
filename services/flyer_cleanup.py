"""Nightly removal of expired flyers and their owned Storage objects."""
from __future__ import annotations

from collections.abc import Iterable
import logging
from datetime import date
from typing import Callable

from core.database import get_supabase
from services.extraction.extraction_log import ERROR, INFO, log_event

logger = logging.getLogger(__name__)

_EXPIRED_FLYER_SELECT = "id, supermarket_name, file_url, preview_path"
_FLYER_FAMILY_SELECT = "id, file_url, preview_path"
_OFFER_SELECT = "id, image_url"
_PRODUCT_IMAGE_PUBLIC_MARKER = "/storage/v1/object/public/product-images/"
_FLYER_PUBLIC_MARKERS = (
    "/storage/v1/object/public/flyers/",
    "/storage/v1/object/sign/flyers/",
)
_STORAGE_REMOVE_BATCH_SIZE = 1_000


class FlyerCleanupService:
    def __init__(
        self,
        supabase_factory: Callable[[], object] | None = None,
        today_factory: Callable[[], date] | None = None,
    ) -> None:
        self._supabase_factory = supabase_factory or get_supabase
        self._today_factory = today_factory or date.today

    def run(self) -> int:
        sb = self._supabase_factory()
        today = self._today_factory().isoformat()
        expired = self._expired_source_flyers(sb, today)
        deleted = sum(self._delete_expired_flyer(sb, flyer) for flyer in expired)
        logger.info(
            "Flyer cleanup: %d offer(s) removed from %d expired flyer(s)",
            deleted,
            len(expired),
        )
        return deleted

    def _expired_source_flyers(self, sb: object, today: str) -> list[dict]:
        result = (
            sb.table("flyers")
            .select(_EXPIRED_FLYER_SELECT)
            .lt("valid_to", today)
            .not_.is_("valid_to", None)
            .is_("source_flyer_id", None)
            .execute()
        )
        return result.data or []

    def _delete_expired_flyer(self, sb: object, flyer: dict) -> int:
        family = self._flyer_family(sb, flyer)
        offers = self._offers_for_flyers(sb, _flyer_ids(family))
        self._log_flyer_deletion(sb, flyer, len(offers))
        if not self._delete_flyer_row(sb, flyer):
            return 0
        self._remove_unshared_images(sb, flyer, offers)
        self._remove_flyer_objects(sb, flyer, family)
        return len(offers)

    def _flyer_family(self, sb: object, source_flyer: dict) -> list[dict]:
        result = (
            sb.table("flyers")
            .select(_FLYER_FAMILY_SELECT)
            .eq("source_flyer_id", source_flyer["id"])
            .execute()
        )
        return [source_flyer, *(result.data or [])]

    def _offers_for_flyers(self, sb: object, flyer_ids: list[str]) -> list[dict]:
        if not flyer_ids:
            return []
        result = sb.table("offers").select(_OFFER_SELECT).in_("flyer_id", flyer_ids).execute()
        return result.data or []

    def _log_flyer_deletion(self, sb: object, flyer: dict, count: int) -> None:
        name = flyer.get("supermarket_name") or "?"
        log_event(
            sb,
            event_type=INFO,
            message=f"Deleting expired flyer and {count} offer(s): {name}",
            flyer_id=flyer["id"],
            supermarket_name=name,
        )

    def _delete_flyer_row(self, sb: object, flyer: dict) -> bool:
        try:
            sb.table("flyers").delete().eq("id", flyer["id"]).execute()
            return True
        except Exception as exc:
            self._log_flyer_delete_failure(sb, flyer, exc)
            return False

    def _log_flyer_delete_failure(self, sb: object, flyer: dict, exc: Exception) -> None:
        name = flyer.get("supermarket_name") or "?"
        logger.error("Failed to delete expired flyer %s: %s", flyer["id"], exc)
        log_event(
            sb,
            event_type=ERROR,
            message=f"Expired flyer delete failed: {exc!s:.200}",
            flyer_id=flyer["id"],
            supermarket_name=name,
        )

    def _remove_unshared_images(self, sb: object, flyer: dict, offers: list[dict]) -> None:
        paths = _product_image_paths(offers)
        if not paths:
            return
        try:
            removable = [path for path in paths if not self._has_remaining_reference(sb, path)]
            self._remove_objects(sb, "product-images", removable)
        except Exception as exc:
            self._log_storage_cleanup_failure(sb, flyer, "offer images", exc)

    def _remove_flyer_objects(self, sb: object, flyer: dict, family: list[dict]) -> None:
        paths = _flyer_storage_paths(family)
        if not paths:
            return
        try:
            self._remove_objects(sb, "flyers", paths)
        except Exception as exc:
            self._log_storage_cleanup_failure(sb, flyer, "source files", exc)

    def _remove_objects(self, sb: object, bucket_name: str, paths: list[str]) -> None:
        bucket = sb.storage.from_(bucket_name)
        for batch in _batches(paths, _STORAGE_REMOVE_BATCH_SIZE):
            bucket.remove(batch)
        logger.info("Deleted %d object(s) from %s", len(paths), bucket_name)

    def _log_storage_cleanup_failure(
        self, sb: object, flyer: dict, object_kind: str, exc: Exception
    ) -> None:
        name = flyer.get("supermarket_name") or "?"
        logger.error("Failed to remove %s for expired flyer %s: %s", object_kind, flyer["id"], exc)
        log_event(
            sb,
            event_type=ERROR,
            message=f"Failed to remove expired flyer {object_kind}: {exc!s:.200}",
            supermarket_name=name,
        )

    def _has_remaining_reference(self, sb: object, path: str) -> bool:
        result = (
            sb.table("offers")
            .select("id")
            .like("image_url", f"%{_PRODUCT_IMAGE_PUBLIC_MARKER}{path}%")
            .limit(1)
            .execute()
        )
        return bool(result.data)

def _product_image_paths(offers: Iterable[dict]) -> list[str]:
    return _unique_paths(_product_image_path(offer.get("image_url")) for offer in offers)


def _product_image_path(image_url: object) -> str | None:
    return _storage_path(image_url, _PRODUCT_IMAGE_PUBLIC_MARKER)


def _flyer_ids(flyers: Iterable[dict]) -> list[str]:
    return [str(flyer["id"]) for flyer in flyers if flyer.get("id")]


def _flyer_storage_paths(flyers: Iterable[dict]) -> list[str]:
    paths = (
        path
        for flyer in flyers
        for path in (_flyer_file_path(flyer.get("file_url")), flyer.get("preview_path"))
    )
    return _unique_paths(paths)


def _flyer_file_path(file_url: object) -> str | None:
    if not isinstance(file_url, str) or not file_url:
        return None
    if "://" not in file_url:
        return file_url
    for marker in _FLYER_PUBLIC_MARKERS:
        if (path := _storage_path(file_url, marker)):
            return path
    return None


def _storage_path(value: object, marker: str) -> str | None:
    if not isinstance(value, str) or marker not in value:
        return None
    return value.split(marker, maxsplit=1)[1].split("?", maxsplit=1)[0] or None


def _unique_paths(paths: Iterable[object]) -> list[str]:
    return sorted({path for path in paths if isinstance(path, str) and path})


def _batches(paths: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(paths), size):
        yield paths[index : index + size]
