"""CombinedBacktestRunner — run multiple vectorized strategies over shared data."""

from __future__ import annotations

from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


class CombinedBacktestRunner:
    """Runs multiple strategies over the same trade/market data.

    Each strategy gets the full trade stream. Signals are tagged by strategy
    name and capped to per-strategy budgets.

    Parameters
    ----------
    strategies:
        List of strategy objects with ``compute_signals(trades, markets)`` method.
    budgets:
        Optional dict mapping strategy name to capital budget in USD.
    """

    def __init__(
        self,
        strategies: list[Any],
        budgets: dict[str, float] | None = None,
    ) -> None:
        self._strategies = strategies
        self._budgets = budgets or {}

    def run(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
        """Run all strategies and return combined signal DataFrame.

        Returns a DataFrame with columns:
        ``strategy``, ``condition_id``, ``signal_time``, ``side``, ``outcome``,
        ``size_usd``, optionally ``entry_price``.
        """
        all_signals: list[pl.DataFrame] = []

        for strategy in self._strategies:
            name = strategy.name
            signals = strategy.compute_signals(trades, markets)

            if signals.is_empty():
                continue

            # Tag with strategy name if not already present
            if "strategy" not in signals.columns:
                signals = signals.with_columns(pl.lit(name).alias("strategy"))

            # Apply budget cap: sort by signal_time, cumulative spend, filter
            budget = self._budgets.get(name)
            if budget is not None and "size_usd" in signals.columns:
                signals = signals.sort("signal_time")
                signals = signals.with_columns(
                    pl.col("size_usd").cum_sum().alias("_cum_spent")
                )
                signals = signals.filter(pl.col("_cum_spent") <= budget)
                signals = signals.drop("_cum_spent")

            all_signals.append(signals)
            logger.info(
                "combined.strategy_signals",
                strategy=name,
                n_signals=len(signals),
            )

        if not all_signals:
            return pl.DataFrame({
                "strategy": [],
                "condition_id": [],
                "signal_time": [],
                "side": [],
                "outcome": [],
                "size_usd": [],
            })

        # Align schemas before concat (some strategies emit entry_price, some don't)
        all_cols: set[str] = set()
        for df in all_signals:
            all_cols.update(df.columns)

        aligned = []
        for df in all_signals:
            for col in all_cols:
                if col not in df.columns:
                    df = df.with_columns(pl.lit(None).alias(col))
            aligned.append(df.select(sorted(all_cols)))

        combined = pl.concat(aligned).sort("signal_time")
        logger.info("combined.total_signals", n=len(combined))
        return combined
