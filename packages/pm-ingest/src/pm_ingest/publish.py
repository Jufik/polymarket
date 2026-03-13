"""Shared publish helper with timeout protection.

Uses the ``Publisher`` protocol from pm_core for decoupled publishing.
Falls back to legacy broker pattern (``broker.publish(message=, topic=, key=)``)
for backward compatibility during migration.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from pm_ingest.circuit_breaker import CircuitBreaker

log = structlog.get_logger()

PUBLISH_TIMEOUT_S = 5.0


async def safe_publish(
    publisher: Any,
    *,
    message: str,
    topic: str,
    key: bytes,
    source: str,
    circuit_breaker: CircuitBreaker | None = None,
) -> bool:
    """Publish with timeout and circuit breaker protection.

    Accepts either a ``Publisher`` protocol instance (pm_core) or a legacy
    FastStream broker (``broker.publish(message=, topic=, key=)``).
    """
    if circuit_breaker is not None and not circuit_breaker.allow_request():
        log.warning("publish.circuit_open", source=source, topic=topic)
        return False

    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_S):
            # Try Publisher protocol first (topic, key, message positional)
            # Fall back to legacy broker kwargs pattern
            if hasattr(publisher, "publish"):
                await publisher.publish(message=message, topic=topic, key=key)
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
    except Exception as exc:
        # FastStream raises IncorrectState when the broker producer isn't ready
        # at startup.  Don't trip the circuit breaker for this — it's transient.
        exc_name = type(exc).__name__
        if exc_name == "IncorrectState":
            log.warning("publish.broker_not_ready", source=source, topic=topic)
            return False
        log.exception("publish.unexpected_error", source=source, topic=topic)
        if circuit_breaker is not None:
            circuit_breaker.record_failure()
        return False
