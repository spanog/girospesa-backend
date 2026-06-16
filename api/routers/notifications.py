from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from core.auth import get_current_user_id
from services.repositories import notifications_repository as repo

router = APIRouter()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeleteManyNotificationsBody(BaseModel):
    notification_ids: list[UUID] = Field(min_length=1)


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


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: UUID,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    repo.delete_notification(str(notification_id), user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/delete-many")
async def delete_many_notifications(
    body: DeleteManyNotificationsBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    notification_ids = [str(notification_id) for notification_id in body.notification_ids]
    return repo.delete_notifications(notification_ids, user_id)
