"""Will-NO strategy: event-driven and vectorized implementations.

Buys NO on binary "Will X happen?" markets where YES is priced 15-40%.
The NO side wins ~75-85% of the time because most proposed events don't
happen, and the favorite-longshot bias makes YES overpriced.
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
    "Will" binary question with YES in the configured price band,
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

        self._signaled.add(cid)

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome="NO",
            size_usd=self._cfg.base_bet_usd,
            urgency="patient",
            max_price=None,
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

        df = trades.join(markets, on="condition_id", how="inner")

        # Filter: question matches "Will" pattern
        df = df.filter(
            pl.col("question").str.contains(self._cfg.question_pattern)
        )

        # Filter: YES price in band (use trade price as proxy)
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

        # First qualifying trade per condition_id
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["condition_id"], keep="first")

        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.lit("NO").alias("outcome"),
            pl.lit(self._cfg.base_bet_usd).alias("size_usd"),
            pl.col("price").alias("entry_price"),
        )

        return result.collect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_eligible(self, market: MarketInfo, trade: NormalizedTrade) -> bool:
        # Question must match "Will" pattern
        if not self._pattern.search(market.question):
            return False

        # Check avoid keywords
        q_lower = market.question.lower()
        for kw in self._cfg.avoid_keywords:
            if kw.lower() in q_lower:
                return False

        # YES price must be in band
        yes_price = market.yes_price
        if yes_price is None:
            yes_price = float(trade.price)
        return self._cfg.yes_price_min <= yes_price <= self._cfg.yes_price_max
