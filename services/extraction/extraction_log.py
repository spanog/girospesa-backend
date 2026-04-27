"""
Structured logging to the `extraction_log` Supabase table.

Provides a single entry-point -- `log_event()` -- used by the AI extraction
pipeline to record successes, warnings, and errors with structured context.
All writes are best-effort: a logging failure must never crash the caller.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Valid event types -- treated as an open enum so callers can use string literals.
EventType = str
SUCCESS = "success"
WARNING = "warning"
ERROR = "error"
INFO = "info"


def log_event(
    sb: Any,
    *,
    event_type: EventType,
    message: str,
    flyer_id: str | None = None,
    supermarket_id: str | None = None,
    supermarket_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Insert one row into `extraction_log`.

    All parameters except *event_type* and *message* are optional context.
    Failures are caught and logged locally -- this must never raise.

    Args:
        sb: Supabase client (service role).
        event_type: One of 'success', 'warning', 'error', 'info'.
        message: Human-readable summary of the event.
        flyer_id: UUID of the flyer being processed, if applicable.
        supermarket_id: UUID of the supermarket, if known.
        supermarket_name: Display name of the supermarket, if known.
        details: Free-form dict with extra debugging context (page index,
                 retry count, elapsed seconds, raw error text, etc.).
    """
    row: dict[str, Any] = {
        "event_type": event_type,
        "message": message,
    }
    if flyer_id is not None:
        row["flyer_id"] = flyer_id
    if supermarket_id is not None:
        row["supermarket_id"] = supermarket_id
    if supermarket_name is not None:
        row["supermarket_name"] = supermarket_name
    if details is not None:
        row["details"] = details

    try:
        sb.table("extraction_log").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        # Logging must never crash pipeline -- degrade gracefully.
        logger.warning("extraction_log insert failed (non-fatal): %s", exc)
