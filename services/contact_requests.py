from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from html import escape
import re
import smtplib
from typing import Sequence

from fastapi import UploadFile
from pydantic import BaseModel, Field, field_validator

from core.config import settings

_ALLOWED_SCREENSHOT_TYPES = frozenset({"image/jpeg", "image/png"})
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
}
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactRequestError(Exception):
    """Base error for contact request delivery failures."""


class ContactRequestConfigurationError(ContactRequestError):
    """Required mail configuration is missing."""


class ContactRequestValidationError(ContactRequestError):
    """User payload is invalid for the chosen request type."""


class ContactRequestDeliveryError(ContactRequestError):
    """Mail or storage delivery failed."""


class ContactRequestType(StrEnum):
    BUG_REPORT = "bug_report"
    COLLABORATION_REQUEST = "collaboration_request"
    FEATURE_REQUEST = "feature_request"
    MISSING_FLYER_REQUEST = "missing_flyer_request"


class BugReportRequest(BaseModel):
    request_type: ContactRequestType = ContactRequestType.BUG_REPORT
    email: str = Field(..., min_length=3, max_length=254)
    subject: str = Field(..., min_length=3, max_length=160)
    message: str = Field(..., min_length=10, max_length=5_000)
    page_url: str | None = Field(default=None, max_length=2_000)

    _normalize_email = field_validator("email")(lambda cls, value: _validate_email(value))
    _normalize_subject = field_validator("subject")(lambda cls, value: _normalize_single_line(value))
    _normalize_message = field_validator("message")(lambda cls, value: _normalize_multiline(value))
    _normalize_page_url = field_validator("page_url")(lambda cls, value: _normalize_optional_single_line(value))


class CollaborationRequest(BaseModel):
    request_type: ContactRequestType = ContactRequestType.COLLABORATION_REQUEST
    email: str = Field(..., min_length=3, max_length=254)
    contact_name: str = Field(..., min_length=2, max_length=160)
    supermarket_name: str = Field(..., min_length=2, max_length=200)
    location: str = Field(..., min_length=2, max_length=200)
    message: str = Field(..., min_length=10, max_length=5_000)

    _normalize_email = field_validator("email")(lambda cls, value: _validate_email(value))
    _normalize_contact_name = field_validator("contact_name")(lambda cls, value: _normalize_single_line(value))
    _normalize_supermarket_name = field_validator("supermarket_name")(lambda cls, value: _normalize_single_line(value))
    _normalize_location = field_validator("location")(lambda cls, value: _normalize_single_line(value))
    _normalize_message = field_validator("message")(lambda cls, value: _normalize_multiline(value))


class FeatureRequest(BaseModel):
    request_type: ContactRequestType = ContactRequestType.FEATURE_REQUEST
    email: str = Field(..., min_length=3, max_length=254)
    subject: str = Field(..., min_length=3, max_length=160)
    message: str = Field(..., min_length=10, max_length=5_000)
    page_url: str | None = Field(default=None, max_length=2_000)

    _normalize_email = field_validator("email")(lambda cls, value: _validate_email(value))
    _normalize_subject = field_validator("subject")(lambda cls, value: _normalize_single_line(value))
    _normalize_message = field_validator("message")(lambda cls, value: _normalize_multiline(value))
    _normalize_page_url = field_validator("page_url")(lambda cls, value: _normalize_optional_single_line(value))


class MissingFlyerRequest(BaseModel):
    request_type: ContactRequestType = ContactRequestType.MISSING_FLYER_REQUEST
    email: str | None = Field(default=None, max_length=254)
    city: str = Field(..., min_length=1, max_length=200)
    supermarket: str | None = Field(default=None, max_length=200)
    flyer_url: str | None = Field(default=None, max_length=2_000)
    notes: str | None = Field(default=None, max_length=500)

    _normalize_email = field_validator("email")(lambda cls, value: _normalize_optional_email(value))
    _normalize_city = field_validator("city")(lambda cls, value: _normalize_single_line(value))
    _normalize_supermarket = field_validator("supermarket")(lambda cls, value: _normalize_optional_single_line(value))
    _normalize_flyer_url = field_validator("flyer_url")(lambda cls, value: _normalize_optional_single_line(value))
    _normalize_notes = field_validator("notes")(lambda cls, value: _normalize_optional_multiline(value))


class ContactRequestResponse(BaseModel):
    status: str


@dataclass(frozen=True)
class ContactRequestContext:
    user_id: str | None
    user_email: str | None
    user_agent: str | None


@dataclass(frozen=True)
class EmailAttachment:
    file_name: str
    content_type: str
    payload: bytes


class ContactMailer:
    def send(
        self,
        subject: str,
        text_body: str,
        html_body: str,
        attachments: Sequence[EmailAttachment] = (),
    ) -> None:
        _validate_mail_settings()
        message = self._build_message(subject, text_body, html_body, attachments)
        self._deliver(message)

    def _build_message(
        self,
        subject: str,
        text_body: str,
        html_body: str,
        attachments: Sequence[EmailAttachment],
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.mail_from
        message["To"] = settings.webmaster_email
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        for attachment in attachments:
            major_type, minor_type = attachment.content_type.split("/", 1)
            message.add_attachment(
                attachment.payload,
                maintype=major_type,
                subtype=minor_type,
                filename=attachment.file_name,
            )
        return message

    def _deliver(self, message: EmailMessage) -> None:
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except smtplib.SMTPException as exc:
            raise ContactRequestDeliveryError("Failed to send contact request email") from exc
        except OSError as exc:
            raise ContactRequestDeliveryError("Failed to connect to SMTP server") from exc


class ContactRequestService:
    def __init__(self, mailer: ContactMailer) -> None:
        self._mailer = mailer

    async def submit_bug_report(
        self,
        payload: BugReportRequest,
        context: ContactRequestContext,
        screenshots: Sequence[UploadFile],
    ) -> ContactRequestResponse:
        attachments = await _read_email_attachments(screenshots)
        self._send_bug_report(payload, context, attachments)
        return ContactRequestResponse(status="sent")

    async def submit_collaboration_request(
        self,
        payload: CollaborationRequest,
        context: ContactRequestContext,
    ) -> ContactRequestResponse:
        subject = f"[Contattaci][Collaborazione] {payload.supermarket_name} - {payload.location}"
        self._mailer.send(
            subject=subject,
            text_body=_collaboration_text(payload, context),
            html_body=_collaboration_html(payload, context),
        )
        return ContactRequestResponse(status="sent")

    async def submit_feature_request(
        self,
        payload: FeatureRequest,
        context: ContactRequestContext,
    ) -> ContactRequestResponse:
        subject = f"[Contattaci][Migliorie] {payload.subject}"
        self._mailer.send(
            subject=subject,
            text_body=_feature_request_text(payload, context),
            html_body=_feature_request_html(payload, context),
        )
        return ContactRequestResponse(status="sent")

    async def submit_missing_flyer_request(
        self,
        payload: MissingFlyerRequest,
        context: ContactRequestContext,
    ) -> ContactRequestResponse:
        subject = f"[Volantino mancante] {payload.city}"
        self._mailer.send(
            subject=subject,
            text_body=_missing_flyer_text(payload, context),
            html_body=_missing_flyer_html(payload, context),
        )
        return ContactRequestResponse(status="sent")

    def _send_bug_report(
        self,
        payload: BugReportRequest,
        context: ContactRequestContext,
        attachments: Sequence[EmailAttachment],
    ) -> None:
        subject = f"[Contattaci][Bug] {payload.subject}"
        self._mailer.send(
            subject=subject,
            text_body=_bug_report_text(payload, context, attachments),
            html_body=_bug_report_html(payload, context, attachments),
            attachments=attachments,
        )


def _validate_mail_settings() -> None:
    required = {
        "mail_from": settings.mail_from,
        "smtp_host": settings.smtp_host,
        "webmaster_email": settings.webmaster_email,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ContactRequestConfigurationError(
            f"Missing contact mail configuration: {', '.join(missing)}"
        )


def _validate_email(value: str) -> str:
    normalized = _normalize_single_line(value)
    if not _EMAIL_RE.fullmatch(normalized):
        raise ValueError("Invalid email address")
    return normalized


def _normalize_optional_email(value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_email(value)


def _normalize_optional_single_line(value: str | None) -> str | None:
    if value is None:
        return None
    return _normalize_single_line(value)


def _normalize_optional_multiline(value: str | None) -> str | None:
    if value is None:
        return None
    return _normalize_multiline(value)


def _normalize_single_line(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("New lines are not allowed")
    if _CONTROL_CHARS_RE.search(normalized):
        raise ValueError("Control characters are not allowed")
    return normalized


def _normalize_multiline(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized
    if _CONTROL_CHARS_RE.search(normalized.replace("\n", "").replace("\r", "")):
        raise ValueError("Control characters are not allowed")
    return normalized


def _validated_content_type(upload: UploadFile) -> str:
    content_type = upload.content_type or ""
    if content_type not in _ALLOWED_SCREENSHOT_TYPES:
        raise ContactRequestValidationError(
            f"Unsupported screenshot type: {content_type or 'unknown'}"
        )
    return content_type


async def _read_email_attachments(
    screenshots: Sequence[UploadFile],
) -> list[EmailAttachment]:
    attachments: list[EmailAttachment] = []
    for screenshot in screenshots:
        content_type = _validated_content_type(screenshot)
        file_name = screenshot.filename or f"screenshot.{_CONTENT_TYPE_EXTENSIONS[content_type]}"
        payload = await screenshot.read()
        attachments.append(
            EmailAttachment(
                file_name=file_name,
                content_type=content_type,
                payload=payload,
            )
        )
    return attachments


def _bug_report_text(
    payload: BugReportRequest,
    context: ContactRequestContext,
    attachments: Sequence[EmailAttachment],
) -> str:
    sections = [
        "Nuova segnalazione bug da GiroSpesa",
        "",
        f"Email contatto: {payload.email}",
        f"Oggetto: {payload.subject}",
        f"Pagina: {payload.page_url or '-'}",
        f"Utente autenticato: {context.user_id or '-'}",
        f"Email sessione: {context.user_email or '-'}",
        f"User-Agent: {context.user_agent or '-'}",
        "",
        "Descrizione:",
        payload.message,
        "",
        "Screenshot:",
    ]
    sections.extend(_attachment_text_lines(attachments))
    return "\n".join(sections)


def _bug_report_html(
    payload: BugReportRequest,
    context: ContactRequestContext,
    attachments: Sequence[EmailAttachment],
) -> str:
    return _wrap_html(
        "Nuova segnalazione bug",
        [
            _meta_row("Email contatto", payload.email),
            _meta_row("Oggetto", payload.subject),
            _meta_row("Pagina", payload.page_url or "-"),
            _meta_row("Utente autenticato", context.user_id or "-"),
            _meta_row("Email sessione", context.user_email or "-"),
            _meta_row("User-Agent", context.user_agent or "-"),
            _paragraph(payload.message),
            _attachment_html_list(attachments),
        ],
    )


def _collaboration_text(
    payload: CollaborationRequest,
    context: ContactRequestContext,
) -> str:
    return "\n".join(
        [
            "Nuova richiesta di collaborazione",
            "",
            f"Email contatto: {payload.email}",
            f"Referente: {payload.contact_name}",
            f"Supermercato: {payload.supermarket_name}",
            f"Luogo: {payload.location}",
            f"Utente autenticato: {context.user_id or '-'}",
            f"Email sessione: {context.user_email or '-'}",
            "",
            "Messaggio:",
            payload.message,
        ]
    )


def _collaboration_html(
    payload: CollaborationRequest,
    context: ContactRequestContext,
) -> str:
    return _wrap_html(
        "Nuova richiesta di collaborazione",
        [
            _meta_row("Email contatto", payload.email),
            _meta_row("Referente", payload.contact_name),
            _meta_row("Supermercato", payload.supermarket_name),
            _meta_row("Luogo", payload.location),
            _meta_row("Utente autenticato", context.user_id or "-"),
            _meta_row("Email sessione", context.user_email or "-"),
            _paragraph(payload.message),
        ],
    )


def _feature_request_text(
    payload: FeatureRequest,
    context: ContactRequestContext,
) -> str:
    return "\n".join(
        [
            "Nuova richiesta funzionalita o miglioria",
            "",
            f"Email contatto: {payload.email}",
            f"Titolo proposta: {payload.subject}",
            f"Pagina: {payload.page_url or '-'}",
            f"Utente autenticato: {context.user_id or '-'}",
            f"Email sessione: {context.user_email or '-'}",
            f"User-Agent: {context.user_agent or '-'}",
            "",
            "Descrizione:",
            payload.message,
        ]
    )


def _feature_request_html(
    payload: FeatureRequest,
    context: ContactRequestContext,
) -> str:
    return _wrap_html(
        "Nuova richiesta funzionalita o miglioria",
        [
            _meta_row("Email contatto", payload.email),
            _meta_row("Titolo proposta", payload.subject),
            _meta_row("Pagina", payload.page_url or "-"),
            _meta_row("Utente autenticato", context.user_id or "-"),
            _meta_row("Email sessione", context.user_email or "-"),
            _meta_row("User-Agent", context.user_agent or "-"),
            _paragraph(payload.message),
        ],
    )


def _missing_flyer_text(
    payload: MissingFlyerRequest,
    context: ContactRequestContext,
) -> str:
    return "\n".join(
        [
            "Nuova segnalazione volantino mancante",
            "",
            f"Città: {payload.city}",
            f"Supermercato: {payload.supermarket or '-'}",
            f"Link volantino: {payload.flyer_url or '-'}",
            f"Note: {payload.notes or '-'}",
            f"Email contatto: {payload.email or '-'}",
            f"Utente autenticato: {context.user_id or '-'}",
            f"Email sessione: {context.user_email or '-'}",
        ]
    )


def _missing_flyer_html(
    payload: MissingFlyerRequest,
    context: ContactRequestContext,
) -> str:
    return _wrap_html(
        "Nuova segnalazione volantino mancante",
        [
            _meta_row("Città", payload.city),
            _meta_row("Supermercato", payload.supermarket or "-"),
            _meta_row("Link volantino", payload.flyer_url or "-"),
            _meta_row("Note", payload.notes or "-"),
            _meta_row("Email contatto", payload.email or "-"),
            _meta_row("Utente autenticato", context.user_id or "-"),
            _meta_row("Email sessione", context.user_email or "-"),
        ],
    )


def _attachment_text_lines(attachments: Sequence[EmailAttachment]) -> list[str]:
    if not attachments:
        return ["- Nessuno"]
    return [f"- {item.file_name}" for item in attachments]


def _attachment_html_list(attachments: Sequence[EmailAttachment]) -> str:
    if not attachments:
        return "<p><strong>Screenshot:</strong> Nessuno</p>"
    items = "".join(
        f"<li>{escape(item.file_name)}</li>"
        for item in attachments
    )
    return f"<div><strong>Screenshot</strong><ul>{items}</ul></div>"


def _meta_row(label: str, value: str) -> str:
    return f"<p><strong>{escape(label)}:</strong> {escape(value)}</p>"


def _paragraph(value: str) -> str:
    safe = escape(value).replace("\n", "<br />")
    return f"<p>{safe}</p>"


def _wrap_html(title: str, sections: Sequence[str]) -> str:
    body = "".join(sections)
    return f"<html><body><h2>{escape(title)}</h2>{body}</body></html>"
