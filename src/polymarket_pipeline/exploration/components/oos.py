"""Out-of-sample validation component.

Splits data into in-sample (training) and out-of-sample (test) periods,
identifies skilled traders on in-sample, and evaluates their performance
on out-of-sample data.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.components.positions import PositionsConfig, positions_cte
from polymarket_pipeline.exploration.components.resolved_markets import (
    ResolvedMarketsConfig,
    resolved_markets_cte,
)
from polymarket_pipeline.exploration.data import ExplorationDataSource


class OOSConfig(ComponentConfig):
    """Config for out-of-sample evaluation."""

    # Data split: in-sample period
    is_start: str = "2025-08-01"
    is_end: str = "2025-11-01"
    # Out-of-sample period
    oos_start: str = "2025-11-01"
    oos_end: str = "2026-02-01"
    # Resolution thresholds
    yes_threshold: float = 0.95
    no_threshold: float = 0.05
    perspective: str = "both"
    min_resolved_markets: int = 10  # lower for OOS (less data)

    model_config = ConfigDict(frozen=True)


def out_of_sample_split(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Compute PnL features for both IS and OOS periods."""
    cfg = config if isinstance(config, OOSConfig) else OOSConfig()

    def _compute_period(start: str, end: str, label: str) -> pl.DataFrame:
        resolved = resolved_markets_cte(ResolvedMarketsConfig(
            lookback_start=start,
            yes_threshold=cfg.yes_threshold,
            no_threshold=cfg.no_threshold,
        ))
        pos = positions_cte(PositionsConfig(
            lookback_start=start,
            perspective=cfg.perspective,
        ))

        # Add end-date filter
        end_filter = f"AND timestamp < '{end}'" if end else ""

        # Positions CTE needs modification for end date - inject via resolved CTE
        resolved_with_end = resolved.replace(
            f"WHERE timestamp >= '{start}'",
            f"WHERE timestamp >= '{start}' {end_filter}",
        )
        pos_with_end = pos.replace(
            f"WHERE timestamp >= '{start}'",
            f"WHERE timestamp >= '{start}' {end_filter}",
        )

        return db.query_df(f"""
            WITH
            {resolved_with_end},
            {pos_with_end},
            market_pnl AS (
                SELECT
                    p.trader AS trader,
                    p.condition_id AS condition_id,
                    sum(
                        p.net_tokens
                        * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
                    ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees)
                        AS pnl,
                    sum(p.market_volume) AS volume,
                    sum(p.trade_count) AS trades,
                    if(
                        sum(
                            p.net_tokens
                            * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
                        ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees) > 0,
                        1, 0
                    ) AS is_win
                FROM positions p
                INNER JOIN resolved r ON p.condition_id = r.condition_id
                GROUP BY trader, condition_id
            )
            SELECT
                trader,
                '{label}' AS period,
                sum(pnl) AS estimated_pnl,
                sum(pnl) / greatest(sum(volume), 1) AS pnl_per_dollar,
                avg(is_win) AS win_rate,
                count() AS resolved_markets,
                sum(volume) AS resolved_volume_usd
            FROM market_pnl
            GROUP BY trader
            HAVING resolved_markets >= {cfg.min_resolved_markets}
        """)

    df_is = _compute_period(cfg.is_start, cfg.is_end, "in_sample")
    df_oos = _compute_period(cfg.oos_start, cfg.oos_end, "out_of_sample")

    df_is.write_parquet(outputs_dir / "oos_in_sample.parquet")
    df_oos.write_parquet(outputs_dir / "oos_out_of_sample.parquet")

    return ComponentResult(
        name="oos_split",
        dataframes={"in_sample": df_is, "out_of_sample": df_oos},
        metrics={
            "is_traders": len(df_is),
            "oos_traders": len(df_oos),
            "is_period": f"{cfg.is_start} to {cfg.is_end}",
            "oos_period": f"{cfg.oos_start} to {cfg.oos_end}",
        },
        artifacts={
            "in_sample": outputs_dir / "oos_in_sample.parquet",
            "out_of_sample": outputs_dir / "oos_out_of_sample.parquet",
        },
    )


def oos_evaluate(
    db: object,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Evaluate in-sample skilled traders' out-of-sample performance.

    Requires 'oos_split' and 'skill' in prior_results.
    """
    prior = prior_results or {}

    split_result = prior.get("oos_split")
    skill_result = prior.get("skill")

    if not split_result or not skill_result:
        return ComponentResult(
            name="oos_eval",
            metrics={"error": "missing oos_split or skill in prior_results"},
        )

    df_is = split_result.dataframes.get("in_sample", pl.DataFrame())
    df_oos = split_result.dataframes.get("out_of_sample", pl.DataFrame())
    df_skilled = skill_result.dataframes.get("skilled_traders", pl.DataFrame())

    if len(df_skilled) == 0 or len(df_oos) == 0:
        return ComponentResult(
            name="oos_eval",
            metrics={"skilled_in_oos": 0, "evaluation": "insufficient data"},
        )

    skilled_traders = set(df_skilled["trader"].to_list())

    # Find which skilled traders also appear in OOS
    df_skilled_oos = df_oos.filter(pl.col("trader").is_in(list(skilled_traders)))
    df_non_skilled_oos = df_oos.filter(~pl.col("trader").is_in(list(skilled_traders)))

    # Compute metrics
    n_skilled_oos = len(df_skilled_oos)
    retention_rate = n_skilled_oos / max(len(df_skilled), 1) * 100

    metrics: dict[str, object] = {
        "skilled_in_oos": n_skilled_oos,
        "skilled_retention_pct": round(retention_rate, 2),
        "skilled_total": len(df_skilled),
    }

    if n_skilled_oos > 0:
        metrics["oos_skilled_mean_pnl"] = round(float(df_skilled_oos["estimated_pnl"].mean()), 2)
        metrics["oos_skilled_mean_pnl_per_dollar"] = round(
            float(df_skilled_oos["pnl_per_dollar"].mean()), 6
        )
        metrics["oos_skilled_win_rate"] = round(float(df_skilled_oos["win_rate"].mean()), 4)

    if len(df_non_skilled_oos) > 0:
        metrics["oos_non_skilled_mean_pnl"] = round(
            float(df_non_skilled_oos["estimated_pnl"].mean()), 2
        )
        metrics["oos_non_skilled_mean_pnl_per_dollar"] = round(
            float(df_non_skilled_oos["pnl_per_dollar"].mean()), 6
        )

    # Forward rank correlation: IS pnl_per_dollar rank vs OOS pnl_per_dollar rank
    if n_skilled_oos >= 5:
        common_traders = set(df_is["trader"].to_list()) & set(df_oos["trader"].to_list())
        if len(common_traders) >= 10:
            df_is_common = df_is.filter(pl.col("trader").is_in(list(common_traders)))
            df_oos_common = df_oos.filter(pl.col("trader").is_in(list(common_traders)))

            df_joined = df_is_common.select(["trader", "pnl_per_dollar"]).rename(
                {"pnl_per_dollar": "is_ppd"}
            ).join(
                df_oos_common.select(["trader", "pnl_per_dollar"]).rename(
                    {"pnl_per_dollar": "oos_ppd"}
                ),
                on="trader",
            ).with_columns([
                pl.col("is_ppd").rank(descending=True).alias("is_rank"),
                pl.col("oos_ppd").rank(descending=True).alias("oos_rank"),
            ])

            fwd_corr = float(df_joined.select(pl.corr("is_rank", "oos_rank")).item())
            metrics["forward_rank_correlation"] = round(fwd_corr, 4)

    # Save
    if n_skilled_oos > 0:
        df_skilled_oos.write_parquet(outputs_dir / "oos_skilled_performance.parquet")

    return ComponentResult(
        name="oos_eval",
        dataframes={"skilled_oos": df_skilled_oos},
        metrics=metrics,
        artifacts=(
            {"oos_skilled_performance": outputs_dir / "oos_skilled_performance.parquet"}
            if n_skilled_oos > 0
            else {}
        ),
    )
