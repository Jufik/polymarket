"""Structured logging configuration with file + console output.

Two modes:
- ``configure_logging(log_file)`` — original single-level setup (INFO to both).
- ``configure_paper_logging(log_dir)`` — paper-trading setup:
    Console: WARNING+ only (quiet).  Key events promoted via ``paper_`` prefix.
    File:    INFO JSON lines, rotated at 50 MB x 5 backups.
"""

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


def configure_paper_logging(log_dir: Path) -> None:
    """Configure three-tier logging for paper trading.

    Console (stderr):              WARNING+ with colored human-readable output.
    File (log_dir/strategy.log):   INFO+ as JSON lines, rotated 50 MB x 5.
    File (log_dir/errors.log):     ERROR+ as JSON lines, rotated 10 MB x 3.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    # Console: WARNING+, colored, human-readable
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
    )
    console_handler.setFormatter(console_formatter)

    # File: INFO+, JSON lines, rotated
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "strategy.log",
        maxBytes=50_000_000,  # 50 MB
        backupCount=5,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(json_formatter)

    # Error file: ERROR+, JSON lines, rotated (smaller — errors should be rare)
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=10_000_000,  # 10 MB
        backupCount=3,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(json_formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
