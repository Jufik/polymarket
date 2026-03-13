"""pm config -- read/write dynamic configuration."""

from __future__ import annotations

import structlog

log = structlog.get_logger()


def get_config(section: str) -> None:
    """Show effective config for a section."""
    log.info("config_get", section=section)


def set_config(section: str, key: str, value: str) -> None:
    """Set a runtime config override."""
    log.info("config_set", section=section, key=key, value=value)
