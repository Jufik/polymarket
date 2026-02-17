"""Skill scoring component.

Multiple scoring methods for identifying skilled traders:
- z_score_filter: absolute PnL z-score (legacy, volume-biased)
- pnl_per_dollar_filter: PnL per dollar as primary skill metric
- bootstrap_filter: bootstrap confidence interval on PnL
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl
from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult


class ScoringConfig(ComponentConfig):
    """Config for skill scoring."""

    method: Literal["z_score", "pnl_per_dollar", "bootstrap"] = "pnl_per_dollar"
    # z-score method
    z_threshold: float = 2.0
    # pnl_per_dollar method
    pnl_per_dollar_threshold: float = 0.15
    # bootstrap method
    n_bootstrap: int = 1000
    bootstrap_ci: float = 0.95
    # Common: which column to score on
    score_column: str = "pnl_per_dollar"

    model_config = ConfigDict(frozen=True)


def z_score_filter(
    db: object,  # unused but matches component signature
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Filter traders by z-score on a metric column.

    Requires prior_results to contain 'trader_aggregates' with a
    'trader_pnl_features' dataframe.
    """
    cfg = config if isinstance(config, ScoringConfig) else ScoringConfig(method="z_score")
    prior = prior_results or {}

    agg_result = prior.get("trader_aggregates") or prior.get("pnl")
    if not agg_result:
        raise ValueError("z_score_filter requires 'trader_aggregates' in prior_results")

    df = agg_result.dataframes.get("trader_pnl_features")
    if df is None or len(df) == 0:
        return ComponentResult(
            name="skill",
            dataframes={"skilled_traders": pl.DataFrame()},
            metrics={"skilled_count": 0},
        )

    col = cfg.score_column
    pop_mean = float(df[col].mean())
    pop_std = float(df[col].std())

    df_scored = df.with_columns(
        ((pl.col(col) - pop_mean) / max(pop_std, 1e-9)).alias("skill_zscore")
    )
    df_skilled = df_scored.filter(pl.col("skill_zscore") >= cfg.z_threshold)

    path = outputs_dir / "skilled_traders_pnl.parquet"
    df_skilled.write_parquet(path)

    return ComponentResult(
        name="skill",
        dataframes={"skilled_traders": df_skilled, "all_scored": df_scored},
        metrics={
            "skilled_count": len(df_skilled),
            "population_mean": pop_mean,
            "population_std": pop_std,
            "z_threshold": cfg.z_threshold,
            "score_column": col,
        },
        artifacts={"skilled_traders_pnl": path},
    )


def pnl_per_dollar_filter(
    db: object,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Filter traders by absolute pnl_per_dollar threshold.

    This addresses the fundamental flaw where z-score on absolute PnL
    is a volume proxy (r=0.918). PnL-per-dollar makes small disciplined
    traders visible.
    """
    cfg = config if isinstance(config, ScoringConfig) else ScoringConfig(method="pnl_per_dollar")
    prior = prior_results or {}

    agg_result = prior.get("trader_aggregates") or prior.get("pnl")
    if not agg_result:
        raise ValueError("pnl_per_dollar_filter requires 'trader_aggregates' in prior_results")

    df = agg_result.dataframes.get("trader_pnl_features")
    if df is None or len(df) == 0:
        return ComponentResult(
            name="skill",
            dataframes={"skilled_traders": pl.DataFrame()},
            metrics={"skilled_count": 0},
        )

    df_skilled = df.filter(pl.col("pnl_per_dollar") >= cfg.pnl_per_dollar_threshold)

    path = outputs_dir / "skilled_traders_pnl.parquet"
    df_skilled.write_parquet(path)

    return ComponentResult(
        name="skill",
        dataframes={"skilled_traders": df_skilled},
        metrics={
            "skilled_count": len(df_skilled),
            "pnl_per_dollar_threshold": cfg.pnl_per_dollar_threshold,
            "mean_pnl_per_dollar": (
                float(df_skilled["pnl_per_dollar"].mean()) if len(df_skilled) > 0 else 0.0
            ),
        },
        artifacts={"skilled_traders_pnl": path},
    )


def bootstrap_filter(
    db: object,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Filter traders whose bootstrap CI lower bound > 0.

    Requires 'market_pnl' in prior_results with per-trader-market data.
    """
    import numpy as np

    cfg = config if isinstance(config, ScoringConfig) else ScoringConfig(method="bootstrap")
    prior = prior_results or {}

    market_result = prior.get("market_pnl") or prior.get("pnl")
    if not market_result:
        raise ValueError("bootstrap_filter requires 'market_pnl' in prior_results")

    df_market = market_result.dataframes.get("trader_market_pnl")
    if df_market is None or len(df_market) == 0:
        return ComponentResult(
            name="skill",
            dataframes={"skilled_traders": pl.DataFrame()},
            metrics={"skilled_count": 0},
        )

    alpha = 1 - cfg.bootstrap_ci
    rng = np.random.default_rng(42)
    results = []

    for trader in df_market["trader"].unique().to_list():
        pnls = df_market.filter(pl.col("trader") == trader)["market_pnl"].to_numpy()
        if len(pnls) < 10:
            continue
        boot_means = np.array([
            rng.choice(pnls, size=len(pnls), replace=True).mean()
            for _ in range(cfg.n_bootstrap)
        ])
        ci_lower = float(np.percentile(boot_means, alpha / 2 * 100))
        ci_upper = float(np.percentile(boot_means, (1 - alpha / 2) * 100))
        if ci_lower > 0:
            results.append({
                "trader": trader,
                "bootstrap_mean": float(boot_means.mean()),
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
            })

    df_skilled = pl.DataFrame(results) if results else pl.DataFrame()

    path = outputs_dir / "skilled_traders_bootstrap.parquet"
    if len(df_skilled) > 0:
        df_skilled.write_parquet(path)

    return ComponentResult(
        name="skill",
        dataframes={"skilled_traders": df_skilled},
        metrics={
            "skilled_count": len(df_skilled),
            "n_bootstrap": cfg.n_bootstrap,
            "ci_level": cfg.bootstrap_ci,
        },
        artifacts={"skilled_traders_bootstrap": path} if len(df_skilled) > 0 else {},
    )
