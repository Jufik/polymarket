"""Structured logging configuration with file + console output."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(log_file: Path | None = None) -> None:
    """Configure structlog with console + optional JSON file output."""
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
    ]

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=50_000_000,  # 50 MB
            backupCount=5,
        )
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            *processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
