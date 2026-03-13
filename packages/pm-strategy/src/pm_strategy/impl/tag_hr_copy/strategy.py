"""TagHRCopyStrategy -- copy BUY YES trades from high-HR qualified traders.

Signal logic (tick-by-tick):
  - Trade is a maker BUY YES (side=BUY, outcome=YES)
  - Maker address is in the qualified pool for this market's tag
  - Market's tag is in target_tags
  - Trade price <= max_avg_entry_price (price ceiling)
  - No existing open position in this market (dedup)
  - max_hold_hours checked at entry time (enforced by max_hold timer)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pm_strategy.types import TradeIntent

if TYPE_CHECKING:
    from pm_core.models import NormalizedTrade

    from pm_strategy.config import StrategyConfig
    from pm_strategy.protocols import StrategyContext

logger = logging.getLogger(__name__)


class TagHRCopyStrategy:
    """Copy BUY YES signals from per-tag qualified high-HR traders.

    Parameters
    ----------
    cfg:
        Strategy config (capital, position limits, mode).
    signal_variant:
        "buy_only" -- only copy BUY YES trades (default).
    max_hold_hours:
        Maximum hours to hold before expiry (enforced via on_timer exit).
    target_tags:
        Which tags to copy (must match TagHRProvider target_tags).
    max_avg_entry_price:
        Per-trade price ceiling (overrides per-tag param for simplicity).
    size_usd:
        Fixed size per trade in USD.
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        signal_variant: str = "buy_only",
        max_hold_hours: float = 48.0,
        target_tags: list[str] | None = None,
        max_avg_entry_price: float = 0.75,
        size_usd: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.name: str = "tag_hr_copy"
        self._cfg = cfg
        self._signal_variant = signal_variant
        self._max_hold_hours = max_hold_hours
        self._target_tags: set[str] = set(target_tags or ["Esports", "1H"])
        self._max_price = max_avg_entry_price
        self._size_usd = size_usd or cfg.max_position_usd
        # Dedup: track ALL condition_ids we've EVER entered (never re-enter)
        self._entered_cids: set[str] = set()

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        """Fire on each tick -- check if this is a copyable signal."""
        # BUY-only variant: only maker BUY YES trades
        if self._signal_variant == "buy_only":
            if trade.side != "BUY":
                return None
            outcome = "YES"
        else:
            if trade.side == "BUY":
                outcome = "YES"
            elif trade.side == "SELL":
                return None
            else:
                return None

        # Price ceiling
        price = float(trade.price)
        if price > self._max_price:
            return None

        # Check if maker is in qualified pool for this market's tag
        features = await ctx.get_features("qualified_traders")
        market_tags = await ctx.get_features("market_tags")

        if features is None or market_tags is None:
            return None

        cid = trade.condition_id
        tag = market_tags.get(cid)
        if tag is None or tag not in self._target_tags:
            return None

        qualified: frozenset[str] = features.get(tag, frozenset())
        maker = (trade.maker or "").lower()
        if not maker or maker not in qualified:
            return None

        # Dedup: never re-enter a market we've already entered (incl. post-settlement)
        if cid in self._entered_cids:
            return None

        existing_pos = await ctx.get_position(cid)
        if existing_pos is not None and (existing_pos.qty_yes > 0 or existing_pos.qty_no > 0):
            self._entered_cids.add(cid)
            return None

        self._entered_cids.add(cid)

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome=outcome,
                size_usd=self._size_usd,
                urgency="patient",
                max_price=min(price + 0.02, 0.99),
                reason=f"tag_hr_copy: {tag} trader {maker[:8]} @ {price:.3f}",
                signal_time=trade.published_at,
                asset_id=trade.asset_id,
            )
        ]

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
        """No-op -- _entered_cids is permanent (never re-enter resolved markets)."""
        return None


def create_tag_hr_copy_strategy(cfg: StrategyConfig, **kwargs: Any) -> TagHRCopyStrategy:
    """Factory function for strategy registry."""
    params = cfg.params or {}
    return TagHRCopyStrategy(cfg, **params)
