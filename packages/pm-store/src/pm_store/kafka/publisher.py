"""FastStream Kafka publisher implementing pm_core.protocols.Publisher."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger()

_DEFAULT_TIMEOUT = 10.0  # seconds


class FastStreamPublisher:
    """Publisher implementation wrapping FastStream KafkaBroker.

    Implements pm_core.protocols.Publisher protocol::

        async def publish(self, topic: str, key: str, message: str) -> bool

    Args:
        broker: A FastStream KafkaBroker instance (already started).
        timeout: Publish timeout in seconds (default: 10s).
    """

    def __init__(self, broker: Any, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._broker = broker
        self._timeout = timeout

    async def publish(self, topic: str, key: str, message: str) -> bool:
        """Publish a message to a Kafka topic.

        Args:
            topic: Kafka topic name.
            key: Message key (used for partitioning).
            message: JSON-encoded message body.

        Returns:
            True if published successfully, False on timeout or error.
        """
        try:
            await asyncio.wait_for(
                self._broker.publish(
                    message,
                    topic=topic,
                    key=key.encode() if isinstance(key, str) else key,
                ),
                timeout=self._timeout,
            )
            return True
        except TimeoutError:
            log.error("kafka.publish_timeout", topic=topic, key=key, timeout=self._timeout)
            return False
        except Exception:
            log.exception("kafka.publish_error", topic=topic, key=key)
            return False
