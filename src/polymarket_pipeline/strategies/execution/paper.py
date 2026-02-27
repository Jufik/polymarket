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
    ``max_price`` when no orderbook exists. Rejects fills when the
    price gap between intent and market is too large.

    Parameters
    ----------
    ctx:
        Strategy context for orderbook lookups.
    fee_pct:
        Fee as fraction of ``min(price, 1-price) * size_usd``.
        Default 0.0 — most Polymarket markets have zero trading fees.
    max_price_gap:
        Maximum allowed gap between intent max_price and actual market
        price. Fills outside this band are rejected. Default 0.10 (10%).
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.0,
        max_price_gap: float = 0.10,
    ) -> None:
        self._ctx = ctx
        self._fee_pct = fee_pct
        self._max_price_gap = max_price_gap

    async def execute(self, intent: TradeIntent) -> Fill:
        """Simulate a fill using orderbook or fallback pricing.

        Orderbook snapshots are YES-side (from CLOB WS). For NO positions
        we flip: NO_ask = 1 - YES_bid, NO_bid = 1 - YES_ask.

        When orderbook is available but the gap vs max_price is too large,
        the fill is rejected (stale signal or wrong price).
        """
        ob = await self._ctx.get_orderbook(intent.condition_id)
        is_no = intent.outcome == "NO"
        source = "orderbook"

        if ob is not None:
            if is_no:
                # NO ask = 1 - YES bid, NO bid = 1 - YES ask
                market_price = (1.0 - ob.best_bid) if intent.side == "BUY" else (1.0 - ob.best_ask)
            else:
                market_price = ob.best_ask if intent.side == "BUY" else ob.best_bid
            price = market_price
        elif intent.max_price is not None:
            price = intent.max_price
            market_price = None
            source = "max_price"
        else:
            # No orderbook AND no max_price — reject (don't guess at 0.50)
            intent_id = uuid.uuid4().hex[:12]
            logger.warning(
                "paper_fill.rejected",
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                reason="no_orderbook_no_max_price",
            )
            return Fill(
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                side=intent.side,
                outcome=intent.outcome,
                filled_price=0.0,
                filled_size_usd=0.0,
                fee_usd=0.0,
                status=FillStatus.REJECTED,
                error="no price available (no orderbook, no max_price)",
                filled_at=time.time(),
            )

        # Price gap validation: reject if intent max_price diverges too much
        # from actual market price (stale signal or wrong price)
        if market_price is not None and intent.max_price is not None:
            gap = abs(market_price - intent.max_price)
            if gap > self._max_price_gap:
                intent_id = uuid.uuid4().hex[:12]
                logger.warning(
                    "paper_fill.rejected",
                    intent_id=intent_id,
                    strategy=intent.strategy,
                    condition_id=intent.condition_id,
                    reason="price_gap_too_large",
                    market_price=round(market_price, 4),
                    max_price=round(intent.max_price, 4),
                    gap=round(gap, 4),
                )
                return Fill(
                    intent_id=intent_id,
                    strategy=intent.strategy,
                    condition_id=intent.condition_id,
                    side=intent.side,
                    outcome=intent.outcome,
                    filled_price=0.0,
                    filled_size_usd=0.0,
                    fee_usd=0.0,
                    status=FillStatus.REJECTED,
                    error=f"price gap {gap:.4f} > {self._max_price_gap} "
                          f"(market={market_price:.4f}, max_price={intent.max_price:.4f})",
                    filled_at=time.time(),
                )

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
            source=source,
        )

        return fill
