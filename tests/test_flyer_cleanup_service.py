"""Unit tests for expired-offer cleanup — no DB or network."""

from __future__ import annotations

import os
import sys
import types
from datetime import date
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "requests"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings = MagicMock()
_settings.supabase_url = "https://test.supabase.co"
_config_mod.settings = _settings
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

from services.flyer_cleanup import FlyerCleanupService  # noqa: E402

_TODAY = date(2026, 4, 27)
_FLYER_1 = {"id": "flyer-aaa", "supermarket_name": "Esselunga"}
_FLYER_2 = {"id": "flyer-bbb", "supermarket_name": "Lidl"}
_PUBLIC_IMAGE_URL = "https://test.supabase.co/storage/v1/object/public/product-images/"


def _offer(offer_id: str, path: str | None = None) -> dict:
    return {"id": offer_id, "image_url": f"{_PUBLIC_IMAGE_URL}{path}" if path else None}


def _result(data: list[dict]) -> MagicMock:
    result = MagicMock()
    result.data = data
    return result


def _make_sb(
    expired_flyers: list[dict],
    offers_by_flyer: dict[str, list[dict]] | None = None,
    target_flyers_by_source: dict[str, list[dict]] | None = None,
    shared_paths: set[str] | None = None,
) -> MagicMock:
    sb = MagicMock()
    offers_by_flyer = offers_by_flyer or {}
    target_flyers_by_source = target_flyers_by_source or {}
    shared_paths = shared_paths or set()
    flyers_table = MagicMock()
    offers_table = MagicMock()
    sb.table.side_effect = lambda name: offers_table if name == "offers" else flyers_table
    selector = _flyer_select(expired_flyers, target_flyers_by_source)
    flyers_table.select.side_effect = selector
    flyers_table.expired_query = selector.expired_query
    offers_table.select.side_effect = _offer_select(offers_by_flyer, shared_paths)
    return sb


def _flyer_select(expired_flyers: list[dict], target_flyers_by_source: dict[str, list[dict]]):
    expired_query = MagicMock()
    expired_query.lt.return_value.not_.is_.return_value.is_.return_value.execute.return_value = _result(expired_flyers)

    def select(columns: str) -> MagicMock:
        if columns == "id, supermarket_name, file_url, preview_path":
            return expired_query
        if columns == "id, file_url, preview_path":
            query = MagicMock()
            query.eq.side_effect = _family_flyers(target_flyers_by_source)
            return query
        raise AssertionError(f"unexpected flyer select: {columns}")

    select.expired_query = expired_query
    return select


def _family_flyers(target_flyers_by_source: dict[str, list[dict]]):
    def eq(column: str, source_flyer_id: str) -> MagicMock:
        assert column == "source_flyer_id"
        query = MagicMock()
        query.execute.return_value = _result(target_flyers_by_source.get(source_flyer_id, []))
        return query

    return eq


def _offer_select(offers_by_flyer: dict[str, list[dict]], shared_paths: set[str]):
    def select(columns: str) -> MagicMock:
        query = MagicMock()
        if columns == "id, image_url":
            query.in_.side_effect = _offers_for_flyers(offers_by_flyer)
        elif columns == "id":
            query.like.side_effect = _remaining_references(shared_paths)
        else:
            raise AssertionError(f"unexpected offer select: {columns}")
        return query

    return select


def _offers_for_flyers(offers_by_flyer: dict[str, list[dict]]):
    def in_(column: str, flyer_ids: list[str]) -> MagicMock:
        assert column == "flyer_id"
        query = MagicMock()
        offers = [offer for flyer_id in flyer_ids for offer in offers_by_flyer.get(flyer_id, [])]
        query.execute.return_value = _result(offers)
        return query

    return in_


def _remaining_references(shared_paths: set[str]):
    def like(column: str, pattern: str) -> MagicMock:
        assert column == "image_url"
        data = [{"id": "shared-offer"}] if any(path in pattern for path in shared_paths) else []
        query = MagicMock()
        query.limit.return_value.execute.return_value = _result(data)
        return query

    return like


def _make_svc(sb: MagicMock, today: date = _TODAY) -> FlyerCleanupService:
    return FlyerCleanupService(supabase_factory=lambda: sb, today_factory=lambda: today)


class TestNoExpiredFlyers:
    def test_no_expired_returns_zero(self):
        sb = _make_sb([])

        assert _make_svc(sb).run() == 0
        sb.table("flyers").delete.assert_not_called()
        sb.storage.from_.assert_not_called()

    def test_query_excludes_null_valid_to(self):
        sb = _make_sb([])

        _make_svc(sb).run()

        flyers_table = sb.table("flyers")
        expiry_query = flyers_table.expired_query.lt.return_value
        expiry_query.not_.is_.assert_called_once_with("valid_to", None)
        expiry_query.not_.is_.return_value.is_.assert_called_once_with(
            "source_flyer_id", None
        )


class TestExpiredOfferCleanup:
    def test_deletes_offer_rows_and_unshared_images(self):
        path = "flyer-aaa/crop.webp"
        sb = _make_sb([_FLYER_1], {"flyer-aaa": [_offer("offer-1", path)]})

        assert _make_svc(sb).run() == 1

        sb.table("flyers").delete.return_value.eq.assert_called_once_with("id", "flyer-aaa")
        sb.storage.from_.assert_called_once_with("product-images")
        sb.storage.from_.return_value.remove.assert_called_once_with([path])

    def test_preserves_image_shared_by_a_remaining_offer(self):
        path = "shared/crop.webp"
        sb = _make_sb(
            [_FLYER_1],
            {"flyer-aaa": [_offer("offer-1", path)]},
            shared_paths={path},
        )

        assert _make_svc(sb).run() == 1
        sb.table("flyers").delete.return_value.eq.assert_called_once_with("id", "flyer-aaa")
        sb.storage.from_.return_value.remove.assert_not_called()

    def test_skips_external_or_missing_images(self):
        offers = [
            _offer("offer-without-image"),
            {"id": "external-image", "image_url": "https://example.com/crop.webp"},
        ]
        sb = _make_sb([_FLYER_1], {"flyer-aaa": offers})

        assert _make_svc(sb).run() == 2
        sb.storage.from_.assert_not_called()

    def test_deduplicates_storage_path_before_removal(self):
        path = "flyer-aaa/shared-crop.webp"
        offers = {"flyer-aaa": [_offer("one", path), _offer("two", path)]}
        sb = _make_sb([_FLYER_1], offers)

        assert _make_svc(sb).run() == 2
        sb.storage.from_.return_value.remove.assert_called_once_with([path])

    def test_multiple_flyers_all_delete_their_offers(self):
        offers = {"flyer-aaa": [_offer("offer-1")], "flyer-bbb": [_offer("offer-2")]}
        sb = _make_sb([_FLYER_1, _FLYER_2], offers)

        assert _make_svc(sb).run() == 2
        assert sb.table("flyers").delete.return_value.eq.call_count == 2

    def test_zero_offer_flyer_is_deleted(self):
        sb = _make_sb([_FLYER_1])

        assert _make_svc(sb).run() == 0
        sb.table("flyers").delete.return_value.eq.assert_called_once_with("id", "flyer-aaa")

    def test_removes_source_file_preview_and_target_offer_crops(self):
        source = {
            **_FLYER_1,
            "file_url": "uploads/flyer.pdf",
            "preview_path": "previews/flyer-aaa.webp",
        }
        target = {
            "id": "target-bbb",
            "file_url": "uploads/flyer.pdf",
            "preview_path": "previews/flyer-aaa.webp",
        }
        offers = {
            "flyer-aaa": [_offer("source-offer", "source/crop.webp")],
            "target-bbb": [_offer("target-offer", "target/crop.webp")],
        }
        sb = _make_sb([source], offers, {"flyer-aaa": [target]})

        assert _make_svc(sb).run() == 2

        sb.table("flyers").delete.return_value.eq.assert_called_once_with("id", "flyer-aaa")
        assert sb.storage.from_.call_args_list == [
            call("product-images"),
            call("flyers"),
        ]
        sb.storage.from_.return_value.remove.assert_has_calls(
            [
                call(["source/crop.webp", "target/crop.webp"]),
                call(["previews/flyer-aaa.webp", "uploads/flyer.pdf"]),
            ]
        )


class TestFlyerDeleteFailure:
    def test_does_not_remove_storage_when_flyer_delete_fails(self):
        path = "flyer-aaa/crop.webp"
        sb = _make_sb([_FLYER_1], {"flyer-aaa": [_offer("offer-1", path)]})
        sb.table("flyers").delete.return_value.eq.return_value.execute.side_effect = RuntimeError("db error")

        assert _make_svc(sb).run() == 0
        sb.storage.from_.assert_not_called()

    def test_failure_for_one_flyer_continues_with_the_next(self):
        offers = {"flyer-aaa": [_offer("offer-1")], "flyer-bbb": [_offer("offer-2")]}
        sb = _make_sb([_FLYER_1, _FLYER_2], offers)
        sb.table("flyers").delete.return_value.eq.return_value.execute.side_effect = [
            RuntimeError("db error"),
            MagicMock(),
        ]

        assert _make_svc(sb).run() == 1
