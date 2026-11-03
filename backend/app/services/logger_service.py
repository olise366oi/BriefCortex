from __future__ import annotations

import logging
import sys
from typing import Optional

from app.core.config import get_settings

_configured = False


def configure_logging(force: bool = False) -> None:
    """Configure process logging once based on ENABLE_LOGS."""
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    root = logging.getLogger("briefcortex")
    root.handlers.clear()

    if settings.ENABLE_LOGS:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    else:
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.CRITICAL + 1)
        root.propagate = False

    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a namespaced logger that respects ENABLE_LOGS."""
    configure_logging()
    if name:
        return logging.getLogger(f"briefcortex.{name}")
    return logging.getLogger("briefcortex")
