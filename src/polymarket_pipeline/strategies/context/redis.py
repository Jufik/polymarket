"""Redis-backed StrategyContext -- orderbook reads from Redis, everything else in-memory."""

from __future__ import annotations

from typing import Any

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
)


class RedisContext:
    """StrategyContext using Redis for orderbooks and InMemoryContext for the rest.

    Satisfies the ``StrategyContext`` protocol structurally (duck typing).
    ``get_orderbook_by_asset`` is async (unlike InMemoryContext's sync version)
    to support the Redis round-trip.

    Falls back to the inner ``InMemoryContext`` when Redis returns ``None``
    (key expired, Redis down, etc.).
    """

    __slots__ = ("_inner", "_redis")

    def __init__(self, redis: Any, inner: InMemoryContext | None = None) -> None:
        self._redis = redis
        self._inner = inner or InMemoryContext()

    # ------------------------------------------------------------------
    # Protocol methods (async reads)
    # ------------------------------------------------------------------

    async def get_position(self, condition_id: str) -> Position | None:
        return await self._inner.get_position(condition_id)

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return await self._inner.get_market(condition_id)

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        from polymarket_pipeline.live.redis_orderbook import read_by_condition

        ob = await read_by_condition(self._redis, condition_id)
        if ob is not None:
            return ob
        return await self._inner.get_orderbook(condition_id)

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return await self._inner.get_price(condition_id, outcome)

    async def now(self) -> float:
        return await self._inner.now()

    async def get_features(self, key: str) -> Any:
        return await self._inner.get_features(key)

    # ------------------------------------------------------------------
    # Duck-typed methods (not in protocol, used by PaperExecutor/LiveRunner)
    # ------------------------------------------------------------------

    async def get_orderbook_by_asset(self, asset_id: str) -> OrderbookSnapshot | None:
        """Async orderbook lookup by asset_id. Redis first, inner fallback."""
        from polymarket_pipeline.live.redis_orderbook import read_by_asset

        ob = await read_by_asset(self._redis, asset_id)
        if ob is not None:
            return ob
        return self._inner.get_orderbook_by_asset(asset_id)

    # ------------------------------------------------------------------
    # Mutation methods (delegated to inner)
    # ------------------------------------------------------------------

    def set_position(self, condition_id: str, position: Position) -> None:
        self._inner.set_position(condition_id, position)

    def set_market(self, condition_id: str, market: MarketInfo) -> None:
        self._inner.set_market(condition_id, market)

    def set_time(self, t: float) -> None:
        self._inner.set_time(t)

    def set_orderbook(
        self, condition_id: str, ob: OrderbookSnapshot, asset_id: str | None = None,
    ) -> None:
        self._inner.set_orderbook(condition_id, ob, asset_id=asset_id)

    @property
    def _markets(self) -> dict[str, MarketInfo]:
        """Proxy for LiveRunner._update_market_price() which accesses ctx._markets."""
        return self._inner._markets  # noqa: SLF001

    def get_all_positions(self) -> dict[str, Position]:
        return self._inner.get_all_positions()

    def update_features(self, features: dict[str, Any]) -> None:
        self._inner.update_features(features)
