"""Tests for runtime boot guards."""

import pytest

from core.runtime import ensure_supported_python


def test_ensure_supported_python_rejects_python_310():
    with pytest.raises(RuntimeError) as exc_info:
        ensure_supported_python((3, 10, 9))

    assert "Python 3.11+" in str(exc_info.value)


def test_ensure_supported_python_accepts_python_311():
    ensure_supported_python((3, 11, 0))
