from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo


_ROME_TZ = ZoneInfo("Europe/Rome")


def _current_day(today: date | None = None) -> date:
    return today or datetime.now(_ROME_TZ).date()


def _today_iso(today: date | None) -> str:
    return _current_day(today).isoformat()


def offer_is_current(offer: Mapping[str, object], today: date | None = None) -> bool:
    """Return whether an offer's validity window includes the current Rome day."""
    current_day = _current_day(today)
    valid_from = _parse_iso_date(offer.get("valid_from"))
    valid_to = _parse_iso_date(offer.get("valid_to"))
    if valid_from and valid_from > current_day:
        return False
    if valid_to and valid_to < current_day:
        return False
    return True


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def apply_current_offer_window(
    query: Any,
    *,
    today: date | None = None,
    reference_table: str | None = None,
) -> Any:
    today_iso = _today_iso(today)
    key = f"{reference_table}.and" if reference_table else "and"
    query.params = query.params.add(
        key,
        (
            f"(or(valid_from.is.null,valid_from.lte.{today_iso}),"
            f"or(valid_to.is.null,valid_to.gte.{today_iso}))"
        ),
    )
    return query
