"""Maker volume fraction component.

Computes per-trader maker vs taker volume split to identify
pure takers, pure makers, and mixed traders.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.components.resolved_markets import (
    ResolvedMarketsConfig,
    resolved_markets_cte,
)
from polymarket_pipeline.exploration.data import ExplorationDataSource


class MakerFractionConfig(ComponentConfig):
    """Config for maker volume fraction computation."""

    lookback_start: str = "2025-08-01"
    max_fraction: float = 0.10  # traders with mvf <= this are "pure takers"
    yes_threshold: float = 0.95
    no_threshold: float = 0.05

    model_config = ConfigDict(frozen=True)


def maker_volume_fraction_cte(cfg: MakerFractionConfig) -> str:
    """CTE computing maker volume fraction per trader in resolved markets."""
    resolved = resolved_markets_cte(
        ResolvedMarketsConfig(
            lookback_start=cfg.lookback_start,
            yes_threshold=cfg.yes_threshold,
            no_threshold=cfg.no_threshold,
        )
    )
    return f"""{resolved},
maker_volume_fraction AS (
    SELECT
        trader,
        sum(maker_vol) AS maker_volume,
        sum(taker_vol) AS taker_volume,
        sum(maker_vol) + sum(taker_vol) AS total_volume,
        sum(maker_vol) / greatest(sum(maker_vol) + sum(taker_vol), 0.01) AS mvf
    FROM (
        SELECT maker AS trader, sum(amount_usd) AS maker_vol, 0 AS taker_vol
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND maker IS NOT NULL
          AND condition_id IN (SELECT condition_id FROM resolved)
        GROUP BY maker

        UNION ALL

        SELECT taker AS trader, 0 AS maker_vol, sum(amount_usd) AS taker_vol
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
          AND taker IS NOT NULL
          AND condition_id IN (SELECT condition_id FROM resolved)
        GROUP BY taker
    )
    GROUP BY trader
)"""


def compute_mvf(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Compute maker volume fractions and identify pure takers."""
    cfg = config if isinstance(config, MakerFractionConfig) else MakerFractionConfig()
    cte = maker_volume_fraction_cte(cfg)

    df = db.query_df(f"""
        WITH {cte}
        SELECT
            trader,
            maker_volume,
            taker_volume,
            total_volume,
            mvf
        FROM maker_volume_fraction
        ORDER BY total_volume DESC
    """)

    path = outputs_dir / "maker_volume_fractions.parquet"
    df.write_parquet(path)

    import polars as pl

    pure_takers = df.filter(pl.col("mvf") <= cfg.max_fraction)

    return ComponentResult(
        name="mvf",
        dataframes={"maker_volume_fractions": df, "pure_takers": pure_takers},
        metrics={
            "total_traders": len(df),
            "pure_taker_count": len(pure_takers),
            "pure_taker_pct": len(pure_takers) / max(len(df), 1) * 100,
            "max_fraction_threshold": cfg.max_fraction,
        },
        artifacts={"maker_volume_fractions": path},
    )
