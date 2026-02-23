"""Crypto OTM NO strategy: event-driven and vectorized implementations.

Buys NO on out-of-the-money crypto price checkpoint markets (e.g.
"Bitcoin above $120K at 4PM ET?" where YES trades at 5-25%).
The NO side wins ~98.9% of the time because crypto rarely moves
5-25% in 4 hours.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import MarketInfo, TradeIntent
from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig

# ---------------------------------------------------------------------------
# Asset extraction helper
# ---------------------------------------------------------------------------

_ASSET_MAP: dict[str, str] = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
}


def _extract_asset(question: str) -> str | None:
    """Extract crypto ticker from the first word of a market question."""
    if not question:
        return None
    first_word = question.split()[0].lower()
    return _ASSET_MAP.get(first_word)


# ---------------------------------------------------------------------------
# CryptoOTMNoStrategy
# ---------------------------------------------------------------------------


class CryptoOTMNoStrategy:
    """Crypto OTM NO strategy implementing both Strategy and VectorizedStrategy.

    Event-driven (``on_trade``): checks market metadata against crypto price
    filters and fires a signal on the first qualifying trade per market.

    Vectorized (``compute_signals``): same logic over a full Polars DataFrame.
    """

    name: str = "crypto_otm_no"

    def __init__(self, config: CryptoOTMNoConfig) -> None:
        self._cfg = config
        self._signaled: set[str] = set()
        self._pattern = re.compile(config.question_pattern, re.IGNORECASE)

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Process a single trade and potentially emit a signal."""
        cid = trade.condition_id

        if cid in self._signaled:
            return None

        market = await ctx.get_market(cid)
        if market is None:
            return None

        if not self._is_eligible(market, trade):
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
            reason=f"crypto_otm_no: {market.question}",
            signal_time=trade.published_at,
        )
        return [intent]

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        """Not used by crypto-otm-no."""
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
        """Not used by crypto-otm-no."""
        return None

    # ------------------------------------------------------------------
    # Vectorized path
    # ------------------------------------------------------------------

    def compute_signals(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
        """Compute crypto-otm-no signals over historical trade data.

        Returns a DataFrame with columns:
        ``condition_id``, ``signal_time``, ``side``, ``outcome``, ``size_usd``.
        """
        assets_lower = {a.lower() for a in self._cfg.assets}

        # 1. Join trades with markets
        df = trades.join(markets, on="condition_id", how="inner")

        # 2. Filter: category contains "crypto" (case-insensitive)
        df = df.filter(pl.col("category").str.to_lowercase().str.contains("crypto"))

        # 3. Filter: question matches pattern
        df = df.filter(pl.col("question").str.contains(self._cfg.question_pattern))

        # 4. Filter: asset in allowed set (first word of question)
        df = df.with_columns(
            pl.col("question").str.split(" ").list.first().str.to_lowercase().alias("_first_word"),
        )

        # Map first word to ticker, then filter
        # Build a mapping frame for the join
        asset_map_df = pl.DataFrame(
            {
                "_first_word": list(_ASSET_MAP.keys()),
                "_ticker": list(_ASSET_MAP.values()),
            }
        ).lazy()

        df = df.join(asset_map_df, on="_first_word", how="inner")
        df = df.filter(pl.col("_ticker").str.to_lowercase().is_in(assets_lower))

        # 5. Filter: YES price in band (use trade price as proxy)
        df = df.filter(
            (pl.col("price") >= self._cfg.yes_price_min)
            & (pl.col("price") <= self._cfg.yes_price_max)
        )

        # 6. Sort and take first qualifying trade per condition_id
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["condition_id"], keep="first")

        # 7. Return output columns
        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.lit("NO").alias("outcome"),
            pl.lit(self._cfg.base_bet_usd).alias("size_usd"),
        )

        return result.collect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_eligible(self, market: MarketInfo, trade: NormalizedTrade) -> bool:
        """Check if a market + trade pair qualifies for a signal."""
        # Category must contain "crypto"
        if market.category is None or "crypto" not in market.category.lower():
            return False

        # Question must match pattern (above/below)
        if not self._pattern.search(market.question):
            return False

        # Must be a supported asset
        asset = _extract_asset(market.question)
        if asset is None or asset not in self._cfg.assets:
            return False

        # YES price must be in band
        yes_price = market.yes_price
        if yes_price is None:
            yes_price = float(trade.price)
        return self._cfg.yes_price_min <= yes_price <= self._cfg.yes_price_max
