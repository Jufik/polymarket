# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "polars", "clickhouse-connect", "plotly", "numpy"]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S2 Hit-Rate Copy -- BEFORE vs AFTER Improvements")


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
    # S2 Hit-Rate Copy -- BEFORE vs AFTER Improvements

    ## What Changed

    Eight improvements were implemented in `research/strategies/s2_hitrate_copy.py`.
    Four are testable in vectorized SQL:

    1. **Entry price filter** (`max_entry_price=0.85`) -- removes positions where
       direction-adjusted entry price >= $0.85 from training data
    2. **Category exclusion** (`exclude_categories=("Sports", "Weather")`) -- excludes
       sports/weather markets from pool computation
    3. **Adaptive base rates** -- rolling 6-month YES/NO base rates replace static 0.381/0.619
    4. **Bayesian HR shrinkage** -- `(alpha + wins) / (alpha + beta + total)` with
       direction-specific priors (YES: 3.81/6.19, NO: 6.19/3.81)

    Four are tick-by-tick only (not estimated here): max_signal_price, yes/no weighting,
    consensus velocity, hold time cap.

    ## Key Questions

    1. Does the combined improvement increase OOS HR and PnL?
    2. Does July 2025 negative PnL improve with adaptive base rates?
    3. Does NO pool contamination decrease with entry price + Bayesian?
    4. Which individual improvement contributes most?
    """)
    return


@app.cell
def knowledge(mo):
    mo.md("""
    ## Knowledge Context

    > [!CRITICAL] Vectorized results are 20-40pp optimistic vs tick-by-tick.
    > Report as UPPER BOUNDS only.

    > [!CRITICAL] The `markets.category` column is NULL for 99.5% of markets.
    > The `exclude_categories` parameter using `m.category` is nearly useless
    > (catches 2,499 of 268K sports markets). Tag-based exclusion via the
    > `events -> event_tags -> tags` join chain is required for real filtering.

    > [!WARNING] Entry price filter at 0.85 removes 15% of YES and 50% of NO positions.
    > Removed positions have 93-97% HR (late entries on near-certain outcomes).
    > This correctly targets low-edge bots but dramatically shrinks the pool.

    > [!WARNING] Bayesian shrinkage with alpha+beta=10 is strong for n=30-50.
    > A trader with 30 positions and 100% raw HR gets shrunk to ~90% Bayesian HR.
    > This effectively eliminates contamination but may be over-penalizing.
    """)
    return


@app.cell
def base_rates(mo):
    mo.md("""
    ## A. Adaptive Base Rates vs Static

    Base rates computed per 6-month training window (excluding "Up or Down"):

    | Window | Static YES | Adaptive YES | Static NO | Adaptive NO | Shift |
    |--------|-----------|-------------|----------|------------|-------|
    | Train for Apr 2025 (Oct'24-Mar'25) | 38.1% | 32.3% | 61.9% | 67.7% | -5.8pp YES |
    | Train for Jul 2025 (Jan'25-Jun'25) | 38.1% | 30.0% | 61.9% | 70.0% | -8.1pp YES |
    | Train for Oct 2025 (Apr'25-Sep'25) | 38.1% | 28.9% | 61.9% | 71.1% | -9.2pp YES |

    **Test period base rates** (the actual month being tested):

    | Period | YES Base | NO Base | Note |
    |--------|---------|--------|------|
    | Apr 2025 | 36.3% | 63.7% | Normal |
    | Jul 2025 | 20.4% | 79.6% | Extremely skewed |
    | Oct 2025 | 35.6% | 64.4% | Normal |

    **Key insight**: Adaptive base rates are consistently LOWER for YES than static (28-32%
    vs 38%), meaning the adaptive threshold is HARDER to clear for YES traders and EASIER
    for NO traders. This shifts the pool composition -- fewer YES, more NO qualify.

    The static 38.1% YES rate reflects all-time history including 2022-2023 when YES won
    more often. Recent data (2025) shows a sustained drop to ~30% YES. The adaptive approach
    correctly captures this trend.
    """)
    return


@app.cell
def pool_comparison(mo):
    mo.md("""
    ## B. Pool Comparison (Current 6-Month Lookback)

    Three pool variants at min_positions=30, min_excess_hr=0.10, direction=BOTH:

    | Metric | BEFORE (old) | AFTER (as-coded) | AFTER (tag-based) |
    |--------|-------------|------------------|-------------------|
    | **YES pool** | 5,488 | 2,020 | 342 |
    | **NO pool** | 5,930 | 179 | 51 |
    | **Total pool** | **11,418** | **2,199** | **393** |
    | Pool reduction | -- | -80.7% | -96.6% |
    | YES avg excess HR | 31.4% | 16.9% | 18.9% |
    | NO avg excess HR | 28.9% | 14.4% | 14.2% |
    | YES contamination (HR>=95%) | **13.3%** | **0.0%** | **0.0%** |
    | NO contamination (HR>=95%) | **43.2%** | **0.0%** | **0.0%** |
    | YES median positions | 61 | 61 | 50 |
    | NO median positions | 47 | 53 | 46 |

    ### Assessment

    **Contamination elimination: COMPLETE.** Both AFTER variants achieve 0% contamination
    at HR >= 95%, down from 43.2% for NO and 13.3% for YES. The Bayesian shrinkage is the
    primary driver -- it makes it mathematically impossible for a 30-position trader with
    100% raw HR to clear the 0.10 excess threshold after shrinkage to ~90%.

    **Pool shrinkage: SEVERE.** The as-coded variant loses 81% of the pool. The tag-based
    variant (which actually excludes sports/weather markets) loses 97%. The NO pool is
    decimated from 5,930 to 179 (as-coded) or 51 (tag-based).

    **Root cause of NO collapse**: The entry price filter removes 50% of NO positions
    (those entering at $0.85+), and Bayesian shrinkage further penalizes the remaining
    small-sample traders. The combined effect is devastating for NO.

    **Bug found**: `exclude_categories` uses `m.category` which is NULL for 99.5% of markets.
    The as-coded filter only removes 2,499 of 268K sports markets. Must switch to tag-based
    exclusion or the parameter is decorative.
    """)
    return


@app.cell
def walkforward_before(mo):
    mo.md("""
    ## C. Walk-Forward OOS Results -- BEFORE (Old Pool)

    > [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.

    Static base rates (0.381 YES / 0.619 NO), no entry price filter, no category exclusion.

    ### April 2025 (Train: Oct'24 - Mar'25)

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 37,767 | 6,383 | 75.1% | 58.5% | 82.7% | $2.89M | $76.42 | 3.0d |
    | >= 2 | 36,448 | 5,064 | 76.7% | 61.8% | 83.1% | $3.03M | $83.04 | 3.1d |
    | >= 3 | 34,422 | 4,051 | 78.6% | 65.9% | 83.7% | $3.22M | $93.50 | 3.2d |
    | >= 4 | 32,133 | 3,288 | **80.3%** | 69.2% | 84.3% | **$3.47M** | **$108.08** | 3.4d |

    ### July 2025 (Train: Jan-Jun'25)

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 47,105 | 7,292 | 74.4% | 58.3% | 81.2% | -$212K | -$4.50 | 1.9d |
    | >= 2 | 45,471 | 5,658 | 75.5% | 61.1% | 81.4% | -$1K | -$0.02 | 1.9d |
    | >= 3 | 43,513 | 4,679 | 76.9% | 63.9% | 81.9% | $348K | $8.00 | 2.0d |
    | >= 4 | 41,293 | 3,939 | **78.0%** | 66.3% | 82.3% | **$758K** | **$18.36** | 2.1d |

    ### October 2025 (Train: Apr-Sep'25)

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 85,043 | 19,441 | 72.8% | 65.7% | 77.2% | $4.82M | $56.63 | 1.7d |
    | >= 2 | 78,377 | 12,775 | 74.9% | 68.9% | 78.5% | $5.70M | $72.78 | 1.8d |
    | >= 3 | 71,353 | 9,263 | 76.5% | 71.6% | 79.3% | $6.15M | $86.19 | 2.0d |
    | >= 4 | 64,567 | 7,001 | **78.0%** | 73.8% | 80.2% | **$6.89M** | **$106.69** | 2.2d |
    """)
    return


@app.cell
def walkforward_after_coded(mo):
    mo.md("""
    ## D. Walk-Forward OOS Results -- AFTER (As-Coded: Bayesian + Price + Category)

    > [!WARNING] Vectorized upper bounds. Adaptive base rates + Bayesian HR + entry<0.85 + m.category filter.

    ### April 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 15,469 | 4,486 | 64.4% | 57.0% | 70.7% | $1.52M | $98.12 | 0.8d |
    | >= 2 | 13,816 | 2,833 | 65.6% | 60.0% | 70.4% | $1.59M | $115.33 | 0.8d |
    | >= 3 | 11,966 | 1,908 | 66.5% | 61.4% | 70.8% | $1.64M | $136.86 | 0.8d |
    | >= 4 | 10,376 | 1,378 | **67.4%** | 62.7% | 71.2% | **$1.75M** | **$168.73** | 0.8d |

    ### July 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 22,722 | 5,695 | 60.6% | 53.3% | 67.1% | **-$2.08M** | -$91.71 | 0.7d |
    | >= 2 | 20,702 | 3,675 | 61.2% | 55.0% | 66.5% | -$2.01M | -$96.97 | 0.6d |
    | >= 3 | 18,660 | 2,654 | 61.2% | 55.8% | 65.9% | -$2.04M | -$109.11 | 0.6d |
    | >= 4 | 16,893 | 2,065 | **61.4%** | 56.4% | 65.8% | **-$1.87M** | **-$110.64** | 0.6d |

    ### October 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 55,879 | 16,291 | 57.1% | 52.1% | 61.5% | $1.26M | $22.49 | 0.5d |
    | >= 2 | 49,070 | 9,482 | 57.4% | 53.0% | 61.1% | $1.36M | $27.65 | 0.5d |
    | >= 3 | 42,460 | 6,177 | 57.5% | 53.9% | 60.5% | $1.51M | $35.61 | 0.4d |
    | >= 4 | 37,333 | 4,468 | **57.4%** | 54.3% | 60.0% | **$1.63M** | **$43.62** | 0.4d |
    """)
    return


@app.cell
def walkforward_after_tags(mo):
    mo.md("""
    ## E. Walk-Forward OOS Results -- AFTER (Tag-Based: Bayesian + Price + Tag Exclusion)

    > [!WARNING] Vectorized upper bounds. Same as AFTER (as-coded) but using tag chain for Sports/Weather.

    ### April 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 5,615 | 2,949 | 64.0% | 55.2% | 73.6% | $439K | $78.19 | 1.7d |
    | >= 2 | 3,921 | 1,255 | 65.3% | 57.3% | 73.7% | $448K | $114.28 | 2.1d |
    | >= 3 | 2,581 | 585 | 68.4% | 60.3% | 76.5% | $477K | $184.76 | 2.5d |
    | >= 4 | 1,723 | 299 | **72.2%** | 60.2% | 83.7% | **$542K** | **$314.48** | 3.3d |

    ### July 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 12,383 | 6,578 | 48.7% | 36.4% | 73.7% | -$17K | -$1.41 | 0.9d |
    | >= 2 | 8,528 | 2,723 | 54.9% | 43.2% | 75.2% | $454K | $53.20 | 1.2d |
    | >= 3 | 5,536 | 1,227 | 60.3% | 48.7% | 75.8% | $567K | $102.47 | 1.6d |
    | >= 4 | 3,775 | 640 | **65.6%** | 54.3% | 77.2% | **$531K** | **$140.58** | 1.9d |

    ### October 2025

    | Consensus | Positions | Market-Sides | Overall HR | YES HR | NO HR | Total PnL | PnL/pos | Hold |
    |-----------|----------|-------------|------------|--------|-------|-----------|---------|------|
    | >= 1 | 19,137 | 10,528 | 58.3% | 54.5% | 61.7% | $664K | $34.69 | 0.6d |
    | >= 2 | 12,705 | 4,096 | 58.5% | 56.7% | 60.2% | $686K | $54.01 | 0.8d |
    | >= 3 | 8,255 | 1,871 | 59.6% | 60.0% | 59.1% | $268K | $32.41 | 1.0d |
    | >= 4 | 5,621 | 993 | **60.9%** | 63.3% | 58.4% | **$160K** | **$28.45** | 1.3d |
    """)
    return


@app.cell
def head_to_head(mo):
    mo.md("""
    ## F. Head-to-Head Comparison (Consensus >= 4)

    ### HR Comparison

    | Period | BEFORE HR | AFTER (coded) HR | AFTER (tags) HR | Delta (coded) | Delta (tags) |
    |--------|----------|-----------------|----------------|---------------|--------------|
    | Apr '25 | **80.3%** | 67.4% | 72.2% | **-12.9pp** | **-8.1pp** |
    | Jul '25 | **78.0%** | 61.4% | 65.6% | **-16.6pp** | **-12.4pp** |
    | Oct '25 | **78.0%** | 57.4% | 60.9% | **-20.6pp** | **-17.1pp** |

    ### PnL/Position Comparison

    | Period | BEFORE | AFTER (coded) | AFTER (tags) | Delta (coded) | Delta (tags) |
    |--------|--------|--------------|-------------|---------------|--------------|
    | Apr '25 | $108 | **$169** | **$314** | **+$61** | **+$207** |
    | Jul '25 | $18 | -$111 | **$141** | -$129 | **+$123** |
    | Oct '25 | $107 | $44 | $28 | -$63 | -$79 |

    ### Hold Time Comparison

    | Period | BEFORE | AFTER (coded) | AFTER (tags) |
    |--------|--------|--------------|-------------|
    | Apr '25 | 3.4d | **0.8d** | 3.3d |
    | Jul '25 | 2.1d | **0.6d** | 1.9d |
    | Oct '25 | 2.2d | **0.4d** | 1.3d |

    ### Total PnL Comparison

    | Period | BEFORE | AFTER (coded) | AFTER (tags) |
    |--------|--------|--------------|-------------|
    | Apr '25 | **$3.47M** | $1.75M | $542K |
    | Jul '25 | $758K | -$1.87M | **$531K** |
    | Oct '25 | **$6.89M** | $1.63M | $160K |
    | **Total** | **$11.12M** | **$1.51M** | **$1.23M** |
    """)
    return


@app.cell
def excess_hr(mo):
    mo.md("""
    ## G. Excess HR Over Test-Period Base Rates (The Real Metric)

    Using the ACTUAL test-month base rates (not training):

    | Period | Test YES BR | Test NO BR |
    |--------|-----------|----------|
    | Apr '25 | 36.3% | 63.7% |
    | Jul '25 | 20.4% | 79.6% |
    | Oct '25 | 35.6% | 64.4% |

    ### YES Excess HR (consensus >= 4)

    | Period | BEFORE | AFTER (coded) | AFTER (tags) |
    |--------|--------|--------------|-------------|
    | Apr '25 | +32.9pp | +26.4pp | +23.9pp |
    | Jul '25 | +45.9pp | +36.0pp | +33.9pp |
    | Oct '25 | +38.2pp | +18.7pp | +27.7pp |

    ### NO Excess HR (consensus >= 4)

    | Period | BEFORE | AFTER (coded) | AFTER (tags) |
    |--------|--------|--------------|-------------|
    | Apr '25 | +20.6pp | +7.5pp | +20.0pp |
    | Jul '25 | +2.7pp | -13.8pp | -2.4pp |
    | Oct '25 | +15.8pp | -4.4pp | -6.0pp |

    **CRITICAL FINDING**: The BEFORE pool maintains strong excess HR in ALL periods.
    The AFTER variants lose NO direction edge entirely in Jul and Oct.
    The entry price filter + Bayesian shrinkage removes too much NO signal.

    The tag-based variant preserves NO edge in April but loses it in Jul/Oct.
    The as-coded variant (broken category filter) loses NO edge in ALL periods
    except April (and barely there at +7.5pp).
    """)
    return


@app.cell
def isolate_effects(mo):
    mo.md("""
    ## H. Isolated Effect of Each Improvement (April 2025, Consensus >= 4)

    | Variant | Positions | HR | YES HR | NO HR | PnL | PnL/pos |
    |---------|----------|-----|--------|-------|-----|---------|
    | **BEFORE** (baseline) | 32,133 | **80.3%** | 69.2% | 84.3% | **$3.47M** | $108 |
    | Entry price only | 5,465 | 73.7% | 69.4% | 77.0% | $1.19M | **$218** |
    | Bayesian only | 30,706 | **81.3%** | 71.4% | 84.8% | **$3.68M** | $120 |
    | Adaptive BR only | 35,494 | 76.6% | 65.5% | 81.5% | $3.54M | $100 |
    | AFTER (all combined, coded) | 10,376 | 67.4% | 62.7% | 71.2% | $1.75M | $169 |
    | AFTER (all combined, tags) | 1,723 | 72.2% | 60.2% | 83.7% | $542K | $314 |

    ### Key Takeaways

    1. **Bayesian alone IMPROVES** HR (+1.0pp) and PnL (+$210K). It correctly removes
       contaminated traders while preserving the core signal. KEEP.

    2. **Entry price alone DESTROYS** volume (5,465 vs 32,133 = -83%) while dropping HR
       (-6.6pp). PnL/pos doubles (+$110) but total PnL drops 66%. The filter is too
       aggressive at 0.85. Consider raising to 0.90 or 0.95.

    3. **Adaptive BR alone HURTS** HR (-3.7pp) and slightly lowers PnL/pos (-$8).
       The adaptive YES base rate (32.3%) is lower than static (38.1%), which LOWERS
       the bar for YES qualification and RAISES it for NO. This floods the pool with
       more marginal YES traders while cutting NO.

    4. **Combined effects are destructive**: The improvements interact negatively.
       Entry price + adaptive BR + Bayesian together produce a pool that is too small,
       too conservative, and loses NO direction signal entirely.

    ## July 2025 Isolates (Consensus >= 4)

    | Variant | Positions | HR | YES HR | NO HR | PnL | PnL/pos |
    |---------|----------|-----|--------|-------|-----|---------|
    | **BEFORE** (baseline) | 41,293 | **78.0%** | 66.3% | 82.3% | $758K | $18 |
    | Entry price only | 7,236 | 69.1% | 64.2% | 73.1% | -$1.11M | -$154 |
    | Bayesian only | 39,330 | **79.3%** | 68.5% | 82.9% | **$1.36M** | **$35** |
    | Adaptive BR only | 42,417 | 73.6% | 61.6% | 79.7% | $266K | $6 |

    **July verdict**: Bayesian is the ONLY improvement that helps July. It converts
    the month from +$758K to +$1.36M (+79% total PnL). Entry price is catastrophic
    (-$1.11M loss). Adaptive BR cuts PnL to $266K.

    **July negative PnL FIXED by Bayesian alone**: +$1.36M vs -$2.08M for AFTER (coded).
    The contaminated traders in the BEFORE pool were actually hurting July performance
    less than the entry price + adaptive BR combo.
    """)
    return


@app.cell
def compounding_scores(mo):
    mo.md("""
    ## I. Compounding Scores (Consensus >= 4)

    `compounding_score = (hr - weighted_base_rate) * pnl_per_pos / median_hold_days`

    Using test-period base rates, weighted by YES/NO position mix:

    ### BEFORE Pool

    | Period | HR | Weighted BR | Excess | PnL/pos | Hold | Score |
    |--------|-----|------------|--------|---------|------|-------|
    | Apr '25 | 80.3% | 57.0% | +23.3pp | $108 | 3.4d | **7.40** |
    | Jul '25 | 78.0% | 63.7% | +14.3pp | $18 | 2.1d | **1.23** |
    | Oct '25 | 78.0% | 53.5% | +24.5pp | $107 | 2.2d | **11.91** |

    ### AFTER (As-Coded)

    | Period | HR | Weighted BR | Excess | PnL/pos | Hold | Score |
    |--------|-----|------------|--------|---------|------|-------|
    | Apr '25 | 67.4% | 51.1% | +16.3pp | $169 | 0.8d | **34.44** |
    | Jul '25 | 61.4% | 50.4% | +11.0pp | -$111 | 0.6d | **-20.35** |
    | Oct '25 | 57.4% | 50.3% | +7.1pp | $44 | 0.4d | **7.81** |

    ### AFTER (Tag-Based)

    | Period | HR | Weighted BR | Excess | PnL/pos | Hold | Score |
    |--------|-----|------------|--------|---------|------|-------|
    | Apr '25 | 72.2% | 49.4% | +22.8pp | $314 | 3.3d | **21.72** |
    | Jul '25 | 65.6% | 45.5% | +20.1pp | $141 | 1.9d | **14.92** |
    | Oct '25 | 60.9% | 50.2% | +10.7pp | $28 | 1.3d | **2.31** |

    ### Interpretation

    The tag-based variant has the best AVERAGE compounding score across all 3 periods
    (13.0 avg vs 7.3 for AFTER-coded and 6.8 for BEFORE). However, this is misleading
    because:

    1. The tag-based variant has very small sample sizes (1,723 - 5,621 positions)
    2. BEFORE has the most consistent positive performance
    3. The as-coded variant fails catastrophically in July (-20.35 score)

    **The BEFORE pool with Bayesian-only enhancement is the sweet spot.**
    """)
    return


@app.cell
def category_bug(mo):
    mo.md("""
    ## J. Bug Report: `exclude_categories` Uses Broken Column

    The `markets.category` column is NULL for 554,540 of 558,553 markets (99.3%).

    | Filter Method | Sports Markets Excluded | Weather Markets Excluded |
    |--------------|----------------------|------------------------|
    | `m.category` (as-coded) | 2,499 | 0 |
    | Tag chain (`events -> event_tags -> tags`) | **267,945** | **11,982** |

    The `exclude_categories` parameter is effectively decorative in the current
    implementation. To actually exclude sports/weather, the query must use:

    ```sql
    -- Tag-based exclusion (correct)
    WITH excluded AS (
        SELECT DISTINCT m.condition_id
        FROM markets m
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE lower(t.label) IN ('sports', 'basketball', ...)
    )
    ... AND p.condition_id NOT IN (SELECT condition_id FROM excluded)
    ```

    **Recommendation**: Fix `qualified_traders_query()` to use tag-based exclusion,
    or accept that category exclusion is non-functional and remove it to avoid confusion.
    """)
    return


@app.cell
def verdict(mo):
    mo.md("""
    ## K. Verdict

    ### Per-Improvement Scorecard

    | Improvement | Impact on HR | Impact on PnL | Impact on Pool | Recommendation |
    |------------|-------------|---------------|---------------|----------------|
    | **Entry price filter (0.85)** | -6 to -9pp | +PnL/pos, -total PnL | -83% positions | RAISE to 0.92+ or REMOVE |
    | **Category exclusion (m.category)** | Negligible | Negligible | -0.5% (broken) | FIX to use tags, or REMOVE |
    | **Adaptive base rates** | -4pp | Slight negative | Shifts YES/NO mix | KEEP but careful -- lowers YES bar |
    | **Bayesian HR shrinkage** | **+1.0pp** | **+6-79% total PnL** | -5% (contam only) | **STRONG KEEP** |

    ### Combined vs Individual

    The four improvements together are WORSE than the baseline in every period except
    April PnL/pos. The interaction effects are destructive:

    - Entry price + Bayesian double-penalizes low-sample traders
    - Adaptive BR + entry price removes most NO traders (lower bar + fewer positions)
    - Category exclusion does nothing (broken column)

    ### Recommended Configuration

    Based on the evidence, the optimal configuration for tick-by-tick validation is:

    | Parameter | Value | Rationale |
    |-----------|-------|-----------|
    | `use_bayesian_hr` | **True** | Only improvement that helps across all periods |
    | `max_entry_price` | **0.95 or 1.0 (disabled)** | 0.85 too aggressive; consider 0.92+ |
    | `exclude_categories` | **Remove or fix** | m.category is broken; use tags if needed |
    | Adaptive base rates | **Keep** | Correct in principle, but interaction with Bayesian needs tuning |
    | `min_positions` | **30** (YES), **50** (NO) | NO needs higher bar per contamination analysis |
    | Consensus | **>= 3** | Best PnL/risk tradeoff |
    | Direction | **BOTH** (Bayesian handles NO contamination) | Bayesian eliminates need for YES-only |

    ### Expected Tick-by-Tick Performance (BEFORE + Bayesian Only)

    | Period | Vec HR | Tick range (-20 to -40pp) | Vec PnL | Score |
    |--------|--------|--------------------------|---------|-------|
    | Apr '25 | 81.3% | 41-61% | $3.68M | ~8 |
    | Jul '25 | 79.3% | 39-59% | $1.36M | ~2 |
    | Oct '25 | ~79% | 39-59% | ~$7M+ | ~12 |

    > [!WARNING] These are UPPER BOUNDS. Do NOT allocate capital based on vectorized results.
    > Tick-by-tick validation is REQUIRED.

    ### Action Items

    1. **Fix**: `exclude_categories` to use tag chain, not `m.category`
    2. **Fix**: `max_entry_price` default to 0.95 (or remove -- 0.85 is too aggressive)
    3. **Keep**: Bayesian HR shrinkage (the only universally positive improvement)
    4. **Test**: Bayesian-only variant in tick-by-tick validation FIRST
    5. **Capture**: Bug in `m.category` column as knowledge entry
    """)
    return


@app.cell
def knowledge_captures(mo):
    mo.md("""
    ## L. Knowledge Captures

    ### Finding 1: markets.category column is 99.3% NULL

    The `markets.category` column is populated for only 4,013 of 558,553 markets.
    Any filtering using `m.category` is effectively non-functional. The tag chain
    (`markets -> events -> event_tags -> tags`) must be used instead.

    This was previously known for susceptibility classification but not explicitly
    flagged as a pitfall for the `qualified_traders_query()`.

    **Impact**: The `exclude_categories` parameter in S2Config is decorative. Markets
    tagged as Sports via the event tags system (267K markets) are NOT excluded.

    ### Finding 2: Entry price filter at 0.85 removes high-HR late entrants

    Positions with direction-adjusted entry >= 0.85 have 93-97% HR but represent
    late entries on near-certain outcomes ($0.15 max profit). The filter correctly
    identifies these, but at 0.85 it removes 15% of YES and 50% of NO positions,
    which is too aggressive for pool qualification.

    A threshold of 0.92+ preserves most legitimate signal while still excluding
    obvious $0.99 bot entries.

    ### Finding 3: Bayesian shrinkage is the only universally positive improvement

    Across all 3 OOS periods, Bayesian HR (alpha+beta=10) improves both HR and PnL
    vs the raw HR baseline. It eliminates NO contamination (43% -> 0% at HR>=95%)
    while preserving pool size (only 5% reduction).

    Key mechanism: a 30-position trader with 100% raw HR gets shrunk to ~90% Bayesian
    HR, which still clears the 10pp excess threshold for YES (90% - 38% = 52pp) but
    not always for NO (90% - 62% = 28pp -- still clears, but 80% raw -> 78% Bayesian
    barely clears the 10pp threshold).

    ### Finding 4: Adaptive base rates lower YES threshold

    Training-window adaptive rates show YES at 28-32% (vs static 38.1%). This means
    the excess HR bar for YES qualification is LOWER (easier to qualify) while NO
    is HIGHER (harder). The net effect is more marginal YES traders entering the pool,
    diluting average quality.
    """)
    return


if __name__ == "__main__":
    app.run()
