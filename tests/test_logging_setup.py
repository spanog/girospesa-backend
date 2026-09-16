from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from core.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_log_level():
    root = logging.getLogger()
    original = root.level
    yield
    root.setLevel(original)


def test_production_sets_root_logger_to_warning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")

    with patch("core.logging_setup.logging.basicConfig"):
        configure_logging()

    assert logging.getLogger().level == logging.WARNING


def test_development_sets_root_logger_to_info(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    with patch("core.logging_setup.logging.basicConfig"):
        configure_logging()

    assert logging.getLogger().level == logging.INFO
