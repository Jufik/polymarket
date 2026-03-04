"""Redis orderbook cache -- write/read helpers for shared orderbook state.

Stores the latest orderbook snapshot per asset in Redis with dual-key scheme:
- ``ob:asset:{asset_id}`` for outcome-specific lookups (PaperExecutor)
- ``ob:cond:{condition_id}`` for condition_id lookups (StrategyContext protocol)

All operations are fire-and-forget: failures log a warning and return gracefully.
"""

from __future__ import annotations

from typing import Any

import msgpack
import structlog

from polymarket_pipeline.strategies.types import OrderbookSnapshot

log = structlog.get_logger()

_KEY_ASSET = "ob:asset:{}"
_KEY_COND = "ob:cond:{}"


async def create_redis_client(url: str) -> Any:
    """Create an async Redis client with connection pooling.

    Returns ``None`` if the connection cannot be established.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, decode_responses=False)
        await client.ping()
        log.info("redis_orderbook.connected", url=url)
        return client
    except Exception:
        log.warning("redis_orderbook.connection_failed", url=url)
        return None


def _serialize(snapshot: dict[str, Any]) -> bytes:
    """Serialize an orderbook snapshot dict to msgpack bytes."""
    return msgpack.packb(snapshot, use_bin_type=True)


def _deserialize(raw: bytes) -> OrderbookSnapshot:
    """Deserialize msgpack bytes to an OrderbookSnapshot."""
    data = msgpack.unpackb(raw, raw=False)
    return OrderbookSnapshot(
        condition_id=data["condition_id"],
        best_bid=float(data["best_bid"]),
        best_ask=float(data["best_ask"]),
        bid_depth=float(data.get("bid_depth_usd", 0.0)),
        ask_depth=float(data.get("ask_depth_usd", 0.0)),
        timestamp=float(data["timestamp"]),
        bids=tuple(tuple(float(x) for x in lvl) for lvl in data.get("bids", [])),
        asks=tuple(tuple(float(x) for x in lvl) for lvl in data.get("asks", [])),
    )


async def write_orderbook(
    redis: Any,
    condition_id: str,
    asset_id: str,
    snapshot: dict[str, Any],
    ttl_s: int = 300,
) -> None:
    """Write orderbook to Redis with dual-key scheme. Fire-and-forget."""
    try:
        payload = _serialize(snapshot)
        pipe = redis.pipeline(transaction=False)
        pipe.set(_KEY_ASSET.format(asset_id), payload, ex=ttl_s)
        pipe.set(_KEY_COND.format(condition_id), payload, ex=ttl_s)
        await pipe.execute()
    except Exception:
        log.warning("redis_orderbook.write_error", condition_id=condition_id[:16])


async def read_by_condition(
    redis: Any, condition_id: str
) -> OrderbookSnapshot | None:
    """Read orderbook snapshot by condition_id."""
    try:
        raw = await redis.get(_KEY_COND.format(condition_id))
        if raw is None:
            return None
        return _deserialize(raw)
    except Exception:
        log.warning("redis_orderbook.read_error", key=f"cond:{condition_id[:16]}")
        return None


async def read_by_asset(
    redis: Any, asset_id: str
) -> OrderbookSnapshot | None:
    """Read orderbook snapshot by asset_id."""
    try:
        raw = await redis.get(_KEY_ASSET.format(asset_id))
        if raw is None:
            return None
        return _deserialize(raw)
    except Exception:
        log.warning("redis_orderbook.read_error", key=f"asset:{asset_id[:16]}")
        return None
