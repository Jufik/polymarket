"""Proportional-copy strategy: copy graded skilled traders' positions.

Tracks individual pool traders' entries and copies their direction.
Supports two sizing modes:

- **equal**: Fixed ``capital_per_trader_usd`` per signal (simple, robust).
- **proportional**: Scale bet by the trader's relative trade size —
  a 5x-average trade is a much stronger signal than a 0.5x trade.
  Research shows winners are 2.4x larger than losers by volume.

Detects contradictions (pool traders disagreeing) and optionally skips
conflicted markets. Sets ``max_price`` on intents to prevent chasing
expensive fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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


@dataclass
class _TraderStats:
    """Running average of a trader's trade sizes for proportional sizing."""

    total_usd: float = 0.0
    count: int = 0

    @property
    def avg_usd(self) -> float:
        return self.total_usd / self.count if self.count > 0 else 0.0


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
        self._trader_stats: dict[str, _TraderStats] = {}

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _compute_size(self, maker: str, trade_usd: float) -> float:
        """Compute copy bet size based on sizing mode.

        For "proportional": scale by the trader's relative trade size,
        capped at ``max_sizing_mult``.
        For "equal": fixed ``capital_per_trader_usd``.
        """
        if self._cfg.sizing == "proportional":
            stats = self._trader_stats.get(maker)
            if stats is not None and stats.avg_usd > 0:
                relative = trade_usd / stats.avg_usd
                mult = min(relative, self._cfg.max_sizing_mult)
                return self._cfg.capital_per_trader_usd * mult

        return self._cfg.capital_per_trader_usd

    def _compute_max_price(self, trade_price: float, is_yes: bool) -> float | None:
        """Compute max entry price for the copy intent.

        Directional entry price + slippage buffer, capped at max_entry_price.
        """
        entry = trade_price if is_yes else 1.0 - trade_price
        max_price = entry + self._cfg.price_slippage

        if self._cfg.max_entry_price is not None:
            max_price = min(max_price, self._cfg.max_entry_price)

        return max_price

    def _update_trader_stats(self, maker: str, amount_usd: float) -> None:
        """Update running trade-size average for a trader."""
        stats = self._trader_stats.get(maker)
        if stats is None:
            stats = _TraderStats()
            self._trader_stats[maker] = stats
        stats.total_usd += amount_usd
        stats.count += 1

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

        trade_usd = float(trade.amount_usd)
        trade_price = float(trade.price)

        # Compute size BEFORE updating stats (use prior average)
        size_usd = self._compute_size(maker, trade_usd)

        # Always update running average for pool traders
        self._update_trader_stats(maker, trade_usd)

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

        outcome: Literal["YES", "NO"] = "YES" if is_yes else "NO"
        max_price = self._compute_max_price(trade_price, is_yes)

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome=outcome,
            size_usd=size_usd,
            urgency="patient",
            max_price=max_price,
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
            market_dirs = df.group_by("condition_id").agg(
                pl.col("bet_yes").sum().alias("n_yes"),
                (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
            )
            # Keep only markets with unanimous direction
            unanimous = market_dirs.filter(
                (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
            ).select("condition_id")
            df = df.join(unanimous, on="condition_id", how="inner")

        # Compute sizing
        if self._cfg.sizing == "proportional":
            # Per-trader average trade size
            avg_sizes = (
                trades.filter(pl.col("maker").is_in(pool))
                .group_by("maker")
                .agg(pl.col("amount_usd").cast(pl.Float64).mean().alias("avg_trade_usd"))
            )
            df = df.join(avg_sizes, on="maker", how="left")
            df = df.with_columns(
                (
                    pl.col("amount_usd").cast(pl.Float64) / pl.col("avg_trade_usd")
                )
                .clip(0.0, self._cfg.max_sizing_mult)
                .fill_null(1.0)
                .alias("sizing_mult"),
            )
            size_expr = (
                pl.lit(self._cfg.capital_per_trader_usd) * pl.col("sizing_mult")
            ).alias("size_usd")
        else:
            size_expr = pl.lit(self._cfg.capital_per_trader_usd).alias("size_usd")

        # Max price: directional entry + slippage, capped at max_entry_price
        price_col = pl.col("price").cast(pl.Float64)
        dir_entry = (
            pl.when(pl.col("bet_yes"))
            .then(price_col)
            .otherwise(1.0 - price_col)
        )
        max_price_expr = (dir_entry + pl.lit(self._cfg.price_slippage)).alias(
            "max_price"
        )
        if self._cfg.max_entry_price is not None:
            max_price_expr = (
                (dir_entry + pl.lit(self._cfg.price_slippage))
                .clip(upper_bound=self._cfg.max_entry_price)
                .alias("max_price")
            )

        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.when(pl.col("bet_yes"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("outcome"),
            size_expr,
            pl.col("price").alias("entry_price"),
            max_price_expr,
        )

        return result.collect()
