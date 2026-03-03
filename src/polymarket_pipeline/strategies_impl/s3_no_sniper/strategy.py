"""S3 NO Sniper Strategy — buy NO on new markets in mispriced tags.

==========================================================================
VALIDATED RESULTS (tick-by-tick, 3 OOS periods: Jul-25, Oct-25, Jan-26)
==========================================================================

    Config              | Fills | HR    | PnL   | Sharpe | PF
    --------------------+-------+-------+-------+--------+-----
    Economy+Tech mp0.50 |   366 | 77.0% | +$213 |   0.43 | 1.33

    Per-period: Jul +$84 (79.7%), Oct +$104 (75.6%), Jan +$26 (76.7%)
    Avg win: $4.04, Avg loss: $10.17, Per bet: $0.77

==========================================================================
VALIDATED HYPOTHESES (constraints encoded below)
==========================================================================

    - Edge is in first 5 min of market life (decays 16-34pp by 5-15 min)
    - Only Economy + Tech tags (tick-by-tick profitable)
    - Trump collapses in tick-by-tick (53-60% HR, negative PnL) → EXCLUDED
    - Crypto has no timing edge (48.9% NO WR) → EXCLUDED
    - YES price 0.15-0.50 is optimal (tighter zones kill payoff ratio)
    - Only BUY side trades as signal
    - One position per market (dedup via _positioned set)
    - No stop loss (cuts winners = losers at same rate)
    - Tighter price zones are COUNTERPRODUCTIVE

==========================================================================
REQUIRED DEPENDENCIES
==========================================================================

    Provider: s3_data_provider (tag_map, token_map, known_market_ids)
    Kafka topics: trades.raw (main feed)

==========================================================================
TAG SAFETY
==========================================================================

    Hardcoded _EXCLUDED_TAGS = {"Trump", "Crypto"} — belt-and-suspenders
    on top of TOML eligible_tags. Trump collapses to 53-60% HR in
    tick-by-tick despite 79.8% vectorized. Crypto has no timing edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class S3Config:
    """Strategy parameters — all validated via tick-by-tick backtesting."""

    eligible_tags: frozenset[str] = frozenset({"Economy", "Tech"})
    min_yes_price: float = 0.15
    max_yes_price: float = 0.50
    max_market_age_s: int = 300  # 5 minutes
    spread_buffer: float = 0.02
    position_size_usd: float = 10.0


# Tags that must NEVER be traded — hardcoded safety net on top of TOML config.
# Trump collapses to 53-60% HR in tick-by-tick despite 79.8% vectorized.
# Crypto has no timing edge (48.9% NO WR, no decay).
_EXCLUDED_TAGS = frozenset({"Trump", "Crypto"})


class S3NoSniperStrategy:
    """Buy NO on new markets in mispriced tags within the first 5 minutes.

    Entry: first BUY trade in eligible tag with YES price in [0.15, 0.50]
    within 5 minutes of market birth. Exit: hold to resolution (no stop loss).
    """

    name: str = "s3_no_sniper"

    def __init__(self, cfg: S3Config | None = None) -> None:
        self._cfg = cfg or S3Config()
        # condition_id -> first trade published_at (tracked at runtime)
        self._first_seen: dict[str, float] = {}
        # condition_ids where we already entered
        self._positioned: set[str] = set()
        # whether we've initialized known markets into _first_seen
        self._initialized_known: bool = False

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id

        # --- Load features from provider ---
        feats = await ctx.get_features("s3_data_provider")
        if not feats:
            return None
        tag_map: dict[str, str] = feats.get("tag_map", {})
        token_map: dict[str, dict[str, str]] = feats.get("token_map", {})
        known_market_ids: frozenset[str] = feats.get("known_market_ids", frozenset())

        # --- Market birth safety: pre-populate known markets on first call ---
        if not self._initialized_known:
            for known_cid in known_market_ids:
                self._first_seen[known_cid] = 0.0  # epoch 0 → age always > 300s
            self._initialized_known = True

        # 1. Tag filter
        tag = tag_map.get(cid)
        if tag is None or tag not in self._cfg.eligible_tags:
            return None

        # 1b. Hardcoded safety — reject excluded tags even if TOML says otherwise
        if tag in _EXCLUDED_TAGS:
            return None

        # 2. Track market birth
        ts = trade.published_at
        if cid not in self._first_seen:
            self._first_seen[cid] = ts

        # 3. Market age filter
        age_s = ts - self._first_seen[cid]
        if age_s > self._cfg.max_market_age_s:
            return None

        # 4. Already positioned (one position per market)
        if cid in self._positioned:
            return None

        # 5. Determine YES-equivalent price from trade
        # Build asset_id -> {condition_id, outcome} lookup from token_map
        asset_info = None
        cid_tokens = token_map.get(cid)
        if cid_tokens:
            for outcome, asset_id in cid_tokens.items():
                if asset_id == trade.asset_id:
                    asset_info = {"condition_id": cid, "outcome": outcome}
                    break

        trade_outcome = asset_info["outcome"] if asset_info is not None else "YES"

        trade_price = float(trade.price)
        yes_price = trade_price if trade_outcome == "YES" else 1.0 - trade_price

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
        no_asset_id = token_map.get(cid, {}).get("NO", trade.asset_id)

        logger.info(
            "s3_no_sniper.entry",
            condition_id=cid,
            tag=tag,
            yes_price=round(yes_price, 3),
            no_price=round(no_price, 3),
            age_s=round(age_s, 1),
        )

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
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None


def create_s3_no_sniper_strategy(config: Any) -> S3NoSniperStrategy:
    """Factory function for CLI registry.

    Reads [strategy.*.params] from TOML and constructs S3NoSniperStrategy.
    """
    params = config.params if hasattr(config, "params") else {}
    eligible_tags_raw = params.get("eligible_tags", ["Economy", "Tech"])
    cfg = S3Config(
        eligible_tags=frozenset(eligible_tags_raw),
        min_yes_price=params.get("min_yes_price", 0.15),
        max_yes_price=params.get("max_yes_price", 0.50),
        max_market_age_s=params.get("max_market_age_s", 300),
        spread_buffer=params.get("spread_buffer", 0.02),
        position_size_usd=params.get("position_size_usd", 10.0),
    )
    strat = S3NoSniperStrategy(cfg)
    strat.name = config.name
    return strat
