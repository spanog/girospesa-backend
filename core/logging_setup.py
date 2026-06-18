from __future__ import annotations

import logging
import os


_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() == "production"


def configure_logging() -> None:
    root_level = logging.WARNING if _is_production() else logging.INFO
    logging.basicConfig(level=root_level, format=_LOG_FORMAT, force=False)

    if _is_production():
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)

