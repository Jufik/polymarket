"""Stage: 01c_pnl_based_skill_scoring — PnL-Based Skill Scoring

Hypothesis: Token-aware PnL will identify a distinct set of 'skilled' traders who
differ from high-volume traders, with top PnL traders showing consistent positive
returns across time periods.

Parent: 00_initial (Initial Skilled Traders)

Approach:
  1. Identify confidently resolved markets (last YES-token price >= 0.95 or <= 0.05).
  2. Compute per-trader, per-market PnL using token-level resolution inference:
       - resolution_price = 1 if last YES price >= 0.95, else 0
       - YES token BUY:  pnl = (resolution_price - entry_price) * size
       - YES token SELL: pnl = (entry_price - resolution_price) * size
       - NO  token BUY:  pnl = ((1 - resolution_price) - entry_price) * size
       - NO  token SELL: pnl = (entry_price - (1 - resolution_price)) * size
  3. Filter to traders with >= 20 resolved markets and >= $10,000 resolved volume.
  4. Compute features: estimated_pnl, pnl_per_dollar, win_rate_by_market,
     avg_entry_price_vs_resolution, pnl_sharpe_ratio.
  5. Apply statistical significance filter: PnL > 2 std above random baseline.
  6. Validate consistency across 2+ time sub-periods (halves of data).

Expected outcome: 200-500 traders with significant positive PnL, >=50% consistent
across sub-periods.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "01c_pnl_based_skill_scoring"

# ---------------------------------------------------------------------------
# Filter parameters
# ---------------------------------------------------------------------------
MIN_RESOLVED_MARKETS = 20
MIN_RESOLVED_VOLUME_USD = 10_000
LOOKBACK_START = "2025-01-01"       # broader window than parent for PnL
RESOLUTION_CONFIDENCE = 0.95        # last YES price >= this → YES resolved
MIN_TRADES_PER_TRADER = 10


# ---------------------------------------------------------------------------
# SQL building blocks — resolved markets CTE
# ---------------------------------------------------------------------------
# ClickHouse type notes:
#   - markets.resolved_at is Nullable(DateTime('Etc/UTC'))
#   - trades_raw.timestamp is DateTime64(3)
#   - When interpolating datetime values from query results back into SQL,
#     always strip timezone suffixes (+00:00) and use toString(..., 'UTC')
#     because ClickHouse DateTime('Etc/UTC') cannot parse '+00:00' strings.
#   - Use explicit toDateTime() casts for string-to-DateTime comparisons.

def _resolved_markets_cte(lookback: str, confidence: float) -> str:
    """Return a CTE body (without WITH keyword) producing:
    condition_id, last_yes_price, resolution_price, resolved_at."""
    return f"""
        SELECT
            condition_id,
            last_yes_price,
            if(last_yes_price >= {confidence}, 1, 0) AS resolution_price,
            resolved_at
        FROM (
            SELECT
                m.condition_id AS condition_id,
                m.resolved_at  AS resolved_at,
                argMax(t.price, t.timestamp) AS last_yes_price
            FROM polymarket.markets AS m
            INNER JOIN polymarket.token_market_map AS tm
                ON m.condition_id = tm.condition_id AND tm.outcome = 'YES'
            INNER JOIN polymarket.trades_raw AS t FINAL
                ON t.asset_id = tm.asset_id
            WHERE m.status = 'closed'
              AND m.resolved_at IS NOT NULL
              AND m.resolved_at >= toDateTime('{lookback}', 'UTC')
              AND t.timestamp >= toDateTime64('{lookback}', 3, 'UTC')
            GROUP BY m.condition_id, m.resolved_at
        )
        WHERE last_yes_price >= {confidence}
           OR last_yes_price <= {1 - confidence}
    """


def _sanitize_datetime_str(dt_str: str) -> str:
    """Strip timezone suffix (+00:00, Z, etc.) from a datetime string
    so it can be safely interpolated into ClickHouse SQL for
    DateTime('Etc/UTC') columns."""
    s = str(dt_str).strip()
    # Remove common TZ suffixes that ClickHouse DateTime cannot parse
    for suffix in ("+00:00", "+00", "Z"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def run(strategy_root: Path, outputs_dir: Path) -> dict:
    db = ExplorationDataSource()

    resolved_cte = _resolved_markets_cte(LOOKBACK_START, RESOLUTION_CONFIDENCE)

    # ==================================================================
    # STEP 1-2: Per-trader, per-market PnL via trade-level resolution.
    # ==================================================================

    trader_market_pnl_sql = f"""
        WITH resolved AS (
            {resolved_cte}
        ),
        trade_pnl AS (
            SELECT
                t.maker                    AS trader,
                t.condition_id             AS cond_id,
                r.resolution_price         AS res_price,
                r.resolved_at              AS res_at,
                tm.outcome                 AS outcome,
                t.side                     AS side,
                t.price                    AS entry_price,
                t.size                     AS size,
                t.amount_usd               AS amount_usd,
                t.fee_usd                  AS fee_usd,
                -- PnL per trade (before fees)
                CASE
                    WHEN tm.outcome = 'YES' AND t.side = 'BUY'
                        THEN (r.resolution_price - t.price) * t.size
                    WHEN tm.outcome = 'YES' AND t.side = 'SELL'
                        THEN (t.price - r.resolution_price) * t.size
                    WHEN tm.outcome = 'NO'  AND t.side = 'BUY'
                        THEN ((1 - r.resolution_price) - t.price) * t.size
                    WHEN tm.outcome = 'NO'  AND t.side = 'SELL'
                        THEN (t.price - (1 - r.resolution_price)) * t.size
                    ELSE 0
                END AS trade_pnl_raw,
                t.timestamp                AS ts
            FROM polymarket.trades_raw AS t FINAL
            INNER JOIN polymarket.token_market_map AS tm
                ON t.asset_id = tm.asset_id
            INNER JOIN resolved AS r
                ON t.condition_id = r.condition_id
            WHERE t.maker IS NOT NULL
              AND t.timestamp >= toDateTime64('{LOOKBACK_START}', 3, 'UTC')
        )
        SELECT
            trader,
            cond_id                                     AS condition_id,
            res_price                                   AS resolution_price,
            res_at                                      AS resolved_at,
            -- Per-market aggregates
            sum(trade_pnl_raw) - sum(fee_usd)           AS market_pnl,
            sum(amount_usd)                              AS market_volume,
            count()                                      AS market_trades,
            -- Win: positive PnL on this market
            if(sum(trade_pnl_raw) - sum(fee_usd) > 0, 1, 0) AS market_win,
            -- Average entry price (volume-weighted) for YES-token buys
            sumIf(entry_price * amount_usd, outcome = 'YES' AND side = 'BUY')
              / greatest(sumIf(amount_usd, outcome = 'YES' AND side = 'BUY'), 0.01)
              AS avg_yes_buy_price,
            min(ts) AS first_trade_ts,
            max(ts) AS last_trade_ts
        FROM trade_pnl
        GROUP BY trader, cond_id, res_price, res_at
    """

    # ==================================================================
    # STEP 3: Aggregate to per-trader level with all features.
    # ==================================================================

    trader_features_sql = f"""
        WITH trader_markets AS (
            {trader_market_pnl_sql}
        )
        SELECT
            trader,
            -- Core PnL features
            sum(market_pnl)                                        AS estimated_pnl,
            sum(market_volume)                                     AS total_resolved_volume,
            sum(market_pnl) / greatest(sum(market_volume), 1)      AS pnl_per_dollar,
            -- Win rate
            sum(market_win)                                        AS markets_won,
            count()                                                AS resolved_market_count,
            sum(market_win) / count()                              AS win_rate_by_market,
            -- Entry price vs resolution
            avg(avg_yes_buy_price)                                 AS avg_entry_price,
            avg(resolution_price)                                  AS avg_resolution_price,
            avg(abs(avg_yes_buy_price - resolution_price))         AS avg_entry_vs_resolution,
            -- Sharpe-like metric: mean(market_pnl) / stddev(market_pnl)
            avg(market_pnl)                                        AS mean_market_pnl,
            stddevPop(market_pnl)                                  AS std_market_pnl,
            if(stddevPop(market_pnl) > 0,
               avg(market_pnl) / stddevPop(market_pnl),
               0)                                                  AS pnl_sharpe_ratio,
            -- Volume and trade stats
            sum(market_trades)                                     AS total_trades,
            min(first_trade_ts)                                    AS first_trade,
            max(last_trade_ts)                                     AS last_trade
        FROM trader_markets
        GROUP BY trader
        HAVING resolved_market_count >= {MIN_RESOLVED_MARKETS}
           AND total_resolved_volume >= {MIN_RESOLVED_VOLUME_USD}
        ORDER BY estimated_pnl DESC
    """

    print(f"[{STAGE_ID}] Querying trader-level PnL features...")
    df_traders = db.query_df(trader_features_sql)
    print(f"[{STAGE_ID}] Found {len(df_traders)} traders passing base filters")

    if len(df_traders) == 0:
        raise RuntimeError("No traders found -- check filter thresholds or data range")

    # ==================================================================
    # STEP 4: Statistical significance filter.
    # Under null: random trading -> expected PnL/dollar ~ 0.
    # We require estimated_pnl > mean + 2*std of the full population.
    # ==================================================================

    pnl_mean = df_traders["estimated_pnl"].mean()
    pnl_std  = df_traders["estimated_pnl"].std()

    ppd_mean = df_traders["pnl_per_dollar"].mean()
    ppd_std  = df_traders["pnl_per_dollar"].std()

    significance_threshold_pnl = pnl_mean + 2 * pnl_std
    significance_threshold_ppd = ppd_mean + 2 * ppd_std

    df_traders = df_traders.with_columns([
        ((pl.col("estimated_pnl") - pnl_mean)
         / pl.when(pnl_std > 0).then(pnl_std).otherwise(1))
            .alias("pnl_z_score"),
        ((pl.col("pnl_per_dollar") - ppd_mean)
         / pl.when(ppd_std > 0).then(ppd_std).otherwise(1))
            .alias("ppd_z_score"),
    ])

    # Tag as "significant" if PnL z-score > 2
    df_traders = df_traders.with_columns(
        (pl.col("pnl_z_score") > 2.0).alias("pnl_significant")
    )

    df_significant = df_traders.filter(pl.col("pnl_significant"))
    print(f"[{STAGE_ID}] {len(df_significant)} traders with PnL > 2 std above mean")

    # ==================================================================
    # STEP 5: Time consistency — split into two halves, compute PnL in
    #         each.  Traders with positive PnL in both halves are
    #         "consistent".
    # ==================================================================

    # FIX for TYPE_MISMATCH: Use toString(..., 'UTC') to return a clean
    # datetime string without the +00:00 timezone suffix.  ClickHouse
    # DateTime('Etc/UTC') columns cannot parse strings with '+00:00'.
    median_date_sql = f"""
        WITH resolved AS (
            {resolved_cte}
        )
        SELECT toString(quantile(0.5)(resolved_at), 'UTC') AS median_resolved_at
        FROM resolved
    """
    median_result = db.query_raw(median_date_sql)
    median_date_raw = str(median_result[0]["median_resolved_at"])
    # Safety belt: strip any remaining timezone suffix
    median_date = _sanitize_datetime_str(median_date_raw)
    print(f"[{STAGE_ID}] Median resolution date for split: {median_date}")

    if len(df_significant) > 0:
        top_sig = df_significant.sort("pnl_z_score", descending=True).head(2000)
        sig_trader_list = ", ".join(f"'{t}'" for t in top_sig["trader"].to_list())

        consistency_sql = f"""
            WITH resolved AS (
                {resolved_cte}
            ),
            trade_pnl AS (
                SELECT
                    t.maker                    AS trader,
                    t.condition_id             AS cond_id,
                    r.resolution_price         AS res_price,
                    r.resolved_at              AS res_at,
                    tm.outcome                 AS outcome,
                    t.side                     AS side,
                    t.price                    AS entry_price,
                    t.size                     AS size,
                    t.amount_usd               AS amount_usd,
                    t.fee_usd                  AS fee_usd,
                    CASE
                        WHEN tm.outcome = 'YES' AND t.side = 'BUY'
                            THEN (r.resolution_price - t.price) * t.size
                        WHEN tm.outcome = 'YES' AND t.side = 'SELL'
                            THEN (t.price - r.resolution_price) * t.size
                        WHEN tm.outcome = 'NO'  AND t.side = 'BUY'
                            THEN ((1 - r.resolution_price) - t.price) * t.size
                        WHEN tm.outcome = 'NO'  AND t.side = 'SELL'
                            THEN (t.price - (1 - r.resolution_price)) * t.size
                        ELSE 0
                    END AS trade_pnl_raw,
                    if(r.resolved_at <= toDateTime('{median_date}', 'UTC'), 1, 2) AS period
                FROM polymarket.trades_raw AS t FINAL
                INNER JOIN polymarket.token_market_map AS tm
                    ON t.asset_id = tm.asset_id
                INNER JOIN resolved AS r
                    ON t.condition_id = r.condition_id
                WHERE t.maker IS NOT NULL
                  AND t.timestamp >= toDateTime64('{LOOKBACK_START}', 3, 'UTC')
                  AND t.maker IN ({sig_trader_list})
            )
            SELECT
                trader,
                period,
                sum(trade_pnl_raw) - sum(fee_usd)  AS period_pnl,
                sum(amount_usd)                     AS period_volume,
                count(DISTINCT cond_id)              AS period_markets,
                sum(if(trade_pnl_raw > 0, 1, 0))   AS winning_trades,
                count()                             AS total_trades
            FROM trade_pnl
            GROUP BY trader, period
            ORDER BY trader, period
        """

        print(f"[{STAGE_ID}] Computing sub-period consistency...")
        df_periods = db.query_df(consistency_sql)

        # Pivot: determine which traders are positive in both periods
        df_p1 = (
            df_periods
            .filter(pl.col("period") == 1)
            .select([
                pl.col("trader"),
                pl.col("period_pnl").alias("pnl_period1"),
                pl.col("period_volume").alias("vol_period1"),
                pl.col("period_markets").alias("markets_period1"),
            ])
        )
        df_p2 = (
            df_periods
            .filter(pl.col("period") == 2)
            .select([
                pl.col("trader"),
                pl.col("period_pnl").alias("pnl_period2"),
                pl.col("period_volume").alias("vol_period2"),
                pl.col("period_markets").alias("markets_period2"),
            ])
        )

        df_consistency = (
            df_p1
            .join(df_p2, on="trader", how="full", coalesce=True)
            .with_columns([
                pl.col("pnl_period1").fill_null(0),
                pl.col("pnl_period2").fill_null(0),
                pl.col("markets_period1").fill_null(0),
                pl.col("markets_period2").fill_null(0),
            ])
            .with_columns(
                ((pl.col("pnl_period1") > 0).cast(pl.Int8)
                 + (pl.col("pnl_period2") > 0).cast(pl.Int8))
                    .alias("positive_periods"),
            )
            .with_columns(
                (pl.col("positive_periods") >= 2).alias("consistent"),
            )
        )

        # Merge consistency back onto significant traders
        df_significant = df_significant.join(
            df_consistency.select([
                "trader", "pnl_period1", "pnl_period2",
                "markets_period1", "markets_period2",
                "positive_periods", "consistent",
            ]),
            on="trader",
            how="left",
        ).with_columns(
            pl.col("consistent").fill_null(False),
            pl.col("positive_periods").fill_null(0),
        )
    else:
        df_significant = df_significant.with_columns([
            pl.lit(0.0).alias("pnl_period1"),
            pl.lit(0.0).alias("pnl_period2"),
            pl.lit(0).alias("markets_period1"),
            pl.lit(0).alias("markets_period2"),
            pl.lit(0).alias("positive_periods"),
            pl.lit(False).alias("consistent"),
        ])

    df_consistent = df_significant.filter(pl.col("consistent"))
    print(f"[{STAGE_ID}] {len(df_consistent)} traders consistent across both periods")

    # ==================================================================
    # STEP 6: Compare skilled (PnL-based) vs high-volume traders
    # ==================================================================

    parent_outputs = strategy_root / "stages" / "00_initial" / "outputs"
    overlap_stats = {}
    if (parent_outputs / "traders_summary.parquet").exists():
        df_parent = pl.read_parquet(parent_outputs / "traders_summary.parquet")
        top_volume_n = max(len(df_significant), 200)
        parent_top_volume = set(
            df_parent
            .sort("total_volume_usd", descending=True)
            .head(top_volume_n)["maker"]
            .to_list()
        )
        skilled_set = set(df_significant["trader"].to_list())
        overlap = skilled_set & parent_top_volume
        overlap_stats = {
            "top_volume_count": len(parent_top_volume),
            "skilled_pnl_count": len(skilled_set),
            "overlap_count": len(overlap),
            "jaccard_index": len(overlap) / max(len(skilled_set | parent_top_volume), 1),
            "pct_skilled_in_top_volume": len(overlap) / max(len(skilled_set), 1),
            "pct_top_volume_in_skilled": len(overlap) / max(len(parent_top_volume), 1),
        }
        print(f"[{STAGE_ID}] Overlap with top-volume traders: {overlap_stats}")

    # ==================================================================
    # STEP 7: Save outputs
    # ==================================================================

    df_traders.write_parquet(outputs_dir / "trader_pnl_features.parquet")
    df_significant.write_parquet(outputs_dir / "significant_pnl_traders.parquet")
    df_consistent.write_parquet(outputs_dir / "consistent_skilled_traders.parquet")

    consistency_rate = (
        len(df_consistent) / max(len(df_significant), 1) if len(df_significant) > 0 else 0
    )

    summary_stats = {
        "base_filter_traders": len(df_traders),
        "significant_pnl_traders": len(df_significant),
        "consistent_skilled_traders": len(df_consistent),
        "consistency_rate": round(consistency_rate, 4),
        "significance_threshold_pnl_usd": round(float(significance_threshold_pnl), 2),
        "significance_threshold_ppd": round(float(significance_threshold_ppd), 6),
        "population_pnl_mean": round(float(pnl_mean), 2),
        "population_pnl_std": round(float(pnl_std), 2),
        "population_ppd_mean": round(float(ppd_mean), 6),
        "population_ppd_std": round(float(ppd_std), 6),
        "median_split_date": median_date,
        "overlap_with_volume_traders": overlap_stats,
    }

    # Feature distributions for the skilled group
    if len(df_significant) > 0:
        feature_dist = {
            "estimated_pnl": {
                "mean":   round(float(df_significant["estimated_pnl"].mean()), 2),
                "median": round(float(df_significant["estimated_pnl"].median()), 2),
                "std":    round(float(df_significant["estimated_pnl"].std()), 2),
                "min":    round(float(df_significant["estimated_pnl"].min()), 2),
                "max":    round(float(df_significant["estimated_pnl"].max()), 2),
            },
            "pnl_per_dollar": {
                "mean":   round(float(df_significant["pnl_per_dollar"].mean()), 6),
                "median": round(float(df_significant["pnl_per_dollar"].median()), 6),
                "std":    round(float(df_significant["pnl_per_dollar"].std()), 6),
            },
            "win_rate_by_market": {
                "mean":   round(float(df_significant["win_rate_by_market"].mean()), 4),
                "median": round(float(df_significant["win_rate_by_market"].median()), 4),
                "std":    round(float(df_significant["win_rate_by_market"].std()), 4),
            },
            "pnl_sharpe_ratio": {
                "mean":   round(float(df_significant["pnl_sharpe_ratio"].mean()), 4),
                "median": round(float(df_significant["pnl_sharpe_ratio"].median()), 4),
                "std":    round(float(df_significant["pnl_sharpe_ratio"].std()), 4),
            },
            "resolved_market_count": {
                "mean":   round(float(df_significant["resolved_market_count"].mean()), 1),
                "median": round(float(df_significant["resolved_market_count"].median()), 1),
                "max":    int(df_significant["resolved_market_count"].max()),
            },
        }
    else:
        feature_dist = {}

    summary_stats["feature_distributions_significant"] = feature_dist

    with open(outputs_dir / "summary_stats.json", "w") as f:
        json.dump(summary_stats, f, indent=2, default=str)

    # ==================================================================
    # STEP 8: Build top traders table for inspection
    # ==================================================================

    df_top_skilled = (
        df_significant
        .sort("pnl_z_score", descending=True)
        .head(50)
        .select([
            "trader", "estimated_pnl", "pnl_per_dollar", "win_rate_by_market",
            "pnl_sharpe_ratio", "pnl_z_score", "resolved_market_count",
            "total_resolved_volume", "total_trades", "consistent",
        ])
    )
    df_top_skilled.write_parquet(outputs_dir / "top_50_skilled.parquet")

    # ==================================================================
    # Metrics & return
    # ==================================================================

    metrics = StageMetrics(
        sample_size=len(df_traders),
        unique_traders=len(df_significant),
        unique_markets=0,  # market-level not primary here
        custom={
            "lookback_start": LOOKBACK_START,
            "min_resolved_markets": MIN_RESOLVED_MARKETS,
            "min_resolved_volume_usd": MIN_RESOLVED_VOLUME_USD,
            "resolution_confidence": RESOLUTION_CONFIDENCE,
            "base_filter_traders": len(df_traders),
            "significant_pnl_traders": len(df_significant),
            "consistent_skilled_traders": len(df_consistent),
            "consistency_rate": round(consistency_rate, 4),
            "significance_threshold_pnl_usd": round(float(significance_threshold_pnl), 2),
            "population_pnl_mean_usd": round(float(pnl_mean), 2),
            "population_pnl_std_usd": round(float(pnl_std), 2),
            "overlap_jaccard": overlap_stats.get("jaccard_index", None),
        },
    )

    return {
        "stage_id": STAGE_ID,
        "metrics": metrics.model_dump(exclude_none=True),
        "findings": {
            "summary": summary_stats,
            "top_10_skilled": df_top_skilled.head(10).to_dicts(),
            "hypothesis_supported": (
                len(df_significant) >= 200
                and consistency_rate >= 0.50
                and overlap_stats.get("jaccard_index", 1) < 0.5
            ),
            "hypothesis_details": {
                "target_skilled_range": "200-500",
                "actual_skilled_count": len(df_significant),
                "target_consistency_rate": 0.50,
                "actual_consistency_rate": round(consistency_rate, 4),
                "skilled_differ_from_volume": overlap_stats.get("jaccard_index", None),
            },
        },
    }


if __name__ == "__main__":
    root = Path(__file__).parent.parent.parent
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    summary = run(root, out)
    print(json.dumps(summary, indent=2, default=str))
