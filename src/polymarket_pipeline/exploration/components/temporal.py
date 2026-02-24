"""Temporal consistency component.

Tests whether skilled traders maintain positive PnL across
multiple time sub-periods (not just lucky in one stretch).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.components.positions import PositionsConfig
from polymarket_pipeline.exploration.components.resolved_markets import (
    ResolvedMarketsConfig,
    resolved_markets_cte,
)
from polymarket_pipeline.exploration.data import ExplorationDataSource


class TemporalConfig(ComponentConfig):
    """Config for temporal consistency testing."""

    lookback_start: str = "2025-08-01"
    num_periods: int = 4
    min_positive_periods: int = 2
    perspective: str = "both"
    yes_threshold: float = 0.95
    no_threshold: float = 0.05

    model_config = ConfigDict(frozen=True)


def temporal_consistency(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Test temporal consistency of skilled traders across sub-periods.

    Requires prior_results to contain 'skill' with 'skilled_traders' dataframe.
    """
    cfg = config if isinstance(config, TemporalConfig) else TemporalConfig()
    prior = prior_results or {}

    skill_result = prior.get("skill")
    if not skill_result:
        raise ValueError("temporal_consistency requires 'skill' in prior_results")

    df_skilled = skill_result.dataframes.get("skilled_traders")
    if df_skilled is None or len(df_skilled) == 0:
        return ComponentResult(
            name="temporal",
            dataframes={"consistent_traders": pl.DataFrame()},
            metrics={"consistent_count": 0, "consistency_rate_pct": 0.0},
        )

    resolved = resolved_markets_cte(
        ResolvedMarketsConfig(
            lookback_start=cfg.lookback_start,
            yes_threshold=cfg.yes_threshold,
            no_threshold=cfg.no_threshold,
        )
    )

    # Build trader trades subquery based on perspective
    pos_cfg = PositionsConfig(
        lookback_start=cfg.lookback_start,
        perspective=cfg.perspective,
    )
    if pos_cfg.perspective == "taker_only":
        trader_trades_sql = f"""
            SELECT
                taker AS trader, condition_id, asset_id,
                if(side = 'BUY', 'SELL', 'BUY') AS trader_side, size, amount_usd,
                fee_usd AS trader_fee_usd, price, timestamp
            FROM polymarket.trades_raw FINAL
            WHERE timestamp >= '{cfg.lookback_start}' AND taker IS NOT NULL"""
    else:
        trader_trades_sql = f"""
            SELECT
                maker AS trader, condition_id, asset_id,
                side AS trader_side, size, amount_usd,
                toFloat32(0.0) AS trader_fee_usd, price, timestamp
            FROM polymarket.trades_raw FINAL
            WHERE timestamp >= '{cfg.lookback_start}' AND maker IS NOT NULL

            UNION ALL

            SELECT
                taker AS trader, condition_id, asset_id,
                if(side = 'BUY', 'SELL', 'BUY') AS trader_side, size, amount_usd,
                fee_usd AS trader_fee_usd, price, timestamp
            FROM polymarket.trades_raw FINAL
            WHERE timestamp >= '{cfg.lookback_start}' AND taker IS NOT NULL"""

    n = cfg.num_periods

    df_temporal = db.query_df(f"""
        WITH
        {resolved},
        positions_tp AS (
            SELECT
                tt.trader AS trader,
                tt.condition_id AS condition_id,
                tm.outcome AS outcome,
                sumIf(tt.size, tt.trader_side = 'BUY')
                    - sumIf(tt.size, tt.trader_side = 'SELL') AS net_tokens,
                sumIf(tt.amount_usd, tt.trader_side = 'BUY') AS total_spent,
                sumIf(tt.amount_usd, tt.trader_side = 'SELL') AS total_received,
                sum(tt.trader_fee_usd) AS total_fees,
                sum(tt.amount_usd) AS market_volume,
                toUInt8(
                    least(
                        intDiv(
                            toUnixTimestamp(min(tt.timestamp))
                                - toUnixTimestamp(toDateTime('{cfg.lookback_start}')),
                            greatest(
                                (toUnixTimestamp(now())
                                    - toUnixTimestamp(toDateTime('{cfg.lookback_start}')))
                                / {n},
                                1
                            )
                        ),
                        {n - 1}
                    )
                ) AS sub_period
            FROM ({trader_trades_sql}
            ) tt
            INNER JOIN polymarket.token_market_map tm ON tt.asset_id = tm.asset_id
            WHERE tt.condition_id IN (SELECT condition_id FROM resolved)
            GROUP BY trader, condition_id, outcome
        ),
        market_pnl_tp AS (
            SELECT
                p.trader AS trader,
                p.condition_id AS condition_id,
                min(p.sub_period) AS sub_period,
                sum(
                    p.net_tokens * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
                ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees)
                    AS pnl,
                sum(p.market_volume) AS volume
            FROM positions_tp p
            INNER JOIN resolved r ON p.condition_id = r.condition_id
            GROUP BY trader, condition_id
        )
        SELECT
            trader,
            sub_period,
            sum(pnl) AS period_pnl,
            sum(volume) AS period_volume,
            count() AS period_markets,
            if(sum(pnl) > 0, 1, 0) AS period_positive
        FROM market_pnl_tp
        GROUP BY trader, sub_period
    """)

    path_temporal = outputs_dir / "temporal_pnl.parquet"
    df_temporal.write_parquet(path_temporal)

    # Compute consistency for skilled traders
    if len(df_temporal) > 0:
        df_consistency = (
            df_temporal.filter(pl.col("trader").is_in(df_skilled["trader"]))
            .group_by("trader")
            .agg(
                [
                    pl.col("period_positive").sum().alias("positive_periods"),
                    pl.col("sub_period").n_unique().alias("active_periods"),
                    pl.col("period_pnl").sum().alias("total_pnl_check"),
                ]
            )
        )

        consistent_traders = df_consistency.filter(
            pl.col("positive_periods") >= cfg.min_positive_periods
        )
        consistency_rate = len(consistent_traders) / max(len(df_skilled), 1) * 100

        df_consistency.write_parquet(outputs_dir / "trader_consistency.parquet")

        # Final skilled + consistent traders
        df_final = df_skilled.filter(pl.col("trader").is_in(consistent_traders["trader"])).sort(
            "estimated_pnl", descending=True
        )

        path_final = outputs_dir / "skilled_traders_consistent.parquet"
        df_final.write_parquet(path_final)
    else:
        consistency_rate = 0.0
        df_final = df_skilled
        df_consistency = pl.DataFrame()

    return ComponentResult(
        name="temporal",
        dataframes={
            "temporal_pnl": df_temporal,
            "trader_consistency": df_consistency,
            "consistent_traders": df_final,
        },
        metrics={
            "consistent_count": len(df_final),
            "consistency_rate_pct": round(consistency_rate, 2),
            "num_periods": cfg.num_periods,
            "min_positive_periods": cfg.min_positive_periods,
        },
        artifacts={
            "temporal_pnl": path_temporal,
            "skilled_traders_consistent": outputs_dir / "skilled_traders_consistent.parquet",
        },
    )


def empirical_baseline(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Compute random trader baseline for temporal consistency comparison.

    Samples N random traders and computes their consistency rate
    to establish what "chance level" consistency looks like.
    """
    import numpy as np

    cfg = config if isinstance(config, TemporalConfig) else TemporalConfig()
    prior = prior_results or {}

    temporal_result = prior.get("temporal")
    if not temporal_result:
        raise ValueError("empirical_baseline requires 'temporal' in prior_results")

    df_temporal = temporal_result.dataframes.get("temporal_pnl")
    if df_temporal is None or len(df_temporal) == 0:
        return ComponentResult(
            name="baseline",
            metrics={"baseline_consistency_rate": 0.0},
        )

    all_traders = df_temporal["trader"].unique().to_list()
    rng = np.random.default_rng(42)

    n_samples = 100
    sample_size = min(200, len(all_traders))
    rates = []

    for _ in range(n_samples):
        sample = rng.choice(all_traders, size=sample_size, replace=False)
        sample_consistency = (
            df_temporal.filter(pl.col("trader").is_in(list(sample)))
            .group_by("trader")
            .agg(pl.col("period_positive").sum().alias("positive_periods"))
            .filter(pl.col("positive_periods") >= cfg.min_positive_periods)
        )
        rates.append(len(sample_consistency) / sample_size * 100)

    return ComponentResult(
        name="baseline",
        metrics={
            "baseline_consistency_rate_mean": round(float(np.mean(rates)), 2),
            "baseline_consistency_rate_std": round(float(np.std(rates)), 2),
            "n_samples": n_samples,
            "sample_size": sample_size,
        },
    )
