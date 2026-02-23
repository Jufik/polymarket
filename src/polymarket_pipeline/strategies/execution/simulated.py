"""Simulated executor for backtesting — fills instantly at deterministic prices."""

from __future__ import annotations

import uuid

import structlog

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

logger = structlog.get_logger(__name__)


class SimulatedExecutor:
    """Executor that fills every intent instantly at a deterministic price.

    Useful for backtesting and paper-trading where real order submission
    is not desired.

    Parameters
    ----------
    fee_pct:
        Fee percentage applied as ``fee_pct * min(price, 1 - price) * size_usd``.
    default_price:
        Price used when the intent's ``max_price`` is ``None``.
    """

    def __init__(self, fee_pct: float = 0.02, default_price: float = 0.50) -> None:
        self.fee_pct = fee_pct
        self.default_price = default_price

    async def execute(self, intent: TradeIntent) -> Fill:
        """Fill *intent* instantly at ``max_price`` (or *default_price*)."""
        price = intent.max_price if intent.max_price is not None else self.default_price
        fee = self.fee_pct * min(price, 1 - price) * intent.size_usd
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
            "simulated_fill",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            price=price,
            size_usd=intent.size_usd,
            fee_usd=fee,
        )

        return fill
