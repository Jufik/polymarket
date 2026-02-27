"""PaperExecutor — paper-trading executor with orderbook-driven pricing."""

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
    """Executor that simulates fills using real orderbook prices.

    Fills are ONLY executed when an orderbook snapshot is available for the
    market. The intent's ``max_price`` acts as a limit — if the market price
    exceeds it, the fill is rejected.

    When no orderbook is available, the fill is rejected with a clear error.

    Parameters
    ----------
    ctx:
        Strategy context for orderbook lookups.
    fee_pct:
        Fee as fraction of ``min(price, 1-price) * size_usd``.
        Default 0.0 — most Polymarket markets have zero trading fees.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.0,
    ) -> None:
        self._ctx = ctx
        self._fee_pct = fee_pct

    async def execute(self, intent: TradeIntent) -> Fill:
        """Fill at orderbook price or reject.

        Orderbook snapshots are YES-side (from CLOB WS). For NO positions
        we flip: NO_ask = 1 - YES_bid, NO_bid = 1 - YES_ask.
        """
        ob = await self._ctx.get_orderbook(intent.condition_id)
        is_no = intent.outcome == "NO"

        # No orderbook → reject
        if ob is None:
            intent_id = uuid.uuid4().hex[:12]
            logger.warning(
                "paper_fill.rejected",
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                outcome=intent.outcome,
                reason="no_orderbook",
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
                error="no orderbook available",
                filled_at=time.time(),
            )

        # Compute market price from YES-side orderbook
        if is_no:
            market_price = (1.0 - ob.best_bid) if intent.side == "BUY" else (1.0 - ob.best_ask)
        else:
            market_price = ob.best_ask if intent.side == "BUY" else ob.best_bid

        # max_price acts as a limit — reject if market exceeds it
        if intent.max_price is not None and intent.side == "BUY" and market_price > intent.max_price:
            intent_id = uuid.uuid4().hex[:12]
            logger.warning(
                "paper_fill.rejected",
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                outcome=intent.outcome,
                reason="market_exceeds_limit",
                market_price=round(market_price, 4),
                max_price=round(intent.max_price, 4),
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
                error=f"market {market_price:.4f} > limit {intent.max_price:.4f}",
                filled_at=time.time(),
            )

        fee = self._fee_pct * min(market_price, 1.0 - market_price) * intent.size_usd
        intent_id = uuid.uuid4().hex[:12]

        fill = Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=market_price,
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
            price=round(market_price, 4),
            size_usd=round(intent.size_usd, 2),
            fee_usd=round(fee, 4),
        )

        return fill
