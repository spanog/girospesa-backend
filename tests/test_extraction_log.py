"""
Unit tests for extraction_log.py.

Verifies:
1. Correct row shape inserted into `extraction_log`.
2. Optional fields are omitted when not provided.
3. A Supabase insert failure is swallowed (best-effort logging).
4. Constants are defined with expected string values.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.extraction.extraction_log import ERROR, INFO, SUCCESS, WARNING, log_event


class TestConstants:
    def test_success_value(self):
        assert SUCCESS == "success"

    def test_warning_value(self):
        assert WARNING == "warning"

    def test_error_value(self):
        assert ERROR == "error"

    def test_info_value(self):
        assert INFO == "info"


def _make_sb() -> tuple:
    """Return (sb, extraction_log_mock) with wired table router."""
    sb = MagicMock()
    log_mock = MagicMock()
    log_mock.insert.return_value.execute.return_value.data = []
    sb.table.side_effect = lambda name: log_mock if name == "extraction_log" else MagicMock()
    return sb, log_mock


class TestLogEvent:
    def test_inserts_required_fields(self):
        sb, log_mock = _make_sb()

        log_event(sb, event_type=SUCCESS, message="All good")

        log_mock.insert.assert_called_once()
        row = log_mock.insert.call_args[0][0]
        assert row["event_type"] == "success"
        assert row["message"] == "All good"

    def test_optional_fields_included_when_provided(self):
        sb, log_mock = _make_sb()

        log_event(
            sb,
            event_type=ERROR,
            message="Pipeline failed",
            flyer_id="flyer-uuid-1",
            supermarket_id="sm-uuid-1",
            supermarket_name="Lidl",
            details={"elapsed_seconds": 42, "error": "timeout"},
        )

        row = log_mock.insert.call_args[0][0]
        assert row["flyer_id"] == "flyer-uuid-1"
        assert row["supermarket_id"] == "sm-uuid-1"
        assert row["supermarket_name"] == "Lidl"
        assert row["details"]["elapsed_seconds"] == 42
        assert row["details"]["error"] == "timeout"

    def test_optional_fields_omitted_when_not_provided(self):
        sb, log_mock = _make_sb()

        log_event(sb, event_type=WARNING, message="Low count")

        row = log_mock.insert.call_args[0][0]
        assert "flyer_id" not in row
        assert "supermarket_id" not in row
        assert "supermarket_name" not in row
        assert "details" not in row

    def test_details_omitted_when_none(self):
        sb, log_mock = _make_sb()

        log_event(sb, event_type=INFO, message="Info event", details=None)

        row = log_mock.insert.call_args[0][0]
        assert "details" not in row

    def test_insert_failure_is_swallowed(self):
        """A DB error must never propagate to caller."""
        sb = MagicMock()
        sb.table.return_value.insert.side_effect = RuntimeError("DB unavailable")

        log_event(sb, event_type=ERROR, message="Some error")

    def test_insert_called_on_extraction_log_table(self):
        sb, log_mock = _make_sb()

        log_event(sb, event_type=SUCCESS, message="done")

        sb.table.assert_called_with("extraction_log")
        log_mock.insert.assert_called_once()
