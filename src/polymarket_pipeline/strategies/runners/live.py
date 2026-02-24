"""LiveRunner — Kafka consumer dispatching trades to providers then strategies.

Connects to the existing trades.raw topic, runs FeatureProviders (hot path update),
then dispatches to strategies. Manages timer and refresh background loops.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.runners.helpers import apply_fill_to_position, check_risk_gate

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
    ) -> None:
        self.strategies = strategies
        self.providers = providers
        self.gateway = gateway
        self.ctx = ctx
        self.backend = backend
        self.timer_interval_s = timer_interval_s
        self.refresh_interval_s = refresh_interval_s
        self.hot_path_warn_ms = hot_path_warn_ms
        self._tasks: list[asyncio.Task[Any]] = []
        self._trades_processed: int = 0
        self._intents_submitted: int = 0
        self._last_trade_times: dict[str, float] = {}

    async def initialize(self) -> None:
        """Run provider compute() at startup."""
        for provider in self.providers:
            await provider.compute(self.backend)
            self.ctx.update_features(provider.get_features())
            logger.info("provider.initialized", provider=provider.name)

    async def _handle_trade(self, trade: NormalizedTrade) -> None:
        """Hot path: dispatch trade to providers then strategies."""
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

        # 3. Update context time
        self.ctx.set_time(trade.published_at)

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
                        continue

                    fill = await self.gateway.submit(intent)
                    self._intents_submitted += 1

                    # Position tracking
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

    async def _timer_loop(self) -> None:
        """Periodic timer callbacks for strategies."""
        while True:
            await asyncio.sleep(self.timer_interval_s)
            now = time.time()
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
                            continue

                        fill = await self.gateway.submit(intent)
                        self._intents_submitted += 1

                        # Position tracking
                        old_pos = await self.ctx.get_position(fill.condition_id)
                        new_pos = apply_fill_to_position(old_pos, fill)
                        self.ctx.set_position(fill.condition_id, new_pos)
                        self._last_trade_times[intent.strategy] = fill.filled_at

    async def _refresh_loop(self) -> None:
        """Periodic provider refresh (expensive recomputation)."""
        while True:
            await asyncio.sleep(self.refresh_interval_s)
            for provider in self.providers:
                logger.info("provider.refresh_start", provider=provider.name)
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
                logger.info("provider.refresh_done", provider=provider.name)

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
        )
