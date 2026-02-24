"""Shared publish helper with timeout protection."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker

log = structlog.get_logger()

PUBLISH_TIMEOUT_S = 5.0


async def safe_publish(
    broker: Any,
    *,
    message: str,
    topic: str,
    key: bytes,
    source: str,
    circuit_breaker: CircuitBreaker | None = None,
) -> bool:
    """Publish with timeout and circuit breaker protection."""
    if circuit_breaker is not None and not circuit_breaker.allow_request():
        log.warning("publish.circuit_open", source=source, topic=topic)
        return False

    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_S):
            await broker.publish(message=message, topic=topic, key=key)
        if circuit_breaker is not None:
            circuit_breaker.record_success()
        return True
    except TimeoutError:
        log.warning("publish.timeout", source=source, topic=topic, timeout=PUBLISH_TIMEOUT_S)
        if circuit_breaker is not None:
            circuit_breaker.record_failure()
        return False
    except (ConnectionError, OSError) as exc:
        log.warning("publish.connection_error", source=source, topic=topic, error=str(exc))
        if circuit_breaker is not None:
            circuit_breaker.record_failure()
        return False
    except Exception:
        log.exception("publish.unexpected_error", source=source, topic=topic)
        if circuit_breaker is not None:
            circuit_breaker.record_failure()
        return False
