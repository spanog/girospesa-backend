"""Backend auth router — BFF for frontend login/logout/session."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from core.config import settings
from core.database import get_supabase
from core.session import (
    clear_session_cookie,
    create_session_token,
    read_session_token,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_NAME = "girospesa_session"


def login_with_password(email: str, password: str) -> dict:
    """Authenticate via Supabase and return user + profile dict."""
    sb = get_supabase()
    try:
        auth_resp = sb.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc

    user = auth_resp.user
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    profile_resp = (
        sb.table("user_profiles")
        .select("*")
        .eq("id", user.id)
        .maybe_single()
        .execute()
    )
    profile = profile_resp.data if profile_resp else None

    return {
        "user": {"id": user.id, "email": user.email},
        "profile": profile,
    }


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: LoginBody, response: Response) -> dict:
    result = login_with_password(body.email, body.password)
    token = create_session_token(
        {
            "sub": result["user"]["id"],
            "email": result["user"]["email"],
            "role": (result["profile"] or {}).get("role", "customer"),
        }
    )
    set_session_cookie(response, token, secure=settings.environment == "production")
    return result


@router.get("/session")
async def session(request: Request) -> dict:
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return {"authenticated": False}

    payload = read_session_token(token)
    if not payload:
        return {"authenticated": False}

    return {
        "authenticated": True,
        "user": {"id": payload["sub"], "email": payload["email"]},
        "profile": {"role": payload["role"]},
    }


@router.post("/logout", status_code=204, response_model=None)
async def logout(response: Response) -> None:
    clear_session_cookie(response)
