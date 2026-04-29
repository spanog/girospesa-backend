from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import get_optional_user_id
from core.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()


class FlyerRequestBody(BaseModel):
    city: str = Field(..., min_length=1, max_length=200)
    supermarket: str | None = Field(None, max_length=200)
    flyer_url: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=500)
    email: str | None = Field(None, max_length=254)


class FlyerRequestResponse(BaseModel):
    id: str
    status: str


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FlyerRequestResponse)
async def create_flyer_request(
    body: FlyerRequestBody,
    user_id: Annotated[str | None, Depends(get_optional_user_id)],
) -> FlyerRequestResponse:
    """Submit a request for a supermarket flyer not yet covered by the app."""
    sb = get_supabase()
    row = (
        sb.table("flyer_requests")
        .insert(
            {
                "city": body.city,
                "supermarket": body.supermarket,
                "flyer_url": body.flyer_url,
                "notes": body.notes,
                "email": body.email,
                "user_id": user_id,
                "status": "pending",
            }
        )
        .execute()
    )

    if not row.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save flyer request",
        )

    record = row.data[0]
    return FlyerRequestResponse(id=record["id"], status=record["status"])
