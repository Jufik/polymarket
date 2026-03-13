"""Redis-backed asset registry -- single source of truth for orderbook subscriptions.

Stores the set of asset IDs that need orderbook coverage.  Multiple discovery
mechanisms (firehose events, Gamma API sync, CH bootstrap) write to the
registry via ``add`` / ``add_many``.  The reconciliation loop reads from it
to decide which WS connections to maintain.

Redis keys (``ob:reg:`` prefix to avoid collision with orderbook cache)::

    ob:desired              SET of all desired asset_ids
    ob:desired:{group}      SET of desired asset_ids for a group
    ob:reg:cid:{cid}        SET of asset_ids for a given condition_id
    ob:reg:meta:{asset_id}  HASH {condition_id, outcome, group}

All operations are fire-and-forget: Redis failures log a warning and return
gracefully.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

log = structlog.get_logger()

DEFAULT_GROUP = "default"


class AssetRegistry:
    """Redis-backed registry of assets needing orderbook coverage."""

    DESIRED_KEY = "ob:desired"
    GROUP_PREFIX = "ob:desired:"
    CID_PREFIX = "ob:reg:cid:"
    META_PREFIX = "ob:reg:meta:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def add(
        self,
        asset_id: str,
        condition_id: str,
        outcome: str,
        group: str = DEFAULT_GROUP,
    ) -> bool:
        """Add a single asset. Returns True if newly added."""
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.sadd(self.DESIRED_KEY, asset_id)
            pipe.sadd(f"{self.GROUP_PREFIX}{group}", asset_id)
            pipe.sadd(f"{self.CID_PREFIX}{condition_id}", asset_id)
            pipe.hset(
                f"{self.META_PREFIX}{asset_id}",
                mapping={
                    "condition_id": condition_id,
                    "outcome": outcome,
                    "group": group,
                },
            )
            results = await pipe.execute()
            return bool(results[0])  # SADD returns 1 if new, 0 if exists
        except Exception:
            log.warning("asset_registry.add_error", asset_id=asset_id[:16])
            return False

    async def add_many(
        self,
        entries: list[tuple[str, str, str]],
        group: str = DEFAULT_GROUP,
    ) -> int:
        """Bulk add (asset_id, condition_id, outcome) tuples.

        Returns the number of newly added assets.
        """
        if not entries:
            return 0
        try:
            pipe = self._redis.pipeline(transaction=False)
            for asset_id, condition_id, outcome in entries:
                pipe.sadd(self.DESIRED_KEY, asset_id)
                pipe.sadd(f"{self.GROUP_PREFIX}{group}", asset_id)
                pipe.sadd(f"{self.CID_PREFIX}{condition_id}", asset_id)
                pipe.hset(
                    f"{self.META_PREFIX}{asset_id}",
                    mapping={
                        "condition_id": condition_id,
                        "outcome": outcome,
                        "group": group,
                    },
                )
            results = await pipe.execute()
            return sum(1 for i in range(0, len(results), 4) if results[i])
        except Exception:
            log.warning("asset_registry.add_many_error", count=len(entries))
            return 0

    async def remove_by_condition(self, condition_id: str) -> set[str]:
        """Remove all assets for a condition_id.  Returns removed asset_ids."""
        try:
            raw = await self._redis.smembers(f"{self.CID_PREFIX}{condition_id}")
            if not raw:
                return set()

            aid_list = {v.decode() if isinstance(v, bytes) else v for v in raw}

            groups_by_aid: dict[str, str] = {}
            for aid in aid_list:
                try:
                    val = await self._redis.hget(f"{self.META_PREFIX}{aid}", "group")
                    if val is not None:
                        groups_by_aid[aid] = val.decode() if isinstance(val, bytes) else val
                except Exception:
                    pass

            pipe = self._redis.pipeline(transaction=False)
            pipe.srem(self.DESIRED_KEY, *aid_list)
            pipe.delete(f"{self.CID_PREFIX}{condition_id}")
            for aid in aid_list:
                grp = groups_by_aid.get(aid)
                if grp:
                    pipe.srem(f"{self.GROUP_PREFIX}{grp}", aid)
                pipe.delete(f"{self.META_PREFIX}{aid}")
            await pipe.execute()

            log.info(
                "asset_registry.removed",
                condition_id=condition_id[:16],
                assets=len(aid_list),
            )
            return aid_list
        except Exception:
            log.warning("asset_registry.remove_error", condition_id=condition_id[:16])
            return set()

    async def get_desired(self) -> set[str]:
        """Return the full desired set (SMEMBERS)."""
        try:
            raw = await self._redis.smembers(self.DESIRED_KEY)
            return {v.decode() if isinstance(v, bytes) else v for v in raw}
        except Exception:
            log.warning("asset_registry.get_desired_error")
            raise

    async def get_desired_for_group(self, group: str) -> set[str]:
        """Return desired assets for a specific group (SMEMBERS)."""
        try:
            raw = await self._redis.smembers(f"{self.GROUP_PREFIX}{group}")
            return {v.decode() if isinstance(v, bytes) else v for v in raw}
        except Exception:
            log.warning("asset_registry.get_desired_group_error", group=group)
            raise

    async def get_condition_id(self, asset_id: str) -> str | None:
        """Lookup condition_id for an asset (HGET)."""
        try:
            val = await self._redis.hget(f"{self.META_PREFIX}{asset_id}", "condition_id")
            if val is None:
                return None
            return val.decode() if isinstance(val, bytes) else val
        except Exception:
            log.warning("asset_registry.meta_error", asset_id=asset_id[:16])
            return None

    async def count(self) -> int:
        """Number of desired assets (SCARD)."""
        try:
            return await self._redis.scard(self.DESIRED_KEY)
        except Exception:
            return -1


class FilteredRegistryView:
    """Thin wrapper that filters ``get_desired()`` by group and/or shard hash.

    Drop-in replacement for :class:`AssetRegistry` in the
    :class:`Reconciler` — it only needs ``get_desired()``.
    """

    def __init__(
        self,
        registry: AssetRegistry,
        group: str | None = None,
        shard: int = 0,
        total_shards: int = 1,
    ) -> None:
        self._registry = registry
        self._group = group
        self._shard = shard
        self._total_shards = total_shards

    async def get_desired(self) -> set[str]:
        """Return desired assets filtered by group and/or shard."""
        if self._group is not None:
            desired = await self._registry.get_desired_for_group(self._group)
        else:
            desired = await self._registry.get_desired()

        if self._total_shards > 1:
            desired = {
                aid
                for aid in desired
                if int(hashlib.md5(aid.encode()).hexdigest(), 16) % self._total_shards
                == self._shard
            }
        return desired
