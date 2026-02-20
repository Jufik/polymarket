"""
Analyze the relationship between maker/taker behavior (MVF) and profitability.
Writes results to insights/03_mvf_patterns.md
"""

import json
import os
from pathlib import Path

import duckdb
import numpy as np

DATA = Path("data/derived")
OUT = Path("insights")
OUT.mkdir(exist_ok=True)

con = duckdb.connect()

# ──────────────────────────────────────────────────────────────
# 1. MVF Distribution
# ──────────────────────────────────────────────────────────────
print("=== 1. MVF Distribution ===")

dist = con.execute("""
    SELECT
        COUNT(*) AS total_traders,
        COUNT(*) FILTER (WHERE mvf < 0.05) AS pure_takers,
        COUNT(*) FILTER (WHERE mvf >= 0.05 AND mvf < 0.10) AS near_takers,
        COUNT(*) FILTER (WHERE mvf >= 0.10 AND mvf < 0.50) AS taker_leaning,
        COUNT(*) FILTER (WHERE mvf >= 0.50 AND mvf < 0.90) AS maker_leaning,
        COUNT(*) FILTER (WHERE mvf >= 0.90 AND mvf < 0.95) AS near_makers,
        COUNT(*) FILTER (WHERE mvf >= 0.95) AS pure_makers,
        AVG(mvf) AS mean_mvf,
        MEDIAN(mvf) AS median_mvf,
        STDDEV(mvf) AS std_mvf
    FROM read_parquet('{tp}')
""".format(tp=DATA / "maker_volume_fractions.parquet")).fetchdf()
print(dist.T)

# Histogram bins (20 bins)
hist = con.execute("""
    SELECT
        FLOOR(mvf * 20) / 20 AS bin_start,
        COUNT(*) AS count
    FROM read_parquet('{tp}')
    GROUP BY 1 ORDER BY 1
""".format(tp=DATA / "maker_volume_fractions.parquet")).fetchdf()
print("\nHistogram (20 bins):")
print(hist.to_string(index=False))

results = {"distribution": dist.to_dict(orient="records")[0]}
results["distribution"]["histogram"] = hist.to_dict(orient="list")

# ──────────────────────────────────────────────────────────────
# 2. MVF vs Profitability by Decile
# ──────────────────────────────────────────────────────────────
print("\n=== 2. MVF vs Profitability by Decile ===")

decile_stats = con.execute("""
    WITH trader_totals AS (
        SELECT
            p.trader,
            m.mvf,
            NTILE(10) OVER (ORDER BY m.mvf) AS mvf_decile,
            SUM(p.market_pnl) AS total_pnl,
            SUM(p.market_volume) AS total_volume,
            SUM(p.trade_count) AS total_trades,
            COUNT(DISTINCT p.condition_id) AS num_markets,
            -- Win rate: fraction of markets with positive PnL
            AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM read_parquet('{pnl}') p
        JOIN read_parquet('{mvf}') m ON p.trader = m.trader
        GROUP BY p.trader, m.mvf
    )
    SELECT
        mvf_decile,
        MIN(mvf) AS mvf_min,
        MAX(mvf) AS mvf_max,
        COUNT(*) AS num_traders,
        AVG(total_pnl) AS mean_pnl,
        MEDIAN(total_pnl) AS median_pnl,
        STDDEV(total_pnl) AS std_pnl,
        AVG(total_pnl) / NULLIF(STDDEV(total_pnl), 0) AS sharpe_like,
        AVG(win_rate) AS mean_win_rate,
        MEDIAN(win_rate) AS median_win_rate,
        AVG(total_volume) AS mean_volume,
        AVG(total_trades) AS mean_trades,
        AVG(num_markets) AS mean_markets,
        SUM(total_pnl) AS aggregate_pnl,
        -- PnL per dollar volume
        SUM(total_pnl) / NULLIF(SUM(total_volume), 0) AS pnl_per_dollar
    FROM trader_totals
    GROUP BY mvf_decile
    ORDER BY mvf_decile
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print(decile_stats.to_string(index=False))
results["decile_stats"] = decile_stats.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# 3. MVF vs Consistency (month-to-month)
# ──────────────────────────────────────────────────────────────
print("\n=== 3. MVF vs Consistency ===")

consistency = con.execute("""
    WITH monthly AS (
        SELECT
            p.trader,
            m.mvf,
            DATE_TRUNC('month', p.first_trade::TIMESTAMP) AS month,
            SUM(p.market_pnl) AS monthly_pnl
        FROM read_parquet('{pnl}') p
        JOIN read_parquet('{mvf}') m ON p.trader = m.trader
        GROUP BY p.trader, m.mvf, DATE_TRUNC('month', p.first_trade::TIMESTAMP)
    ),
    trader_consistency AS (
        SELECT
            trader,
            mvf,
            COUNT(DISTINCT month) AS active_months,
            AVG(monthly_pnl) AS mean_monthly_pnl,
            STDDEV(monthly_pnl) AS std_monthly_pnl,
            AVG(monthly_pnl) / NULLIF(STDDEV(monthly_pnl), 0) AS monthly_sharpe,
            -- Fraction of months profitable
            AVG(CASE WHEN monthly_pnl > 0 THEN 1.0 ELSE 0.0 END) AS profitable_month_frac
        FROM monthly
        GROUP BY trader, mvf
        HAVING COUNT(DISTINCT month) >= 3  -- at least 3 months active
    ),
    bucketed AS (
        SELECT
            *,
            CASE
                WHEN mvf < 0.1 THEN 'pure_taker'
                WHEN mvf < 0.3 THEN 'taker_leaning'
                WHEN mvf < 0.7 THEN 'mixed'
                WHEN mvf < 0.9 THEN 'maker_leaning'
                ELSE 'pure_maker'
            END AS mvf_bucket
        FROM trader_consistency
    )
    SELECT
        mvf_bucket,
        COUNT(*) AS num_traders,
        AVG(active_months) AS avg_active_months,
        AVG(mean_monthly_pnl) AS avg_mean_monthly_pnl,
        MEDIAN(mean_monthly_pnl) AS med_mean_monthly_pnl,
        AVG(monthly_sharpe) AS avg_monthly_sharpe,
        MEDIAN(monthly_sharpe) AS med_monthly_sharpe,
        AVG(profitable_month_frac) AS avg_profit_month_frac,
        MEDIAN(profitable_month_frac) AS med_profit_month_frac
    FROM bucketed
    GROUP BY mvf_bucket
    ORDER BY
        CASE mvf_bucket
            WHEN 'pure_taker' THEN 1
            WHEN 'taker_leaning' THEN 2
            WHEN 'mixed' THEN 3
            WHEN 'maker_leaning' THEN 4
            WHEN 'pure_maker' THEN 5
        END
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print(consistency.to_string(index=False))
results["consistency"] = consistency.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# 4. Market Maker Edge
# ──────────────────────────────────────────────────────────────
print("\n=== 4. Market Maker Edge (MVF > 0.9) ===")

maker_edge = con.execute("""
    WITH maker_traders AS (
        SELECT trader, mvf, total_volume
        FROM read_parquet('{mvf}')
        WHERE mvf > 0.9
    ),
    maker_pnl AS (
        SELECT
            p.trader,
            mt.mvf,
            mt.total_volume,
            SUM(p.market_pnl) AS total_pnl,
            COUNT(DISTINCT p.condition_id) AS num_markets,
            SUM(p.market_volume) AS sum_volume,
            SUM(p.trade_count) AS sum_trades,
            AVG(p.market_pnl) AS avg_pnl_per_market,
            AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM read_parquet('{pnl}') p
        JOIN maker_traders mt ON p.trader = mt.trader
        GROUP BY p.trader, mt.mvf, mt.total_volume
    )
    SELECT
        COUNT(*) AS num_makers,
        AVG(total_pnl) AS mean_total_pnl,
        MEDIAN(total_pnl) AS median_total_pnl,
        AVG(avg_pnl_per_market) AS mean_pnl_per_market,
        MEDIAN(avg_pnl_per_market) AS median_pnl_per_market,
        AVG(total_pnl / NULLIF(sum_volume, 0)) AS mean_pnl_per_dollar,
        MEDIAN(total_pnl / NULLIF(sum_volume, 0)) AS median_pnl_per_dollar,
        AVG(win_rate) AS mean_win_rate,
        MEDIAN(win_rate) AS median_win_rate,
        AVG(num_markets) AS mean_markets,
        AVG(sum_trades) AS mean_trades,
        SUM(total_pnl) AS aggregate_pnl,
        SUM(sum_volume) AS aggregate_volume
    FROM maker_pnl
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print(maker_edge.T)
results["maker_edge"] = maker_edge.to_dict(orient="records")[0]

# Top 20 makers by total PnL
top_makers = con.execute("""
    WITH maker_traders AS (
        SELECT trader, mvf, total_volume
        FROM read_parquet('{mvf}')
        WHERE mvf > 0.9
    )
    SELECT
        p.trader,
        mt.mvf,
        SUM(p.market_pnl) AS total_pnl,
        SUM(p.market_volume) AS total_volume,
        COUNT(DISTINCT p.condition_id) AS num_markets,
        SUM(p.trade_count) AS total_trades,
        AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
        SUM(p.market_pnl) / NULLIF(SUM(p.market_volume), 0) AS pnl_per_dollar
    FROM read_parquet('{pnl}') p
    JOIN maker_traders mt ON p.trader = mt.trader
    GROUP BY p.trader, mt.mvf
    ORDER BY total_pnl DESC
    LIMIT 20
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print("\nTop 20 Makers by PnL:")
print(top_makers.to_string(index=False))
results["top_makers"] = top_makers.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# 5. Informed Takers (MVF < 0.1)
# ──────────────────────────────────────────────────────────────
print("\n=== 5. Informed Takers (MVF < 0.1) ===")

taker_analysis = con.execute("""
    WITH taker_traders AS (
        SELECT trader, mvf, total_volume
        FROM read_parquet('{mvf}')
        WHERE mvf < 0.1
    ),
    taker_pnl AS (
        SELECT
            p.trader,
            tt.mvf,
            tt.total_volume,
            SUM(p.market_pnl) AS total_pnl,
            COUNT(DISTINCT p.condition_id) AS num_markets,
            SUM(p.market_volume) AS sum_volume,
            SUM(p.trade_count) AS sum_trades,
            AVG(p.market_pnl) AS avg_pnl_per_market,
            AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM read_parquet('{pnl}') p
        JOIN taker_traders tt ON p.trader = tt.trader
        GROUP BY p.trader, tt.mvf, tt.total_volume
    ),
    classified AS (
        SELECT
            *,
            CASE WHEN total_pnl > 0 THEN 'profitable' ELSE 'unprofitable' END AS profit_class
        FROM taker_pnl
    )
    SELECT
        profit_class,
        COUNT(*) AS num_traders,
        AVG(total_pnl) AS mean_pnl,
        MEDIAN(total_pnl) AS median_pnl,
        AVG(total_volume) AS mean_volume,
        MEDIAN(total_volume) AS median_volume,
        AVG(num_markets) AS mean_markets,
        MEDIAN(num_markets) AS median_markets,
        AVG(sum_trades) AS mean_trades,
        MEDIAN(sum_trades) AS median_trades,
        AVG(win_rate) AS mean_win_rate,
        MEDIAN(win_rate) AS median_win_rate,
        AVG(avg_pnl_per_market) AS mean_pnl_per_market,
        MEDIAN(avg_pnl_per_market) AS median_pnl_per_market,
        AVG(total_pnl / NULLIF(sum_volume, 0)) AS mean_pnl_per_dollar,
        MEDIAN(total_pnl / NULLIF(sum_volume, 0)) AS median_pnl_per_dollar
    FROM classified
    GROUP BY profit_class
    ORDER BY profit_class
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print(taker_analysis.to_string(index=False))
results["taker_analysis"] = taker_analysis.to_dict(orient="records")

# Volume buckets for takers
taker_volume_buckets = con.execute("""
    WITH taker_traders AS (
        SELECT trader, mvf, total_volume
        FROM read_parquet('{mvf}')
        WHERE mvf < 0.1
    ),
    taker_pnl AS (
        SELECT
            p.trader,
            tt.total_volume,
            SUM(p.market_pnl) AS total_pnl,
            COUNT(DISTINCT p.condition_id) AS num_markets,
            SUM(p.trade_count) AS sum_trades,
            AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM read_parquet('{pnl}') p
        JOIN taker_traders tt ON p.trader = tt.trader
        GROUP BY p.trader, tt.total_volume
    ),
    bucketed AS (
        SELECT
            *,
            CASE
                WHEN total_volume < 100 THEN '< $100'
                WHEN total_volume < 1000 THEN '$100-$1K'
                WHEN total_volume < 10000 THEN '$1K-$10K'
                WHEN total_volume < 100000 THEN '$10K-$100K'
                WHEN total_volume < 1000000 THEN '$100K-$1M'
                ELSE '> $1M'
            END AS volume_bucket,
            CASE
                WHEN total_volume < 100 THEN 1
                WHEN total_volume < 1000 THEN 2
                WHEN total_volume < 10000 THEN 3
                WHEN total_volume < 100000 THEN 4
                WHEN total_volume < 1000000 THEN 5
                ELSE 6
            END AS bucket_order
        FROM taker_pnl
    )
    SELECT
        volume_bucket,
        COUNT(*) AS num_traders,
        AVG(total_pnl) AS mean_pnl,
        MEDIAN(total_pnl) AS median_pnl,
        AVG(win_rate) AS mean_win_rate,
        AVG(CASE WHEN total_pnl > 0 THEN 1.0 ELSE 0.0 END) AS frac_profitable,
        AVG(num_markets) AS mean_markets,
        AVG(sum_trades) AS mean_trades
    FROM bucketed
    GROUP BY volume_bucket, bucket_order
    ORDER BY bucket_order
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print("\nTaker profitability by volume bucket:")
print(taker_volume_buckets.to_string(index=False))
results["taker_volume_buckets"] = taker_volume_buckets.to_dict(orient="records")

# Top 20 informed takers
top_takers = con.execute("""
    WITH taker_traders AS (
        SELECT trader, mvf, total_volume
        FROM read_parquet('{mvf}')
        WHERE mvf < 0.1
    )
    SELECT
        p.trader,
        tt.mvf,
        SUM(p.market_pnl) AS total_pnl,
        SUM(p.market_volume) AS total_volume,
        COUNT(DISTINCT p.condition_id) AS num_markets,
        SUM(p.trade_count) AS total_trades,
        AVG(CASE WHEN p.market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
        SUM(p.market_pnl) / NULLIF(SUM(p.market_volume), 0) AS pnl_per_dollar
    FROM read_parquet('{pnl}') p
    JOIN taker_traders tt ON p.trader = tt.trader
    GROUP BY p.trader, tt.mvf
    ORDER BY total_pnl DESC
    LIMIT 20
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print("\nTop 20 Informed Takers by PnL:")
print(top_takers.to_string(index=False))
results["top_takers"] = top_takers.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# 6. MVF Migration over time for top 100 profitable takers
# ──────────────────────────────────────────────────────────────
print("\n=== 6. MVF Migration ===")

# We need per-trade maker/taker info, but we only have aggregated MVF.
# Instead, we can look at early vs late trading activity patterns.
# Use first_trade/last_trade from trader_market_pnl to split into halves.

mvf_migration = con.execute("""
    WITH top_takers AS (
        SELECT m.trader, m.mvf AS overall_mvf, m.total_volume
        FROM read_parquet('{mvf}') m
        WHERE m.mvf < 0.1
        ORDER BY m.total_volume DESC
        LIMIT 500  -- broad pool for analysis
    ),
    -- Get trader temporal spread
    trader_span AS (
        SELECT
            p.trader,
            MIN(p.first_trade::TIMESTAMP) AS earliest,
            MAX(p.last_trade::TIMESTAMP) AS latest,
            SUM(p.market_pnl) AS total_pnl
        FROM read_parquet('{pnl}') p
        JOIN top_takers tt ON p.trader = tt.trader
        GROUP BY p.trader
        HAVING MAX(p.last_trade::TIMESTAMP) - MIN(p.first_trade::TIMESTAMP) > INTERVAL '90 days'
    ),
    -- Split into early half (first_trade before midpoint) and late half
    halves AS (
        SELECT
            p.trader,
            ts.earliest,
            ts.latest,
            ts.total_pnl,
            ts.earliest + (ts.latest - ts.earliest) / 2 AS midpoint,
            CASE
                WHEN p.first_trade::TIMESTAMP < ts.earliest + (ts.latest - ts.earliest) / 2
                THEN 'early'
                ELSE 'late'
            END AS half,
            p.condition_id,
            p.market_pnl,
            p.market_volume,
            p.trade_count
        FROM read_parquet('{pnl}') p
        JOIN trader_span ts ON p.trader = ts.trader
    ),
    half_stats AS (
        SELECT
            trader,
            total_pnl,
            half,
            SUM(market_pnl) AS half_pnl,
            SUM(market_volume) AS half_volume,
            SUM(trade_count) AS half_trades,
            COUNT(DISTINCT condition_id) AS half_markets
        FROM halves
        GROUP BY trader, total_pnl, half
    )
    SELECT
        'early' AS period,
        COUNT(DISTINCT trader) AS num_traders,
        AVG(half_pnl) AS mean_pnl,
        AVG(half_volume) AS mean_volume,
        AVG(half_trades) AS mean_trades,
        AVG(half_markets) AS mean_markets,
        AVG(half_pnl / NULLIF(half_volume, 0)) AS mean_pnl_per_dollar
    FROM half_stats WHERE half = 'early'
    UNION ALL
    SELECT
        'late' AS period,
        COUNT(DISTINCT trader) AS num_traders,
        AVG(half_pnl) AS mean_pnl,
        AVG(half_volume) AS mean_volume,
        AVG(half_trades) AS mean_trades,
        AVG(half_markets) AS mean_markets,
        AVG(half_pnl / NULLIF(half_volume, 0)) AS mean_pnl_per_dollar
    FROM half_stats WHERE half = 'late'
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print(mvf_migration.to_string(index=False))
results["mvf_migration"] = mvf_migration.to_dict(orient="records")

# Do the most profitable takers scale up?
taker_scaling = con.execute("""
    WITH top_profitable_takers AS (
        SELECT m.trader, m.mvf, m.total_volume
        FROM read_parquet('{mvf}') m
        JOIN (
            SELECT trader, SUM(market_pnl) AS total_pnl
            FROM read_parquet('{pnl}')
            GROUP BY trader
        ) tp ON m.trader = tp.trader
        WHERE m.mvf < 0.1
        ORDER BY tp.total_pnl DESC
        LIMIT 100
    ),
    trader_span AS (
        SELECT
            p.trader,
            MIN(p.first_trade::TIMESTAMP) AS earliest,
            MAX(p.last_trade::TIMESTAMP) AS latest
        FROM read_parquet('{pnl}') p
        JOIN top_profitable_takers tt ON p.trader = tt.trader
        GROUP BY p.trader
        HAVING MAX(p.last_trade::TIMESTAMP) - MIN(p.first_trade::TIMESTAMP) > INTERVAL '30 days'
    ),
    quarterly AS (
        SELECT
            p.trader,
            DATE_TRUNC('quarter', p.first_trade::TIMESTAMP) AS quarter,
            SUM(p.market_pnl) AS q_pnl,
            SUM(p.market_volume) AS q_volume,
            SUM(p.trade_count) AS q_trades,
            COUNT(DISTINCT p.condition_id) AS q_markets
        FROM read_parquet('{pnl}') p
        JOIN trader_span ts ON p.trader = ts.trader
        GROUP BY p.trader, DATE_TRUNC('quarter', p.first_trade::TIMESTAMP)
    )
    SELECT
        quarter,
        COUNT(DISTINCT trader) AS active_traders,
        AVG(q_pnl) AS mean_pnl,
        SUM(q_pnl) AS total_pnl,
        AVG(q_volume) AS mean_volume,
        SUM(q_volume) AS total_volume,
        AVG(q_trades) AS mean_trades,
        AVG(q_pnl / NULLIF(q_volume, 0)) AS mean_pnl_per_dollar
    FROM quarterly
    GROUP BY quarter
    ORDER BY quarter
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet")).fetchdf()

print("\nTop 100 profitable takers - quarterly evolution:")
print(taker_scaling.to_string(index=False))
results["taker_scaling"] = taker_scaling.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# 7. Optimal MVF for Copying (forward returns)
# ──────────────────────────────────────────────────────────────
print("\n=== 7. Optimal MVF for Copying ===")

# Split each trader's markets into "in-sample" (before midpoint) and "out-of-sample" (after)
# based on resolved_at date. Then check which MVF bucket's in-sample signal
# best predicts out-of-sample returns.

optimal_mvf = con.execute("""
    WITH trader_totals AS (
        SELECT
            p.trader,
            m.mvf,
            p.condition_id,
            p.market_pnl,
            p.market_volume,
            p.first_trade::TIMESTAMP AS first_trade,
            p.last_trade::TIMESTAMP AS last_trade,
            r.resolved_at::TIMESTAMP AS resolved_at
        FROM read_parquet('{pnl}') p
        JOIN read_parquet('{mvf}') m ON p.trader = m.trader
        JOIN read_parquet('{res}') r ON p.condition_id = r.condition_id
        WHERE r.resolved_at IS NOT NULL
    ),
    -- Use 2025-06-01 as cutoff: markets resolved before = training, after = test
    split AS (
        SELECT
            *,
            CASE WHEN resolved_at < TIMESTAMP '2025-06-01' THEN 'train' ELSE 'test' END AS split
        FROM trader_totals
    ),
    -- For each MVF bucket, compute train and test performance
    bucketed AS (
        SELECT
            *,
            CASE
                WHEN mvf < 0.05 THEN '0.00-0.05'
                WHEN mvf < 0.10 THEN '0.05-0.10'
                WHEN mvf < 0.20 THEN '0.10-0.20'
                WHEN mvf < 0.30 THEN '0.20-0.30'
                WHEN mvf < 0.50 THEN '0.30-0.50'
                WHEN mvf < 0.70 THEN '0.50-0.70'
                WHEN mvf < 0.90 THEN '0.70-0.90'
                WHEN mvf < 0.95 THEN '0.90-0.95'
                ELSE '0.95-1.00'
            END AS mvf_bucket,
            CASE
                WHEN mvf < 0.05 THEN 1
                WHEN mvf < 0.10 THEN 2
                WHEN mvf < 0.20 THEN 3
                WHEN mvf < 0.30 THEN 4
                WHEN mvf < 0.50 THEN 5
                WHEN mvf < 0.70 THEN 6
                WHEN mvf < 0.90 THEN 7
                WHEN mvf < 0.95 THEN 8
                ELSE 9
            END AS bucket_order
        FROM split
    ),
    -- Aggregate per trader per bucket per split
    trader_split_stats AS (
        SELECT
            trader,
            mvf_bucket,
            bucket_order,
            split,
            SUM(market_pnl) AS pnl,
            SUM(market_volume) AS volume,
            COUNT(*) AS num_markets,
            AVG(CASE WHEN market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM bucketed
        GROUP BY trader, mvf_bucket, bucket_order, split
    ),
    -- Per bucket per split
    bucket_split AS (
        SELECT
            mvf_bucket,
            bucket_order,
            split,
            COUNT(DISTINCT trader) AS num_traders,
            AVG(pnl) AS mean_pnl,
            MEDIAN(pnl) AS median_pnl,
            STDDEV(pnl) AS std_pnl,
            AVG(pnl) / NULLIF(STDDEV(pnl), 0) AS sharpe,
            AVG(win_rate) AS mean_win_rate,
            AVG(pnl / NULLIF(volume, 0)) AS mean_pnl_per_dollar,
            SUM(pnl) AS aggregate_pnl,
            SUM(volume) AS aggregate_volume
        FROM trader_split_stats
        GROUP BY mvf_bucket, bucket_order, split
    )
    SELECT *
    FROM bucket_split
    ORDER BY bucket_order, split
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet",
           res=DATA / "markets_resolved.parquet")).fetchdf()

print(optimal_mvf.to_string(index=False))
results["optimal_mvf"] = optimal_mvf.to_dict(orient="records")

# Also: identify best traders per MVF bucket by in-sample, then check OOS
# "Copyable" analysis: select top traders by train PnL, see if they persist in test
copy_analysis = con.execute("""
    WITH trader_resolved AS (
        SELECT
            p.trader,
            m.mvf,
            p.condition_id,
            p.market_pnl,
            p.market_volume,
            r.resolved_at::TIMESTAMP AS resolved_at,
            CASE WHEN r.resolved_at::TIMESTAMP < TIMESTAMP '2025-06-01' THEN 'train' ELSE 'test' END AS split
        FROM read_parquet('{pnl}') p
        JOIN read_parquet('{mvf}') m ON p.trader = m.trader
        JOIN read_parquet('{res}') r ON p.condition_id = r.condition_id
        WHERE r.resolved_at IS NOT NULL
    ),
    -- Trader-level performance per split
    trader_split AS (
        SELECT
            trader,
            mvf,
            split,
            SUM(market_pnl) AS pnl,
            SUM(market_volume) AS volume,
            COUNT(DISTINCT condition_id) AS num_markets,
            AVG(CASE WHEN market_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM trader_resolved
        GROUP BY trader, mvf, split
    ),
    -- Get traders who have BOTH train and test
    both_splits AS (
        SELECT trader, mvf
        FROM trader_split
        GROUP BY trader, mvf
        HAVING COUNT(DISTINCT split) = 2
    ),
    -- For each MVF range, pick top 50 by train PnL and measure test PnL
    train_ranked AS (
        SELECT
            ts.trader,
            bs.mvf,
            ts.pnl AS train_pnl,
            ts.volume AS train_volume,
            ts.win_rate AS train_win_rate,
            ts.num_markets AS train_markets,
            CASE
                WHEN bs.mvf < 0.1 THEN 'pure_taker'
                WHEN bs.mvf < 0.3 THEN 'taker_leaning'
                WHEN bs.mvf < 0.7 THEN 'mixed'
                WHEN bs.mvf < 0.9 THEN 'maker_leaning'
                ELSE 'pure_maker'
            END AS mvf_bucket,
            ROW_NUMBER() OVER (
                PARTITION BY
                    CASE
                        WHEN bs.mvf < 0.1 THEN 'pure_taker'
                        WHEN bs.mvf < 0.3 THEN 'taker_leaning'
                        WHEN bs.mvf < 0.7 THEN 'mixed'
                        WHEN bs.mvf < 0.9 THEN 'maker_leaning'
                        ELSE 'pure_maker'
                    END
                ORDER BY ts.pnl DESC
            ) AS rank_in_bucket
        FROM trader_split ts
        JOIN both_splits bs ON ts.trader = bs.trader
        WHERE ts.split = 'train'
    ),
    -- Join with test performance
    copy_results AS (
        SELECT
            tr.mvf_bucket,
            tr.rank_in_bucket,
            tr.trader,
            tr.mvf,
            tr.train_pnl,
            tr.train_volume,
            tr.train_win_rate,
            tr.train_markets,
            ts_test.pnl AS test_pnl,
            ts_test.volume AS test_volume,
            ts_test.win_rate AS test_win_rate,
            ts_test.num_markets AS test_markets
        FROM train_ranked tr
        JOIN trader_split ts_test ON tr.trader = ts_test.trader AND ts_test.split = 'test'
        WHERE tr.rank_in_bucket <= 50
    )
    SELECT
        mvf_bucket,
        COUNT(*) AS num_traders,
        AVG(train_pnl) AS mean_train_pnl,
        AVG(test_pnl) AS mean_test_pnl,
        MEDIAN(test_pnl) AS median_test_pnl,
        AVG(CASE WHEN test_pnl > 0 THEN 1.0 ELSE 0.0 END) AS frac_profitable_oos,
        AVG(test_win_rate) AS mean_test_win_rate,
        AVG(test_pnl / NULLIF(test_volume, 0)) AS mean_test_pnl_per_dollar,
        SUM(test_pnl) AS aggregate_test_pnl,
        SUM(test_volume) AS aggregate_test_volume,
        CORR(train_pnl, test_pnl) AS train_test_corr
    FROM copy_results
    GROUP BY mvf_bucket
    ORDER BY
        CASE mvf_bucket
            WHEN 'pure_taker' THEN 1
            WHEN 'taker_leaning' THEN 2
            WHEN 'mixed' THEN 3
            WHEN 'maker_leaning' THEN 4
            WHEN 'pure_maker' THEN 5
        END
""".format(pnl=DATA / "trader_market_pnl.parquet",
           mvf=DATA / "maker_volume_fractions.parquet",
           res=DATA / "markets_resolved.parquet")).fetchdf()

print("\nCopy analysis (top 50 per bucket by train PnL -> test performance):")
print(copy_analysis.to_string(index=False))
results["copy_analysis"] = copy_analysis.to_dict(orient="records")

# ──────────────────────────────────────────────────────────────
# Save raw results as JSON for reference
# ──────────────────────────────────────────────────────────────
import datetime

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return super().default(obj)

with open(OUT / "03_mvf_patterns_raw.json", "w") as f:
    json.dump(results, f, indent=2, cls=NumpyEncoder)

print(f"\nRaw results saved to {OUT / '03_mvf_patterns_raw.json'}")
print("Done!")
