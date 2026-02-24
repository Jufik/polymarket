"""Base ingestor -- shared heartbeat infrastructure for all ingestors."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker
from polymarket_pipeline.live.ingestors._publish import safe_publish

HEARTBEAT_INTERVAL = 10.0


class BaseIngestor(ABC):
    """Shared heartbeat infrastructure for all ingestors.

    Subclasses must set ``source_name`` and implement ``run()``.
    Override ``_heartbeat_fields()`` to add source-specific heartbeat data.
    """

    source_name: str = ""  # Override in subclass

    def __init__(
        self,
        broker: Any,
        topic: str,
        status_topic: str = "pipeline.status",
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._trade_count: int = 0
        self._drops_queue_full: int = 0
        self._circuit_breaker = CircuitBreaker()
        self._log = structlog.get_logger(source=self.source_name)

    def _heartbeat_fields(self) -> dict[str, Any]:
        """Override to add source-specific fields to heartbeat."""
        return {}

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status topic."""
        payload = {
            "source": self.source_name,
            "event": "heartbeat",
            "trade_count": self._trade_count,
            "drops_queue_full": self._drops_queue_full,
            "ts": time.time(),
            **self._heartbeat_fields(),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._status_topic,
            key=self.source_name.encode(),
            source=self.source_name,
            circuit_breaker=self._circuit_breaker,
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    @abstractmethod
    async def run(self) -> None:
        """Run the ingestor. Must be implemented by subclasses."""
        ...
