"""BacktestRunner — event-driven replay of NormalizedTrades through a strategy.

Sorts trades by ``published_at``, advances the simulated clock on each tick,
and routes any emitted :class:`TradeIntent` through an :class:`ExecutionGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.types import Fill

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.protocol import Strategy

logger = structlog.get_logger(__name__)


@dataclass
class BacktestResult:
    """Aggregate output of a backtest run."""

    total_trades: int = 0
    total_intents: int = 0
    total_fills: int = 0
    fills: list[Fill] = field(default_factory=list)


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
    """

    __slots__ = ("_ctx", "_gateway", "_strategy")

    def __init__(
        self,
        strategy: Strategy,
        ctx: InMemoryContext,
        gateway: ExecutionGateway,
    ) -> None:
        self._strategy = strategy
        self._ctx = ctx
        self._gateway = gateway

    async def run(self, trades: list[NormalizedTrade]) -> BacktestResult:
        """Replay *trades* in ``published_at`` order and collect results.

        Steps for each trade:
        1. Advance the context clock to the trade's ``published_at``.
        2. Call ``strategy.on_trade(trade, ctx)``.
        3. If intents are returned, submit each through the gateway and record fills.
        """
        result = BacktestResult()
        sorted_trades = sorted(trades, key=lambda t: t.published_at)

        for trade in sorted_trades:
            self._ctx.set_time(trade.published_at)

            intents = await self._strategy.on_trade(trade, self._ctx)
            result.total_trades += 1

            if intents is None:
                continue

            for intent in intents:
                result.total_intents += 1
                fill = await self._gateway.submit(intent)
                result.total_fills += 1
                result.fills.append(fill)

        logger.info(
            "backtest_complete",
            total_trades=result.total_trades,
            total_intents=result.total_intents,
            total_fills=result.total_fills,
        )

        return result
