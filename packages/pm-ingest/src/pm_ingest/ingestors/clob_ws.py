"""CLOB WebSocket orderbook ingestor -- price data + market events from Polymarket.

Runs two kinds of WebSocket connections:

1. **Firehose** (empty arrays, ``custom_feature_enabled=True``):
   Receives broadcast events (``new_market``, ``market_resolved``) and publishes
   them to the ``markets.events`` Kafka topic.

2. **Managed slot pool** (N fixed slots, each <=500 asset IDs):
   Receives ``book`` snapshots and ``price_change`` events for subscribed assets.
   Maintains an in-memory L2 orderbook per asset via plain Python dicts.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
import websockets

from pm_ingest.asset_registry import AssetRegistry
from pm_ingest.base import BaseIngestor
from pm_ingest.ingestors.managed_slot import ManagedSlot
from pm_ingest.publish import safe_publish
from pm_ingest.reconciler import Reconciler

if TYPE_CHECKING:
    from pm_core.protocols import BookWriter

log = structlog.get_logger()

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
_FIREHOSE_STALE_TIMEOUT = 120.0  # Force reconnect if firehose silent for 2 min
_PUBLISH_DEPTH = 10  # Levels published to Kafka
_OWNER_STALE_S = 5.0  # Transfer asset ownership after 5s silence from active slot


class CLOBOrderbookIngestor(BaseIngestor):
    """CLOB WS ingestor: firehose for market events + managed slot pool for orderbooks."""

    source_name = "clob_orderbook"

    def __init__(
        self,
        publisher: Any | None = None,
        registry: AssetRegistry | None = None,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        topic: str = "orderbooks.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        markets_events_topic: str = "markets.events",
        max_slots: int = 4,
        redis_client: Any | None = None,
        redis_orderbook_ttl_s: int = 300,
        slot_stale_timeout_s: float = 120.0,
        reconcile_interval_s: float = 30.0,
        book_refresh_s: float = 300.0,
        firehose_enabled: bool = True,
        reconcile_registry: Any | None = None,
        redundancy: int = 1,
        flush_interval_s: float = 0.01,
        book_writer: BookWriter | None = None,
        *,
        broker: Any = None,
    ) -> None:
        super().__init__(publisher=publisher, topic=topic, status_topic=status_topic, broker=broker)
        self._ws_url = ws_url
        self._registry = registry
        self._reconcile_registry = reconcile_registry or registry
        self._token_map = token_market_map or {}
        self._markets_events_topic = markets_events_topic
        self._max_slots = max_slots
        self._firehose_enabled = firehose_enabled
        self._update_count: int = 0
        self._market_event_count: int = 0
        # L2 orderbook state — plain dicts (no C library)
        self._bid_levels: dict[str, dict[float, float]] = {}  # asset_id -> {price: size}
        self._ask_levels: dict[str, dict[float, float]] = {}  # asset_id -> {price: size}
        self._prev_tops: dict[str, tuple[Any, ...]] = {}
        self._skipped_unchanged: int = 0
        self._dropped_inverted: int = 0
        # Redis orderbook cache
        self._redis = redis_client
        self._redis_ttl_s = redis_orderbook_ttl_s
        self._redis_writes: int = 0
        self._ltp_count: int = 0
        self._bba_count: int = 0
        # Slot pool + reconciler (created in run())
        self._slot_stale_timeout_s = slot_stale_timeout_s
        self._reconcile_interval_s = reconcile_interval_s
        self._book_refresh_s = book_refresh_s
        self._redundancy = max(1, redundancy)
        self._slots: list[ManagedSlot] = []
        self._reconciler: Reconciler | None = None
        # Batched I/O flush
        self._flush_interval_s = flush_interval_s
        self._dirty_obs: dict[str, tuple[str, dict[str, Any]]] = {}
        self._dirty_ltp: dict[str, tuple[float, float]] = {}
        self._dirty_tip: dict[str, tuple[float, float, float]] = {}
        self._pending_kafka: list[tuple[str, str, bytes]] = []
        self._flush_count: int = 0
        self._kafka_flush_count: int = 0
        # Per-asset slot ownership
        self._asset_owner: dict[str, int] = {}
        self._asset_owner_at: dict[str, float] = {}
        self._ownership_transfers: int = 0
        self._ownership_drops: int = 0
        # Shared memory orderbook writer (optional, additive to Redis)
        self._book_writer = book_writer
        self._shmem_writes: int = 0

    def wake_reconciler(self) -> None:
        """Trigger immediate reconciliation from external callers."""
        if self._reconciler is not None:
            self._reconciler.wake()

    # -- L2 book helpers ----------------------------------------------------

    def _extract_top_n(
        self, asset_id: str, n: int = _PUBLISH_DEPTH
    ) -> tuple[list[list[float]], list[list[float]]]:
        """Extract top-N bid/ask levels from plain dicts."""
        bids_dict = self._bid_levels.get(asset_id, {})
        asks_dict = self._ask_levels.get(asset_id, {})
        bids = [[p, s] for p, s in sorted(bids_dict.items(), reverse=True)[:n]]
        asks = [[p, s] for p, s in sorted(asks_dict.items())[:n]]
        return bids, asks

    def _clear_book(self, asset_id: str) -> None:
        """Clear L2 book state only (not ownership tracking)."""
        self._bid_levels.pop(asset_id, None)
        self._ask_levels.pop(asset_id, None)
        self._prev_tops.pop(asset_id, None)

    def remove_asset(self, asset_id: str) -> None:
        """Clean up all state for an asset including ownership."""
        self._clear_book(asset_id)
        self._asset_owner.pop(asset_id, None)
        self._asset_owner_at.pop(asset_id, None)

    def _clear_assets(self, asset_ids: set[str]) -> None:
        """Clear book state for a batch of assets (called on slot reconnect)."""
        for asset_id in asset_ids:
            self._clear_book(asset_id)

    # -- per-asset ownership (redundancy > 1) --------------------------------

    def _accept_for_asset(self, asset_id: str, slot_id: int) -> bool:
        """Check whether *slot_id* may feed data for *asset_id*."""
        now = time.monotonic()
        current_owner = self._asset_owner.get(asset_id)

        if current_owner is None:
            self._asset_owner[asset_id] = slot_id
            self._asset_owner_at[asset_id] = now
            return True

        if current_owner == slot_id:
            self._asset_owner_at[asset_id] = now
            return True

        last_seen = self._asset_owner_at.get(asset_id, 0.0)
        if (now - last_seen) > _OWNER_STALE_S:
            self._clear_book(asset_id)
            self._asset_owner[asset_id] = slot_id
            self._asset_owner_at[asset_id] = now
            self._ownership_transfers += 1
            log.info(
                "ownership.transfer",
                asset=asset_id[:12],
                from_slot=current_owner,
                to_slot=slot_id,
                stale_s=round(now - last_seen, 2),
            )
            return True

        self._ownership_drops += 1
        return False

    def _make_slot_handler(self, slot_id: int) -> Callable[[str], Awaitable[None]]:
        """Create a per-slot message handler closure capturing *slot_id*."""

        async def handler(raw: str) -> None:
            await self._handle_slot_message(slot_id, raw)

        return handler

    async def _handle_slot_message(self, slot_id: int, raw: str) -> None:
        """Process a WS message from a specific slot (with ownership gating)."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(msg, list):
            for entry in msg:
                if isinstance(entry, dict) and "asset_id" in entry:
                    await self._process_book_snapshot(entry, slot_id=slot_id)
            return

        if not isinstance(msg, dict):
            return

        event_type = msg.get("event_type")
        if event_type == "book":
            await self._process_book_snapshot(msg, slot_id=slot_id)
        elif "price_changes" in msg or event_type == "price_change":
            await self._process_price_change(msg, slot_id=slot_id)
        elif event_type == "last_trade_price":
            await self._process_last_trade_price(msg, slot_id=slot_id)
        elif event_type == "best_bid_ask":
            await self._process_best_bid_ask(msg, slot_id=slot_id)
        elif "question" in msg and "market" in msg:
            await self._process_new_market(msg)
        elif event_type in ("market_resolved", "new_market"):
            await self._process_market_event(msg)

    # -- message handling (firehose -- no ownership) -------------------------

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WS message, routing by structure (firehose path)."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(msg, list):
            for entry in msg:
                if isinstance(entry, dict) and "asset_id" in entry:
                    await self._process_book_snapshot(entry)
            return

        if not isinstance(msg, dict):
            return

        event_type = msg.get("event_type")
        if event_type == "book":
            await self._process_book_snapshot(msg)
        elif "price_changes" in msg or event_type == "price_change":
            await self._process_price_change(msg)
        elif event_type == "last_trade_price":
            await self._process_last_trade_price(msg)
        elif event_type == "best_bid_ask":
            await self._process_best_bid_ask(msg)
        elif "question" in msg and "market" in msg:
            await self._process_new_market(msg)
        elif event_type in ("market_resolved", "new_market"):
            await self._process_market_event(msg)

    async def _process_book_snapshot(
        self,
        event: dict[str, Any],
        *,
        slot_id: int | None = None,
    ) -> None:
        """Process an initial orderbook snapshot."""
        asset_id = event.get("asset_id")
        if not asset_id:
            return

        if slot_id is not None and not self._accept_for_asset(asset_id, slot_id):
            return

        self._clear_book(asset_id)

        for lvl in event.get("bids") or []:
            price = _safe_float(lvl.get("price"))
            size = _safe_float(lvl.get("size"))
            if price is not None and size is not None and size > 0:
                self._bid_levels.setdefault(asset_id, {})[price] = size

        for lvl in event.get("asks") or []:
            price = _safe_float(lvl.get("price"))
            size = _safe_float(lvl.get("size"))
            if price is not None and size is not None and size > 0:
                self._ask_levels.setdefault(asset_id, {})[price] = size

        mapping = self._token_map.get(asset_id)
        condition_id = mapping[0] if mapping else event.get("market", asset_id)
        await self._maybe_publish(condition_id, asset_id, force=True)

    async def _process_price_change(
        self,
        event: dict[str, Any],
        *,
        slot_id: int | None = None,
    ) -> None:
        """Apply incremental price changes, then publish if top-of-book changed."""
        for change in event.get("price_changes") or []:
            asset_id = change.get("asset_id")
            if not asset_id:
                continue

            if slot_id is not None and not self._accept_for_asset(asset_id, slot_id):
                continue

            price = _safe_float(change.get("price"))
            size = _safe_float(change.get("size"))
            side = change.get("side")

            if price is not None and side is not None:
                if side == "BUY":
                    levels = self._bid_levels.setdefault(asset_id, {})
                    if size is not None and size > 0:
                        levels[price] = size
                    else:
                        levels.pop(price, None)
                elif side == "SELL":
                    levels = self._ask_levels.setdefault(asset_id, {})
                    if size is not None and size > 0:
                        levels[price] = size
                    else:
                        levels.pop(price, None)
            else:
                best_bid = _safe_float(change.get("best_bid"))
                best_ask = _safe_float(change.get("best_ask"))
                if best_bid is None or best_ask is None:
                    continue
                self._bid_levels[asset_id] = {best_bid: 1.0}
                self._ask_levels[asset_id] = {best_ask: 1.0}

            mapping = self._token_map.get(asset_id)
            condition_id = mapping[0] if mapping else event.get("market", asset_id)
            await self._maybe_publish(condition_id, asset_id)

    async def _process_last_trade_price(
        self,
        event: dict[str, Any],
        *,
        slot_id: int | None = None,
    ) -> None:
        """Write CLOB WS ``last_trade_price`` to Redis + Kafka."""
        asset_id = event.get("asset_id")
        if not asset_id:
            return

        if slot_id is not None and not self._accept_for_asset(asset_id, slot_id):
            return

        price = _safe_float(event.get("price"))
        if price is None:
            return

        now = time.time()
        if self._redis is not None:
            self._dirty_ltp[asset_id] = (price, now)

        mapping = self._token_map.get(asset_id) if self._token_map else None
        condition_id = mapping[0] if mapping else ""
        self._pending_kafka.append(
            (
                json.dumps(
                    {
                        "asset_id": asset_id,
                        "condition_id": condition_id,
                        "source": "ltp",
                        "bid": price,
                        "ask": price,
                        "price": price,
                        "timestamp": now,
                    }
                ),
                "prices.ticks",
                asset_id.encode(),
            )
        )

        self._ltp_count += 1

    async def _process_best_bid_ask(
        self,
        event: dict[str, Any],
        *,
        slot_id: int | None = None,
    ) -> None:
        """Write CLOB WS ``best_bid_ask`` to Redis + Kafka."""
        asset_id = event.get("asset_id")
        if not asset_id:
            return

        if slot_id is not None and not self._accept_for_asset(asset_id, slot_id):
            return

        best_bid = _safe_float(event.get("best_bid"))
        best_ask = _safe_float(event.get("best_ask"))
        if best_bid is None or best_ask is None:
            return

        now = time.time()
        if self._redis is not None:
            self._dirty_tip[asset_id] = (best_bid, best_ask, now)

        mapping = self._token_map.get(asset_id) if self._token_map else None
        condition_id = mapping[0] if mapping else ""
        mid = (best_bid + best_ask) / 2
        self._pending_kafka.append(
            (
                json.dumps(
                    {
                        "asset_id": asset_id,
                        "condition_id": condition_id,
                        "source": "bba_tip",
                        "bid": best_bid,
                        "ask": best_ask,
                        "price": mid,
                        "timestamp": now,
                    }
                ),
                "prices.ticks",
                asset_id.encode(),
            )
        )

        self._bba_count += 1

    async def _maybe_publish(
        self,
        condition_id: str,
        asset_id: str,
        force: bool = False,
    ) -> None:
        """Publish orderbook to Kafka (deduped) and Redis (batched)."""
        bids, asks = self._extract_top_n(asset_id)

        if not bids and not asks:
            return

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0

        if best_bid > 0 and best_ask > 0 and best_bid >= best_ask:
            self._dropped_inverted += 1
            return

        bid_depth_usd = sum(p * s for p, s in bids)
        ask_depth_usd = sum(p * s for p, s in asks)

        snapshot = {
            "condition_id": condition_id,
            "asset_id": asset_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bids": bids,
            "asks": asks,
            "bid_depth_usd": bid_depth_usd,
            "ask_depth_usd": ask_depth_usd,
            "timestamp": time.time(),
        }

        if self._redis is not None:
            self._dirty_obs[asset_id] = (condition_id, snapshot)

        # Shared memory write — ~0.1ms sync, does NOT starve the event loop
        if self._book_writer is not None:
            try:
                self._book_writer.update(
                    asset_id,
                    best_bid,
                    best_ask,
                    [(p, s) for p, s in bids],
                    [(p, s) for p, s in asks],
                    bid_depth_usd,
                    ask_depth_usd,
                )
                self._shmem_writes += 1
            except Exception:
                log.warning("shmem.write_error", asset_id=asset_id[:12])

        fingerprint = (
            tuple(tuple(lvl) for lvl in bids),
            tuple(tuple(lvl) for lvl in asks),
        )

        if not force:
            prev = self._prev_tops.get(asset_id)
            if prev == fingerprint:
                self._skipped_unchanged += 1
                return

        self._prev_tops[asset_id] = fingerprint

        self._pending_kafka.append(
            (
                json.dumps(snapshot),
                self._topic,
                condition_id.encode(),
            )
        )
        self._update_count += 1

    async def _process_new_market(self, event: dict[str, Any]) -> None:
        """Forward new market broadcast to the events topic + register assets."""
        condition_id = event.get("market", "")
        payload = {
            "type": "new_market",
            "condition_id": condition_id,
            "payload": event,
            "timestamp": time.time(),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode() if condition_id else b"unknown",
            source="clob_orderbook",
        )
        self._market_event_count += 1

        new_aids = event.get("assets_ids") or []
        if not new_aids and condition_id and self._token_map:
            new_aids = [aid for aid, (cid, _) in self._token_map.items() if cid == condition_id]
        if new_aids and self._registry is not None:
            for aid in new_aids:
                mapping = self._token_map.get(aid)
                outcome = mapping[1] if mapping else "?"
                await self._registry.add(aid, condition_id, outcome)
            self.wake_reconciler()

    async def _process_market_event(self, event: dict[str, Any]) -> None:
        """Forward market_resolved / new_market events to the events topic."""
        condition_id = event.get("condition_id", "")
        payload = {
            "type": event["event_type"],
            "condition_id": condition_id,
            "payload": event,
            "timestamp": event.get("timestamp", time.time()),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode() if condition_id else b"unknown",
            source="clob_orderbook",
        )
        self._market_event_count += 1

        if event["event_type"] == "new_market":
            new_aids = event.get("assets_ids") or []
            if not new_aids and condition_id and self._token_map:
                new_aids = [aid for aid, (cid, _) in self._token_map.items() if cid == condition_id]
            if new_aids and self._registry is not None:
                for aid in new_aids:
                    mapping = self._token_map.get(aid)
                    outcome = mapping[1] if mapping else "?"
                    await self._registry.add(aid, condition_id, outcome)
                self.wake_reconciler()

        elif event["event_type"] == "market_resolved":
            if self._registry is not None:
                await self._registry.remove_by_condition(condition_id)
            self.wake_reconciler()

    async def _flush_loop(self) -> None:
        """Drain all pending I/O (Redis + Kafka) in batched bursts."""
        import msgpack

        from polymarket_pipeline.live.redis_orderbook import (
            _KEY_ASSET,
            _KEY_COND,
            _KEY_LTP,
            _KEY_TIP,
            _serialize,
        )

        while True:
            await asyncio.sleep(self._flush_interval_s)

            has_redis = self._redis is not None and (
                self._dirty_obs or self._dirty_ltp or self._dirty_tip
            )
            has_kafka = bool(self._pending_kafka)

            if not has_redis and not has_kafka:
                continue

            if has_redis:
                obs = self._dirty_obs
                ltps = self._dirty_ltp
                tips = self._dirty_tip
                self._dirty_obs = {}
                self._dirty_ltp = {}
                self._dirty_tip = {}
                try:
                    pipe = self._redis.pipeline(transaction=False)
                    for asset_id, (cid, snap) in obs.items():
                        payload = _serialize(snap)
                        pipe.set(_KEY_ASSET.format(asset_id), payload, ex=self._redis_ttl_s)
                        pipe.set(_KEY_COND.format(cid), payload, ex=self._redis_ttl_s)
                    for asset_id, (price, ts) in ltps.items():
                        payload = msgpack.packb(
                            {"price": price, "timestamp": ts},
                            use_bin_type=True,
                        )
                        pipe.set(_KEY_LTP.format(asset_id), payload, ex=120)
                    for asset_id, (bid, ask, ts) in tips.items():
                        payload = msgpack.packb(
                            {"best_bid": bid, "best_ask": ask, "timestamp": ts},
                            use_bin_type=True,
                        )
                        pipe.set(_KEY_TIP.format(asset_id), payload, ex=120)
                    await pipe.execute()
                    self._redis_writes += len(obs) + len(ltps) + len(tips)
                except Exception:
                    log.warning(
                        "flush.redis_error",
                        obs=len(obs),
                        ltps=len(ltps),
                        tips=len(tips),
                    )
                self._flush_count += 1

            if has_kafka:
                msgs = self._pending_kafka
                self._pending_kafka = []
                for message, topic, key in msgs:
                    await safe_publish(
                        self._broker,
                        message=message,
                        topic=topic,
                        key=key,
                        source="clob_orderbook",
                    )
                self._kafka_flush_count += 1

    async def drain_pending(self) -> None:
        """Flush all pending Kafka messages immediately (for tests)."""
        msgs = self._pending_kafka
        self._pending_kafka = []
        for message, topic, key in msgs:
            await safe_publish(
                self._broker,
                message=message,
                topic=topic,
                key=key,
                source="clob_orderbook",
            )

    def _heartbeat_fields(self) -> dict[str, Any]:
        slot_states = [s.state for s in self._slots]
        reconciler_stats = self._reconciler.stats() if self._reconciler else {}
        return {
            "update_count": self._update_count,
            "market_event_count": self._market_event_count,
            "assets_tracked": len(self._bid_levels),
            "skipped_unchanged": self._skipped_unchanged,
            "dropped_inverted": self._dropped_inverted,
            "redis_writes": self._redis_writes,
            "flush_count": self._flush_count,
            "kafka_flush_count": self._kafka_flush_count,
            "pending_redis": len(self._dirty_obs) + len(self._dirty_ltp) + len(self._dirty_tip),
            "pending_kafka": len(self._pending_kafka),
            "ltp_count": self._ltp_count,
            "bba_count": self._bba_count,
            "slots": [
                {
                    "id": s.slot_id,
                    "desired": s.desired_assets,
                    "subscribed": s.subscribed_assets,
                    "connected": s.connected,
                    "msgs": s.msg_count,
                    "reconnects": s.reconnect_count,
                    "rotations": slot._rotation_count,
                }
                for s, slot in zip(slot_states, self._slots, strict=False)
            ],
            "total_subscribed": sum(s.subscribed_assets for s in slot_states),
            "total_desired": sum(s.desired_assets for s in slot_states),
            "ownership_transfers": self._ownership_transfers,
            "ownership_drops": self._ownership_drops,
            "owned_assets": len(self._asset_owner),
            "shmem_writes": self._shmem_writes,
            **reconciler_stats,
        }

    # -- connection management -----------------------------------------------

    async def _run_firehose(self) -> None:
        """Firehose connection: empty arrays -> broadcast events only."""
        backoff = RECONNECT_BASE
        while True:
            ping_task: asyncio.Task[None] | None = None
            try:
                async with websockets.connect(self._ws_url, ping_interval=None) as ws:
                    backoff = RECONNECT_BASE
                    payload = {
                        "type": "market",
                        "markets": [],
                        "assets_ids": [],
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(payload))
                    log.info("clob_orderbook.firehose_connected")

                    async def _firehose_ping() -> None:
                        while True:
                            await asyncio.sleep(10.0)
                            try:
                                await ws.send("PING")
                            except Exception:
                                return

                    ping_task = asyncio.create_task(_firehose_ping())

                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=_FIREHOSE_STALE_TIMEOUT)
                        except TimeoutError:
                            log.warning(
                                "clob_orderbook.firehose_stale",
                                timeout_s=_FIREHOSE_STALE_TIMEOUT,
                            )
                            break
                        await self._handle_message(str(raw))
            except websockets.ConnectionClosed as exc:
                log.warning(
                    "clob_orderbook.firehose_disconnected",
                    code=exc.code,
                    reason=exc.reason[:120] if exc.reason else "",
                    backoff=backoff,
                )
            except Exception:
                log.exception("clob_orderbook.firehose_error", backoff=backoff)
            finally:
                if ping_task is not None:
                    ping_task.cancel()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def run(self) -> None:
        """Run firehose + managed slot pool + reconciler."""
        tasks: list[asyncio.Task[Any]] = []

        if self._firehose_enabled:
            tasks.append(asyncio.create_task(self._run_firehose()))

        if self._max_slots > 0:
            total_physical = self._max_slots * self._redundancy
            self._slots = []
            for i in range(total_physical):
                replica = i // self._max_slots
                delay = replica * 60.0
                self._slots.append(
                    ManagedSlot(
                        slot_id=i,
                        ws_url=self._ws_url,
                        on_message=self._make_slot_handler(i),
                        stale_timeout_s=self._slot_stale_timeout_s,
                        on_reconnect=self._clear_assets if self._redundancy == 1 else None,
                        startup_delay_s=delay,
                    )
                )
            log.info(
                "clob_orderbook.slots_created",
                num=total_physical,
                logical=self._max_slots,
                redundancy=self._redundancy,
            )
            for slot in self._slots:
                tasks.append(asyncio.create_task(slot.run()))

            self._reconciler = Reconciler(
                registry=self._reconcile_registry,
                slots=self._slots,
                on_remove_asset=self.remove_asset,
                interval_s=self._reconcile_interval_s,
                book_refresh_s=self._book_refresh_s,
                redundancy=self._redundancy,
            )
            tasks.append(asyncio.create_task(self._reconciler.run()))

        tasks.append(asyncio.create_task(self._flush_loop()))
        tasks.append(asyncio.create_task(self._heartbeat_loop()))

        log.info(
            "clob_orderbook.started",
            firehose=self._firehose_enabled,
            slots=self._max_slots,
        )

        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if exc := task.exception():
                    log.error("clob_orderbook.task_crashed", error=str(exc))
        finally:
            for task in tasks:
                task.cancel()


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
