"""Backend auth router — BFF for frontend login/logout/session/signup/reset."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from core.config import settings
from core.database import get_supabase
from core.session import (
    clear_session_cookie,
    create_session_token,
    read_session_token,
    set_session_cookie,
)

_PASSWORD_RESET_TTL_SECONDS = 15 * 60  # 15-minute recovery window

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


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

class SignupBody(BaseModel):
    first_name: str
    last_name: str
    email: str
    password: str
    home_address: str
    home_city: str
    home_province: str
    home_postal_code: str


def signup_user(body: SignupBody) -> None:
    """Register a new user via Supabase Auth and trigger profile DB setup."""
    sb = get_supabase()
    try:
        sb.auth.sign_up(
            {
                "email": body.email,
                "password": body.password,
                "options": {
                    "data": {
                        "first_name": body.first_name,
                        "last_name": body.last_name,
                        "home_address": body.home_address,
                        "home_city": body.home_city,
                        "home_province": body.home_province,
                        "home_postal_code": body.home_postal_code,
                    }
                },
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Signup failed") from exc


@router.post("/signup", status_code=201)
async def signup(body: SignupBody) -> dict:
    signup_user(body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------

class ForgotPasswordBody(BaseModel):
    email: str


def send_password_reset(email: str) -> None:
    """Send a password-reset email via Supabase — never leaks whether email exists."""
    sb = get_supabase()
    try:
        redirect_to = f"{settings.frontend_url}/auth/callback"
        sb.auth.reset_password_email(email, {"redirect_to": redirect_to})
    except Exception:
        pass


@router.post("/forgot-password", status_code=204, response_model=None)
async def forgot_password(body: ForgotPasswordBody) -> None:
    send_password_reset(body.email)


# ---------------------------------------------------------------------------
# Auth callback (Supabase → backend recovery token)
# ---------------------------------------------------------------------------

@router.get("/callback")
async def auth_callback(
    token_hash: str,
    type: str,
    next: str | None = None,
) -> RedirectResponse:
    """Exchange a Supabase token_hash for a short-lived backend recovery token.

    Only the 'recovery' type is supported.  For other types, redirect to the
    home page without issuing a recovery token.
    """
    if type != "recovery":
        return RedirectResponse(url=settings.frontend_url, status_code=302)

    sb = get_supabase()
    try:
        result = sb.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
        user = result.user
        if not user:
            raise ValueError("No user returned from OTP verification")
    except Exception:
        return RedirectResponse(
            url=f"{settings.frontend_url}/link-scaduto",
            status_code=302,
        )

    recovery_token = create_session_token(
        {"sub": user.id, "purpose": "password_reset"},
        lifetime_seconds=_PASSWORD_RESET_TTL_SECONDS,
    )
    redirect_to = next or f"{settings.frontend_url}/reimposta-password"
    return RedirectResponse(
        url=f"{redirect_to}?token={recovery_token}",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------

class ResetPasswordBody(BaseModel):
    recovery_token: str
    password: str


@router.post("/reset-password", status_code=204, response_model=None)
async def reset_password(body: ResetPasswordBody) -> None:
    payload = read_session_token(body.recovery_token)
    if not payload or payload.get("purpose") != "password_reset":
        raise HTTPException(status_code=400, detail="Invalid or expired recovery token")

    user_id: str = payload["sub"]
    sb = get_supabase()
    try:
        sb.auth.admin.update_user_by_id(user_id, {"password": body.password})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Password reset failed") from exc
