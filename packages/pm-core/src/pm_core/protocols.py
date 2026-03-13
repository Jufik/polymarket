"""Cross-package protocol definitions for dependency inversion."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pm_core.models import Event, Market, NormalizedTrade


@runtime_checkable
class Publisher(Protocol):
    """Decouples ingestors from Kafka."""

    async def publish(self, topic: str, key: str, message: str) -> bool: ...


@runtime_checkable
class TradeSink(Protocol):
    """Write trades to storage (CH, file, mock)."""

    async def insert_trades(self, trades: Sequence[NormalizedTrade]) -> int: ...


@runtime_checkable
class TradeQuery(Protocol):
    """Read trades from storage."""

    async def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...


@runtime_checkable
class MetadataSink(Protocol):
    """Write metadata to storage (PG)."""

    async def upsert_events(self, events: Sequence[Event]) -> int: ...
    async def upsert_markets(self, markets: Sequence[Market]) -> int: ...
    async def fetch_token_map(self) -> dict[str, tuple[str, str]]: ...


@runtime_checkable
class Checkpoint(Protocol):
    """Resume support for long-running operations."""

    def save(self, cursor: str, progress: int, metadata: dict[str, Any]) -> None: ...
    def load(self) -> tuple[str, int, dict[str, Any]] | None: ...


@runtime_checkable
class BookReader(Protocol):
    """Read orderbook from SharedMemory (zero-copy)."""

    def get(self, asset_id: str) -> dict[str, Any] | None: ...
    def stale_ns(self, asset_id: str) -> int | None: ...


@runtime_checkable
class BookWriter(Protocol):
    """Write orderbook to SharedMemory."""

    def update(
        self,
        asset_id: str,
        bid: float,
        ask: float,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        bid_depth: float,
        ask_depth: float,
    ) -> None: ...
