"""LiveExecutor — real execution via CLOB API."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from polymarket_pipeline.execution.clob_client import ClobClient, OrderSide, OrderType
from polymarket_pipeline.execution.models import FillRecord, Position
from polymarket_pipeline.execution.position_tracker import PositionTracker
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OrderConfig:
    """Configuration for order lifecycle management."""

    patient_timeout_s: float = 30.0
    patient_poll_interval_s: float = 2.0
    immediate_cancel_delay_s: float = 0.5


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
        order_config: OrderConfig | None = None,
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
        self._order_config = order_config or OrderConfig()

    async def execute(self, intent: TradeIntent) -> Fill:
        """Execute a trade intent with position limit checks.

        Dispatches to urgency-specific handlers:
        - ``immediate`` → FOK: submit, cancel if not instantly filled
        - ``patient`` → resting: submit, poll until filled or timeout
        """
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
                order_id=result.order_id or None,
            )

        # Instant fill — CLOB returned a fill price
        filled_price = result.filled_price
        if filled_price is not None and filled_price > 0:
            return await self._record_fill(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                filled_price=filled_price,
                filled_size=result.filled_size or intent.size_usd,
                order_id=result.order_id,
            )

        # No instant fill — dispatch based on urgency
        if intent.urgency == "patient":
            return await self._execute_patient(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                order_id=result.order_id,
            )
        else:
            return await self._execute_immediate(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                order_id=result.order_id,
            )

    async def _execute_immediate(
        self,
        *,
        intent_id: str,
        intent: TradeIntent,
        asset_id: str,
        order_id: str,
    ) -> Fill:
        """FOK behavior: brief wait, then cancel if not filled."""
        now = time.time()

        # Brief delay to allow fill to propagate
        await asyncio.sleep(self._order_config.immediate_cancel_delay_s)

        # Check if order is still open
        open_orders = await self._clob.get_open_orders(intent.condition_id)
        still_open = any(o.order_id == order_id for o in open_orders)

        if not still_open:
            # Order gone from open list → filled between submit and check
            logger.info(
                "live.immediate_late_fill",
                intent_id=intent_id,
                order_id=order_id,
                condition_id=intent.condition_id,
            )
            return await self._record_fill(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                filled_price=intent.max_price or 0.50,
                filled_size=intent.size_usd,
                order_id=order_id,
            )

        # Still resting — cancel it (FOK semantics)
        await self._clob.cancel_order(order_id)
        logger.info(
            "live.immediate_cancelled",
            intent_id=intent_id,
            order_id=order_id,
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
            error=f"FOK: order not filled, cancelled (order_id={order_id})",
            order_id=order_id,
        )

    async def _execute_patient(
        self,
        *,
        intent_id: str,
        intent: TradeIntent,
        asset_id: str,
        order_id: str,
    ) -> Fill:
        """Resting limit: poll until filled or timeout, cancel remainder."""
        cfg = self._order_config
        deadline = time.monotonic() + cfg.patient_timeout_s

        while time.monotonic() < deadline:
            await asyncio.sleep(cfg.patient_poll_interval_s)

            open_orders = await self._clob.get_open_orders(intent.condition_id)
            matching = [o for o in open_orders if o.order_id == order_id]

            if not matching:
                # Order gone from open list → fully filled
                logger.info(
                    "live.patient_filled",
                    intent_id=intent_id,
                    order_id=order_id,
                    condition_id=intent.condition_id,
                )
                return await self._record_fill(
                    intent_id=intent_id,
                    intent=intent,
                    asset_id=asset_id,
                    filled_price=intent.max_price or 0.50,
                    filled_size=intent.size_usd,
                    order_id=order_id,
                )

            order = matching[0]
            if order.size_matched > 0:
                logger.debug(
                    "live.patient_progress",
                    intent_id=intent_id,
                    order_id=order_id,
                    size_matched=order.size_matched,
                    size_total=order.size,
                )
                # Check if fully matched
                if order.size_matched >= order.size:
                    return await self._record_fill(
                        intent_id=intent_id,
                        intent=intent,
                        asset_id=asset_id,
                        filled_price=intent.max_price or 0.50,
                        filled_size=intent.size_usd,
                        order_id=order_id,
                    )

        # Timeout — check final state
        open_orders = await self._clob.get_open_orders(intent.condition_id)
        matching = [o for o in open_orders if o.order_id == order_id]

        if not matching:
            # Filled during final check
            return await self._record_fill(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                filled_price=intent.max_price or 0.50,
                filled_size=intent.size_usd,
                order_id=order_id,
            )

        order = matching[0]
        await self._clob.cancel_order(order_id)

        if order.size_matched > 0:
            # Partial fill — record what we got
            fill_fraction = order.size_matched / order.size if order.size > 0 else 0.0
            partial_size = intent.size_usd * fill_fraction
            logger.info(
                "live.patient_partial",
                intent_id=intent_id,
                order_id=order_id,
                size_matched=order.size_matched,
                size_total=order.size,
                partial_size_usd=round(partial_size, 2),
            )
            return await self._record_fill(
                intent_id=intent_id,
                intent=intent,
                asset_id=asset_id,
                filled_price=intent.max_price or 0.50,
                filled_size=partial_size,
                order_id=order_id,
                status=FillStatus.PARTIAL,
            )

        # Nothing matched — fully rejected
        logger.info(
            "live.patient_timeout",
            intent_id=intent_id,
            order_id=order_id,
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
            filled_at=time.time(),
            error=f"patient timeout: no fills after {cfg.patient_timeout_s}s (order_id={order_id})",
            order_id=order_id,
        )

    async def _record_fill(
        self,
        *,
        intent_id: str,
        intent: TradeIntent,
        asset_id: str,
        filled_price: float,
        filled_size: float,
        order_id: str,
        status: FillStatus = FillStatus.FILLED,
    ) -> Fill:
        """Record a fill in position tracker and return Fill."""
        now = time.time()

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
            status=status.value,
            order_id=order_id,
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
            status=status,
            filled_at=now,
            order_id=order_id,
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
