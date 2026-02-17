"""Data quality validation component.

Pre-flight checks to catch common data issues before running analysis.
"""

from __future__ import annotations

from pathlib import Path

from polymarket_pipeline.exploration.components.base import ComponentConfig, ComponentResult
from polymarket_pipeline.exploration.data import ExplorationDataSource


class ValidationConfig(ComponentConfig):
    """Config for validation checks."""

    lookback_start: str = "2025-08-01"


def dedup_check(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Verify trades_raw FINAL dedup is working correctly.

    Compares row counts between trades_raw FINAL and trades view FINAL.
    The view should have more rows (duplicates) than trades_raw FINAL.
    """
    cfg = config if isinstance(config, ValidationConfig) else ValidationConfig()

    rows = db.query_raw(f"""
        SELECT
            'trades_raw_final' AS source,
            count() AS row_count,
            sum(amount_usd) AS total_volume
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'

        UNION ALL

        SELECT
            'trades_view_final' AS source,
            count() AS row_count,
            sum(amount_usd) AS total_volume
        FROM polymarket.trades FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
    """)

    stats = {row["source"]: row for row in rows}
    raw_count = int(stats.get("trades_raw_final", {}).get("row_count", 0))
    view_count = int(stats.get("trades_view_final", {}).get("row_count", 0))
    dup_rate = (view_count - raw_count) / max(view_count, 1) * 100

    return ComponentResult(
        name="dedup_check",
        metrics={
            "trades_raw_final_rows": raw_count,
            "trades_view_final_rows": view_count,
            "duplicate_rate_pct": round(dup_rate, 2),
            "dedup_valid": raw_count < view_count,
        },
    )


def fee_coverage_check(
    db: ExplorationDataSource,
    config: ComponentConfig,
    outputs_dir: Path,
    prior_results: dict[str, ComponentResult] | None = None,
) -> ComponentResult:
    """Check that fee data is present and reasonable."""
    cfg = config if isinstance(config, ValidationConfig) else ValidationConfig()

    rows = db.query_raw(f"""
        SELECT
            count() AS total_trades,
            countIf(fee_usd > 0) AS trades_with_fees,
            avg(fee_usd) AS avg_fee,
            quantile(0.99)(fee_usd) AS fee_p99
        FROM polymarket.trades_raw FINAL
        WHERE timestamp >= '{cfg.lookback_start}'
    """)

    row = rows[0] if rows else {}
    total = int(row.get("total_trades", 0))
    with_fees = int(row.get("trades_with_fees", 0))
    fee_rate = with_fees / max(total, 1) * 100

    return ComponentResult(
        name="fee_check",
        metrics={
            "total_trades": total,
            "trades_with_fees": with_fees,
            "fee_coverage_pct": round(fee_rate, 2),
            "avg_fee_usd": float(row.get("avg_fee", 0)),
            "fee_p99_usd": float(row.get("fee_p99", 0)),
        },
    )
