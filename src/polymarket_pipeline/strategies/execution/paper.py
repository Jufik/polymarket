"""PaperExecutor — paper-trading executor with orderbook-aware pricing."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger(__name__)


class PaperExecutor:
    """Executor that simulates fills with market-aware pricing.

    Checks orderbook from context when available. Falls back to
    ``max_price`` or ``default_price`` otherwise.

    Parameters
    ----------
    ctx:
        Strategy context for orderbook lookups.
    fee_pct:
        Fee as fraction of ``min(price, 1-price) * size_usd``.
    default_price:
        Fallback price when neither orderbook nor max_price is available.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.02,
        default_price: float = 0.50,
    ) -> None:
        self._ctx = ctx
        self._fee_pct = fee_pct
        self._default_price = default_price

    async def execute(self, intent: TradeIntent) -> Fill:
        """Simulate a fill using orderbook or fallback pricing."""
        ob = await self._ctx.get_orderbook(intent.condition_id)

        if ob is not None:
            price = ob.best_ask if intent.side == "BUY" else ob.best_bid
        elif intent.max_price is not None:
            price = intent.max_price
        else:
            price = self._default_price

        fee = self._fee_pct * min(price, 1.0 - price) * intent.size_usd
        intent_id = uuid.uuid4().hex[:12]

        fill = Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=price,
            filled_size_usd=intent.size_usd,
            fee_usd=fee,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )

        logger.info(
            "paper_fill",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            price=price,
            source="orderbook" if ob is not None else "fallback",
        )

        return fill
