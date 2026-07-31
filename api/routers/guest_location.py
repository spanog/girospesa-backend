"""Guest location cookie endpoints."""
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from core.guest_location import (
    GUEST_LOCATION_COOKIE,
    GUEST_LOCATION_TTL_SECONDS,
    cookie_secure,
    cookie_samesite,
    create_guest_location_token,
)

router = APIRouter()


class GuestLocationBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def set_guest_location(
    body: GuestLocationBody, request: Request, response: Response
) -> None:
    origin = request.headers.get("origin")
    response.set_cookie(
        GUEST_LOCATION_COOKIE,
        create_guest_location_token(body.lat, body.lng),
        max_age=GUEST_LOCATION_TTL_SECONDS,
        httponly=True,
        secure=cookie_secure(origin),
        samesite=cookie_samesite(origin),
        path="/",
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_guest_location(request: Request, response: Response) -> None:
    origin = request.headers.get("origin")
    response.delete_cookie(
        GUEST_LOCATION_COOKIE,
        httponly=True,
        secure=cookie_secure(origin),
        samesite=cookie_samesite(origin),
        path="/",
    )
