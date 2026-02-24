"""LiveExecutor — real execution via CLOB API."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import structlog

from polymarket_pipeline.execution.clob_client import ClobClient, OrderSide, OrderType
from polymarket_pipeline.execution.models import FillRecord
from polymarket_pipeline.execution.position_tracker import PositionTracker
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

logger = structlog.get_logger(__name__)


class LiveExecutor:
    """Executor that submits real orders to the Polymarket CLOB API.

    Parameters
    ----------
    clob:
        CLOB API client.
    tracker:
        Position tracker for recording fills.
    max_position_usd:
        Maximum position size per market.
    max_total_exposure_usd:
        Maximum total exposure across all markets.
    """

    def __init__(
        self,
        clob: ClobClient,
        tracker: PositionTracker,
        *,
        token_market_map: dict[str, tuple[str, str]] | None = None,
        max_position_usd: float = 100.0,
        max_total_exposure_usd: float = 500.0,
    ) -> None:
        self._clob = clob
        self._tracker = tracker
        # Reverse map: (condition_id, outcome) -> asset_id
        self._asset_lookup: dict[tuple[str, str], str] = {}
        if token_market_map:
            for asset_id, (cid, outcome) in token_market_map.items():
                self._asset_lookup[(cid, outcome)] = asset_id
        self._max_position_usd = max_position_usd
        self._max_total_exposure_usd = max_total_exposure_usd

    async def execute(self, intent: TradeIntent) -> Fill:
        """Execute a trade intent with position limit checks."""
        intent_id = uuid.uuid4().hex[:12]
        now = time.time()

        # Check position limits
        rejection = self._check_limits(intent)
        if rejection is not None:
            logger.warning(
                "live.rejected",
                intent_id=intent_id,
                reason=rejection,
                condition_id=intent.condition_id,
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
                filled_at=now,
                error=rejection,
            )

        # Resolve asset_id from intent or token_market_map
        asset_id = intent.asset_id
        if asset_id is None:
            asset_id = self._asset_lookup.get((intent.condition_id, intent.outcome))
        if asset_id is None:
            logger.warning(
                "live.no_asset_id",
                condition_id=intent.condition_id,
                outcome=intent.outcome,
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
                filled_at=now,
                error="asset_id not found in token_market_map",
            )

        # Submit order via CLOB
        side = OrderSide.BUY if intent.side == "BUY" else OrderSide.SELL
        order_type = OrderType.LIMIT if intent.max_price is not None else OrderType.MARKET
        result = await self._clob.submit_order(
            condition_id=intent.condition_id,
            asset_id=asset_id,
            side=side,
            size=intent.size_usd,
            price=intent.max_price,
            order_type=order_type,
        )

        if not result.success:
            logger.warning(
                "live.order_failed",
                intent_id=intent_id,
                condition_id=intent.condition_id,
                error=result.error,
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
                filled_at=now,
                error=result.error or "order failed",
            )

        filled_price = result.filled_price or intent.max_price or 0.50
        filled_size = result.filled_size or intent.size_usd

        # Record fill in position tracker
        fill_record = FillRecord(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            asset_id=asset_id,
            side=intent.side,
            outcome=intent.outcome,
            price=filled_price,
            size_usd=filled_size,
            fee_usd=0.0,
            filled_at=datetime.fromtimestamp(now, tz=UTC),
        )
        await self._tracker.record_fill(fill_record)

        logger.info(
            "live.filled",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            price=filled_price,
            size=filled_size,
        )

        return Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=filled_price,
            filled_size_usd=filled_size,
            fee_usd=0.0,
            status=FillStatus.FILLED,
            filled_at=now,
        )

    def _check_limits(self, intent: TradeIntent) -> str | None:
        """Check position limits. Returns rejection reason or None if OK."""
        # Check per-market limit
        pos = self._tracker.get_position(intent.condition_id)
        if pos is not None:
            current_value = pos.size * pos.last_price
            if current_value + intent.size_usd > self._max_position_usd:
                return (
                    f"position limit: {current_value:.2f} + {intent.size_usd:.2f}"
                    f" > {self._max_position_usd:.2f}"
                )

        # Check total exposure
        total = self._tracker.get_total_exposure()
        if total + intent.size_usd > self._max_total_exposure_usd:
            return (
                f"exposure limit: {total:.2f} + {intent.size_usd:.2f}"
                f" > {self._max_total_exposure_usd:.2f}"
            )

        return None
