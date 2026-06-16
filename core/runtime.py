"""Runtime guards for local boot."""

from __future__ import annotations

import sys

MIN_PYTHON = (3, 11)


def ensure_supported_python(version_info: tuple[int, ...] | None = None) -> None:
    current = version_info or sys.version_info
    if current < MIN_PYTHON:
        raise RuntimeError(
            "Python 3.11+ required for girospesa-backend. "
            "Recreate .venv with `python3.11 -m venv .venv` and rerun boot."
        )
