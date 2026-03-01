"""Example research strategy — demonstrates protocol usage.

This file serves as a template for writing new strategies. It implements
the Strategy protocol (event-driven) and can be run through the research
harness without being registered in the CLI.

Usage:
    from research.strategies.example import ExampleStrategy
    from research.harness import run_backtest

    result, summary = await run_backtest(
        ExampleStrategy(threshold=0.30),
        trades,
        config,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


class ExampleStrategy:
    """Buy YES when price drops below threshold. For demonstration only."""

    name: str = "example_research"

    def __init__(self, threshold: float = 0.30) -> None:
        self._threshold = threshold

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        price = float(trade.price)
        if price < self._threshold:
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="patient",
                    max_price=price + 0.02,
                    reason=f"price {price:.2f} < {self._threshold}",
                    signal_time=trade.published_at,
                    asset_id=trade.asset_id,
                ),
            ]
        return None

    async def on_market_update(self, update: object, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None
