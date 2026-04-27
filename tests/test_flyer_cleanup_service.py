"""Unit tests for services/flyer_cleanup.py — no DB, no network."""

from __future__ import annotations

import sys
import os
import types
from datetime import date
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders", "requests"):
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
_FLYER_1 = {
    "id": "flyer-aaa",
    "file_url": "https://test.supabase.co/storage/v1/object/public/flyers/user1/file.pdf",
    "supermarket_name": "Esselunga",
}
_FLYER_2 = {
    "id": "flyer-bbb",
    "file_url": "https://test.supabase.co/storage/v1/object/public/flyers/user2/file2.pdf",
    "supermarket_name": "Lidl",
}


def _make_sb(expired_flyers: list[dict]) -> MagicMock:
    sb = MagicMock()
    result = MagicMock()
    result.data = expired_flyers
    sb.table.return_value.select.return_value.lt.return_value.neq.return_value.execute.return_value = result
    return sb


def _make_svc(sb: MagicMock, today: date = _TODAY) -> FlyerCleanupService:
    return FlyerCleanupService(supabase_factory=lambda: sb, today_factory=lambda: today)


class TestNoExpiredFlyers:
    def test_no_expired_returns_zero(self):
        sb = _make_sb([])
        assert _make_svc(sb).run() == 0
        sb.table.return_value.delete.assert_not_called()


class TestDeletesStorageAndRow:
    def test_single_flyer_deleted(self):
        sb = _make_sb([_FLYER_1])
        result = _make_svc(sb).run()
        assert result == 1
        sb.storage.from_.assert_called_with("flyers")
        sb.storage.from_.return_value.remove.assert_called_once_with(["user1/file.pdf"])
        sb.table.return_value.delete.return_value.eq.assert_called_with("id", "flyer-aaa")

    def test_multiple_flyers_all_deleted(self):
        sb = _make_sb([_FLYER_1, _FLYER_2])
        result = _make_svc(sb).run()
        assert result == 2
        assert sb.storage.from_.return_value.remove.call_count == 2


class TestStorageFailureContinues:
    def test_storage_error_does_not_abort_row_delete(self):
        sb = _make_sb([_FLYER_1])
        sb.storage.from_.return_value.remove.side_effect = RuntimeError("bucket error")
        _make_svc(sb).run()
        sb.table.return_value.delete.return_value.eq.assert_called_with("id", "flyer-aaa")

    def test_storage_error_multiple_flyers_both_rows_attempted(self):
        sb = _make_sb([_FLYER_1, _FLYER_2])
        sb.storage.from_.return_value.remove.side_effect = RuntimeError("bucket error")
        result = _make_svc(sb).run()
        assert sb.table.return_value.delete.return_value.eq.call_count == 2
        assert result == 2


class TestRowDeleteFailureContinues:
    def test_row_delete_failure_counted_and_next_flyer_attempted(self):
        sb = _make_sb([_FLYER_1, _FLYER_2])
        sb.table.return_value.delete.return_value.eq.return_value.execute.side_effect = [
            RuntimeError("db error"),
            MagicMock(),
        ]
        result = _make_svc(sb).run()
        assert result == 1

    def test_all_row_deletes_fail_returns_zero(self):
        sb = _make_sb([_FLYER_1])
        sb.table.return_value.delete.return_value.eq.return_value.execute.side_effect = RuntimeError("db error")
        result = _make_svc(sb).run()
        assert result == 0


class TestExtractStoragePath:
    def test_extracts_path_from_public_url(self):
        svc = FlyerCleanupService(supabase_factory=lambda: MagicMock(), today_factory=lambda: _TODAY)
        path = svc._extract_storage_path(
            "https://test.supabase.co/storage/v1/object/public/flyers/user-id/abc.pdf"
        )
        assert path == "user-id/abc.pdf"

    def test_unknown_url_returns_empty_string(self):
        svc = FlyerCleanupService(supabase_factory=lambda: MagicMock(), today_factory=lambda: _TODAY)
        path = svc._extract_storage_path("https://other-host.com/file.pdf")
        assert path == ""

    def test_empty_url_returns_empty_string(self):
        svc = FlyerCleanupService(supabase_factory=lambda: MagicMock(), today_factory=lambda: _TODAY)
        assert svc._extract_storage_path("") == ""


class TestNoStorageUrlSkipsStorageDelete:
    def test_no_file_url_skips_storage_delete(self):
        flyer = {"id": "flyer-x", "file_url": None, "supermarket_name": "Test"}
        sb = _make_sb([flyer])
        _make_svc(sb).run()
        sb.storage.from_.assert_not_called()
