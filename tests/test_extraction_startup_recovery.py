"""Unit tests for startup recovery of interrupted extraction jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

sys.modules.setdefault("supabase", MagicMock())
sys.modules.setdefault("core.database", MagicMock())

from services.extraction_startup_recovery import ExtractionStartupRecoveryService


def _make_sb(flyers: list[dict]) -> MagicMock:
    sb = MagicMock()
    result = MagicMock()
    result.data = flyers
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = result
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return sb


def test_run_marks_resumable_processing_flyer_and_returns_id():
    flyer = {
        "id": "flyer-1",
        "file_name": "demo.pdf",
        "supermarket_name": "Conad",
        "extraction_metadata": {
            "last_completed_chunk": 4,
            "next_chunk_index": 5,
            "partial_products_count": 166,
        },
    }
    sb = _make_sb([flyer])
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    service = ExtractionStartupRecoveryService(
        supabase_factory=lambda: sb,
        now_factory=lambda: now,
    )

    resumable = service.run()

    assert resumable == ["flyer-1"]
    update_payload = sb.table.return_value.update.call_args[0][0]
    assert update_payload["status"] == "error"
    assert "automatic resume queued" in update_payload["error_message"]
    assert update_payload["extraction_metadata"]["resume_available"] is True
    assert update_payload["extraction_metadata"]["extraction_finished_at"] == "2026-06-20T12:00:00Z"


def test_run_marks_non_resumable_processing_flyer_as_error():
    flyer = {
        "id": "flyer-2",
        "file_name": "demo.pdf",
        "supermarket_name": "Conad",
        "extraction_metadata": {
            "pages_total": 32,
            "progress_percent": 5,
        },
    }
    sb = _make_sb([flyer])
    now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    service = ExtractionStartupRecoveryService(
        supabase_factory=lambda: sb,
        now_factory=lambda: now,
    )

    resumable = service.run()

    assert resumable == []
    update_payload = sb.table.return_value.update.call_args[0][0]
    assert update_payload["status"] == "error"
    assert "before a resumable checkpoint" in update_payload["error_message"]
    assert update_payload["extraction_metadata"]["resume_available"] is False


def test_run_no_processing_flyers_returns_empty():
    sb = _make_sb([])
    service = ExtractionStartupRecoveryService(supabase_factory=lambda: sb)

    assert service.run() == []
    assert sb.table.return_value.update.call_count == 0

