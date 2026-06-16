from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import ValidationError

from core.auth import get_optional_user
from services.contact_requests import (
    BugReportRequest,
    CollaborationRequest,
    ContactMailer,
    ContactRequestConfigurationError,
    ContactRequestContext,
    ContactRequestDeliveryError,
    ContactRequestResponse,
    ContactRequestType,
    ContactRequestValidationError,
    ContactRequestService,
    FeatureRequest,
    MissingFlyerRequest,
)

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ContactRequestResponse)
async def create_contact_request(
    request: Request,
    request_type: Annotated[ContactRequestType, Form(...)],
    email: Annotated[str | None, Form()] = None,
    subject: Annotated[str | None, Form()] = None,
    message: Annotated[str | None, Form()] = None,
    page_url: Annotated[str | None, Form()] = None,
    contact_name: Annotated[str | None, Form()] = None,
    supermarket_name: Annotated[str | None, Form()] = None,
    location: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    supermarket: Annotated[str | None, Form()] = None,
    flyer_url: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    screenshots: Annotated[list[UploadFile] | None, File()] = None,
    auth_payload: Annotated[dict | None, Depends(get_optional_user)] = None,
) -> ContactRequestResponse:
    context = _build_context(request, auth_payload)
    try:
        return await _dispatch_request(
            request_type=request_type,
            context=context,
            email=email,
            subject=subject,
            message=message,
            page_url=page_url,
            contact_name=contact_name,
            supermarket_name=supermarket_name,
            location=location,
            city=city,
            supermarket=supermarket,
            flyer_url=flyer_url,
            notes=notes,
            screenshots=screenshots or [],
        )
    except ContactRequestValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_serializable_validation_errors(exc),
        ) from exc
    except ContactRequestConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ContactRequestDeliveryError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


def _build_service() -> ContactRequestService:
    return ContactRequestService(mailer=ContactMailer())


def _build_context(request: Request, auth_payload: dict | None) -> ContactRequestContext:
    return ContactRequestContext(
        user_id=_payload_value(auth_payload, "sub"),
        user_email=_payload_value(auth_payload, "email"),
        user_agent=request.headers.get("user-agent"),
    )


def _payload_value(payload: dict | None, key: str) -> str | None:
    value = (payload or {}).get(key)
    return value if isinstance(value, str) and value else None


def _serializable_validation_errors(exc: ValidationError) -> list[dict]:
    errors: list[dict] = []
    for item in exc.errors():
        normalized = dict(item)
        ctx = normalized.get("ctx")
        if isinstance(ctx, dict) and "error" in ctx:
            normalized["ctx"] = {
                key: (str(value) if key == "error" else value)
                for key, value in ctx.items()
            }
        errors.append(normalized)
    return errors


async def _dispatch_request(
    request_type: ContactRequestType,
    context: ContactRequestContext,
    email: str | None,
    subject: str | None,
    message: str | None,
    page_url: str | None,
    contact_name: str | None,
    supermarket_name: str | None,
    location: str | None,
    city: str | None,
    supermarket: str | None,
    flyer_url: str | None,
    notes: str | None,
    screenshots: list[UploadFile],
) -> ContactRequestResponse:
    if request_type == ContactRequestType.BUG_REPORT:
        service = _build_service()
        payload = BugReportRequest(
            email=_required_value(email, "email"),
            subject=_required_value(subject, "subject"),
            message=_required_value(message, "message"),
            page_url=_optional_value(page_url),
        )
        return await service.submit_bug_report(payload, context, screenshots)
    if request_type == ContactRequestType.COLLABORATION_REQUEST:
        service = _build_service()
        payload = CollaborationRequest(
            email=_required_value(email, "email"),
            contact_name=_required_value(contact_name, "contact_name"),
            supermarket_name=_required_value(supermarket_name, "supermarket_name"),
            location=_required_value(location, "location"),
            message=_required_value(message, "message"),
        )
        return await service.submit_collaboration_request(payload, context)
    if request_type == ContactRequestType.FEATURE_REQUEST:
        service = _build_service()
        payload = FeatureRequest(
            email=_required_value(email, "email"),
            subject=_required_value(subject, "subject"),
            message=_required_value(message, "message"),
            page_url=_optional_value(page_url),
        )
        return await service.submit_feature_request(payload, context)
    service = _build_service()
    payload = MissingFlyerRequest(
        email=_optional_value(email),
        city=_required_value(city, "city"),
        supermarket=_optional_value(supermarket),
        flyer_url=_optional_value(flyer_url),
        notes=_optional_value(notes),
    )
    return await service.submit_missing_flyer_request(payload, context)


def _required_value(value: str | None, field_name: str) -> str:
    normalized = _optional_value(value)
    if normalized is None:
        raise ContactRequestValidationError(f"Missing required field: {field_name}")
    return normalized


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
