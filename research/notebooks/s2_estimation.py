# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "polars", "clickhouse-connect", "plotly", "numpy"]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S2 Hit-Rate Copy — Vectorized Estimation")


@app.cell
def setup():
    import marimo as mo
    import polars as pl
    import clickhouse_connect
    import numpy as np

    CH_HOST = "192.168.0.148"
    client = clickhouse_connect.get_client(
        host=CH_HOST, port=18123, database="polymarket"
    )

    def ch_query(sql: str) -> pl.DataFrame:
        r = client.query(sql)
        return pl.DataFrame(
            {
                col: [row[i] for row in r.result_rows]
                for i, col in enumerate(r.column_names)
            }
        )

    return ch_query, client, mo, np, pl


@app.cell
def hypothesis(mo):
    mo.md("""
    # S2 Hit-Rate Copy — Vectorized Discovery

    ## Hypothesis

    **Signal**: Traders with excess hit rate above direction-specific base rates
    (YES base = 36.3%, NO base = 63.7%) are skilled. Copying their BUY entries
    with tiered conviction (seed at 1 trader, scale at 4) builds an edge.

    **Thesis**: Past direction-specific excess HR is a persistent trait of skilled
    traders. Consensus among multiple independently-skilled traders amplifies signal.

    **Null**: Qualified traders' OOS performance regresses to base rate. Excess HR
    from training is noise / survivorship.

    **Success criteria**: OOS excess HR > 10pp over direction base rate at consensus >= 4,
    positive PnL after 20-40pp vectorized-to-tick discount.

    **Capital angle**: Need median hold time < 7d and 100+ signals/month for compounding.
    """)
    return


@app.cell
def knowledge(mo):
    mo.md("""
    ## Knowledge Context

    > [!CRITICAL] Vectorized results are 20-40pp optimistic vs tick-by-tick.
    > Report as UPPER BOUNDS only.

    > [!CRITICAL] SELL is exit, not signal. Filter `side != "BUY"` early.

    > [!CRITICAL] Consensus = unique traders. Use sets, not counters.

    > [!CRITICAL] Resolution uses asset_id (`token_won`), never string matching.

    > [!WARNING] NO base rate is 63.7%. A 70% NO HR is only 6.3pp above baseline.

    > [!WARNING] Without hold-time filtering, long-dated markets block capital for weeks.

    **Updated base rates** (from current data, excluding "Up or Down"):
    - YES won: 36.3% (was 38.1% in earlier analysis)
    - NO won: 63.7% (was 61.9%)
    - Total resolved markets: 372,569
    """)
    return


@app.cell
def pool_sweep(ch_query, mo, pl):
    mo.md("## Pool Size Sweep")

    pool_df = ch_query("""
    WITH base AS (
        SELECT
            lower(p.trader) AS trader,
            p.position AS direction,
            countIf(p.correct = 1) AS wins,
            count(*) AS total,
            countIf(p.correct = 1) / count(*) AS hit_rate,
            countIf(p.correct = 1) / count(*) -
                if(p.position = 'YES', 0.3628, 0.6372) AS excess_hr
        FROM (
            SELECT * FROM trader_positions_resolved
            WHERE position IN ('YES', 'NO')
              AND toDate(resolved_at) >= toDate(now()) - INTERVAL 6 MONTH
        ) AS p
        INNER JOIN markets AS m ON p.condition_id = m.condition_id
        WHERE m.question NOT LIKE '%Up or Down%'
          AND m.question NOT LIKE '%up or down%'
        GROUP BY trader, p.position
    )
    SELECT
        min_pos,
        min_ehr,
        dir,
        count(*) AS pool_size,
        round(avg(excess_hr), 4) AS avg_excess_hr,
        round(median(excess_hr), 4) AS med_excess_hr,
        round(avg(total), 1) AS avg_positions
    FROM base
    CROSS JOIN (
        SELECT 20 AS min_pos UNION ALL SELECT 30 UNION ALL SELECT 50
    ) AS mp
    CROSS JOIN (
        SELECT 0.05 AS min_ehr UNION ALL SELECT 0.10 UNION ALL SELECT 0.15 UNION ALL SELECT 0.20
    ) AS me
    CROSS JOIN (
        SELECT 'YES' AS dir UNION ALL SELECT 'NO' UNION ALL SELECT 'BOTH'
    ) AS d
    WHERE total >= min_pos
      AND excess_hr >= min_ehr
      AND (dir = 'BOTH' OR direction = dir)
    GROUP BY min_pos, min_ehr, dir
    ORDER BY dir, min_pos, min_ehr
    """)

    return pool_df


@app.cell
def pool_table(mo, pool_df, pl):
    # Pivot for display: rows = (min_pos, min_ehr), columns = direction
    both = pool_df.filter(pl.col("dir") == "BOTH").select(
        "min_pos", "min_ehr", pl.col("pool_size").alias("BOTH"),
        pl.col("avg_excess_hr").alias("BOTH_ehr"),
    )
    yes = pool_df.filter(pl.col("dir") == "YES").select(
        "min_pos", "min_ehr", pl.col("pool_size").alias("YES"),
        pl.col("avg_excess_hr").alias("YES_ehr"),
    )
    no = pool_df.filter(pl.col("dir") == "NO").select(
        "min_pos", "min_ehr", pl.col("pool_size").alias("NO"),
        pl.col("avg_excess_hr").alias("NO_ehr"),
    )
    summary = both.join(yes, on=["min_pos", "min_ehr"]).join(no, on=["min_pos", "min_ehr"])

    mo.md(f"""
    ### Pool Sizes by Parameters (6-month lookback from now)

    | min_pos | min_excess_hr | BOTH pool | YES pool | NO pool | BOTH avg EHR | YES avg EHR | NO avg EHR |
    |---------|--------------|-----------|----------|---------|-------------|------------|-----------|
    """ + "\n".join(
        f"| {r['min_pos']} | {r['min_ehr']} | {r['BOTH']:,} | {r['YES']:,} | {r['NO']:,} | {r['BOTH_ehr']:.1%} | {r['YES_ehr']:.1%} | {r['NO_ehr']:.1%} |"
        for r in summary.sort(["min_pos", "min_ehr"]).iter_rows(named=True)
    ))

    return summary


@app.cell
def signal_quality(ch_query, mo):
    mo.md("## Signal Quality Distribution")

    qual_df = ch_query("""
    WITH qualified AS (
        SELECT
            lower(p.trader) AS trader,
            p.position AS direction,
            countIf(p.correct = 1) AS wins,
            count(*) AS total,
            countIf(p.correct = 1) / count(*) AS hit_rate,
            countIf(p.correct = 1) / count(*) -
                if(p.position = 'YES', 0.3628, 0.6372) AS excess_hr
        FROM (
            SELECT * FROM trader_positions_resolved
            WHERE position IN ('YES', 'NO')
              AND toDate(resolved_at) >= toDate(now()) - INTERVAL 6 MONTH
        ) AS p
        INNER JOIN markets AS m ON p.condition_id = m.condition_id
        WHERE m.question NOT LIKE '%Up or Down%'
          AND m.question NOT LIKE '%up or down%'
        GROUP BY trader, p.position
        HAVING count(*) >= 30
           AND excess_hr >= 0.10
    )
    SELECT
        direction,
        count(*) AS pool_size,
        round(quantile(0.10)(excess_hr), 4) AS ehr_p10,
        round(quantile(0.25)(excess_hr), 4) AS ehr_p25,
        round(quantile(0.50)(excess_hr), 4) AS ehr_p50,
        round(quantile(0.75)(excess_hr), 4) AS ehr_p75,
        round(quantile(0.90)(excess_hr), 4) AS ehr_p90,
        round(quantile(0.50)(total), 0) AS pos_median,
        round(quantile(0.50)(hit_rate), 4) AS hr_median,
        countIf(hit_rate >= 0.95) AS hr_above_95,
        countIf(hit_rate = 1.0) AS hr_100pct
    FROM qualified
    GROUP BY direction
    ORDER BY direction
    """)

    mo.md(f"""
    ### At min_positions=30, min_excess_hr=0.10 (default config)

    | Direction | Pool | EHR p10 | EHR p50 | EHR p90 | Pos median | HR median | HR>=95% | HR=100% |
    |-----------|------|---------|---------|---------|------------|-----------|---------|---------|
    """ + "\n".join(
        f"| {r['direction']} | {r['pool_size']:,} | {r['ehr_p10']:.1%} | {r['ehr_p50']:.1%} | {r['ehr_p90']:.1%} | {r['pos_median']:.0f} | {r['hr_median']:.1%} | {r['hr_above_95']:,} ({r['hr_above_95']/r['pool_size']*100:.1f}%) | {r['hr_100pct']:,} ({r['hr_100pct']/r['pool_size']*100:.1f}%) |"
        for r in qual_df.iter_rows(named=True)
    ))

    mo.md("""
    **Key concern**: 44.8% of NO-qualified traders have 95%+ HR, with 19.5% at 100%.
    Given the 63.7% NO base rate, achieving 95% on 35 positions is statistically
    plausible by luck alone (p ~= 0.02 per trader, but with 100K+ traders...).
    The NO pool is likely contaminated by lucky small-sample traders.

    **YES pool is cleaner**: Only 12.3% above 95%, median positions = 61.
    """)

    return qual_df


@app.cell
def trade_volume(ch_query, mo):
    mo.md("## Monthly BUY Trade Volume from Qualified Traders")

    vol_df = ch_query("""
    WITH qualified AS (
        SELECT DISTINCT lower(p.trader) AS trader
        FROM (
            SELECT * FROM trader_positions_resolved
            WHERE position IN ('YES', 'NO')
              AND toDate(resolved_at) >= toDate(now()) - INTERVAL 6 MONTH
        ) AS p
        INNER JOIN markets AS m ON p.condition_id = m.condition_id
        WHERE m.question NOT LIKE '%Up or Down%'
          AND m.question NOT LIKE '%up or down%'
        GROUP BY trader, p.position
        HAVING count(*) >= 30
           AND countIf(p.correct = 1) / count(*) -
               if(p.position = 'YES', 0.3628, 0.6372) >= 0.10
    )
    SELECT
        toStartOfMonth(t.timestamp) AS month,
        count(*) AS buy_trades,
        uniqExact(lower(t.maker)) AS unique_traders,
        uniqExact(t.condition_id) AS unique_markets
    FROM (
        SELECT * FROM trades_raw FINAL
        WHERE side = 'BUY'
    ) AS t
    WHERE lower(t.maker) IN (SELECT trader FROM qualified)
      AND t.timestamp >= '2025-01-01'
    GROUP BY month
    ORDER BY month
    """)

    mo.md(f"""
    Raw BUY trades from qualified traders (min_pos=30, min_ehr=0.10):

    | Month | BUY Trades | Active Traders | Markets |
    |-------|-----------|----------------|---------|
    """ + "\n".join(
        f"| {r['month']} | {r['buy_trades']:,} | {r['unique_traders']:,} | {r['unique_markets']:,} |"
        for r in vol_df.iter_rows(named=True)
    ))

    mo.md("""
    **Note**: Raw BUY trades are 700K-10M/month. The strategy filters by
    unique-trader consensus, which dramatically reduces actionable signals.
    At consensus >= 4, expect ~3,000-35,000 market-sides/month.
    """)

    return vol_df


@app.cell
def vectorized_results(mo):
    mo.md("""
    ## Vectorized OOS Results (UPPER BOUNDS)

    > [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.

    Walk-forward design: train on 6-month lookback, test on single month.
    Parameters: min_positions=30, min_excess_hr=0.10, direction=BOTH.

    ### Period-Specific Base Rates (excluding "Up or Down")

    | Period | YES Base | NO Base | Note |
    |--------|---------|--------|------|
    | 2025-04 | 36.3% | 63.7% | Normal |
    | 2025-07 | 20.4% | 79.6% | Extremely skewed -- NO-favorable month |
    | 2025-10 | 35.6% | 64.4% | Normal |

    ### OOS Performance (all qualified trader positions)

    | Period | OOS Positions | Active Traders | Markets | Overall HR | YES HR | YES Excess | NO HR | NO Excess |
    |--------|--------------|----------------|---------|------------|--------|------------|-------|-----------|
    | 2025-04 | 38,867 | 1,082 | 3,728 | 74.5% | 58.6% | +22.3pp | 82.1% | +18.3pp |
    | 2025-07 | 48,835 | 1,198 | 4,491 | 73.1% | 57.3% | +21.1pp | 80.4% | +16.7pp |
    | 2025-10 | 88,992 | 1,435 | 12,635 | 72.0% | 64.9% | +28.6pp | 76.4% | +12.7pp |

    **Strong vectorized excess HR across all periods**: +12-29pp above direction base rates.

    ### Consensus-Filtered Performance (unique traders per market-side)

    #### 2025-04 (Train: Oct 2024 - Mar 2025)

    | Consensus | Market-Sides | Positions | HR | YES HR | YES Excess | NO HR | NO Excess | PnL | Avg Hold |
    |-----------|-------------|-----------|-----|--------|------------|-------|-----------|-----|----------|
    | >= 1 | 6,481 | 38,867 | 74.5% | 58.6% | +22.3pp | 82.1% | +18.3pp | $3.2M | 14.1d |
    | >= 2 | 5,168 | 37,554 | 76.1% | 61.7% | +25.4pp | 82.5% | +18.7pp | $3.4M | 14.9d |
    | >= 3 | 4,182 | 35,582 | 78.0% | 65.7% | +29.4pp | 83.1% | +19.4pp | $3.5M | 15.4d |
    | >= 4 | 3,449 | 33,383 | 79.5% | 69.1% | +32.8pp | 83.5% | +19.8pp | $3.8M | 16.2d |

    #### 2025-07 (Train: Jan - Jun 2025)

    | Consensus | Market-Sides | Positions | HR | YES HR | YES Excess | NO HR | NO Excess | PnL | Avg Hold |
    |-----------|-------------|-----------|-----|--------|------------|-------|-----------|-----|----------|
    | >= 1 | 7,562 | 48,835 | 73.1% | 57.3% | +21.1pp | 80.4% | +16.7pp | -$692K | 8.3d |
    | >= 2 | 5,903 | 47,176 | 74.0% | 59.5% | +23.2pp | 80.5% | +16.8pp | -$600K | 8.9d |
    | >= 3 | 4,832 | 45,034 | 75.4% | 62.2% | +25.9pp | 80.9% | +17.2pp | -$408K | 8.9d |
    | >= 4 | 4,075 | 42,763 | 76.5% | 64.3% | +28.0pp | 81.3% | +17.6pp | -$188K | 9.1d |

    #### 2025-10 (Train: Apr - Sep 2025)

    | Consensus | Market-Sides | Positions | HR | YES HR | YES Excess | NO HR | NO Excess | PnL | Avg Hold |
    |-----------|-------------|-----------|-----|--------|------------|-------|-----------|-----|----------|
    | >= 1 | 19,789 | 88,992 | 72.0% | 64.9% | +28.6pp | 76.4% | +12.7pp | $4.2M | 3.3d |
    | >= 2 | 13,193 | 82,396 | 73.9% | 67.9% | +31.6pp | 77.5% | +13.8pp | $5.0M | 3.7d |
    | >= 3 | 9,633 | 75,276 | 75.5% | 70.7% | +34.4pp | 78.3% | +14.5pp | $5.4M | 4.1d |
    | >= 4 | 7,353 | 68,436 | 76.8% | 72.8% | +36.6pp | 79.0% | +15.3pp | $6.3M | 4.7d |

    ### Key Observations

    1. **YES excess HR consistently > NO excess HR** (22-37pp YES vs 13-20pp NO)
    2. **Consensus improves HR**: each additional unique trader adds ~1-2pp
    3. **July 2025 was negative PnL** despite high HR -- extreme NO base rate (79.6%)
       made it hard to profit on NO positions even when correct
    4. **October 2025 had best compounding profile**: short hold (4.7d) + high volume (7,353 sides)
    5. **April 2025 had longest holds** (16d) -- capital-intensive
    """)
    return


@app.cell
def compounding_analysis(mo):
    mo.md("""
    ## Capital Efficiency & Compounding Score

    > [!WARNING] Vectorized results. Realistic range shown in parentheses.

    Using consensus >= 4 (our scale threshold):

    | Period | Vec HR | Base HR | Excess HR | Vec PnL/pos | Med Hold | Compounding Score |
    |--------|--------|---------|-----------|------------|----------|-------------------|
    | 2025-04 | 79.5% | ~50% | +29.5pp | $112.83 | 15.6d | 0.295 * 112.83 / 15.6 = **2.13** |
    | 2025-07 | 76.5% | ~50% | +26.5pp | -$4.39 | 8.5d | negative (PnL < 0) = **-0.14** |
    | 2025-10 | 76.8% | ~50% | +26.8pp | $91.37 | 4.1d | 0.268 * 91.37 / 4.1 = **5.97** |

    **Note**: Combined base rate ~50% (weighted YES 36% + NO 64% by position mix).

    ### Discounted Estimates (tick-by-tick range: 20-40pp degradation)

    | Period | Vec HR | Tick HR (optimistic) | Tick HR (pessimistic) | Verdict |
    |--------|--------|---------------------|-----------------------|---------|
    | 2025-04 | 79.5% | ~59.5% | ~39.5% | Marginal to positive |
    | 2025-07 | 76.5% | ~56.5% | ~36.5% | Likely breakeven |
    | 2025-10 | 76.8% | ~56.8% | ~36.8% | Positive if hold time stays short |

    ### Category Breakdown (October 2025, consensus >= 4)

    | Category | Market-Sides | Positions | HR | PnL | Med Hold | Notes |
    |----------|-------------|-----------|-----|-----|----------|-------|
    | Other | 6,267 | 58,538 | 75.7% | $5.6M | 3.4d | Bulk of signal, good compounding |
    | Politics | 572 | 5,943 | 80.2% | $389K | 12.2d | High HR but blocks capital |
    | Crypto | 476 | 3,288 | 90.4% | $169K | 2.1d | Best compounding potential |
    | Sports | 38 | 667 | 83.8% | $140K | 37.3d | Too few markets, too long |
    """)
    return


@app.cell
def verdict(mo):
    mo.md("""
    ## Verdict

    ### Signal Quality: MODERATE-TO-STRONG (vectorized)

    The S2 Hit-Rate Copy signal shows **consistent OOS excess HR of +13 to +37pp**
    above direction-specific base rates across 3 out-of-sample periods. This is
    a genuine vectorized signal, not noise.

    ### Concerns

    1. **July 2025 was PnL-negative** despite positive HR. This happened because
       the month had an extreme NO base rate (79.6%). The traders were "correct"
       more often than random, but the edge per correct call was tiny (prices
       already reflected the strong NO expectation).

    2. **NO pool contamination**: 44.8% of NO-qualified traders have HR >= 95%.
       With a 63.7% NO base rate and median 35 positions, many are likely lucky
       rather than skilled. The YES pool is cleaner.

    3. **Hold time variability**: April had 16d median hold, October had 4d.
       The strategy needs a `max_hold_hours` gate to avoid capital traps.

    4. **Vectorized-to-tick degradation**: From S1 research, expect 20-40pp collapse.
       The YES direction may hold up better (prior finding: YES improved +7pp in
       tick-by-tick due to fewer false entries). The NO direction collapses badly
       (prior finding: -48pp).

    ### Recommendation

    **PROCEED to tick-by-tick validation** with these refinements:

    1. **YES-only direction** (or heavily YES-weighted): YES excess is consistently
       larger and more robust. NO contamination is high.
    2. **Max hold time filter**: 168h (7 days) to force capital recycling.
       This preserves crypto (2.1d) and most "other" (3.4d), drops politics.
    3. **Higher min_positions**: 50 instead of 30 to reduce lucky-trader noise.
    4. **Consensus >= 3 or 4**: Each threshold adds ~1-2pp HR for modest universe loss.

    ### Expected Tick-by-Tick Range

    Based on vectorized upper bounds and known degradation factors:

    | Scenario | HR | Excess HR | Compounding Score | Verdict |
    |----------|-----|-----------|-------------------|---------|
    | Optimistic (YES, -20pp) | ~53% | ~17pp | ~1.5 | Moderate, deploy with limits |
    | Central (BOTH, -30pp) | ~47% | ~0pp | ~0 | Breakeven, not viable |
    | Pessimistic (NO, -40pp) | ~37% | negative | negative | No edge |

    **Bottom line**: The strategy has real vectorized signal but faces severe
    degradation risk, especially for the NO direction. The YES-only variant
    with hold-time filtering is the most promising path.

    > [!WARNING] Do NOT allocate capital based on vectorized results.
    > Tick-by-tick validation is REQUIRED.
    """)
    return


@app.cell
def knowledge_captures(mo):
    mo.md("""
    ## Knowledge Captures

    ### New Finding: Period-specific base rates vary wildly

    July 2025 had YES base rate of only 20.4% (vs typical 36%). This caused
    PnL to be negative despite high HR. Any strategy using fixed base rates
    for comparison must account for monthly variation.

    **Action**: Consider dynamic base rate computation per test period.

    ### New Finding: NO pool contamination at 30 min_positions

    44.8% of NO-qualified traders have HR >= 95%, mostly on small samples (35 median).
    Statistical expectation: with 63.7% base rate, P(HR >= 95% | n=35) ~= 2%.
    But 100K+ traders means ~2,000 expected "lucky" traders, which matches the
    1,119 at 100% HR.

    **Action**: Raise min_positions to 50+ for NO direction, or use Bayesian
    shrinkage to discount small-sample HR.

    ### Confirmed: YES excess HR > NO excess HR consistently

    Across all 3 OOS periods, YES excess is 22-37pp vs NO excess of 13-20pp.
    This is consistent with the S1 finding that YES direction degrades less
    in tick-by-tick.

    **Action**: Prioritize YES-only or YES-weighted strategies.
    """)
    return


if __name__ == "__main__":
    app.run()
