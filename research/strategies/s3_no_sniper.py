"""S3 NO Sniper — first-5-minute base rate strategy.

Buys NO on newly created markets where the YES price is in the 0.15-0.50
"mispricing zone". Edge is concentrated in the first 5 minutes of market
life before smart money corrects the price.

Eligible tags (tick-by-tick validated, 3 OOS periods):
  Economy (77.6% HR tick), Tech (72.7% HR tick)

Excluded: Trump (collapses to 53-60% in tick), Crypto (no timing edge).

Best config: Economy+Tech, mp0.50 → 77.0% HR, +$213, 0.43 Sharpe, 1.33 PF
  Tighter price zones (mp0.30) increase HR but kill payoff ratio.

Usage:
    from research.strategies.s3_no_sniper import S3NoSniper, S3Config
    strat = S3NoSniper(S3Config())
    strat.set_tag_map({"cid_1": "Tech", "cid_2": "Economy"})
    strat.set_token_map({"cid_1": {"YES": "asset_y", "NO": "asset_n"}})
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


DEFAULT_ELIGIBLE_TAGS = frozenset({"Economy", "Tech"})


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
        # asset_id -> {"condition_id": str, "outcome": str}
        self._asset_lookup: dict[str, dict[str, str]] = {}
        # condition_id -> {"YES": asset_id, "NO": asset_id}
        self._token_map: dict[str, dict[str, str]] = {}
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

    def set_token_map(self, token_map: dict[str, dict[str, str]]) -> None:
        """Set condition_id -> {"YES": asset_id, "NO": asset_id}.

        Also builds the inverse lookup asset_id -> {condition_id, outcome}.
        """
        self._token_map = token_map
        self._asset_lookup = {}
        for cid, outcomes in token_map.items():
            for outcome, asset_id in outcomes.items():
                self._asset_lookup[asset_id] = {
                    "condition_id": cid,
                    "outcome": outcome,
                }

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

        # 5. Determine YES-equivalent price from trade
        # If trade's asset is the YES token, price IS the YES price.
        # If trade's asset is the NO token, YES price = 1 - trade.price.
        asset_info = self._asset_lookup.get(trade.asset_id)
        if asset_info is not None:
            trade_outcome = asset_info["outcome"]
        else:
            # No token map — assume price is YES price (best effort)
            trade_outcome = "YES"

        trade_price = float(trade.price)
        if trade_outcome == "YES":
            yes_price = trade_price
        else:
            yes_price = 1.0 - trade_price

        # 6. YES price zone filter
        if yes_price < self._cfg.min_yes_price or yes_price > self._cfg.max_yes_price:
            return None

        # 7. Only BUY side trades as signal
        if str(trade.side) not in ("BUY", "Side.BUY"):
            return None

        # --- Entry ---
        self._positioned.add(cid)
        no_price = 1.0 - yes_price
        max_price = min(no_price + self._cfg.spread_buffer, 0.99)

        # Resolve NO asset_id from token map
        no_asset_id = self._token_map.get(cid, {}).get("NO", trade.asset_id)

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
                signal_time=ts,
                asset_id=no_asset_id,
            ),
        ]

    async def on_market_update(
        self, market_id: str, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None
