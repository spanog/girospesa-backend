from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from starlette.datastructures import UploadFile

from services.contact_requests import (
    BugReportRequest,
    CollaborationRequest,
    ContactRequestContext,
    ContactRequestService,
    ContactRequestValidationError,
)


def _upload_file(
    name: str = "bug.png",
    content_type: str = "image/png",
) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(b"img"), headers={"content-type": content_type})


@pytest.mark.asyncio
async def test_bug_report_without_screenshots_still_sends_mail():
    mailer = MagicMock()
    service = ContactRequestService(mailer=mailer)
    payload = BugReportRequest(
        email="user@example.com",
        subject="Crash login",
        message="La pagina si blocca dopo click sul bottone login.",
    )

    response = await service.submit_bug_report(
        payload,
        ContactRequestContext(None, None, None),
        [],
    )

    assert response.status == "sent"
    mailer.send.assert_called_once()


@pytest.mark.asyncio
async def test_bug_report_sends_email_attachments():
    mailer = MagicMock()
    service = ContactRequestService(mailer=mailer)
    payload = BugReportRequest(
        email="user@example.com",
        subject="Crash login",
        message="La pagina si blocca dopo click sul bottone login.",
    )

    await service.submit_bug_report(
        payload,
        ContactRequestContext("user-1", "user@example.com", "pytest"),
        [_upload_file()],
    )

    attachments = mailer.send.call_args.kwargs["attachments"]
    assert len(attachments) == 1
    assert attachments[0].file_name == "bug.png"
    assert attachments[0].content_type == "image/png"


@pytest.mark.asyncio
async def test_bug_report_rejects_invalid_content_type():
    service = ContactRequestService(mailer=MagicMock())
    with pytest.raises(ContactRequestValidationError):
        await service.submit_bug_report(
            BugReportRequest(
                email="user@example.com",
                subject="Crash login",
                message="La pagina si blocca dopo click sul bottone login.",
            ),
            ContactRequestContext(None, None, None),
            [_upload_file(content_type="text/plain")],
        )


def test_single_line_fields_reject_header_injection():
    with pytest.raises(Exception):
        BugReportRequest(
            email="user@example.com",
            subject="Bug\r\nBcc: attacker@example.com",
            message="Descrizione valida di almeno dieci caratteri.",
        )


def test_email_fields_reject_invalid_email():
    with pytest.raises(Exception):
        CollaborationRequest(
            email="user@example.com\r\nBcc: attacker@example.com",
            contact_name="Mario Rossi",
            supermarket_name="Coop",
            location="Milano",
            message="Messaggio valido di almeno dieci caratteri.",
        )
