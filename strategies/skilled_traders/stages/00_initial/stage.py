"""Stage: 00_initial — Initial Trader Segmentation

Hypothesis: Some traders consistently outperform and their patterns are detectable.

This stage establishes the baseline: how many traders are there, how is volume
distributed, and what does the population look like across key dimensions?
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "00_initial"


def run(strategy_root: Path, outputs_dir: Path) -> dict:
    db = ExplorationDataSource()

    # ------------------------------------------------------------------
    # 1. Trader summary — top-level population stats
    # ------------------------------------------------------------------
    df_traders = db.query_df("""
        SELECT
            maker,
            count() AS trade_count,
            sum(amount_usd) AS total_volume_usd,
            uniq(condition_id) AS unique_markets,
            min(timestamp) AS first_trade,
            max(timestamp) AS last_trade,
            avg(price) AS avg_price,
            countIf(side = 'BUY' AND price < 0.5) AS cheap_buys,
            countIf(side = 'SELL' AND price > 0.5) AS expensive_sells
        FROM polymarket.trades FINAL
        WHERE maker IS NOT NULL
          AND timestamp >= '2025-08-01'
        GROUP BY maker
        HAVING trade_count >= 10
        ORDER BY total_volume_usd DESC
    """)

    df_traders.write_parquet(outputs_dir / "traders_summary.parquet")

    # ------------------------------------------------------------------
    # 2. Volume distribution — percentile thresholds
    # ------------------------------------------------------------------
    volume_stats = db.query_raw("""
        SELECT
            quantile(0.50)(total_vol) AS p50,
            quantile(0.80)(total_vol) AS p80,
            quantile(0.95)(total_vol) AS p95,
            quantile(0.99)(total_vol) AS p99,
            max(total_vol) AS max_vol,
            count() AS trader_count
        FROM (
            SELECT maker, sum(amount_usd) AS total_vol
            FROM polymarket.trades FINAL
            WHERE maker IS NOT NULL AND timestamp >= '2025-08-01'
            GROUP BY maker
            HAVING count() >= 10
        )
    """)[0]

    # ------------------------------------------------------------------
    # 3. Volume tier segmentation (in ClickHouse)
    # ------------------------------------------------------------------
    df_tiers = db.query_df(f"""
        SELECT
            multiIf(
                total_vol >= {volume_stats['p99']}, 'whale',
                total_vol >= {volume_stats['p95']}, 'large',
                total_vol >= {volume_stats['p80']}, 'medium',
                total_vol >= {volume_stats['p50']}, 'small',
                'micro'
            ) AS volume_tier,
            count() AS trader_count,
            sum(total_vol) AS tier_volume,
            avg(trade_cnt) AS avg_trades,
            avg(mkt_cnt) AS avg_markets
        FROM (
            SELECT
                maker,
                sum(amount_usd) AS total_vol,
                count() AS trade_cnt,
                uniq(condition_id) AS mkt_cnt
            FROM polymarket.trades FINAL
            WHERE maker IS NOT NULL AND timestamp >= '2025-08-01'
            GROUP BY maker
            HAVING trade_cnt >= 10
        )
        GROUP BY volume_tier
        ORDER BY tier_volume DESC
    """)

    # ------------------------------------------------------------------
    # 4. Activity pattern segmentation
    # ------------------------------------------------------------------
    df_activity = db.query_df("""
        SELECT
            multiIf(
                trades_per_day >= 5, 'hyperactive',
                trades_per_day >= 1, 'daily',
                trades_per_day >= 0.14, 'weekly',
                'sporadic'
            ) AS activity_tier,
            count() AS trader_count,
            avg(total_vol) AS avg_volume,
            avg(trades_per_day) AS avg_trades_per_day
        FROM (
            SELECT
                maker,
                sum(amount_usd) AS total_vol,
                count() AS trade_cnt,
                count() / greatest(dateDiff('day', min(timestamp), max(timestamp)), 1) AS trades_per_day
            FROM polymarket.trades FINAL
            WHERE maker IS NOT NULL AND timestamp >= '2025-08-01'
            GROUP BY maker
            HAVING trade_cnt >= 10
        )
        GROUP BY activity_tier
        ORDER BY avg_volume DESC
    """)

    # ------------------------------------------------------------------
    # 5. Top 20 traders by volume
    # ------------------------------------------------------------------
    df_top = db.query_df("""
        SELECT
            maker,
            count() AS trade_count,
            sum(amount_usd) AS total_volume_usd,
            uniq(condition_id) AS unique_markets,
            min(timestamp) AS first_trade,
            max(timestamp) AS last_trade
        FROM polymarket.trades FINAL
        WHERE maker IS NOT NULL AND timestamp >= '2025-08-01'
        GROUP BY maker
        HAVING trade_count >= 10
        ORDER BY total_volume_usd DESC
        LIMIT 20
    """)

    # ------------------------------------------------------------------
    # 6. Save everything
    # ------------------------------------------------------------------
    df_tiers.write_parquet(outputs_dir / "volume_tiers.parquet")
    df_activity.write_parquet(outputs_dir / "activity_tiers.parquet")
    df_top.write_parquet(outputs_dir / "top_traders.parquet")

    metrics = StageMetrics(
        sample_size=len(df_traders),
        unique_traders=len(df_traders),
        unique_markets=int(df_traders["unique_markets"].max()) if len(df_traders) > 0 else 0,
        custom={
            "total_volume_usd": float(df_traders["total_volume_usd"].sum()),
            "volume_p50": float(volume_stats["p50"]),
            "volume_p95": float(volume_stats["p95"]),
            "volume_p99": float(volume_stats["p99"]),
            "lookback_start": "2025-08-01",
            "min_trades": 10,
        },
    )

    return {
        "stage_id": STAGE_ID,
        "metrics": metrics.model_dump(exclude_none=True),
        "findings": {
            "volume_tiers": df_tiers.to_dicts(),
            "activity_tiers": df_activity.to_dicts(),
            "top_20_traders": df_top.to_dicts(),
            "volume_distribution": volume_stats,
        },
    }


if __name__ == "__main__":
    root = Path(__file__).parent.parent.parent
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    summary = run(root, out)
    print(json.dumps(summary, indent=2, default=str))
