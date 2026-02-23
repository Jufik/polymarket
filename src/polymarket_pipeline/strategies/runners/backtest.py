"""BacktestRunner — event-driven replay of NormalizedTrades through a strategy.

Sorts trades by ``published_at``, advances the simulated clock on each tick,
and routes any emitted :class:`TradeIntent` through an :class:`ExecutionGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.runners.helpers import apply_fill_to_position, check_risk_gate
from polymarket_pipeline.strategies.types import Fill

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.protocol import Strategy
    from polymarket_pipeline.strategies.types import TradeIntent

logger = structlog.get_logger(__name__)


@dataclass
class BacktestResult:
    """Aggregate output of a backtest run."""

    total_trades: int = 0
    total_intents: int = 0
    total_fills: int = 0
    fills: list[Fill] = field(default_factory=list)
    rejected_intents: list[tuple[TradeIntent, str]] = field(default_factory=list)


class BacktestRunner:
    """Replays :class:`NormalizedTrade` events through a :class:`Strategy` in timestamp order.

    Parameters
    ----------
    strategy:
        The event-driven strategy whose ``on_trade`` callback is invoked per trade.
    ctx:
        The in-memory context whose simulated clock is advanced on each trade.
    gateway:
        The execution gateway used to submit intents and collect fills.
    config:
        Optional strategy config for risk gate checks.  When ``None``, no risk
        gate is applied.
    delay_s:
        Simulated execution delay in seconds.  When positive, intents are queued
        and only submitted once ``signal_time + delay_s`` has elapsed (evaluated
        against arriving trade timestamps).
    """

    __slots__ = (
        "_config",
        "_ctx",
        "_delay_s",
        "_gateway",
        "_last_trade_times",
        "_pending_intents",
        "_strategy",
    )

    def __init__(
        self,
        strategy: Strategy,
        ctx: InMemoryContext,
        gateway: ExecutionGateway,
        config: StrategyConfig | None = None,
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._strategy = strategy
        self._ctx = ctx
        self._gateway = gateway
        self._config = config
        self._delay_s = delay_s
        self._last_trade_times: dict[str, float] = {}
        self._pending_intents: list[tuple[float, TradeIntent]] = []

    async def run(self, trades: list[NormalizedTrade]) -> BacktestResult:
        """Replay *trades* in ``published_at`` order and collect results.

        Steps for each trade:
        1. Advance the context clock to the trade's ``published_at``.
        2. Drain any delayed intents whose ready-time has elapsed.
        3. Call ``strategy.on_trade(trade, ctx)``.
        4. If intents are returned, either queue them (when *delay_s* > 0)
           or execute them immediately.
        """
        result = BacktestResult()
        sorted_trades = sorted(trades, key=lambda t: t.published_at)

        for trade in sorted_trades:
            current_time = trade.published_at
            self._ctx.set_time(current_time)

            # Drain ready delayed intents
            if self._delay_s > 0:
                await self._drain_pending(current_time, result)

            intents = await self._strategy.on_trade(trade, self._ctx)
            result.total_trades += 1

            if intents is None:
                continue

            for intent in intents:
                result.total_intents += 1
                if self._delay_s > 0:
                    self._pending_intents.append((intent.signal_time + self._delay_s, intent))
                else:
                    await self._execute_intent(intent, current_time, result)

        # Drain remaining pending intents at the end
        if self._delay_s > 0 and self._pending_intents:
            # Use a far-future time to drain everything
            await self._drain_pending(float("inf"), result)

        logger.info(
            "backtest_complete",
            total_trades=result.total_trades,
            total_intents=result.total_intents,
            total_fills=result.total_fills,
            rejected=len(result.rejected_intents),
        )

        return result

    async def _drain_pending(self, current_time: float, result: BacktestResult) -> None:
        """Submit pending intents whose delay has elapsed."""
        still_pending: list[tuple[float, TradeIntent]] = []
        for ready_at, intent in self._pending_intents:
            if current_time >= ready_at:
                await self._execute_intent(intent, current_time, result)
            else:
                still_pending.append((ready_at, intent))
        self._pending_intents = still_pending

    async def _execute_intent(
        self, intent: TradeIntent, current_time: float, result: BacktestResult
    ) -> None:
        """Run risk gate, execute intent, update position."""
        # Risk gate (skip if no config)
        if self._config is not None:
            positions = self._ctx.get_all_positions()
            allowed, reason = check_risk_gate(
                intent,
                self._config,
                positions,
                self._last_trade_times,
                current_time,
            )
            if not allowed:
                result.rejected_intents.append((intent, reason))
                return

        fill = await self._gateway.submit(intent)
        result.total_fills += 1
        result.fills.append(fill)

        # Position tracking
        old_pos = await self._ctx.get_position(fill.condition_id)
        new_pos = apply_fill_to_position(old_pos, fill)
        self._ctx.set_position(fill.condition_id, new_pos)
        self._last_trade_times[intent.strategy] = fill.filled_at
