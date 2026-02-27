"""PaperExecutor — paper-trading executor with orderbook-aware pricing."""

from __future__ import annotations

import time
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
        Default 0.0 — most Polymarket markets have zero trading fees.
    default_price:
        Fallback price when neither orderbook nor max_price is available.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.0,
        default_price: float = 0.50,
    ) -> None:
        self._ctx = ctx
        self._fee_pct = fee_pct
        self._default_price = default_price

    async def execute(self, intent: TradeIntent) -> Fill:
        """Simulate a fill using orderbook or fallback pricing.

        Orderbook snapshots are YES-side (from CLOB WS). For NO positions
        we flip: NO_ask = 1 - YES_bid, NO_bid = 1 - YES_ask.
        """
        ob = await self._ctx.get_orderbook(intent.condition_id)
        is_no = intent.outcome == "NO"

        if ob is not None:
            if is_no:
                # NO ask = 1 - YES bid, NO bid = 1 - YES ask
                price = (1.0 - ob.best_bid) if intent.side == "BUY" else (1.0 - ob.best_ask)
            else:
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
            filled_at=time.time(),
        )

        logger.warning(
            "paper_fill",
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            price=round(price, 4),
            size_usd=round(intent.size_usd, 2),
            fee_usd=round(fee, 4),
            source="orderbook" if ob is not None else "fallback",
        )

        return fill
