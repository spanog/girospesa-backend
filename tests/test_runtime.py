"""Tests for runtime boot guards."""

import pytest

from core.runtime import ensure_supported_python


def test_ensure_supported_python_rejects_python_313():
    with pytest.raises(RuntimeError) as exc_info:
        ensure_supported_python((3, 13, 9))

    assert "Python 3.14+" in str(exc_info.value)


def test_ensure_supported_python_accepts_python_314():
    ensure_supported_python((3, 14, 0))
