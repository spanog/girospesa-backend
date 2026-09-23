"""Deterministic safety policy shared by the documented Codex skill flow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IngestionAction(StrEnum):
    """The only safe next action for a discovered flyer candidate."""

    REQUEST_DATES = "request_dates"
    RECORD_KNOWN = "record_known"
    UPLOAD = "upload"
    STOP = "stop"
    CONFIRM = "confirm"
    RETRY_EXTRACTION = "retry_extraction"


@dataclass(frozen=True)
class FlyerCandidate:
    """The facts discovered from a retailer listing before a preflight request."""

    valid_from: str | None
    valid_to: str | None


class FlyerIngestionPolicy:
    """Apply the skill's no-upload and no-publication guardrails deterministically."""

    _retry_delays_seconds = (2, 10, 30)

    def candidate_action(self, candidate: FlyerCandidate, preflight_status: str | None) -> IngestionAction:
        if not candidate.valid_from or not candidate.valid_to:
            return IngestionAction.REQUEST_DATES
        if preflight_status == "known":
            return IngestionAction.RECORD_KNOWN
        if preflight_status in {"new", "partial"}:
            return IngestionAction.UPLOAD
        return IngestionAction.STOP

    def retry_delay_seconds(self, retries_completed: int) -> int | None:
        if retries_completed < 0 or retries_completed >= len(self._retry_delays_seconds):
            return None
        return self._retry_delays_seconds[retries_completed]

    def extraction_action(self, flyer_status: str, draft_count: int) -> IngestionAction:
        if flyer_status == "done" and draft_count > 0:
            return IngestionAction.CONFIRM
        return IngestionAction.STOP
