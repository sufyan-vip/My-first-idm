"""
Logging configuration.

Rotating file logs (5 x 1 MB) plus optional console output.  The file is
created lazily under the application data directory so that importing this
module never has side effects before the data directory is known.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup_logging(log_dir: str, level: int = logging.INFO, console: bool = False) -> None:
    """Configure the root ``idm`` logger once.

    Parameters
    ----------
    log_dir:
        Directory that will contain ``idm.log`` (created if missing).
    level:
        Minimum level written to the file.
    console:
        Also mirror log records to stderr (useful in CLI mode).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger("idm")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "idm.log"),
        maxBytes=1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger, e.g. ``get_logger('engine')``."""
    return logging.getLogger(f"idm.{name}")
