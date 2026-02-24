"""Shared publish helper with timeout protection."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger()

PUBLISH_TIMEOUT_S = 5.0


async def safe_publish(
    broker: Any,
    *,
    message: str,
    topic: str,
    key: bytes,
    source: str,
) -> bool:
    """Publish with timeout. Returns True on success, False on timeout."""
    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_S):
            await broker.publish(message=message, topic=topic, key=key)
        return True
    except TimeoutError:
        log.warning("publish.timeout", source=source, topic=topic, timeout=PUBLISH_TIMEOUT_S)
        return False
