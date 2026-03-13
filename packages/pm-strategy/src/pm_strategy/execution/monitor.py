"""PositionMonitor -- trailing stop loss on the trade hot path.

Piggybacks on every trade flowing through LiveRunner._handle_trade().
Keyed by asset_id for O(1) lookup. Emits SELL TradeIntents when the
trailing stop is breached.

Usage:
    monitor = PositionMonitor()
    # After a BUY fill:
    monitor.register(fill, trail_delta=0.08)
    # On every trade:
    intent = monitor.check(trade.asset_id, float(trade.price), trade.published_at)
    # On resolution / reset:
    monitor.unregister(condition_id)
    monitor.clear()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pm_strategy.types import Fill

from pm_strategy.types import TradeIntent

logger = structlog.get_logger(__name__)


@dataclass
class TrailingStop:
    """Mutable trailing stop state for a single position."""

    condition_id: str
    strategy: str
    outcome: str  # "YES" or "NO" -- what we hold
    asset_id: str
    entry_price: float
    high_watermark: float
    trail_delta: float
    registered_at: float = field(default_factory=time.time)

    @property
    def stop_level(self) -> float:
        return self.high_watermark - self.trail_delta


class PositionMonitor:
    """Trailing stop loss monitor -- O(1) per trade on the hot path.

    Parameters
    ----------
    default_trail_delta:
        Fallback trail delta when none is provided at registration.
    min_entry_price:
        Skip registration for fills below this price. Longshots (<0.30) have
        3-10x upside that the SL clips catastrophically.
    max_entry_price:
        Skip registration for fills above this price. High-entry positions
        (>0.70) have 80%+ hit rates and limited upside -- SL mostly hurts.
    """

    def __init__(
        self,
        default_trail_delta: float = 0.10,
        min_entry_price: float = 0.30,
        max_entry_price: float = 0.70,
    ) -> None:
        self._default_trail_delta = default_trail_delta
        self._min_entry_price = min_entry_price
        self._max_entry_price = max_entry_price
        # Primary index: asset_id -> TrailingStop (hot path lookup)
        self._stops: dict[str, TrailingStop] = {}
        # Reverse index: condition_id -> asset_id (for unregister by market)
        self._cid_to_asset: dict[str, str] = {}

    def register(
        self,
        fill: Fill,
        trail_delta: float | None = None,
        asset_id: str | None = None,
    ) -> None:
        """Register a trailing stop after a BUY fill.

        Parameters
        ----------
        fill:
            The BUY fill that opened the position.
        trail_delta:
            Absolute trailing delta (e.g., 0.08 = 8 cents). Falls back
            to ``default_trail_delta`` if not provided.
        asset_id:
            Token asset_id to track. If not provided, must be resolvable
            from the fill's order context (caller's responsibility).
        """
        if fill.side != "BUY":
            return

        # Entry price gate: SL only helps in the 0.30-0.70 band
        if fill.filled_price < self._min_entry_price:
            logger.debug(
                "monitor.skip_longshot",
                condition_id=fill.condition_id[:16],
                price=round(fill.filled_price, 4),
            )
            return
        if fill.filled_price >= self._max_entry_price:
            logger.debug(
                "monitor.skip_high_entry",
                condition_id=fill.condition_id[:16],
                price=round(fill.filled_price, 4),
            )
            return

        delta = trail_delta if trail_delta is not None else self._default_trail_delta
        aid = asset_id or ""
        if not aid:
            logger.warning(
                "monitor.register_no_asset_id",
                condition_id=fill.condition_id,
                strategy=fill.strategy,
            )
            return

        stop = TrailingStop(
            condition_id=fill.condition_id,
            strategy=fill.strategy,
            outcome=fill.outcome,
            asset_id=aid,
            entry_price=fill.filled_price,
            high_watermark=fill.filled_price,
            trail_delta=delta,
        )
        self._stops[aid] = stop
        self._cid_to_asset[fill.condition_id] = aid

        logger.info(
            "monitor.registered",
            strategy=fill.strategy,
            condition_id=fill.condition_id[:16],
            outcome=fill.outcome,
            entry=round(fill.filled_price, 4),
            trail_delta=delta,
            initial_sl=round(stop.stop_level, 4),
        )

    def check(
        self,
        asset_id: str,
        price: float,
        signal_time: float,
    ) -> TradeIntent | None:
        """Check if a trailing stop is breached. Called on every trade.

        Returns a SELL TradeIntent if triggered, else None.
        This is the hot path -- one dict lookup, one or two comparisons.
        """
        stop = self._stops.get(asset_id)
        if stop is None:
            return None

        # Ratchet high watermark up (never down)
        if price > stop.high_watermark:
            stop.high_watermark = price

        # Check breach
        if price > stop.stop_level:
            return None

        # === TRIGGERED ===
        # Remove from tracking (one-shot)
        del self._stops[asset_id]
        self._cid_to_asset.pop(stop.condition_id, None)

        logger.warning(
            "monitor.sl_triggered",
            strategy=stop.strategy,
            condition_id=stop.condition_id[:16],
            outcome=stop.outcome,
            entry=round(stop.entry_price, 4),
            hwm=round(stop.high_watermark, 4),
            stop_level=round(stop.stop_level, 4),
            trigger_price=round(price, 4),
            gain_from_entry=round(stop.high_watermark - stop.entry_price, 4),
        )

        return TradeIntent(
            strategy=stop.strategy,
            condition_id=stop.condition_id,
            side="SELL",
            outcome=stop.outcome,
            size_usd=0.0,  # sentinel: runner fills with full position size
            urgency="immediate",
            max_price=None,
            reason=(
                f"trailing_sl:entry={stop.entry_price:.3f}"
                f"|hwm={stop.high_watermark:.3f}"
                f"|sl={stop.stop_level:.3f}"
                f"|trigger={price:.3f}"
            ),
            signal_time=signal_time,
            asset_id=stop.asset_id,
        )

    def unregister(self, condition_id: str) -> None:
        """Remove trailing stop for a market (e.g., on resolution)."""
        aid = self._cid_to_asset.pop(condition_id, None)
        if aid is not None:
            self._stops.pop(aid, None)

    def clear(self) -> None:
        """Remove all trailing stops (e.g., on runner reset)."""
        self._stops.clear()
        self._cid_to_asset.clear()

    @property
    def active_count(self) -> int:
        return len(self._stops)

    def get_status(self) -> dict[str, dict[str, float]]:
        """Return current stop levels for observability."""
        return {
            stop.condition_id[:16]: {
                "entry": round(stop.entry_price, 4),
                "hwm": round(stop.high_watermark, 4),
                "stop_level": round(stop.stop_level, 4),
                "trail_delta": stop.trail_delta,
            }
            for stop in self._stops.values()
        }
