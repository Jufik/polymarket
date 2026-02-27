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

        # Update yes_price in context (strategy reads this for max_price)
        market = self.ctx._markets.get(cid)
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
            logger.info("provider.initialized", provider=provider.name)

        # Bridge provider market metadata into ctx.get_market() store
        self._sync_markets_from_features()

        # Establish running volume dict reference in features
        self.ctx.update_features({"market_volume": self._market_volumes})

    def _build_intent_metadata(
        self,
        intent: TradeIntent,
        trade: NormalizedTrade,
        strategy: Any,
    ) -> dict[str, Any]:
        """Capture orderbook + strategy rationale as metadata dict."""
        metadata: dict[str, Any] = {}

        # Orderbook snapshot from context (if available)
        ob = self.ctx._orderbooks.get(intent.condition_id)  # type: ignore[attr-defined]
        if ob is not None:
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
            t0 = time.monotonic()
            intents = await strategy.on_trade(trade, self.ctx)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                logger.warning(
                    "strategy.slow_on_trade",
                    strategy=strategy.name,
                    elapsed_ms=round(elapsed_ms, 2),
                )

            if intents:
                for intent in intents:
                    # Capture metadata: orderbook + strategy rationale
                    metadata = self._build_intent_metadata(
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

                    # Position tracking (only for successful fills)
                    if fill.status == FillStatus.FILLED:
                        old_pos = await self.ctx.get_position(fill.condition_id)
                        new_pos = apply_fill_to_position(old_pos, fill)
                        self.ctx.set_position(fill.condition_id, new_pos)
                        self._last_trade_times[intent.strategy] = fill.filled_at

        self._trades_processed += 1

    def handle_orderbook(self, data: dict[str, Any]) -> None:
        """Process an orderbook snapshot and update context.

        Called by the Kafka subscriber for the ``orderbooks.raw`` topic.
        """
        from polymarket_pipeline.strategies.types import OrderbookSnapshot

        condition_id = data.get("condition_id")
        if condition_id is None:
            return

        best_bid = data.get("best_bid")
        best_ask = data.get("best_ask")
        if best_bid is None or best_ask is None:
            return

        ob = OrderbookSnapshot(
            condition_id=condition_id,
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            bid_depth=0.0,
            ask_depth=0.0,
            timestamp=data.get("timestamp", time.time()),
        )
        self.ctx.set_orderbook(condition_id, ob)

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

        logger.warning(
            "live_runner.reset",
            cleared_positions=n_positions,
            realized_pnl=round(realized, 2),
            budget_spent={k: round(v, 2) for k, v in spent.items()},
            prev_trades=prev_trades,
            prev_intents=prev_intents,
        )

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
            logger.warning(
                "paper_stats",
                trades_processed=self._trades_processed,
                intents_submitted=self._intents_submitted,
                open_positions=len(open_positions),
                total_cost_basis=round(total_cost, 2),
                total_realized_pnl=round(total_realized, 2),
                budget_spent={k: round(v, 2) for k, v in budget_spent.items()},
                drops_dedup=self._drops_dedup,
                drops_stale=self._drops_stale,
            )
            for strategy, config in self.strategies:
                intents = await strategy.on_timer(now, self.ctx)
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

                        # Position tracking (only for successful fills)
                        if fill.status == FillStatus.FILLED:
                            old_pos = await self.ctx.get_position(fill.condition_id)
                            new_pos = apply_fill_to_position(old_pos, fill)
                            self.ctx.set_position(fill.condition_id, new_pos)
                            self._last_trade_times[intent.strategy] = fill.filled_at

    async def _refresh_loop(self) -> None:
        """Periodic provider refresh, with support for on-demand triggers."""
        while True:
            # Wait for either the timer or an explicit refresh request
            try:
                async with asyncio.timeout(self.refresh_interval_s):
                    await self._refresh_event.wait()
            except TimeoutError:
                pass  # timer expired — normal periodic refresh
            self._refresh_event.clear()

            for provider in self.providers:
                logger.info("provider.refresh_start", provider=provider.name)
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
                logger.info("provider.refresh_done", provider=provider.name)
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
