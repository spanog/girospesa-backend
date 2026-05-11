from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from core.auth import get_current_user_id
from services.repositories import notifications_repository as repo

router = APIRouter()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("")
async def list_notifications(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    return repo.list_notifications(user_id)


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    row = repo.mark_notification_read(notification_id, user_id)
    return row or {}


@router.post("/read-all")
async def mark_all_notifications_read(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    repo.mark_all_notifications_read(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
