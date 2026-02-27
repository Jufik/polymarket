"""Will-NO strategy: event-driven and vectorized implementations.

Buys NO on binary "Will X happen?" markets where YES is priced in
research-derived bands.  Each band has a bet-size multiplier based on
empirical PnL/day, so the sweet spot (25-30 %) gets full size while
the edges get reduced sizing.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import MarketInfo, TradeIntent
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig


class WillNoStrategy:
    """Will-NO strategy implementing both Strategy and VectorizedStrategy.

    Event-driven (``on_trade``): checks if the market is a qualifying
    "Will" binary question with YES in a configured price band,
    then fires a BUY NO signal on the first qualifying trade.

    Vectorized (``compute_signals``): same logic over Polars DataFrames.
    """

    name: str = "will_no"

    def __init__(self, config: WillNoConfig) -> None:
        self._cfg = config
        self._signaled: set[str] = set()
        self._pattern = re.compile(config.question_pattern, re.IGNORECASE)

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id

        if cid in self._signaled:
            return None

        market = await ctx.get_market(cid)
        if market is None:
            return None

        if not self._is_eligible(market, trade):
            return None

        # Market size filter — skip markets above max_bucket
        if self._cfg.max_bucket is not None:
            buckets = await ctx.get_features("market_size_bucket")
            if buckets is not None:
                bucket = buckets.get(cid)
                if bucket is not None:
                    allowed = ("thin", "med", "thick", "heavy")
                    max_idx = (
                        allowed.index(self._cfg.max_bucket)
                        if self._cfg.max_bucket in allowed
                        else len(allowed) - 1
                    )
                    cur_idx = (
                        allowed.index(bucket)
                        if bucket in allowed
                        else len(allowed) - 1
                    )
                    if cur_idx > max_idx:
                        return None

        # Volume filter — skip markets above max_volume_usd
        if self._cfg.max_volume_usd > 0:
            vol_key = self._cfg.volume_column
            volumes = await ctx.get_features(vol_key)
            if volumes is not None:
                vol = volumes.get(cid)
                if vol is not None and vol > self._cfg.max_volume_usd:
                    return None

        self._signaled.add(cid)

        yes_price = market.yes_price if market.yes_price is not None else float(trade.price)
        bet_size = self._bet_size(yes_price)

        if self._cfg.dual_sided:
            half = bet_size / 2
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="NO",
                    size_usd=half,
                    urgency="patient",
                    max_price=1.0 - yes_price,
                    reason=f"will_no: {market.question}",
                    signal_time=trade.published_at,
                ),
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="SELL",
                    outcome="YES",
                    size_usd=half,
                    urgency="patient",
                    max_price=yes_price,
                    reason=f"will_no: {market.question}",
                    signal_time=trade.published_at,
                ),
            ]

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome="NO",
            size_usd=bet_size,
            urgency="patient",
            max_price=1.0 - yes_price,
            reason=f"will_no: {market.question}",
            signal_time=trade.published_at,
        )
        return [intent]

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    # ------------------------------------------------------------------
    # Vectorized path
    # ------------------------------------------------------------------

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        avoid = list(self._cfg.avoid_keywords)
        prefer = list(self._cfg.prefer_keywords)

        df = trades.join(markets, on="condition_id", how="inner")

        # Filter: question matches "Will" pattern
        df = df.filter(
            pl.col("question").str.contains(self._cfg.question_pattern)
        )

        # Filter: YES price in band or flat range
        if self._cfg.price_bands:
            band_cond = pl.lit(False)
            n = len(self._cfg.price_bands)
            for i, (lo, hi, _mult) in enumerate(self._cfg.price_bands):
                if i < n - 1:
                    band_cond = band_cond | (
                        (pl.col("price") >= lo) & (pl.col("price") < hi)
                    )
                else:
                    band_cond = band_cond | (
                        (pl.col("price") >= lo) & (pl.col("price") <= hi)
                    )
            df = df.filter(band_cond)
        else:
            df = df.filter(
                (pl.col("price") >= self._cfg.yes_price_min)
                & (pl.col("price") <= self._cfg.yes_price_max)
            )

        # Filter: avoid keywords
        if avoid:
            for kw in avoid:
                df = df.filter(
                    ~pl.col("question").str.to_lowercase().str.contains(kw.lower())
                )

        # Filter: prefer keywords — at least one must match (when set)
        if prefer:
            prefer_cond = pl.lit(False)
            q_lower = pl.col("question").str.to_lowercase()
            for kw in prefer:
                prefer_cond = prefer_cond | q_lower.str.contains(kw.lower())
            df = df.filter(prefer_cond)

        # Volume filter (vectorized path only, requires volume_column in data)
        if self._cfg.max_volume_usd > 0:
            vol_col = self._cfg.volume_column
            try:
                cols = df.collect_schema().names()
            except AttributeError:
                cols = list(df.schema)
            if vol_col in cols:
                df = df.filter(pl.col(vol_col) <= self._cfg.max_volume_usd)

        # First qualifying trade per condition_id
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["condition_id"], keep="first")

        # Band-adjusted sizing
        if self._cfg.price_bands:
            size_expr = pl.lit(0.0)
            n = len(self._cfg.price_bands)
            for i, (lo, hi, mult) in enumerate(self._cfg.price_bands):
                if i < n - 1:
                    cond = (pl.col("price") >= lo) & (pl.col("price") < hi)
                else:
                    cond = (pl.col("price") >= lo) & (pl.col("price") <= hi)
                size_expr = (
                    pl.when(cond)
                    .then(pl.lit(self._cfg.base_bet_usd * mult))
                    .otherwise(size_expr)
                )
        else:
            size_expr = pl.lit(self._cfg.base_bet_usd)

        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.lit("NO").alias("outcome"),
            size_expr.alias("size_usd"),
            pl.col("price").alias("entry_price"),
        )

        return result.collect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bet_size(self, yes_price: float) -> float:
        """Compute bet size adjusted by price-band multiplier."""
        if self._cfg.price_bands:
            return self._cfg.base_bet_usd * self._cfg.band_multiplier(yes_price)
        return self._cfg.base_bet_usd

    def _is_eligible(self, market: MarketInfo, trade: NormalizedTrade) -> bool:
        # Question must match "Will" pattern
        if not self._pattern.search(market.question):
            return False

        # Check avoid keywords
        q_lower = market.question.lower()
        for kw in self._cfg.avoid_keywords:
            if kw.lower() in q_lower:
                return False

        # Check prefer keywords — if set, at least one must match
        if self._cfg.prefer_keywords:
            if not any(kw.lower() in q_lower for kw in self._cfg.prefer_keywords):
                return False

        # YES price check
        yes_price = market.yes_price
        if yes_price is None:
            yes_price = float(trade.price)

        if self._cfg.price_bands:
            return self._cfg.band_multiplier(yes_price) > 0
        return self._cfg.yes_price_min <= yes_price <= self._cfg.yes_price_max

    def get_rationale(
        self, condition_id: str, trade: NormalizedTrade, ctx: StrategyContext
    ) -> dict[str, Any]:
        """Return rationale metadata for an intent on this market."""
        rationale: dict[str, Any] = {"strategy": self.name}
        market = None
        # Sync context — called right after on_trade so market should be cached
        # Duck-type: ctx may have _markets dict
        markets = getattr(ctx, "_markets", {})
        market = markets.get(condition_id)

        if market is not None:
            q_lower = market.question.lower()
            matched = [
                kw for kw in self._cfg.prefer_keywords if kw.lower() in q_lower
            ]
            rationale["keywords_matched"] = matched
            yes_price = market.yes_price or float(trade.price)
            rationale["yes_price"] = round(yes_price, 4)
            rationale["multiplier"] = round(self._cfg.band_multiplier(yes_price), 2)

            # Find which band matched
            for lo, hi, mult in self._cfg.price_bands:
                if lo <= yes_price <= hi and mult > 0:
                    rationale["price_band"] = f"{lo:.0%}-{hi:.0%}"
                    break

        rationale["max_volume_usd"] = self._cfg.max_volume_usd
        rationale["max_bucket"] = self._cfg.max_bucket
        return rationale
