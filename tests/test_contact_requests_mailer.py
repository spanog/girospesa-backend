"""Unit tests for the SMTP transport used by contact requests."""

from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

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
from services.contact_requests import ContactMailer

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
