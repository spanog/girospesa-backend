"""Unit tests for the SMTP transport used by contact requests."""

from __future__ import annotations

import os
import sys
import types
from socket import gaierror
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_config_mod = types.ModuleType("core.config")
_settings = SimpleNamespace(
    mail_from="info@girospesa.it",
    webmaster_email="info@girospesa.it",
    smtp_host="smtp.example.com",
    smtp_port=587,
    smtp_username="user",
    smtp_password="secret",
    smtp_use_tls=True,
    smtp_use_ssl=False,
)
_config_mod.settings = _settings  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

import services.contact_requests as _module
from services.contact_requests import ContactMailer, SmtpProbeService

_module.settings = _settings


def test_mailer_uses_starttls_for_submission_ports():
    mailer = ContactMailer()

    with patch("services.contact_requests.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value.__enter__.return_value
        mailer.send("Subject", "Text", "<p>Html</p>")

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
    smtp.ehlo.assert_called()
    smtp.starttls.assert_called_once_with()
    smtp.login.assert_called_once_with("user", "secret")
    smtp.send_message.assert_called_once()


def test_mailer_uses_implicit_ssl_when_enabled():
    _settings.smtp_port = 465
    _settings.smtp_use_tls = False
    _settings.smtp_use_ssl = True
    mailer = ContactMailer()

    with patch("services.contact_requests.smtplib.SMTP") as smtp_cls, patch(
        "services.contact_requests.smtplib.SMTP_SSL"
    ) as smtp_ssl_cls:
        smtp = smtp_ssl_cls.return_value.__enter__.return_value
        mailer.send("Subject", "Text", "<p>Html</p>")

    smtp_cls.assert_not_called()
    smtp_ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=10)
    smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with("user", "secret")
    smtp.send_message.assert_called_once()


def test_smtp_probe_reports_ssl_success():
    _settings.smtp_host = "smtps.aruba.it"
    _settings.smtp_port = 465
    _settings.smtp_use_tls = False
    _settings.smtp_use_ssl = True
    probe = SmtpProbeService()

    smtp_socket = MagicMock()
    smtp_socket.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

    with patch("services.contact_requests.socket.getaddrinfo") as getaddrinfo, patch(
        "services.contact_requests.smtplib.SMTP_SSL"
    ) as smtp_ssl_cls:
        getaddrinfo.return_value = [
            (0, 0, 0, "", ("62.149.128.200", 0)),
            (0, 0, 0, "", ("62.149.128.201", 0)),
        ]
        smtp = smtp_ssl_cls.return_value.__enter__.return_value
        smtp.ehlo.return_value = (250, b"ok")
        smtp.sock = smtp_socket

        response = probe.run(timeout_seconds=12)

    assert response.status == "ok"
    assert response.host == "smtps.aruba.it"
    assert response.port == 465
    assert response.timeout_seconds == 12
    assert response.tls_established is True
    assert response.tls_cipher == "TLS_AES_256_GCM_SHA384"
    assert response.ehlo_code == 250
    assert response.resolved_addresses == ["62.149.128.200", "62.149.128.201"]
    smtp_ssl_cls.assert_called_once_with("smtps.aruba.it", 465, timeout=12)


def test_smtp_probe_reports_dns_failure():
    probe = SmtpProbeService()

    with patch(
        "services.contact_requests.socket.getaddrinfo",
        side_effect=gaierror("lookup failed"),
    ):
        response = probe.run()

    assert response.status == "error"
    assert response.stage == "connect"
    assert response.error_type == "gaierror"
    assert response.error_message == "lookup failed"
    assert response.resolved_addresses == []
