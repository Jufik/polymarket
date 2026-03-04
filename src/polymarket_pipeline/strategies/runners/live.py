"""LiveRunner — Kafka consumer dispatching trades to providers then strategies.

Connects to the existing trades.raw topic, runs FeatureProviders (hot path update),
then dispatches to strategies. Manages timer and refresh background loops.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.live.dedup import TradeDedup
from polymarket_pipeline.strategies.runners.helpers import apply_fill_to_position, check_risk_gate
from polymarket_pipeline.strategies.types import FillStatus, MarketInfo

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.protocol import (
        FeatureBackend,
        FeatureProvider,
        Strategy,
    )
    from polymarket_pipeline.strategies.types import Fill, TradeIntent

# Callback type: (intent_dict, disposition, fill_dict_or_none) -> awaitable
IntentCallback = Callable[[dict[str, Any]], Awaitable[None]]
# Callback type: (provider_name, features_dict) -> awaitable
PoolRefreshCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

logger = structlog.get_logger(__name__)


class LiveRunner:
    """Dispatches trades from Kafka to feature providers then strategies.

    Parameters
    ----------
    strategies:
        List of (strategy, config) tuples to run.
    providers:
        Feature providers that update context before strategies run.
    gateway:
        Execution gateway for submitting trade intents.
    ctx:
        Strategy context (InMemoryContext for paper-dev).
    backend:
        Feature backend for provider compute/refresh calls.
    timer_interval_s:
        Seconds between strategy on_timer() calls.
    refresh_interval_s:
        Seconds between provider refresh() calls.
    hot_path_warn_ms:
        Threshold in milliseconds — log warning if on_trade exceeds this.
    """

    def __init__(
        self,
        strategies: list[tuple[Strategy, StrategyConfig]],
        providers: list[FeatureProvider],
        gateway: ExecutionGateway,
        ctx: InMemoryContext,
        backend: FeatureBackend,
        *,
        timer_interval_s: float = 60.0,
        refresh_interval_s: float = 900.0,
        hot_path_warn_ms: float = 5.0,
        dedup_ttl_s: float = 600.0,
        max_trade_age_s: float = 120.0,
        intent_cb: IntentCallback | None = None,
    ) -> None:
        self.strategies = strategies
        self.providers = providers
        self.gateway = gateway
        self.ctx = ctx
        self.backend = backend
        self.timer_interval_s = timer_interval_s
        self.refresh_interval_s = refresh_interval_s
        self.hot_path_warn_ms = hot_path_warn_ms
        self._dedup = TradeDedup(ttl_s=dedup_ttl_s)
        self._max_trade_age_s = max_trade_age_s
        self._tasks: list[asyncio.Task[Any]] = []
        self._trades_processed: int = 0
        self._intents_submitted: int = 0
        self._drops_dedup: int = 0
        self._drops_stale: int = 0
        self._last_trade_times: dict[str, float] = {}
        self._refresh_event = asyncio.Event()
        self._market_volumes: dict[str, float] = {}
        self._intent_cb = intent_cb
        self._pool_refresh_cb: PoolRefreshCallback | None = None
        # Rate tracking — reset each paper_stats log
        self._trades_at_last_stats: int = 0
        self._stats_last_time: float = time.monotonic()
        self._unique_markets: set[str] = set()

    def _sync_markets_from_features(self) -> None:
        """Bridge MarketInfo from provider features into ctx markets store.

        Providers like WillMarketProvider expose ``dict[str, MarketInfo]``
        via ``get_features()``.  The strategy's ``on_trade`` reads from
        ``ctx.get_market(cid)`` which is a *separate* dict.  This method
        copies MarketInfo objects across so both stores stay in sync.
        """
        count = 0
        for provider in self.providers:
            for value in provider.get_features().values():
                if isinstance(value, dict):
                    for cid, info in value.items():
                        if isinstance(info, MarketInfo):
                            self.ctx.set_market(cid, info)
                            count += 1
        if count:
            logger.info("live_runner.markets_synced", count=count)

    def _update_market_price(self, trade: NormalizedTrade) -> None:
        """Update a market's yes_price from the latest trade + accumulate volume.

        Lightweight per-trade call — one dict lookup + optional set_market.
        """
        cid = trade.condition_id

        # Use get_all_positions()-style access to avoid touching private dict
        market = self.ctx._markets.get(cid)  # noqa: SLF001 — runner owns ctx
        if market is not None:
            self.ctx.set_market(
                cid,
                MarketInfo(
                    condition_id=market.condition_id,
                    question=market.question,
                    active=market.active,
                    yes_price=float(trade.price),
                    event_id=market.event_id,
                    category=market.category,
                ),
            )

        # Running volume accumulator (sum of price * size per market)
        vol = float(trade.price) * float(trade.size)
        self._market_volumes[cid] = self._market_volumes.get(cid, 0.0) + vol

    async def initialize(self) -> None:
        """Run provider compute() at startup."""
        for provider in self.providers:
            await provider.compute(self.backend)
            self.ctx.update_features(provider.get_features())
            feature_sizes = {}
            for _key, val in provider.get_features().items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, (dict, set, frozenset, list)):
                            feature_sizes[k] = len(v)
            logger.info("provider.initialized", provider=provider.name, **feature_sizes)

        # Bridge provider market metadata into ctx.get_market() store
        self._sync_markets_from_features()

        # Establish running volume dict reference in features
        self.ctx.update_features({"market_volume": self._market_volumes})

    async def _build_intent_metadata(
        self,
        intent: TradeIntent,
        trade: NormalizedTrade,
        strategy: Any,
    ) -> dict[str, Any]:
        """Capture orderbook + strategy rationale as metadata dict."""
        metadata: dict[str, Any] = {}

        # Orderbook snapshot — prefer asset-specific (outcome-aware)
        ob = None
        asset_id = intent.asset_id
        # Resolve asset_id from executor's token_map if not on intent
        if not asset_id and hasattr(self, "gateway"):
            executor = getattr(self.gateway, "executor", None)
            tmap = getattr(executor, "_token_map", None)
            if tmap:
                tokens = tmap.get(intent.condition_id)
                if tokens:
                    asset_id = tokens.get(intent.outcome)
        if asset_id and hasattr(self.ctx, "get_orderbook_by_asset"):
            import asyncio

            _result = self.ctx.get_orderbook_by_asset(asset_id)
            ob = await _result if asyncio.iscoroutine(_result) else _result
        if ob is None:
            ob = self.ctx._orderbooks.get(intent.condition_id)  # noqa: SLF001 — runner owns ctx

        # Fallback: CLOB REST API /book (most markets aren't WS-subscribed)
        if ob is None and asset_id:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(
                        "https://clob.polymarket.com/book",
                        params={"token_id": asset_id},
                    )
                    resp.raise_for_status()
                    book = resp.json()
                    bids = book.get("bids") or []
                    asks = book.get("asks") or []
                    if bids and asks:
                        best_bid = max(float(b["price"]) for b in bids)
                        best_ask = min(float(a["price"]) for a in asks)
                        metadata["orderbook"] = {
                            "best_bid": round(best_bid, 4),
                            "best_ask": round(best_ask, 4),
                            "spread": round(best_ask - best_bid, 4),
                            "source": "clob_api",
                        }
            except Exception:
                logger.debug("metadata.clob_book_failed", asset_id=asset_id[:16])

        if ob is not None and "orderbook" not in metadata:
            metadata["orderbook"] = {
                "best_bid": round(ob.best_bid, 4),
                "best_ask": round(ob.best_ask, 4),
                "spread": round(ob.spread, 4),
                "source": "clob_ws",
            }

        # Strategy rationale (duck-typed)
        if hasattr(strategy, "get_rationale"):
            try:
                metadata["rationale"] = strategy.get_rationale(
                    intent.condition_id, trade, self.ctx
                )
            except Exception:
                logger.debug("metadata.rationale_error", strategy=strategy.name)

        return metadata

    async def _fire_intent(
        self,
        intent: TradeIntent,
        disposition: str,
        fill: Fill | None = None,
        rejection_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Fire the intent callback (if configured) with full context."""
        if self._intent_cb is None:
            return
        record: dict[str, Any] = {
            **dataclasses.asdict(intent),
            "disposition": disposition,
            "rejection_reason": rejection_reason,
            "fill": dataclasses.asdict(fill) if fill is not None else None,
            "metadata": metadata or {},
            "captured_at": time.time(),
        }
        try:
            await self._intent_cb(record)
        except Exception:
            logger.exception("intent_cb.error")

    async def _handle_trade(self, trade: NormalizedTrade) -> None:
        """Hot path: dispatch trade to providers then strategies."""
        # Age filter — drop stale trades (Kafka lag, reconnection replays)
        age = time.time() - trade.published_at
        if self._max_trade_age_s > 0 and age > self._max_trade_age_s:
            self._drops_stale += 1
            return

        # Trade-level dedup — drop duplicate trade_ids within TTL window
        if self._dedup.is_duplicate(trade.trade_id):
            self._drops_dedup += 1
            return

        self._unique_markets.add(trade.condition_id)

        # 1. Providers first — update features
        for provider in self.providers:
            t0 = time.monotonic()
            await provider.on_trade(trade)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                logger.warning(
                    "provider.slow_on_trade",
                    provider=provider.name,
                    elapsed_ms=round(elapsed_ms, 2),
                )

        # 2. Inject features into context
        for provider in self.providers:
            self.ctx.update_features(provider.get_features())

        # 3. Update context time + market price + volume
        self.ctx.set_time(trade.published_at)
        self._update_market_price(trade)

        # 4. Strategies — read updated context
        for strategy, config in self.strategies:
            if not config.enabled:
                continue
            try:
                t0 = time.monotonic()
                intents = await strategy.on_trade(trade, self.ctx)
                elapsed_ms = (time.monotonic() - t0) * 1000
                if elapsed_ms > self.hot_path_warn_ms:
                    logger.warning(
                        "strategy.slow_on_trade",
                        strategy=strategy.name,
                        elapsed_ms=round(elapsed_ms, 2),
                    )
            except Exception:
                logger.exception(
                    "strategy.on_trade_error", strategy=strategy.name,
                    condition_id=trade.condition_id,
                )
                continue

            if intents:
                for intent in intents:
                    # Capture metadata: orderbook + strategy rationale
                    metadata = await self._build_intent_metadata(
                        intent, trade, strategy
                    )

                    # Risk gate
                    positions = self.ctx.get_all_positions()
                    allowed, reason = check_risk_gate(
                        intent, config, positions, self._last_trade_times, time.time()
                    )
                    if not allowed:
                        logger.info(
                            "intent.rejected",
                            strategy=strategy.name,
                            reason=reason,
                            condition_id=intent.condition_id,
                        )
                        await self._fire_intent(
                            intent, "risk_rejected",
                            rejection_reason=reason, metadata=metadata,
                        )
                        continue

                    fill = await self.gateway.submit(intent)
                    self._intents_submitted += 1

                    disposition = fill.status.value  # "filled" or "rejected"
                    await self._fire_intent(
                        intent, disposition, fill=fill,
                        rejection_reason=fill.error or "",
                        metadata=metadata,
                    )

                    # Position tracking (filled or partial fills)
                    if fill.status in (FillStatus.FILLED, FillStatus.PARTIAL):
                        old_pos = await self.ctx.get_position(fill.condition_id)
                        new_pos = apply_fill_to_position(old_pos, fill)
                        self.ctx.set_position(fill.condition_id, new_pos)
                        self._last_trade_times[intent.strategy] = fill.filled_at

        self._trades_processed += 1

    def handle_orderbook(self, data: dict[str, Any]) -> None:
        """Process an orderbook snapshot and update context.

        Called by the Kafka subscriber for the ``orderbooks.raw`` topic.
        Stores by both ``condition_id`` (backward compat) and ``asset_id``
        (outcome-specific lookup for PaperExecutor).
        """
        from polymarket_pipeline.strategies.types import OrderbookSnapshot

        condition_id = data.get("condition_id")
        if condition_id is None:
            return

        best_bid = data.get("best_bid")
        best_ask = data.get("best_ask")
        if best_bid is None or best_ask is None:
            return

        raw_bids = data.get("bids")
        raw_asks = data.get("asks")
        bids = (
            tuple((float(lvl[0]), float(lvl[1])) for lvl in raw_bids)
            if raw_bids
            else ()
        )
        asks = (
            tuple((float(lvl[0]), float(lvl[1])) for lvl in raw_asks)
            if raw_asks
            else ()
        )
        bid_depth = float(data["bid_depth_usd"]) if "bid_depth_usd" in data else sum(
            p * s for p, s in bids
        )
        ask_depth = float(data["ask_depth_usd"]) if "ask_depth_usd" in data else sum(
            p * s for p, s in asks
        )

        ob = OrderbookSnapshot(
            condition_id=condition_id,
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            timestamp=data.get("timestamp", time.time()),
            bids=bids,
            asks=asks,
        )
        asset_id = data.get("asset_id")
        self.ctx.set_orderbook(condition_id, ob, asset_id=asset_id)

    def reset(self) -> None:
        """Clear all paper state: positions, budgets, counters, dedup cache.

        Triggered by SIGUSR1 in the strategy CLI. Does NOT re-run providers —
        use ``request_refresh()`` for that.
        """
        # Snapshot for the log
        n_positions = sum(
            1 for p in self.ctx._positions.values()
            if p.qty_yes > 0 or p.qty_no > 0
        )
        realized = sum(p.realized_pnl for p in self.ctx._positions.values())
        spent = dict(self.gateway._strategy_spent)

        self.ctx._positions.clear()
        self.ctx._orderbooks.clear()
        self.gateway._strategy_spent.clear()
        self._dedup._seen.clear()
        self._market_volumes.clear()
        self._last_trade_times.clear()

        prev_trades = self._trades_processed
        prev_intents = self._intents_submitted
        self._trades_processed = 0
        self._intents_submitted = 0
        self._drops_dedup = 0
        self._drops_stale = 0
        self._unique_markets.clear()
        self._trades_at_last_stats = 0
        self._stats_last_time = time.monotonic()

        logger.warning(
            "live_runner.reset",
            cleared_positions=n_positions,
            realized_pnl=round(realized, 2),
            budget_spent={k: round(v, 2) for k, v in spent.items()},
            prev_trades=prev_trades,
            prev_intents=prev_intents,
        )

    def settle_resolved_market(self, condition_id: str, winner: str) -> None:
        """Settle an open position when a market resolves.

        For each outcome (YES/NO), if we hold tokens:
        - If our outcome matches *winner*: each token pays out $1.
        - If our outcome loses: tokens are worth $0.

        The position is zeroed out and ``realized_pnl`` is updated.
        """
        from dataclasses import replace

        from polymarket_pipeline.strategies.types import Position

        positions = self.ctx.get_all_positions()
        old_pos = positions.get(condition_id)
        if old_pos is None:
            return

        # Compute realized PnL from resolution
        pnl_delta = 0.0

        if old_pos.qty_yes > 0:
            if winner == "YES":
                # YES tokens pay $1 each — profit = (1 - avg_entry) * qty
                pnl_delta += (1.0 - old_pos.avg_entry_yes) * old_pos.qty_yes
            else:
                # YES tokens worth $0 — loss = avg_entry * qty
                pnl_delta -= old_pos.avg_entry_yes * old_pos.qty_yes

        if old_pos.qty_no > 0:
            if winner == "NO":
                pnl_delta += (1.0 - old_pos.avg_entry_no) * old_pos.qty_no
            else:
                pnl_delta -= old_pos.avg_entry_no * old_pos.qty_no

        new_pos = replace(
            old_pos,
            qty_yes=0.0,
            qty_no=0.0,
            cost_basis=0.0,
            realized_pnl=old_pos.realized_pnl + pnl_delta,
        )
        self.ctx.set_position(condition_id, new_pos)

        logger.info(
            "runner.settled",
            condition_id=condition_id,
            winner=winner,
            pnl_delta=round(pnl_delta, 4),
            realized_pnl=round(new_pos.realized_pnl, 4),
        )

        # Notify strategies so they can clean up internal state
        self._notify_resolution(condition_id, winner)

    def _notify_resolution(self, condition_id: str, winner: str) -> None:
        """Fire on_market_update with resolution info for all strategies."""
        if not hasattr(self, "strategies"):
            return
        update = {"type": "resolution", "condition_id": condition_id, "winner": winner}
        for strategy, config in self.strategies:
            if not config.enabled:
                continue
            try:
                import asyncio

                coro = strategy.on_market_update(update, self.ctx)
                # We're in a sync method; schedule the coroutine if a loop is running
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(coro)
                except RuntimeError:
                    # No running loop — call synchronously (e.g. in tests)
                    asyncio.run(coro)
            except Exception:
                logger.warning(
                    "strategy.resolution_notify_error",
                    strategy=strategy.name,
                    condition_id=condition_id,
                )

    def settle_voided_market(self, condition_id: str, payout_per_token: float = 0.5) -> None:
        """50/50 resolution: each token redeems for payout_per_token ($0.50)."""
        from dataclasses import replace

        positions = self.ctx.get_all_positions()
        pos = positions.get(condition_id)
        if pos is None:
            return

        pnl_delta = 0.0
        if pos.qty_yes > 0:
            pnl_delta += (payout_per_token - pos.avg_entry_yes) * pos.qty_yes
        if pos.qty_no > 0:
            pnl_delta += (payout_per_token - pos.avg_entry_no) * pos.qty_no

        new_pos = replace(
            pos,
            qty_yes=0.0,
            qty_no=0.0,
            cost_basis=0.0,
            realized_pnl=pos.realized_pnl + pnl_delta,
        )
        self.ctx.set_position(condition_id, new_pos)

        logger.info(
            "settlement.voided",
            condition_id=condition_id,
            pnl_delta=round(pnl_delta, 4),
            payout=payout_per_token,
        )

        # Notify strategies so they can clean up internal state
        self._notify_resolution(condition_id, "VOID")

    def request_refresh(self) -> None:
        """Signal the refresh loop to run immediately (non-blocking)."""
        self._refresh_event.set()

    async def _timer_loop(self) -> None:
        """Periodic timer callbacks for strategies."""
        while True:
            await asyncio.sleep(self.timer_interval_s)
            now = time.time()

            # Periodic stats
            positions = self.ctx.get_all_positions()
            open_positions = {
                cid: p
                for cid, p in positions.items()
                if p.qty_yes > 0 or p.qty_no > 0
            }
            total_cost = sum(p.cost_basis for p in open_positions.values())
            total_realized = sum(p.realized_pnl for p in positions.values())
            budget_spent = dict(self.gateway._strategy_spent)

            # Compute trade rate since last stats log
            now_mono = time.monotonic()
            elapsed = now_mono - self._stats_last_time
            trades_delta = self._trades_processed - self._trades_at_last_stats
            trades_per_sec = round(trades_delta / elapsed, 1) if elapsed > 0 else 0.0
            self._trades_at_last_stats = self._trades_processed
            self._stats_last_time = now_mono

            logger.warning(
                "paper_stats",
                trades_processed=self._trades_processed,
                trades_per_sec=trades_per_sec,
                unique_markets=len(self._unique_markets),
                intents_submitted=self._intents_submitted,
                open_positions=len(open_positions),
                total_cost_basis=round(total_cost, 2),
                total_realized_pnl=round(total_realized, 2),
                budget_spent={k: round(v, 2) for k, v in budget_spent.items()},
                drops_dedup=self._drops_dedup,
                drops_stale=self._drops_stale,
            )
            for strategy, config in self.strategies:
                if not config.enabled:
                    continue
                try:
                    intents = await strategy.on_timer(now, self.ctx)
                except Exception:
                    logger.exception(
                        "strategy.on_timer_error", strategy=strategy.name,
                    )
                    continue
                if intents:
                    for intent in intents:
                        # Risk gate
                        positions = self.ctx.get_all_positions()
                        allowed, reason = check_risk_gate(
                            intent, config, positions, self._last_trade_times, time.time()
                        )
                        if not allowed:
                            logger.info(
                                "timer_intent.rejected",
                                strategy=strategy.name,
                                reason=reason,
                                condition_id=intent.condition_id,
                            )
                            await self._fire_intent(intent, "risk_rejected", rejection_reason=reason)
                            continue

                        fill = await self.gateway.submit(intent)
                        self._intents_submitted += 1

                        disposition = fill.status.value
                        await self._fire_intent(
                            intent, disposition, fill=fill, rejection_reason=fill.error or ""
                        )

                        # Position tracking (filled or partial fills)
                        if fill.status in (FillStatus.FILLED, FillStatus.PARTIAL):
                            old_pos = await self.ctx.get_position(fill.condition_id)
                            new_pos = apply_fill_to_position(old_pos, fill)
                            self.ctx.set_position(fill.condition_id, new_pos)
                            self._last_trade_times[intent.strategy] = fill.filled_at

    async def _refresh_loop(self) -> None:
        """Periodic provider refresh, with support for on-demand triggers."""
        # Cooldown = half the configured interval. Prevents new_market event spam
        # from triggering 40s refresh cycles, while still allowing resolution-
        # triggered refreshes within a reasonable window.
        min_refresh_cooldown_s = max(self.refresh_interval_s / 2, 120.0)
        last_refresh = 0.0
        while True:
            # Wait for either the timer or an explicit refresh request
            try:
                async with asyncio.timeout(self.refresh_interval_s):
                    await self._refresh_event.wait()
            except TimeoutError:
                pass  # timer expired — normal periodic refresh
            self._refresh_event.clear()

            # Enforce minimum cooldown between refreshes
            now = time.monotonic()
            elapsed = now - last_refresh
            if last_refresh > 0 and elapsed < min_refresh_cooldown_s:
                remaining = min_refresh_cooldown_s - elapsed
                logger.debug("refresh.cooldown", wait_s=round(remaining, 1))
                await asyncio.sleep(remaining)

            last_refresh = time.monotonic()
            for provider in self.providers:
                logger.info("provider.refresh_start", provider=provider.name)
                t0 = time.monotonic()
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                # Log feature sizes for observability
                feature_sizes = {}
                for _key, val in provider.get_features().items():
                    if isinstance(val, dict):
                        for k, v in val.items():
                            if isinstance(v, (dict, set, frozenset, list)):
                                feature_sizes[k] = len(v)
                logger.info(
                    "provider.refresh_done",
                    provider=provider.name,
                    elapsed_ms=elapsed_ms,
                    **feature_sizes,
                )
                # Publish pool contents to PG (if callback wired)
                if self._pool_refresh_cb is not None:
                    try:
                        await self._pool_refresh_cb(
                            provider.name, provider.get_features()
                        )
                    except Exception:
                        logger.exception(
                            "pool_refresh_cb.error", provider=provider.name
                        )
            # Re-sync market metadata after refresh (new markets may have appeared)
            self._sync_markets_from_features()

    async def start_background_loops(self) -> None:
        """Start timer and refresh loops as background tasks."""
        self._tasks.append(asyncio.create_task(self._timer_loop()))
        self._tasks.append(asyncio.create_task(self._refresh_loop()))

    async def stop(self) -> None:
        """Cancel background tasks."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info(
            "live_runner.stopped",
            trades_processed=self._trades_processed,
            intents_submitted=self._intents_submitted,
            drops_dedup=self._drops_dedup,
            drops_stale=self._drops_stale,
        )
