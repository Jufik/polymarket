"""
Comprehensive profitability distribution analysis across Polymarket traders.
Reads parquet data via DuckDB and produces a markdown report.
"""

from __future__ import annotations

import duckdb
from pathlib import Path
from textwrap import dedent

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data" / "derived"
OUT = BASE / "insights" / "01_profitability_distribution.md"

PNL = str(DATA / "trader_market_pnl.parquet")
MVF = str(DATA / "maker_volume_fractions.parquet")
MKT = str(DATA / "markets_resolved.parquet")

db = duckdb.connect()

# ── Helper ───────────────────────────────────────────────────────────────────

def fmt(v: float | int | None, decimals: int = 2, prefix: str = "$") -> str:
    """Format a number with commas and optional dollar prefix."""
    if v is None:
        return "N/A"
    if isinstance(v, int) or (isinstance(v, float) and v == int(v) and abs(v) < 1e15):
        if abs(v) >= 1e6:
            return f"{prefix}{v / 1e6:,.{decimals}f}M"
        if abs(v) >= 1e3:
            return f"{prefix}{v:,.0f}"
        return f"{prefix}{v:,.{decimals}f}"
    if abs(v) >= 1e9:
        return f"{prefix}{v / 1e9:,.{decimals}f}B"
    if abs(v) >= 1e6:
        return f"{prefix}{v / 1e6:,.{decimals}f}M"
    if abs(v) >= 1e3:
        return f"{prefix}{v:,.0f}"
    return f"{prefix}{v:,.{decimals}f}"


def pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.1f}%"


sections: list[str] = []

# ── 1. Overall Trader PnL Distribution ──────────────────────────────────────

print("Computing overall PnL distribution...")

trader_pnl = db.sql(f"""
    SELECT
        trader,
        SUM(market_pnl)    AS total_pnl,
        SUM(market_volume)  AS total_volume,
        SUM(trade_count)    AS total_trades,
        COUNT(*)            AS market_count
    FROM '{PNL}'
    GROUP BY trader
""")

overall = db.sql("""
    SELECT
        COUNT(*)                                  AS n_traders,
        COUNT(*) FILTER (WHERE total_pnl > 0)     AS n_profitable,
        100.0 * COUNT(*) FILTER (WHERE total_pnl > 0) / COUNT(*) AS pct_profitable,
        SUM(total_pnl)                             AS agg_pnl,
        AVG(total_pnl)                             AS mean_pnl,
        MEDIAN(total_pnl)                          AS median_pnl,
        PERCENTILE_CONT(0.01) WITHIN GROUP (ORDER BY total_pnl) AS p1,
        PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY total_pnl) AS p5,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY total_pnl) AS p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY total_pnl) AS p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY total_pnl) AS p75,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_pnl) AS p95,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY total_pnl) AS p99,
        STDDEV(total_pnl)                          AS std_pnl,
        MIN(total_pnl)                             AS min_pnl,
        MAX(total_pnl)                             AS max_pnl
    FROM trader_pnl
""").fetchone()

(n_traders, n_profitable, pct_profitable, agg_pnl, mean_pnl, median_pnl,
 p1, p5, p25, p50, p75, p95, p99, std_pnl, min_pnl, max_pnl) = overall  # type: ignore[misc]

sections.append(dedent(f"""\
## 1. Overall Trader PnL Distribution

| Metric | Value |
|--------|-------|
| Total traders | {n_traders:,} |
| Profitable traders | {n_profitable:,} ({pct(pct_profitable)}) |
| Aggregate PnL (all traders) | {fmt(agg_pnl)} |
| Mean PnL | {fmt(mean_pnl)} |
| Median PnL | {fmt(median_pnl)} |
| Std Dev PnL | {fmt(std_pnl)} |
| Min PnL | {fmt(min_pnl)} |
| Max PnL | {fmt(max_pnl)} |

**Percentile Distribution:**

| Percentile | PnL |
|------------|-----|
| p1 | {fmt(p1)} |
| p5 | {fmt(p5)} |
| p25 | {fmt(p25)} |
| p50 (median) | {fmt(p50)} |
| p75 | {fmt(p75)} |
| p95 | {fmt(p95)} |
| p99 | {fmt(p99)} |
"""))

# ── 2. Volume Tiers ────────────────────────────────────────────────────────

print("Computing volume tiers...")

tiers = db.sql("""
    SELECT
        CASE
            WHEN total_volume < 100       THEN '1. < $100'
            WHEN total_volume < 1000      THEN '2. $100 - $1K'
            WHEN total_volume < 10000     THEN '3. $1K - $10K'
            WHEN total_volume < 100000    THEN '4. $10K - $100K'
            WHEN total_volume < 1000000   THEN '5. $100K - $1M'
            ELSE                               '6. $1M+'
        END AS tier,
        COUNT(*)                                  AS n_traders,
        AVG(total_pnl)                             AS mean_pnl,
        MEDIAN(total_pnl)                          AS median_pnl,
        100.0 * COUNT(*) FILTER (WHERE total_pnl > 0) / COUNT(*) AS pct_profitable,
        SUM(total_volume)                          AS total_vol,
        SUM(total_pnl)                             AS total_pnl,
        AVG(market_count)                          AS avg_markets
    FROM trader_pnl
    GROUP BY tier
    ORDER BY tier
""").fetchall()

tier_rows = []
for (tier, n, mean, med, pct_prof, tvol, tpnl, avg_mkts) in tiers:
    label = tier.split(". ", 1)[1]
    tier_rows.append(
        f"| {label} | {n:,} | {fmt(mean)} | {fmt(med)} | {pct(pct_prof)} | {fmt(tvol)} | {fmt(tpnl)} | {avg_mkts:.1f} |"
    )

sections.append(dedent("""\
## 2. Volume Tiers

| Tier | Traders | Mean PnL | Median PnL | % Profitable | Total Volume | Total PnL | Avg Markets |
|------|---------|----------|------------|--------------|--------------|-----------|-------------|
""") + "\n".join(tier_rows) + "\n")

# ── 3. Win Rate Analysis ───────────────────────────────────────────────────

print("Computing win rates...")

win_rate_stats = db.sql(f"""
    WITH per_trader AS (
        SELECT
            trader,
            COUNT(*)                                         AS n_markets,
            COUNT(*) FILTER (WHERE market_pnl > 0)          AS n_wins,
            100.0 * COUNT(*) FILTER (WHERE market_pnl > 0)
                  / COUNT(*)                                 AS win_rate,
            SUM(market_pnl)                                   AS total_pnl
        FROM '{PNL}'
        GROUP BY trader
        HAVING COUNT(*) >= 5  -- at least 5 markets for meaningful win rate
    )
    SELECT
        COUNT(*)                                       AS n_traders,
        AVG(win_rate)                                   AS mean_wr,
        MEDIAN(win_rate)                                AS median_wr,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY win_rate) AS wr_p25,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY win_rate) AS wr_p75,
        -- among profitable traders
        AVG(win_rate) FILTER (WHERE total_pnl > 0)     AS mean_wr_profitable,
        MEDIAN(win_rate) FILTER (WHERE total_pnl > 0)  AS median_wr_profitable,
        -- among losing traders
        AVG(win_rate) FILTER (WHERE total_pnl <= 0)    AS mean_wr_losing,
        MEDIAN(win_rate) FILTER (WHERE total_pnl <= 0) AS median_wr_losing
    FROM per_trader
""").fetchone()

(n_wr, mean_wr, median_wr, wr_p25, wr_p75,
 mean_wr_prof, median_wr_prof, mean_wr_lose, median_wr_lose) = win_rate_stats  # type: ignore[misc]

# Win rate buckets
wr_buckets = db.sql(f"""
    WITH per_trader AS (
        SELECT
            trader,
            COUNT(*)                                         AS n_markets,
            100.0 * COUNT(*) FILTER (WHERE market_pnl > 0)
                  / COUNT(*)                                 AS win_rate,
            SUM(market_pnl) AS total_pnl
        FROM '{PNL}'
        GROUP BY trader
        HAVING COUNT(*) >= 5
    )
    SELECT
        CASE
            WHEN win_rate < 20 THEN '1. 0-20%'
            WHEN win_rate < 40 THEN '2. 20-40%'
            WHEN win_rate < 50 THEN '3. 40-50%'
            WHEN win_rate < 60 THEN '4. 50-60%'
            WHEN win_rate < 80 THEN '5. 60-80%'
            ELSE                    '6. 80-100%'
        END AS bucket,
        COUNT(*) AS n_traders,
        AVG(total_pnl) AS avg_pnl,
        100.0 * COUNT(*) FILTER (WHERE total_pnl > 0) / COUNT(*) AS pct_profitable
    FROM per_trader
    GROUP BY bucket
    ORDER BY bucket
""").fetchall()

wr_bucket_rows = []
for (bucket, n, avg_p, pct_prof) in wr_buckets:
    label = bucket.split(". ", 1)[1]
    wr_bucket_rows.append(f"| {label} | {n:,} | {fmt(avg_p)} | {pct(pct_prof)} |")

sections.append(dedent(f"""\
## 3. Win Rate Analysis

Traders with >= 5 markets traded ({n_wr:,} traders).

| Metric | All Traders | Profitable Traders | Losing Traders |
|--------|-------------|-------------------|----------------|
| Mean Win Rate | {pct(mean_wr)} | {pct(mean_wr_prof)} | {pct(mean_wr_lose)} |
| Median Win Rate | {pct(median_wr)} | {pct(median_wr_prof)} | {pct(median_wr_lose)} |

**Win Rate IQR:** p25 = {pct(wr_p25)}, p75 = {pct(wr_p75)}

**Win Rate Buckets:**

| Win Rate | Traders | Avg PnL | % Profitable |
|----------|---------|---------|--------------|
""") + "\n".join(wr_bucket_rows) + "\n")

# ── 4. Heavy Hitters (Top 50 by PnL) ──────────────────────────────────────

print("Computing heavy hitters...")

top50 = db.sql(f"""
    WITH per_trader AS (
        SELECT
            trader,
            SUM(market_pnl) AS total_pnl,
            SUM(market_volume) AS total_volume,
            SUM(trade_count) AS total_trades,
            COUNT(*) AS market_count,
            100.0 * COUNT(*) FILTER (WHERE market_pnl > 0)
                  / COUNT(*) AS win_rate
        FROM '{PNL}'
        GROUP BY trader
    )
    SELECT
        pt.trader,
        pt.total_pnl,
        pt.total_volume,
        pt.total_trades,
        pt.market_count,
        pt.win_rate,
        mv.mvf
    FROM per_trader pt
    LEFT JOIN '{MVF}' mv ON pt.trader = mv.trader
    ORDER BY pt.total_pnl DESC
    LIMIT 50
""").fetchall()

top50_rows = []
for i, (trader, tpnl, tvol, ttrades, mkts, wr, mvf_val) in enumerate(top50, 1):
    short_addr = trader[:8] + "..." + trader[-4:]
    mvf_str = f"{mvf_val:.1%}" if mvf_val is not None else "N/A"
    top50_rows.append(
        f"| {i} | `{short_addr}` | {fmt(tpnl)} | {fmt(tvol)} | {mkts:,} | {pct(wr)} | {mvf_str} | {ttrades:,} |"
    )

# Summary stats for top 50
top50_summary = db.sql(f"""
    WITH per_trader AS (
        SELECT
            trader,
            SUM(market_pnl) AS total_pnl,
            SUM(market_volume) AS total_volume,
            COUNT(*) AS market_count,
            100.0 * COUNT(*) FILTER (WHERE market_pnl > 0) / COUNT(*) AS win_rate
        FROM '{PNL}'
        GROUP BY trader
        ORDER BY total_pnl DESC
        LIMIT 50
    )
    SELECT
        SUM(total_pnl) AS agg_pnl,
        AVG(total_pnl) AS avg_pnl,
        AVG(win_rate) AS avg_wr,
        AVG(total_volume) AS avg_vol,
        AVG(market_count) AS avg_mkts,
        MIN(total_pnl) AS min_pnl
    FROM per_trader
""").fetchone()

(top_agg_pnl, top_avg_pnl, top_avg_wr, top_avg_vol, top_avg_mkts, top_min_pnl) = top50_summary  # type: ignore[misc]

# Also bottom 50
bottom50 = db.sql(f"""
    WITH per_trader AS (
        SELECT
            trader,
            SUM(market_pnl) AS total_pnl,
            SUM(market_volume) AS total_volume,
            COUNT(*) AS market_count,
            100.0 * COUNT(*) FILTER (WHERE market_pnl > 0) / COUNT(*) AS win_rate
        FROM '{PNL}'
        GROUP BY trader
        ORDER BY total_pnl ASC
        LIMIT 50
    )
    SELECT
        SUM(total_pnl) AS agg_pnl,
        AVG(total_pnl) AS avg_pnl,
        MIN(total_pnl) AS worst_pnl
    FROM per_trader
""").fetchone()

(bot_agg_pnl, bot_avg_pnl, bot_worst_pnl) = bottom50  # type: ignore[misc]

sections.append(dedent(f"""\
## 4. Heavy Hitters -- Top 50 by Total PnL

**Summary:** Top 50 traders collectively earned {fmt(top_agg_pnl)} (avg {fmt(top_avg_pnl)} each).
Average win rate: {pct(top_avg_wr)}. Average volume: {fmt(top_avg_vol)}.
Entry threshold (50th place): {fmt(top_min_pnl)}.

For comparison, the **bottom 50** traders collectively lost {fmt(bot_agg_pnl)} (worst: {fmt(bot_worst_pnl)}).

| Rank | Trader | Total PnL | Volume | Markets | Win Rate | MVF | Trades |
|------|--------|-----------|--------|---------|----------|-----|--------|
""") + "\n".join(top50_rows) + "\n")

# ── 5. The House Edge ──────────────────────────────────────────────────────

print("Computing house edge...")

house = db.sql(f"""
    SELECT
        SUM(market_pnl)    AS total_pnl,
        SUM(market_volume)  AS total_volume,
        COUNT(DISTINCT trader) AS n_traders,
        COUNT(*) AS n_positions,
        -- implied fee rate
        -SUM(market_pnl) / NULLIF(SUM(market_volume), 0) * 100 AS implied_fee_pct,
        -- PnL of profitable vs losing traders
        SUM(market_pnl) FILTER (WHERE market_pnl > 0) AS total_winners_pnl,
        SUM(market_pnl) FILTER (WHERE market_pnl <= 0) AS total_losers_pnl,
        COUNT(*) FILTER (WHERE market_pnl > 0) AS n_winning_positions,
        COUNT(*) FILTER (WHERE market_pnl <= 0) AS n_losing_positions
    FROM '{PNL}'
""").fetchone()

(h_pnl, h_vol, h_traders, h_positions, h_fee_pct,
 h_winners, h_losers, h_n_win, h_n_lose) = house  # type: ignore[misc]

# Trader-level PnL aggregation
house_trader = db.sql(f"""
    WITH per_trader AS (
        SELECT trader, SUM(market_pnl) AS total_pnl, SUM(market_volume) AS total_volume
        FROM '{PNL}'
        GROUP BY trader
    )
    SELECT
        SUM(total_pnl) FILTER (WHERE total_pnl > 0) AS winners_pnl,
        SUM(total_pnl) FILTER (WHERE total_pnl <= 0) AS losers_pnl,
        SUM(total_volume) FILTER (WHERE total_pnl > 0) AS winners_vol,
        SUM(total_volume) FILTER (WHERE total_pnl <= 0) AS losers_vol,
        COUNT(*) FILTER (WHERE total_pnl > 0) AS n_winners,
        COUNT(*) FILTER (WHERE total_pnl <= 0) AS n_losers
    FROM per_trader
""").fetchone()

(ht_winners_pnl, ht_losers_pnl, ht_winners_vol, ht_losers_vol,
 ht_n_winners, ht_n_losers) = house_trader  # type: ignore[misc]

sections.append(dedent(f"""\
## 5. The House Edge

### Position-Level (per trader-market pair)

| Metric | Value |
|--------|-------|
| Total PnL (all positions) | {fmt(h_pnl)} |
| Total Volume | {fmt(h_vol)} |
| Implied fee drag (= -PnL / Volume) | {pct(h_fee_pct)} |
| Winning positions | {h_n_win:,} ({fmt(h_winners)}) |
| Losing positions | {h_n_lose:,} ({fmt(h_losers)}) |
| Net (winners + losers) | {fmt(h_winners + h_losers if h_winners and h_losers else None)} |

### Trader-Level Aggregation

| Group | Count | Total PnL | Total Volume |
|-------|-------|-----------|--------------|
| Profitable traders | {ht_n_winners:,} | {fmt(ht_winners_pnl)} | {fmt(ht_winners_vol)} |
| Losing traders | {ht_n_losers:,} | {fmt(ht_losers_pnl)} | {fmt(ht_losers_vol)} |
| **Net** | **{ht_n_winners + ht_n_losers:,}** | **{fmt(ht_winners_pnl + ht_losers_pnl)}** | **{fmt(ht_winners_vol + ht_losers_vol)}** |

> The aggregate PnL is negative: the total amount lost by losing traders exceeds what winners gained.
> The difference represents the total fee/spread drag extracted by the platform and market makers.
"""))

# ── 6. Time Evolution ──────────────────────────────────────────────────────

print("Computing time evolution...")

monthly = db.sql(f"""
    SELECT
        DATE_TRUNC('month', first_trade AT TIME ZONE 'UTC') AS month,
        SUM(market_pnl)                                      AS total_pnl,
        SUM(market_volume)                                    AS total_volume,
        COUNT(DISTINCT trader)                                AS active_traders,
        COUNT(*)                                              AS positions,
        100.0 * COUNT(*) FILTER (WHERE market_pnl > 0) / COUNT(*) AS pct_winning_positions
    FROM '{PNL}'
    WHERE first_trade >= '2023-01-01'
    GROUP BY month
    ORDER BY month
""").fetchall()

monthly_rows = []
for (month, mpnl, mvol, mtraders, mpositions, m_pct_win) in monthly:
    month_str = month.strftime("%Y-%m")
    monthly_rows.append(
        f"| {month_str} | {fmt(mpnl)} | {fmt(mvol)} | {mtraders:,} | {mpositions:,} | {pct(m_pct_win)} |"
    )

sections.append(dedent("""\
## 6. Time Evolution (Monthly, 2023-2026)

| Month | Total PnL | Volume | Active Traders | Positions | % Winning |
|-------|-----------|--------|----------------|-----------|-----------|
""") + "\n".join(monthly_rows) + "\n")

# ── 7. Additional Insights ──────────────────────────────────────────────────

print("Computing additional insights...")

# Concentration: what % of total volume comes from top 1%, 5%, 10% of traders?
concentration = db.sql(f"""
    WITH per_trader AS (
        SELECT trader, SUM(market_volume) AS vol
        FROM '{PNL}'
        GROUP BY trader
    ),
    ranked AS (
        SELECT
            vol,
            NTILE(100) OVER (ORDER BY vol DESC) AS percentile_bucket
        FROM per_trader
    )
    SELECT
        percentile_bucket,
        SUM(vol) AS bucket_vol,
        COUNT(*) AS bucket_count
    FROM ranked
    GROUP BY percentile_bucket
    ORDER BY percentile_bucket
""").fetchall()

total_vol_all = sum(row[1] for row in concentration)
top1_vol = sum(row[1] for row in concentration if row[0] <= 1)
top5_vol = sum(row[1] for row in concentration if row[0] <= 5)
top10_vol = sum(row[1] for row in concentration if row[0] <= 10)

# Profitable traders who are also makers
maker_edge = db.sql(f"""
    WITH per_trader AS (
        SELECT trader, SUM(market_pnl) AS total_pnl, SUM(market_volume) AS total_volume
        FROM '{PNL}'
        GROUP BY trader
        HAVING SUM(market_volume) >= 10000  -- at least 10K volume for meaningful analysis
    )
    SELECT
        CASE
            WHEN mv.mvf >= 0.8 THEN '1. Heavy Maker (80%+)'
            WHEN mv.mvf >= 0.5 THEN '2. Maker-leaning (50-80%)'
            WHEN mv.mvf >= 0.2 THEN '3. Taker-leaning (20-50%)'
            ELSE                     '4. Heavy Taker (<20%)'
        END AS maker_tier,
        COUNT(*) AS n_traders,
        AVG(pt.total_pnl) AS avg_pnl,
        MEDIAN(pt.total_pnl) AS median_pnl,
        100.0 * COUNT(*) FILTER (WHERE pt.total_pnl > 0) / COUNT(*) AS pct_profitable,
        AVG(pt.total_volume) AS avg_vol
    FROM per_trader pt
    JOIN '{MVF}' mv ON pt.trader = mv.trader
    GROUP BY maker_tier
    ORDER BY maker_tier
""").fetchall()

maker_rows = []
for (tier, n, avg_p, med_p, pct_prof, avg_v) in maker_edge:
    label = tier.split(". ", 1)[1]
    maker_rows.append(
        f"| {label} | {n:,} | {fmt(avg_p)} | {fmt(med_p)} | {pct(pct_prof)} | {fmt(avg_v)} |"
    )

sections.append(dedent(f"""\
## 7. Additional Insights

### Volume Concentration

| Group | % of Total Volume |
|-------|-------------------|
| Top 1% of traders | {pct(100 * top1_vol / total_vol_all)} |
| Top 5% of traders | {pct(100 * top5_vol / total_vol_all)} |
| Top 10% of traders | {pct(100 * top10_vol / total_vol_all)} |

### Maker vs Taker Profitability (traders with >= $10K volume)

| Maker Tier | Traders | Avg PnL | Median PnL | % Profitable | Avg Volume |
|------------|---------|---------|------------|--------------|------------|
""") + "\n".join(maker_rows) + "\n")

# ── Assemble Report ─────────────────────────────────────────────────────────

header = dedent("""\
# Profitability Distribution Analysis

> Polymarket trader profitability across 70.9M trader-market positions.
> Data covers 2022-11 through 2026-01.

---

""")

report = header + "\n---\n\n".join(sections)

OUT.write_text(report)
print(f"\nReport written to {OUT}")
print(f"Report length: {len(report):,} characters, {report.count(chr(10)):,} lines")
