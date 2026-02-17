"""Resolved markets component.

Identifies markets that have resolved (closed with a confident outcome)
using last-traded YES-token price as a resolution proxy.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.data import ExplorationDataSource


class ResolvedMarketsConfig(ComponentConfig):
    """Config for resolved markets identification."""

    lookback_start: str = "2025-08-01"
    yes_threshold: float = 0.95  # last YES price >= this -> YES won
    no_threshold: float = 0.05  # last YES price <= this -> NO won

    model_config = ConfigDict(frozen=True)


def resolved_markets_cte(cfg: ResolvedMarketsConfig) -> str:
    """Return the resolved markets CTE as a SQL string."""
    return f"""resolved AS (
    SELECT
        t.condition_id AS condition_id,
        argMax(t.price, t.timestamp) AS last_yes_price,
        multiIf(
            argMax(t.price, t.timestamp) >= {cfg.yes_threshold}, 1,
            argMax(t.price, t.timestamp) <= {cfg.no_threshold}, 0,
            -1
        ) AS resolution_value
    FROM (
        SELECT condition_id, asset_id, price, timestamp
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
    ) t
    INNER JOIN polymarket.token_market_map tm ON t.asset_id = tm.asset_id
    INNER JOIN polymarket.markets m ON t.condition_id = m.condition_id
    WHERE m.status = 'closed'
      AND m.resolved_at IS NOT NULL
      AND tm.outcome = 'YES'
    GROUP BY condition_id
    HAVING resolution_value >= 0
)"""


def query_resolved_markets(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Query and save resolved markets."""
    cfg = config if isinstance(config, ResolvedMarketsConfig) else ResolvedMarketsConfig()
    cte = resolved_markets_cte(cfg)

    df = db.query_df(f"""
        WITH {cte}
        SELECT
            condition_id,
            last_yes_price,
            resolution_value,
            multiIf(
                last_yes_price >= {cfg.yes_threshold}, 'YES',
                last_yes_price <= {cfg.no_threshold}, 'NO',
                'AMBIGUOUS'
            ) AS resolution_label
        FROM resolved
    """)

    path = outputs_dir / "resolved_markets.parquet"
    df.write_parquet(path)

    return ComponentResult(
        name="resolved",
        dataframes={"resolved_markets": df},
        metrics={"resolved_market_count": len(df)},
        artifacts={"resolved_markets": path},
    )
