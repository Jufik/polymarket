"""S3 NO Sniper — first-5-minute base rate strategy.

Buys NO on newly created markets where the YES price is in the 0.15-0.50
"mispricing zone". Edge is concentrated in the first 5 minutes of market
life before smart money corrects the price.

Eligible tags (high NO base rate, proven first-5-min edge):
  Tech (83.5% NO WR), Trump (79.8%), Economy (75.9%)

Usage:
    from research.strategies.s3_no_sniper import S3NoSniper, S3Config
    strat = S3NoSniper(S3Config())
    strat.set_tag_map({"cid_1": "Tech", "cid_2": "Trump"})
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


DEFAULT_ELIGIBLE_TAGS = frozenset({"Tech", "Trump", "Economy"})


@dataclass(frozen=True)
class S3Config:
    """Strategy parameters — all sweepable."""

    eligible_tags: frozenset[str] = DEFAULT_ELIGIBLE_TAGS
    min_yes_price: float = 0.15
    max_yes_price: float = 0.50
    max_market_age_s: int = 300  # 5 minutes
    spread_buffer: float = 0.02
    position_size_usd: float = 10.0


class S3NoSniper:
    """Buy NO on new markets in mispriced tags within the first few minutes."""

    name = "s3_no_sniper"

    def __init__(self, cfg: S3Config | None = None) -> None:
        self._cfg = cfg or S3Config()
        # condition_id -> tag (set externally)
        self._tag_map: dict[str, str] = {}
        # condition_id -> first trade published_at (tracked per replay)
        self._first_seen: dict[str, float] = {}
        # condition_ids where we already entered
        self._positioned: set[str] = set()

    # ------------------------------------------------------------------
    # Setup (called before replay)
    # ------------------------------------------------------------------

    def set_tag_map(self, tag_map: dict[str, str]) -> None:
        """Set condition_id -> primary_tag mapping."""
        self._tag_map = tag_map

    # ------------------------------------------------------------------
    # Strategy protocol
    # ------------------------------------------------------------------

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id

        # 1. Tag filter
        tag = self._tag_map.get(cid)
        if tag is None or tag not in self._cfg.eligible_tags:
            return None

        # 2. Track market birth
        ts = trade.published_at
        if cid not in self._first_seen:
            self._first_seen[cid] = ts

        # 3. Market age filter
        age_s = ts - self._first_seen[cid]
        if age_s > self._cfg.max_market_age_s:
            return None

        # 4. Already positioned
        if cid in self._positioned:
            return None

        # 5. YES price zone filter
        yes_price = float(trade.price)
        if yes_price < self._cfg.min_yes_price or yes_price > self._cfg.max_yes_price:
            return None

        # 6. Only BUY side trades (we're buying NO on the other side)
        if str(trade.side) not in ("BUY", "Side.BUY"):
            return None

        # --- Entry ---
        self._positioned.add(cid)
        no_price = 1.0 - yes_price
        max_price = min(no_price + self._cfg.spread_buffer, 0.99)

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome="NO",
                size_usd=self._cfg.position_size_usd,
                urgency="normal",
                max_price=max_price,
                reason=f"s3_snipe tag={tag} yes={yes_price:.2f} age={age_s:.0f}s",
                asset_id=trade.asset_id,
            ),
        ]

    async def on_market_update(self, market_id: str, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None
