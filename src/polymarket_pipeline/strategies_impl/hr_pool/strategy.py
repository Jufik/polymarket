"""HR Pool copy strategy: fixed-bet copy of high hit-rate traders.

Sweep-validated edge: skilled traders (HR >= 0.65) buying cheap NO
tokens (entry < 0.50) generate 28-48% ROI across 5 walk-forward
windows. Zero fees on most Polymarket markets.

Simpler than proportional copy — no MVF, no consistency streaks,
no longshot grading. Just HR + monthly consistency + entry cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.hr_pool.config import HRPoolConfig


@dataclass
class _MarketState:
    """Per-market direction tracker for contradiction filter."""

    n_yes: int = 0
    n_no: int = 0
    seen_traders: set[str] = field(default_factory=set)


class HRPoolStrategy:
    """Fixed-bet copy of high hit-rate traders.

    Event-driven: when a pool trader enters a market in the target
    direction (default: NO), emit a fixed-size copy intent if the
    entry price is below the cap.

    Vectorized: batch signal computation over DataFrames.
    """

    name: str = "hr_pool"

    def __init__(self, config: HRPoolConfig) -> None:
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
        pool = await ctx.get_features("hr_pool")
        if pool is None:
            pool = self._cfg.pool_traders
        if maker not in pool:
            return None

        trade_price = float(trade.price)
        cid = trade.condition_id

        # Determine direction: BUY = YES, SELL = NO
        is_yes = trade.side == "BUY"
        outcome: Literal["YES", "NO"] = "YES" if is_yes else "NO"

        # Direction filter
        if self._cfg.direction != "ALL" and outcome != self._cfg.direction:
            return None

        # Entry price filter (directional)
        dir_entry = trade_price if is_yes else 1.0 - trade_price
        if dir_entry > self._cfg.max_entry_price:
            return None

        # Dedup: one entry per trader per market
        state = self._states.get(cid)
        if state is None:
            state = _MarketState()
            self._states[cid] = state
        if maker in state.seen_traders:
            return None
        state.seen_traders.add(maker)

        # Contradiction check
        if self._cfg.contradiction_filter:
            if is_yes and state.n_no > 0:
                return None
            if not is_yes and state.n_yes > 0:
                return None

        if is_yes:
            state.n_yes += 1
        else:
            state.n_no += 1

        max_price = min(
            dir_entry + self._cfg.price_slippage,
            self._cfg.max_entry_price,
        )

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome=outcome,
                size_usd=self._cfg.bet_size_usd,
                urgency="patient",
                max_price=max_price,
                reason=f"hr_pool: {maker[:10]}... NO@{dir_entry:.2f}",
                signal_time=trade.published_at,
            )
        ]

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    # ------------------------------------------------------------------
    # Vectorized path (backtesting)
    # ------------------------------------------------------------------

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        pool = list(self._cfg.pool_traders)

        df = trades.filter(pl.col("maker").is_in(pool))

        # Sort and deduplicate: first trade per (maker, condition_id)
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["maker", "condition_id"], keep="first")

        # Direction
        df = df.with_columns((pl.col("side") == "BUY").alias("bet_yes"))

        # Direction filter
        if self._cfg.direction == "NO":
            df = df.filter(~pl.col("bet_yes"))
        elif self._cfg.direction == "YES":
            df = df.filter(pl.col("bet_yes"))

        # Entry price (directional)
        df = df.with_columns(
            pl.when(pl.col("bet_yes"))
            .then(pl.col("price").cast(pl.Float64))
            .otherwise(1.0 - pl.col("price").cast(pl.Float64))
            .alias("dir_entry")
        )

        # Entry cap
        if self._cfg.max_entry_price is not None:
            df = df.filter(pl.col("dir_entry") <= self._cfg.max_entry_price)

        # Contradiction filter
        if self._cfg.contradiction_filter:
            market_dirs = df.group_by("condition_id").agg(
                pl.col("bet_yes").sum().alias("n_yes"),
                (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
            )
            unanimous = market_dirs.filter(
                (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
            ).select("condition_id")
            df = df.join(unanimous, on="condition_id", how="inner")

        # Max price
        max_price_expr = (
            pl.col("dir_entry") + pl.lit(self._cfg.price_slippage)
        ).clip(upper_bound=self._cfg.max_entry_price).alias("max_price")

        return df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.when(pl.col("bet_yes"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("outcome"),
            pl.lit(self._cfg.bet_size_usd).alias("size_usd"),
            pl.col("dir_entry").alias("entry_price"),
            max_price_expr,
        ).collect()
