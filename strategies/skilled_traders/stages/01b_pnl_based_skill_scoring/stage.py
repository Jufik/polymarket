"""Stage: 01b_pnl_based_skill_scoring — Token-aware PnL Skill Scoring

Hypothesis: Token-aware PnL will identify a distinct set of 'skilled' traders who
differ from high-volume traders, with top PnL traders showing consistent positive
returns across time periods.

Parent: 00_initial (Initial Skilled Traders)

Approach:
    1. Identify resolved markets via last YES-token trade price (>= 0.95 → YES, <= 0.05 → NO).
    2. Compute per-trader PnL across all resolved markets using:
       PnL = direction * (resolution_value - entry_price) * size
       where direction = +1 for BUY, -1 for SELL.
    3. Filter to traders with >= 20 resolved markets and >= $10k resolved volume.
    4. Score skill via: estimated_pnl, pnl_per_dollar, win_rate_by_market,
       avg_entry_price_vs_resolution, pnl_sharpe_ratio.
    5. Identify statistically significant outperformers (> 2 std above population mean).
    6. Validate consistency: require positive PnL in at least 2 of 3 sub-periods.
    7. Compare skilled-by-PnL vs skilled-by-volume (parent stage top traders).

Filter conditions:
    - min_resolved_markets: 20
    - min_resolved_volume_usd: 10000
    - resolution_confidence: last_price >= 0.95 OR last_price <= 0.05
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "01b_pnl_based_skill_scoring"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOOKBACK_START = "2025-08-01"
MIN_RESOLVED_MARKETS = 20
MIN_RESOLVED_VOLUME_USD = 10000
RESOLUTION_YES_THRESHOLD = 0.95
RESOLUTION_NO_THRESHOLD = 0.05
SKILL_Z_THRESHOLD = 2.0  # Std deviations above mean for "skilled"
MIN_CONSISTENT_PERIODS = 2  # Out of 3 sub-periods

# Sub-period boundaries (~2 months each across Aug 2025 – Jan 2026)
SUB_PERIODS = [
    ("2025-08-01", "2025-09-30"),
    ("2025-10-01", "2025-11-30"),
    ("2025-12-01", "2026-01-26"),
]

# ---------------------------------------------------------------------------
# SQL: Resolved-markets CTE (reused across queries)
# ---------------------------------------------------------------------------
RESOLVED_MARKETS_CTE = f"""
    resolved_markets AS (
        SELECT
            tm.condition_id AS cid,
            multiIf(
                argMax(tr.price, tr.timestamp) >= {RESOLUTION_YES_THRESHOLD}, 1,
                argMax(tr.price, tr.timestamp) <= {RESOLUTION_NO_THRESHOLD}, 0,
                -1
            ) AS yes_resolved_to
        FROM polymarket.trades AS tr FINAL
        JOIN polymarket.token_market_map AS tm ON tr.asset_id = tm.asset_id
        WHERE tm.outcome = 'YES'
          AND tr.timestamp >= '{LOOKBACK_START}'
        GROUP BY tm.condition_id
        HAVING yes_resolved_to >= 0
    )
"""


def run(strategy_root: Path, outputs_dir: Path) -> dict:
    db = ExplorationDataSource()

    # ------------------------------------------------------------------
    # 1. Compute per-trader PnL features on resolved markets
    #
    # For each trade, the token's resolution value is 1 if the token's
    # outcome matches the market resolution, else 0.
    #   - YES token on YES-resolved market → value = 1
    #   - YES token on NO-resolved market  → value = 0
    #   - NO  token on YES-resolved market → value = 0
    #   - NO  token on NO-resolved market  → value = 1
    #
    # PnL per trade = direction * (resolution_value - price) * size
    # where direction = +1 for BUY, -1 for SELL.
    # ------------------------------------------------------------------
    df_trader_pnl = db.query_df(f"""
        WITH {RESOLVED_MARKETS_CTE}
        SELECT
            tr.maker                                    AS maker,
            count()                                     AS trade_count,
            uniq(rm.cid)                                AS resolved_markets,
            sum(tr.amount_usd)                          AS resolved_volume_usd,

            -- Core PnL: sum of per-trade profit
            sum(
                multiIf(tr.side = 'BUY', 1, -1)
                * (
                    multiIf(
                        (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                        OR (tm.outcome = 'NO'  AND rm.yes_resolved_to = 0),
                        1, 0
                    ) - tr.price
                  )
                * tr.size
            )                                           AS estimated_pnl,

            -- PnL per dollar risked
            sum(
                multiIf(tr.side = 'BUY', 1, -1)
                * (
                    multiIf(
                        (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                        OR (tm.outcome = 'NO'  AND rm.yes_resolved_to = 0),
                        1, 0
                    ) - tr.price
                  )
                * tr.size
            ) / greatest(sum(tr.amount_usd), 1)        AS pnl_per_dollar,

            -- Average entry price on winning BUY positions
            avg(
                multiIf(
                    tr.side = 'BUY'
                    AND (
                        (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                        OR (tm.outcome = 'NO' AND rm.yes_resolved_to = 0)
                    ),
                    tr.price,
                    NULL
                )
            )                                           AS avg_winning_buy_price,

            -- Average entry price on losing BUY positions
            avg(
                multiIf(
                    tr.side = 'BUY'
                    AND NOT (
                        (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                        OR (tm.outcome = 'NO' AND rm.yes_resolved_to = 0)
                    ),
                    tr.price,
                    NULL
                )
            )                                           AS avg_losing_buy_price,

            -- Fee burden
            sum(tr.fee_usd)                             AS total_fees_usd,

            min(tr.timestamp)                           AS first_trade,
            max(tr.timestamp)                           AS last_trade

        FROM polymarket.trades AS tr FINAL
        JOIN polymarket.token_market_map AS tm ON tr.asset_id = tm.asset_id
        JOIN resolved_markets AS rm ON tm.condition_id = rm.cid
        WHERE tr.maker IS NOT NULL
          AND tr.timestamp >= '{LOOKBACK_START}'
        GROUP BY tr.maker
        HAVING resolved_markets >= {MIN_RESOLVED_MARKETS}
           AND resolved_volume_usd >= {MIN_RESOLVED_VOLUME_USD}
        ORDER BY estimated_pnl DESC
    """)

    # ------------------------------------------------------------------
    # 2. Per-trader per-market PnL → win rate + Sharpe ratio
    #    Push the qualifying-trader filter into a CTE to avoid the
    #    expensive correlated HAVING subquery from the old version.
    # ------------------------------------------------------------------
    df_per_market = db.query_df(f"""
        WITH
        {RESOLVED_MARKETS_CTE},
        qualifying_traders AS (
            SELECT tr.maker AS maker
            FROM polymarket.trades AS tr FINAL
            JOIN polymarket.token_market_map AS tm ON tr.asset_id = tm.asset_id
            JOIN resolved_markets AS rm ON tm.condition_id = rm.cid
            WHERE tr.maker IS NOT NULL
              AND tr.timestamp >= '{LOOKBACK_START}'
            GROUP BY tr.maker
            HAVING uniq(rm.cid) >= {MIN_RESOLVED_MARKETS}
               AND sum(tr.amount_usd) >= {MIN_RESOLVED_VOLUME_USD}
        )
        SELECT
            tr.maker                                    AS maker,
            rm.cid                                      AS condition_id,
            sum(tr.amount_usd)                          AS market_volume,
            sum(
                multiIf(tr.side = 'BUY', 1, -1)
                * (
                    multiIf(
                        (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                        OR (tm.outcome = 'NO'  AND rm.yes_resolved_to = 0),
                        1, 0
                    ) - tr.price
                  )
                * tr.size
            )                                           AS market_pnl
        FROM polymarket.trades AS tr FINAL
        JOIN polymarket.token_market_map AS tm ON tr.asset_id = tm.asset_id
        JOIN resolved_markets AS rm ON tm.condition_id = rm.cid
        JOIN qualifying_traders AS qt ON tr.maker = qt.maker
        WHERE tr.maker IS NOT NULL
          AND tr.timestamp >= '{LOOKBACK_START}'
        GROUP BY tr.maker, rm.cid
    """)

    # Compute win_rate and Sharpe per trader from per-market PnL
    df_market_stats = (
        df_per_market
        .group_by("maker")
        .agg([
            (pl.col("market_pnl") > 0).mean().alias("win_rate_by_market"),
            pl.col("market_pnl").mean().alias("avg_market_pnl"),
            pl.col("market_pnl").std().alias("std_market_pnl"),
            pl.col("market_pnl").count().alias("n_markets"),
        ])
        .with_columns(
            # Sharpe = mean(pnl) / std(pnl)
            # Higher is better — measures risk-adjusted return per market
            (pl.col("avg_market_pnl")
             / pl.col("std_market_pnl").clip(lower_bound=1e-6))
            .alias("pnl_sharpe_ratio")
        )
    )

    # ------------------------------------------------------------------
    # 3. Join market-level stats back to trader-level PnL
    # ------------------------------------------------------------------
    df_scored = (
        df_trader_pnl
        .join(
            df_market_stats.select([
                "maker", "win_rate_by_market", "pnl_sharpe_ratio",
                "avg_market_pnl", "std_market_pnl",
            ]),
            on="maker",
            how="left",
        )
        .with_columns([
            # avg_entry_price_vs_resolution: gap between losing and winning buy prices
            (pl.col("avg_losing_buy_price") - pl.col("avg_winning_buy_price"))
            .alias("avg_entry_price_vs_resolution"),
            # Net PnL after fees
            (pl.col("estimated_pnl") - pl.col("total_fees_usd"))
            .alias("pnl_net_of_fees"),
        ])
    )

    # ------------------------------------------------------------------
    # 4. Statistical significance: Z-score of PnL per dollar
    # ------------------------------------------------------------------
    pop_mean = df_scored["pnl_per_dollar"].mean()
    pop_std = df_scored["pnl_per_dollar"].std()
    # Guard against None (empty frame)
    pop_mean = float(pop_mean) if pop_mean is not None else 0.0
    pop_std = float(pop_std) if pop_std is not None else 1e-9

    df_scored = df_scored.with_columns(
        ((pl.col("pnl_per_dollar") - pop_mean) / max(pop_std, 1e-9))
        .alias("pnl_z_score")
    )

    # Skilled = z-score > threshold
    df_skilled = df_scored.filter(pl.col("pnl_z_score") > SKILL_Z_THRESHOLD)

    # ------------------------------------------------------------------
    # 5. Sub-period consistency: positive PnL in >= 2 of 3 periods
    #
    # BUG FIX: The previous version used sequential outer joins on
    # "maker" which created duplicate "maker_right" columns on the
    # second join. Instead, we collect all period PnLs via
    # pl.concat + pivot, which is cleaner and avoids the collision.
    # ------------------------------------------------------------------
    period_frames = []
    for i, (p_start, p_end) in enumerate(SUB_PERIODS):
        df_p = db.query_df(f"""
            WITH {RESOLVED_MARKETS_CTE}
            SELECT
                tr.maker                                AS maker,
                sum(
                    multiIf(tr.side = 'BUY', 1, -1)
                    * (
                        multiIf(
                            (tm.outcome = 'YES' AND rm.yes_resolved_to = 1)
                            OR (tm.outcome = 'NO'  AND rm.yes_resolved_to = 0),
                            1, 0
                        ) - tr.price
                      )
                    * tr.size
                )                                       AS period_pnl
            FROM polymarket.trades AS tr FINAL
            JOIN polymarket.token_market_map AS tm ON tr.asset_id = tm.asset_id
            JOIN resolved_markets AS rm ON tm.condition_id = rm.cid
            WHERE tr.maker IS NOT NULL
              AND tr.timestamp >= '{p_start}'
              AND tr.timestamp <= '{p_end}'
            GROUP BY tr.maker
        """)
        # Tag each row with its period index so we can pivot later
        df_p = df_p.with_columns(pl.lit(i).cast(pl.Int32).alias("period_idx"))
        period_frames.append(df_p)

    # Stack all periods vertically, then pivot to wide format
    df_all_periods = pl.concat(period_frames)

    df_periods = (
        df_all_periods
        .pivot(
            on="period_idx",
            index="maker",
            values="period_pnl",
        )
    )

    # Rename pivoted columns from "0", "1", "2" → "pnl_period_0", etc.
    period_cols = []
    for i in range(len(SUB_PERIODS)):
        old_name = str(i)
        new_name = f"pnl_period_{i}"
        if old_name in df_periods.columns:
            df_periods = df_periods.rename({old_name: new_name})
        period_cols.append(new_name)

    # Count periods with positive PnL (null → 0 → not positive)
    df_periods = df_periods.with_columns(
        pl.sum_horizontal([
            (pl.col(c).fill_null(0) > 0).cast(pl.Int32)
            for c in period_cols
        ]).alias("positive_periods")
    )

    # Join consistency back to skilled traders
    df_skilled = (
        df_skilled
        .join(
            df_periods.select(["maker", "positive_periods"] + period_cols),
            on="maker",
            how="left",
        )
        .with_columns(
            pl.col("positive_periods").fill_null(0)
        )
    )

    # Final filter: consistent skilled traders
    df_consistent_skilled = (
        df_skilled
        .filter(pl.col("positive_periods") >= MIN_CONSISTENT_PERIODS)
        .sort("estimated_pnl", descending=True)
    )

    # ------------------------------------------------------------------
    # 6. Compare with parent's high-volume traders
    # ------------------------------------------------------------------
    parent_outputs = strategy_root / "stages" / "00_initial" / "outputs"
    overlap_stats: dict = {}

    if (parent_outputs / "top_traders.parquet").exists():
        df_parent_top = pl.read_parquet(parent_outputs / "top_traders.parquet")
        parent_top_set = set(df_parent_top["maker"].to_list())
        skilled_set = set(df_consistent_skilled["maker"].to_list())
        overlap = parent_top_set & skilled_set

        overlap_stats = {
            "parent_top_count": len(parent_top_set),
            "skilled_count": len(skilled_set),
            "overlap_count": len(overlap),
            "overlap_pct_of_skilled": round(
                len(overlap) / max(len(skilled_set), 1) * 100, 1
            ),
            "overlap_pct_of_parent_top": round(
                len(overlap) / max(len(parent_top_set), 1) * 100, 1
            ),
        }

    # Also compare top-500 by volume vs top-500 by PnL
    df_by_volume = df_scored.sort("resolved_volume_usd", descending=True).head(500)
    df_by_pnl = df_scored.sort("estimated_pnl", descending=True).head(500)
    vol_set = set(df_by_volume["maker"].to_list())
    pnl_set = set(df_by_pnl["maker"].to_list())
    vol_vs_pnl_overlap = vol_set & pnl_set

    volume_vs_pnl_stats = {
        "top500_volume_vs_pnl_overlap": len(vol_vs_pnl_overlap),
        "top500_jaccard_similarity": round(
            len(vol_vs_pnl_overlap) / max(len(vol_set | pnl_set), 1), 3
        ),
    }

    # ------------------------------------------------------------------
    # 7. Distribution analysis of the skilled traders
    # ------------------------------------------------------------------
    skilled_summary: dict = {}
    if len(df_consistent_skilled) > 0:
        skilled_summary = {
            "total_skilled_traders": len(df_consistent_skilled),
            "median_pnl": float(df_consistent_skilled["estimated_pnl"].median()),
            "mean_pnl": float(df_consistent_skilled["estimated_pnl"].mean()),
            "median_pnl_per_dollar": float(
                df_consistent_skilled["pnl_per_dollar"].median()
            ),
            "mean_win_rate": float(
                df_consistent_skilled["win_rate_by_market"].drop_nulls().mean()
                or 0.0
            ),
            "median_sharpe": float(
                df_consistent_skilled["pnl_sharpe_ratio"].drop_nulls().median()
                or 0.0
            ),
            "median_resolved_markets": int(
                df_consistent_skilled["resolved_markets"].median()
            ),
            "median_resolved_volume": float(
                df_consistent_skilled["resolved_volume_usd"].median()
            ),
            "pct_3_positive_periods": round(
                (df_consistent_skilled["positive_periods"] == 3).mean() * 100, 1
            ),
        }

    # ------------------------------------------------------------------
    # 8. Population-level distribution for context
    # ------------------------------------------------------------------
    population_summary = {
        "total_qualifying_traders": len(df_scored),
        "pop_mean_pnl_per_dollar": pop_mean,
        "pop_std_pnl_per_dollar": pop_std,
        "pct_positive_pnl": round(
            (df_scored["estimated_pnl"] > 0).mean() * 100, 1
        ) if len(df_scored) > 0 else 0.0,
        "z_threshold": SKILL_Z_THRESHOLD,
        "pnl_per_dollar_threshold": pop_mean + SKILL_Z_THRESHOLD * max(pop_std, 1e-9),
        "traders_above_z_threshold": len(df_skilled),
        "traders_consistent_and_skilled": len(df_consistent_skilled),
    }

    # ------------------------------------------------------------------
    # 9. Save outputs
    # ------------------------------------------------------------------
    # Full scored population
    df_scored.write_parquet(outputs_dir / "all_traders_pnl_scored.parquet")

    # Skilled traders (z > 2, consistent)
    df_consistent_skilled.write_parquet(outputs_dir / "skilled_traders_pnl.parquet")

    # Per-market PnL for skilled traders (useful for downstream)
    skilled_makers = set(df_consistent_skilled["maker"].to_list())
    df_skilled_per_market = df_per_market.filter(
        pl.col("maker").is_in(skilled_makers)
    )
    df_skilled_per_market.write_parquet(
        outputs_dir / "skilled_traders_per_market_pnl.parquet"
    )

    # Sub-period PnL for skilled traders
    df_skilled_periods = df_periods.filter(pl.col("maker").is_in(skilled_makers))
    df_skilled_periods.write_parquet(
        outputs_dir / "skilled_traders_period_pnl.parquet"
    )

    # Summary JSON
    summary_data = {
        "population": population_summary,
        "skilled_traders": skilled_summary,
        "volume_vs_pnl_comparison": volume_vs_pnl_stats,
        "parent_overlap": overlap_stats,
        "sub_periods": [
            {"period": i, "start": s, "end": e}
            for i, (s, e) in enumerate(SUB_PERIODS)
        ],
        "filter_config": {
            "lookback_start": LOOKBACK_START,
            "min_resolved_markets": MIN_RESOLVED_MARKETS,
            "min_resolved_volume_usd": MIN_RESOLVED_VOLUME_USD,
            "resolution_yes_threshold": RESOLUTION_YES_THRESHOLD,
            "resolution_no_threshold": RESOLUTION_NO_THRESHOLD,
            "skill_z_threshold": SKILL_Z_THRESHOLD,
            "min_consistent_periods": MIN_CONSISTENT_PERIODS,
        },
    }

    with open(outputs_dir / "summary.json", "w") as f:
        json.dump(summary_data, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # 10. Metrics
    # ------------------------------------------------------------------
    metrics = StageMetrics(
        sample_size=len(df_consistent_skilled),
        unique_traders=len(df_consistent_skilled),
        unique_markets=int(
            df_consistent_skilled["resolved_markets"].sum()
        ) if len(df_consistent_skilled) > 0 else 0,
        custom={
            "total_qualifying_traders": len(df_scored),
            "traders_above_z_threshold": len(df_skilled),
            "consistent_skilled_traders": len(df_consistent_skilled),
            "population_mean_pnl_per_dollar": pop_mean,
            "population_std_pnl_per_dollar": pop_std,
            "z_score_threshold": SKILL_Z_THRESHOLD,
            "volume_vs_pnl_jaccard": volume_vs_pnl_stats.get(
                "top500_jaccard_similarity", 0
            ),
            "lookback_start": LOOKBACK_START,
            "min_resolved_markets": MIN_RESOLVED_MARKETS,
            "min_resolved_volume_usd": MIN_RESOLVED_VOLUME_USD,
        },
    )

    return {
        "stage_id": STAGE_ID,
        "metrics": metrics.model_dump(exclude_none=True),
        "findings": {
            "population_summary": population_summary,
            "skilled_traders_summary": skilled_summary,
            "volume_vs_pnl_comparison": volume_vs_pnl_stats,
            "parent_overlap": overlap_stats,
            "top_10_by_pnl": (
                df_consistent_skilled
                .head(10)
                .select([
                    "maker", "estimated_pnl", "pnl_per_dollar",
                    "win_rate_by_market", "pnl_sharpe_ratio",
                    "resolved_markets", "resolved_volume_usd",
                    "positive_periods",
                ])
                .to_dicts()
            ),
        },
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent.parent
    out = Path(__file__).resolve().parent / "outputs"
    out.mkdir(exist_ok=True)
    summary = run(root, out)
    print(json.dumps(summary, indent=2, default=str))
