from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure structured logging with console and rotating file output."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if log_dir is None:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler — 5MB per file, keep 5 backups
    file_handler = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.CRITICAL)
    logging.getLogger("hyperliquid").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger."""
    return logging.getLogger(name)
