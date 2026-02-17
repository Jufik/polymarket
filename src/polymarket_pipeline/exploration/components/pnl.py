"""PnL computation component.

Computes per-trader, per-market PnL from positions and resolved markets,
then aggregates to trader-level features.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.components.positions import PositionsConfig, positions_cte
from polymarket_pipeline.exploration.components.resolved_markets import (
    ResolvedMarketsConfig,
    resolved_markets_cte,
)
from polymarket_pipeline.exploration.data import ExplorationDataSource


class PnLConfig(ComponentConfig):
    """Config for PnL computation."""

    lookback_start: str = "2025-08-01"
    perspective: str = "both"  # "both", "taker_only", "maker_only"
    min_resolved_markets: int = 20
    min_resolved_volume_usd: float = 10_000
    yes_threshold: float = 0.95
    no_threshold: float = 0.05
    max_maker_volume_fraction: float | None = None

    model_config = ConfigDict(frozen=True)


def _market_pnl_cte(positions_cte_name: str = "positions") -> str:
    """CTE that computes per-trader, per-market PnL from positions."""
    return f"""market_pnl AS (
    SELECT
        p.trader AS trader,
        p.condition_id AS condition_id,
        r.resolution_value AS resolution_value,
        sum(
            p.net_tokens * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
        ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees)
            AS pnl,
        sum(p.market_volume) AS volume,
        sum(p.trade_count) AS trades,
        min(p.first_trade) AS first_trade,
        max(p.last_trade) AS last_trade,
        if(
            sum(
                p.net_tokens * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
            ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees) > 0,
            1, 0
        ) AS is_win
    FROM {positions_cte_name} p
    INNER JOIN resolved r ON p.condition_id = r.condition_id
    GROUP BY trader, condition_id, resolution_value
)"""


def compute_market_pnl(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Compute per-trader, per-market PnL and save to parquet."""
    cfg = config if isinstance(config, PnLConfig) else PnLConfig()

    resolved = resolved_markets_cte(ResolvedMarketsConfig(
        lookback_start=cfg.lookback_start,
        yes_threshold=cfg.yes_threshold,
        no_threshold=cfg.no_threshold,
    ))
    pos = positions_cte(PositionsConfig(
        lookback_start=cfg.lookback_start,
        perspective=cfg.perspective,
        max_maker_volume_fraction=cfg.max_maker_volume_fraction,
    ))

    df_market_pnl = db.query_df(f"""
        WITH
        {resolved},
        {pos}
        SELECT
            p.trader AS trader,
            p.condition_id AS condition_id,
            r.resolution_value AS resolution_value,
            sum(
                p.net_tokens * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
            ) AS settlement_value,
            sum(p.total_spent) AS total_spent,
            sum(p.total_received) AS total_received,
            sum(p.total_fees) AS total_fees,
            sum(
                p.net_tokens * if(p.outcome = 'YES', r.resolution_value, 1 - r.resolution_value)
            ) - sum(p.total_spent) + sum(p.total_received) - sum(p.total_fees)
                AS market_pnl,
            sum(p.market_volume) AS market_volume,
            sum(p.trade_count) AS trade_count,
            sumIf(p.avg_entry_price * p.market_volume, p.outcome = 'YES')
                / greatest(sumIf(p.market_volume, p.outcome = 'YES'), 0.01)
                AS wavg_yes_entry_price,
            min(p.first_trade) AS first_trade,
            max(p.last_trade) AS last_trade
        FROM positions p
        INNER JOIN resolved r ON p.condition_id = r.condition_id
        GROUP BY trader, condition_id, resolution_value
    """)

    path = outputs_dir / "trader_market_pnl.parquet"
    df_market_pnl.write_parquet(path)

    return ComponentResult(
        name="market_pnl",
        dataframes={"trader_market_pnl": df_market_pnl},
        metrics={"trader_market_pairs": len(df_market_pnl)},
        artifacts={"trader_market_pnl": path},
    )


def compute_trader_aggregates(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Aggregate per-market PnL to trader-level features."""
    cfg = config if isinstance(config, PnLConfig) else PnLConfig()

    resolved = resolved_markets_cte(ResolvedMarketsConfig(
        lookback_start=cfg.lookback_start,
        yes_threshold=cfg.yes_threshold,
        no_threshold=cfg.no_threshold,
    ))
    pos = positions_cte(PositionsConfig(
        lookback_start=cfg.lookback_start,
        perspective=cfg.perspective,
        max_maker_volume_fraction=cfg.max_maker_volume_fraction,
    ))
    market_pnl = _market_pnl_cte()

    df = db.query_df(f"""
        WITH
        {resolved},
        {pos},
        {market_pnl}
        SELECT
            trader,
            sum(pnl) AS estimated_pnl,
            sum(pnl) / greatest(sum(volume), 1) AS pnl_per_dollar,
            avg(is_win) AS win_rate_by_market,
            avg(pnl) / greatest(stddevPop(pnl), 0.001) AS pnl_sharpe_ratio,
            avg(pnl / greatest(volume, 0.01)) AS avg_pnl_per_dollar_per_market,
            count() AS resolved_markets,
            sum(volume) AS resolved_volume_usd,
            sum(trades) AS total_trades,
            min(first_trade) AS first_trade,
            max(last_trade) AS last_trade,
            quantile(0.25)(pnl) AS pnl_p25,
            quantile(0.50)(pnl) AS pnl_p50,
            quantile(0.75)(pnl) AS pnl_p75,
            max(pnl) AS max_market_pnl,
            min(pnl) AS min_market_pnl,
            stddevPop(pnl) AS pnl_stddev
        FROM market_pnl
        GROUP BY trader
        HAVING
            resolved_markets >= {cfg.min_resolved_markets}
            AND resolved_volume_usd >= {cfg.min_resolved_volume_usd}
        ORDER BY estimated_pnl DESC
    """)

    path = outputs_dir / "trader_pnl_features.parquet"
    df.write_parquet(path)

    return ComponentResult(
        name="trader_aggregates",
        dataframes={"trader_pnl_features": df},
        metrics={
            "traders_passing_filters": len(df),
            "population_mean_pnl_per_dollar": (
                float(str(df["pnl_per_dollar"].mean())) if len(df) > 0 else 0.0
            ),
            "population_std_pnl_per_dollar": (
                float(str(df["pnl_per_dollar"].std())) if len(df) > 0 else 0.0
            ),
        },
        artifacts={"trader_pnl_features": path},
    )
