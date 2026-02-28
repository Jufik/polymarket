"""LiveExecutor — real execution via CLOB API."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import structlog

from polymarket_pipeline.execution.clob_client import ClobClient, OrderSide, OrderType
from polymarket_pipeline.execution.models import FillRecord, Position
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

        # Require a confirmed fill price — never guess.  If the CLOB API
        # returns success but no ``filled_price``, the order is likely still
        # sitting on the book (unfilled limit).  Recording a fabricated price
        # corrupts position avg_entry and budget tracking.
        filled_price = result.filled_price
        if filled_price is None or filled_price <= 0:
            logger.warning(
                "live.no_fill_price",
                intent_id=intent_id,
                condition_id=intent.condition_id,
                order_id=result.order_id,
                hint="order may be resting on book, not yet filled",
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
                error=f"no confirmed fill price (order_id={result.order_id})",
            )

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

    # Maximum age (seconds) for a cached orderbook to be used in MTM.
    # Stale prices allow position limits to be exceeded in volatile markets.
    _MTM_CACHE_MAX_AGE_S = 30.0

    def _mark_to_market(self, pos: Position) -> float:
        """Estimate current USD value of a position using orderbook mid-price.

        Only uses cached orderbook if it was fetched within
        ``_MTM_CACHE_MAX_AGE_S`` seconds.  Falls back to
        ``pos.last_price`` (entry price) if no fresh orderbook is available.
        """
        import time as _time

        size = float(pos.size)
        asset_id = pos.asset_id
        if asset_id and hasattr(self._clob, "_ob_cache"):
            cached = self._clob._ob_cache.get(asset_id)
            if cached is not None:
                ts, ob = cached
                age = _time.monotonic() - ts
                if age <= self._MTM_CACHE_MAX_AGE_S:
                    mid = (ob.best_bid + ob.best_ask) / 2
                    if mid > 0:
                        return size * mid
        # Fallback: last known price (from fill or trade)
        return size * float(pos.last_price)

    def _check_limits(self, intent: TradeIntent) -> str | None:
        """Check position limits. Returns rejection reason or None if OK."""
        # Check per-market limit (mark-to-market when possible)
        pos = self._tracker.get_position(intent.condition_id)
        if pos is not None:
            current_value = self._mark_to_market(pos)
            if current_value + intent.size_usd > self._max_position_usd:
                return (
                    f"position limit: {current_value:.2f} + {intent.size_usd:.2f}"
                    f" > {self._max_position_usd:.2f}"
                )

        # Check total exposure (mark-to-market each position)
        total = sum(
            self._mark_to_market(p) for p in self._tracker.get_all_positions()
        )
        if total + intent.size_usd > self._max_total_exposure_usd:
            return (
                f"exposure limit: {total:.2f} + {intent.size_usd:.2f}"
                f" > {self._max_total_exposure_usd:.2f}"
            )

        return None
