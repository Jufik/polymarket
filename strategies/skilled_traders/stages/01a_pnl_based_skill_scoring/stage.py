"""Stage: 01a_pnl_based_skill_scoring — Token-Aware PnL Skill Scoring

Hypothesis: Token-aware PnL will identify a distinct set of 'skilled' traders who
differ from high-volume traders, with top PnL traders showing consistent positive
returns across time periods.

Parent: 00_initial (Initial Skilled Traders)

Refinement: feature — computes estimated_pnl, pnl_per_dollar, win_rate_by_market,
avg_entry_price_vs_resolution, and pnl_sharpe_ratio for traders on resolved markets.

Filters:
  - min_resolved_markets >= 20
  - min_resolved_volume_usd >= 10000
  - resolution_confidence: YES-token last_price >= 0.95 OR <= 0.05

Expected outcome: 200-500 traders with statistically significant positive PnL
(>2 std above random), with at least 50% maintaining positive PnL across 2+ sub-periods.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "01a_pnl_based_skill_scoring"

# ── Configuration ────────────────────────────────────────────────────────────
LOOKBACK_START = "2025-08-01"
MIN_RESOLVED_MARKETS = 20
MIN_RESOLVED_VOLUME_USD = 10000
RESOLUTION_CONFIDENCE_THRESHOLD = 0.95  # last_price >= this OR <= (1 - this)

# Sub-periods for consistency check (approx 2-month windows across the 5-month span)
SUB_PERIODS = [
    ("2025-08-01", "2025-09-30"),
    ("2025-10-01", "2025-11-30"),
    ("2025-12-01", "2026-01-31"),
]

# Significance: trader PnL must be > mean + SIGMA_THRESHOLD * std of population
SIGMA_THRESHOLD = 2.0


# ── SQL building blocks ─────────────────────────────────────────────────────
# These CTEs are reused across multiple queries. Using helper functions avoids
# copy-paste drift and keeps the main run() readable.

def _cte_resolved_markets() -> str:
    """CTE that produces (condition_id, resolved_yes) for confidently resolved markets."""
    return f"""
    resolved_markets AS (
        SELECT
            sub.condition_id,
            if(sub.yes_last_price >= {RESOLUTION_CONFIDENCE_THRESHOLD}, 1, 0) AS resolved_yes
        FROM (
            SELECT lp.condition_id, lp.last_price AS yes_last_price
            FROM (
                SELECT condition_id, asset_id,
                       argMax(price, timestamp) AS last_price
                FROM polymarket.trades FINAL
                WHERE condition_id IN (
                    SELECT condition_id FROM polymarket.markets
                    WHERE status = 'closed' AND resolved_at IS NOT NULL
                )
                GROUP BY condition_id, asset_id
            ) lp
            JOIN polymarket.token_market_map tm ON lp.asset_id = tm.asset_id
            WHERE tm.outcome = 'YES'
              AND (lp.last_price >= {RESOLUTION_CONFIDENCE_THRESHOLD}
                   OR lp.last_price <= {1 - RESOLUTION_CONFIDENCE_THRESHOLD})
        ) sub
    )"""


def _cte_trades_deduped(start: str = LOOKBACK_START,
                         end: str | None = None) -> str:
    """CTE that deduplicates trades on resolved markets within a date range."""
    end_clause = f"AND timestamp < '{end}'" if end else ""
    return f"""
    trades_deduped AS (
        SELECT maker, condition_id, asset_id, side, size,
               amount_usd, fee_usd, price, timestamp
        FROM polymarket.trades FINAL
        WHERE maker IS NOT NULL
          AND timestamp >= '{start}'
          {end_clause}
          AND condition_id IN (SELECT condition_id FROM resolved_markets)
    )"""


def _cte_positions() -> str:
    """CTE that aggregates per-trader, per-market, per-outcome positions."""
    return """
    positions AS (
        SELECT
            td.maker,
            td.condition_id,
            tm.outcome,
            -- net shares held at resolution (positive = long, negative = short)
            sum(td.size * if(td.side = 'BUY', 1, -1))       AS net_shares,
            -- net cost basis (buys minus sell proceeds)
            sum(td.amount_usd * if(td.side = 'BUY', 1, -1)) AS net_cost,
            sum(td.fee_usd)                                   AS total_fees,
            count()                                            AS trade_count,
            sum(td.amount_usd)                                 AS gross_volume,
            -- volume-weighted average entry price for BUY trades
            sumIf(td.price * td.amount_usd, td.side = 'BUY')
                / nullIf(sumIf(td.amount_usd, td.side = 'BUY'), 0) AS avg_buy_price
        FROM trades_deduped td
        JOIN polymarket.token_market_map tm ON td.asset_id = tm.asset_id
        GROUP BY td.maker, td.condition_id, tm.outcome
    )"""


def run(strategy_root: Path, outputs_dir: Path) -> dict:
    db = ExplorationDataSource()

    # ──────────────────────────────────────────────────────────────────────
    # 1. Full-period trader PnL with all features
    # ──────────────────────────────────────────────────────────────────────
    df_trader_pnl = db.query_df(f"""
        WITH
        {_cte_resolved_markets()},
        {_cte_trades_deduped()},
        {_cte_positions()},

        -- Per-market PnL for each trader-outcome position
        market_pnl AS (
            SELECT
                p.maker,
                p.condition_id,
                p.outcome,
                p.net_shares,
                p.net_cost,
                p.total_fees,
                p.trade_count,
                p.gross_volume,
                p.avg_buy_price,
                -- Resolution price for this token: YES token pays 1 if resolved YES
                if(p.outcome = 'YES', rm.resolved_yes, 1 - rm.resolved_yes)
                    AS resolution_price,
                -- PnL = (shares * resolution_price) - cost_basis - fees
                (p.net_shares * if(p.outcome = 'YES', rm.resolved_yes,
                                   1 - rm.resolved_yes))
                    - p.net_cost - p.total_fees
                    AS pnl,
                -- Did this trader "win" on this market-outcome?
                -- Win = positive PnL on this position
                if((p.net_shares * if(p.outcome = 'YES', rm.resolved_yes,
                                      1 - rm.resolved_yes))
                   - p.net_cost - p.total_fees > 0, 1, 0)
                    AS is_win
            FROM positions p
            JOIN resolved_markets rm ON p.condition_id = rm.condition_id
        )

        SELECT
            maker,
            -- Volume & activity
            sum(gross_volume)                       AS total_volume_usd,
            sum(trade_count)                        AS total_trades,
            uniq(condition_id)                      AS resolved_markets,

            -- ── New features ──────────────────────────────────────────
            -- 1. estimated_pnl: total PnL across all resolved positions
            sum(pnl)                                AS estimated_pnl,

            -- 2. pnl_per_dollar: PnL normalised by gross volume traded
            sum(pnl) / nullIf(sum(gross_volume), 0)
                                                    AS pnl_per_dollar,

            -- 3. win_rate_by_market: fraction of unique markets with net positive PnL
            --    (aggregate at market level first, then compute rate)
            sumIf(1, pnl > 0) / nullIf(count(), 0)
                                                    AS win_rate_by_position,

            -- 4. avg_entry_price_vs_resolution: mean gap between buy price and resolution
            --    positive means bought cheap on winning side
            avg(resolution_price - avg_buy_price)   AS avg_entry_vs_resolution,

            -- 5. pnl_sharpe_ratio: mean(market_pnl) / stddev(market_pnl)
            avg(pnl) / nullIf(stddevPop(pnl), 0)   AS pnl_sharpe_ratio,

            -- Supplementary
            sum(total_fees)                         AS total_fees_paid,
            countIf(pnl > 0)                        AS winning_positions,
            countIf(pnl <= 0)                       AS losing_positions

        FROM market_pnl
        GROUP BY maker
        HAVING resolved_markets >= {MIN_RESOLVED_MARKETS}
           AND total_volume_usd >= {MIN_RESOLVED_VOLUME_USD}
        ORDER BY estimated_pnl DESC
    """)

    df_trader_pnl.write_parquet(outputs_dir / "trader_pnl_features.parquet")

    n_traders = len(df_trader_pnl)
    if n_traders == 0:
        return {"stage_id": STAGE_ID, "metrics": {}, "findings": {"error": "No qualifying traders"}}

    # ──────────────────────────────────────────────────────────────────────
    # 2. Population stats for significance thresholding
    # ──────────────────────────────────────────────────────────────────────
    pnl_col = df_trader_pnl["estimated_pnl"]
    pop_mean = float(pnl_col.mean())
    pop_std = float(pnl_col.std())
    significance_threshold = pop_mean + SIGMA_THRESHOLD * pop_std

    # Skilled = PnL > mean + 2*std
    df_skilled = df_trader_pnl.filter(pl.col("estimated_pnl") > significance_threshold)
    n_skilled = len(df_skilled)

    df_skilled.write_parquet(outputs_dir / "skilled_traders_pnl.parquet")

    # ──────────────────────────────────────────────────────────────────────
    # 3. Sub-period consistency — compute PnL in each sub-period
    # ──────────────────────────────────────────────────────────────────────
    period_dfs = []
    for idx, (p_start, p_end) in enumerate(SUB_PERIODS):
        df_period = db.query_df(f"""
            WITH
            {_cte_resolved_markets()},
            {_cte_trades_deduped(start=p_start, end=p_end)},
            {_cte_positions()}

            SELECT
                p.maker,
                uniq(p.condition_id)                     AS period_markets,
                sum(p.gross_volume)                      AS period_volume,
                sum(
                    p.net_shares * if(p.outcome = 'YES', rm.resolved_yes,
                                     1 - rm.resolved_yes)
                    - p.net_cost - p.total_fees
                )                                         AS period_pnl
            FROM positions p
            JOIN resolved_markets rm ON p.condition_id = rm.condition_id
            GROUP BY p.maker
            HAVING period_markets >= 3
        """)
        df_period = df_period.with_columns(
            pl.lit(idx).alias("period_idx"),
            pl.lit(p_start).alias("period_start"),
            pl.lit(p_end).alias("period_end"),
        )
        period_dfs.append(df_period)

    df_periods = pl.concat(period_dfs)
    df_periods.write_parquet(outputs_dir / "trader_pnl_by_period.parquet")

    # For each skilled trader, count how many sub-periods have positive PnL
    skilled_addrs = set(df_skilled["maker"].to_list())
    df_skilled_periods = df_periods.filter(pl.col("maker").is_in(skilled_addrs))

    consistency = (
        df_skilled_periods
        .group_by("maker")
        .agg([
            pl.col("period_pnl").count().alias("periods_with_data"),
            (pl.col("period_pnl") > 0).sum().alias("periods_positive"),
        ])
    )

    n_consistent = int(
        consistency.filter(pl.col("periods_positive") >= 2).height
    )
    consistency_rate = n_consistent / max(n_skilled, 1)

    consistency.write_parquet(outputs_dir / "skilled_consistency.parquet")

    # ──────────────────────────────────────────────────────────────────────
    # 4. Overlap analysis: skilled (PnL) vs high-volume traders
    # ──────────────────────────────────────────────────────────────────────
    # Load parent's top traders by volume for comparison
    parent_outputs = strategy_root / "stages" / "00_initial" / "outputs"
    df_parent_traders = pl.read_parquet(parent_outputs / "traders_summary.parquet")

    # Top N by volume from parent (same N as our skilled set for fair comparison)
    top_vol_addrs = set(
        df_parent_traders
        .sort("total_volume_usd", descending=True)
        .head(max(n_skilled, 1))["maker"]
        .to_list()
    )

    overlap = skilled_addrs & top_vol_addrs
    jaccard = len(overlap) / max(len(skilled_addrs | top_vol_addrs), 1)

    overlap_stats = {
        "n_skilled_pnl": n_skilled,
        "n_top_volume": len(top_vol_addrs),
        "n_overlap": len(overlap),
        "jaccard_similarity": round(jaccard, 4),
        "pct_skilled_also_top_vol": round(len(overlap) / max(n_skilled, 1) * 100, 1),
    }

    # ──────────────────────────────────────────────────────────────────────
    # 5. Summary statistics for the skilled set
    # ──────────────────────────────────────────────────────────────────────
    skilled_summary = {
        "mean_pnl": round(float(df_skilled["estimated_pnl"].mean()), 2),
        "median_pnl": round(float(df_skilled["estimated_pnl"].median()), 2),
        "mean_pnl_per_dollar": round(float(df_skilled["pnl_per_dollar"].mean()), 4),
        "median_pnl_per_dollar": round(float(df_skilled["pnl_per_dollar"].median()), 4),
        "mean_win_rate": round(float(df_skilled["win_rate_by_position"].mean()), 4),
        "mean_sharpe": round(float(df_skilled["pnl_sharpe_ratio"].drop_nulls().mean()), 4)
            if df_skilled["pnl_sharpe_ratio"].drop_nulls().len() > 0 else None,
        "mean_resolved_markets": round(float(df_skilled["resolved_markets"].mean()), 1),
        "mean_volume": round(float(df_skilled["total_volume_usd"].mean()), 2),
    }

    # ──────────────────────────────────────────────────────────────────────
    # 6. Save top skilled traders detail
    # ──────────────────────────────────────────────────────────────────────
    df_top_skilled = df_skilled.sort("estimated_pnl", descending=True).head(50)
    df_top_skilled.write_parquet(outputs_dir / "top_skilled_traders.parquet")

    # ──────────────────────────────────────────────────────────────────────
    # 7. Metrics and return
    # ──────────────────────────────────────────────────────────────────────
    metrics = StageMetrics(
        sample_size=n_traders,
        unique_traders=n_traders,
        unique_markets=int(df_trader_pnl["resolved_markets"].sum()),
        custom={
            "lookback_start": LOOKBACK_START,
            "min_resolved_markets": MIN_RESOLVED_MARKETS,
            "min_resolved_volume_usd": MIN_RESOLVED_VOLUME_USD,
            "resolution_confidence_threshold": RESOLUTION_CONFIDENCE_THRESHOLD,
            "sigma_threshold": SIGMA_THRESHOLD,
            "population_mean_pnl": round(pop_mean, 2),
            "population_std_pnl": round(pop_std, 2),
            "significance_threshold_pnl": round(significance_threshold, 2),
            "n_skilled_traders": n_skilled,
            "n_consistent_across_periods": n_consistent,
            "consistency_rate": round(consistency_rate, 4),
            "sub_periods": SUB_PERIODS,
        },
    )

    return {
        "stage_id": STAGE_ID,
        "metrics": metrics.model_dump(exclude_none=True),
        "findings": {
            "skilled_summary": skilled_summary,
            "overlap_with_volume": overlap_stats,
            "consistency": {
                "n_skilled": n_skilled,
                "n_consistent_2plus_periods": n_consistent,
                "consistency_rate": round(consistency_rate, 4),
                "meets_hypothesis": consistency_rate >= 0.50,
            },
            "population_distribution": {
                "total_qualifying_traders": n_traders,
                "mean_pnl": round(pop_mean, 2),
                "std_pnl": round(pop_std, 2),
                "profitable_count": int(
                    df_trader_pnl.filter(pl.col("estimated_pnl") > 0).height
                ),
                "profitable_pct": round(
                    df_trader_pnl.filter(pl.col("estimated_pnl") > 0).height
                    / n_traders * 100, 1
                ),
            },
            "top_10_skilled": df_top_skilled.head(10).to_dicts(),
        },
    }


if __name__ == "__main__":
    root = Path(__file__).parent.parent.parent
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    summary = run(root, out)
    print(json.dumps(summary, indent=2, default=str))
