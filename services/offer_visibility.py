from __future__ import annotations

from datetime import date
from typing import Any


def _today_iso(today: date | None) -> str:
    return (today or date.today()).isoformat()


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
