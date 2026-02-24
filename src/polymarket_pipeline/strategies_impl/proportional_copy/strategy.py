"""Proportional-copy strategy: copy graded skilled traders' positions.

Tracks individual pool traders' entries and copies their direction
with equal-weight sizing. Detects contradictions (pool traders
disagreeing) and optionally skips conflicted markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)


@dataclass
class _MarketState:
    """Per-market accumulator tracking pool trader entries."""

    n_yes: int = 0
    n_no: int = 0
    seen_traders: set[str] = field(default_factory=set)


class ProportionalCopyStrategy:
    """Proportional copy of graded skilled traders.

    Event-driven (``on_trade``): when a pool trader enters a market,
    emit a copy intent in the same direction. Optionally skip if
    another pool trader already entered in the opposite direction
    (contradiction filter).

    Vectorized (``compute_signals``): batch version over DataFrames.
    """

    name: str = "proportional_copy"

    def __init__(self, config: ProportionalCopyConfig) -> None:
        self._cfg = config
        self._states: dict[str, _MarketState] = {}

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        maker = trade.maker
        if maker is None:
            return None

        # Check pool membership (prefer live features, fall back to config)
        pool = await ctx.get_features("pool_traders")
        if pool is None:
            pool = self._cfg.pool_traders
        if maker not in pool:
            return None

        cid = trade.condition_id
        state = self._states.get(cid)
        if state is None:
            state = _MarketState()
            self._states[cid] = state

        # One entry per trader per market
        if maker in state.seen_traders:
            return None
        state.seen_traders.add(maker)

        # Determine direction: BUY = buying YES, SELL = selling YES (= betting NO)
        is_yes = trade.side == "BUY"

        # Contradiction check: if another trader already bet the opposite direction
        if self._cfg.contradiction_filter:
            if is_yes and state.n_no > 0:
                return None
            if not is_yes and state.n_yes > 0:
                return None

        if is_yes:
            state.n_yes += 1
        else:
            state.n_no += 1

        # Emit copy intent
        outcome = "YES" if is_yes else "NO"
        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome=outcome,
            size_usd=self._cfg.capital_per_trader_usd,
            urgency="patient",
            max_price=None,
            reason=f"proportional_copy: {maker[:10]}... bet {outcome}",
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
        pool = list(self._cfg.pool_traders)

        # Filter to pool traders
        df = trades.filter(pl.col("maker").is_in(pool))

        # Sort and deduplicate: first trade per (maker, condition_id)
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["maker", "condition_id"], keep="first")

        # Infer direction: BUY = YES, SELL = NO
        df = df.with_columns(
            (pl.col("side") == "BUY").alias("bet_yes"),
        )

        if self._cfg.contradiction_filter:
            # Per market: compute yes/no counts. Skip if both > 0.
            market_dirs = (
                df.group_by("condition_id")
                .agg(
                    pl.col("bet_yes").sum().alias("n_yes"),
                    (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
                )
            )
            # Keep only markets with unanimous direction
            unanimous = market_dirs.filter(
                (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
            ).select("condition_id")
            df = df.join(unanimous, on="condition_id", how="inner")

        # Emit one signal per trader per market
        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.when(pl.col("bet_yes"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("outcome"),
            pl.lit(self._cfg.capital_per_trader_usd).alias("size_usd"),
        )

        return result.collect()
