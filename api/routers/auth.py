"""Backend auth router — BFF for frontend login/logout/session/signup/reset."""

from __future__ import annotations

import logging

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

_PASSWORD_RESET_TTL_SECONDS = 60 * 60  # 1-hour recovery window

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

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

    user_id: str = payload["sub"]
    sb = get_supabase()
    profile_resp = (
        sb.table("user_profiles")
        .select("*")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile = profile_resp.data if profile_resp else None

    return {
        "authenticated": True,
        "user": {"id": payload["sub"], "email": payload["email"]},
        "profile": profile,
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


def _signup_error_response(exc: Exception) -> tuple[int, str]:
    detail = str(exc).strip() or "Signup failed"
    lowered = detail.lower()
    if "already registered" in lowered or "already exists" in lowered:
        return 400, "Registrazione non riuscita. Verifica i dati inseriti oppure accedi se hai già un account."
    if "password" in lowered:
        return 400, "Password non valida"
    if "email" in lowered:
        return 400, "Email non valida"
    return 400, "Registrazione non riuscita. Riprova più tardi."


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
        status_code, detail = _signup_error_response(exc)
        logger.exception("Signup failed for %s", body.email)
        raise HTTPException(status_code=status_code, detail=detail) from exc


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
        redirect_to = f"{settings.backend_url}/auth/callback"
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
    finally:
        # verify_otp stores a user session on the singleton client, which would
        # cause subsequent service-role admin calls to fail with 403.
        # Sign out to clear the cached session so the client reverts to the
        # service-role key for all future requests.
        try:
            sb.auth.sign_out()
        except Exception:
            pass

    recovery_token = create_session_token(
        {"sub": user.id, "purpose": "password_reset"},
        lifetime_seconds=_PASSWORD_RESET_TTL_SECONDS,
    )
    redirect_to = next or f"{settings.frontend_url}/reset-password"
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
