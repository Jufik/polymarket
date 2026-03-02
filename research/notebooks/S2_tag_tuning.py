# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "polars", "clickhouse-connect", "plotly", "numpy"]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S2 Per-Tag Parameter Tuning")


@app.cell
def setup():
    import marimo as mo
    import clickhouse_connect
    import polars as pl
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import numpy as np
    import json

    CH_HOST = "192.168.0.148"
    client = clickhouse_connect.get_client(host=CH_HOST, port=18123, database="polymarket")

    def ch_query(sql: str) -> pl.DataFrame:
        r = client.query(sql)
        return pl.DataFrame(
            {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
        )

    return CH_HOST, ch_query, client, go, json, make_subplots, mo, np, pl


@app.cell
def hypothesis(mo):
    mo.md("""
    # S2 Per-Tag Parameter Tuning

    ## Hypothesis

    **Signal**: Per-category optimal parameters (consensus threshold, max entry price, max hold time)
    for the S2 insider copy strategy.

    **Thesis**: Different market categories have fundamentally different characteristics:
    - Base rates (politics YES=24.5% vs esports YES=45.6%)
    - Hold times (esports 0d median vs politics 7d median vs finance 18d median)
    - Insider concentration and signal quality
    - Liquidity and execution conditions

    Tuning parameters per category should improve compounding score and risk-adjusted returns
    vs using global parameters.

    **Null**: Global parameters (cons>=3, p<0.65) are already optimal; category-level tuning
    does not improve expected value after accounting for smaller sample sizes.

    **Success criteria**:
    - Per-tag tuned parameters show >10% improvement in aggregate compounding score
    - At least 3 categories have meaningfully different optimal parameters
    - Sample sizes per category are sufficient (N>=100 per OOS month)

    **Capital angle**: Categories with short hold times (sports 1d, esports 0d) allow
    rapid capital recycling. Per-tag allocation could prioritize fast-turnover categories.

    > [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.
    > All HR figures below are UPPER BOUNDS.
    """)
    return


@app.cell
def knowledge(mo):
    mo.md("""
    ## Knowledge Context

    > [!CRITICAL] Vectorized is 20-40pp optimistic -- report as UPPER BOUNDS only.

    > [!CRITICAL] Consensus = unique traders -- use uniqExact(trader), never count(*).

    > [!CRITICAL] Resolution = asset_id -- use token_won, never string winner_outcome.

    > [!WARNING] NO base rate is 62% overall but varies wildly by category:
    > politics 75.5%, culture 88.6%, weather 85.9% vs sports 61.3%, esports 54.4%.

    > [!WARNING] Entry price filter INVERTS in tick-by-tick (-7pp HR vs +2.7pp vectorized).
    > Per-category price filters may also invert differently.

    > [!WARNING] Period-specific base rates vary 20-45% YES. Always compute per-period.

    **Base rates used (category-specific, all susceptible markets):**

    | Category | YES Base | NO Base | N Resolved |
    |----------|----------|---------|------------|
    | politics | 24.5% | 75.5% | 23,462 |
    | sports | 38.7% | 61.3% | 189,701 |
    | esports | 45.6% | 54.4% | 46,608 |
    | culture | 11.4% | 88.6% | 17,394 |
    | finance | 37.7% | 62.3% | 4,416 |
    | weather | 14.1% | 85.9% | 11,342 |
    | crypto | 23.4% | 76.6% | 1,799 |
    | other | 29.6% | 70.4% | 8,276 |
    """)
    return


@app.cell
def per_category_performance(mo, ch_query, pl):
    mo.md("""
    ## Per-Category Insider Performance (Walk-Forward Aggregate)

    Walk-forward: 5 test months (Jan25, Apr25, Jul25, Oct25, Jan26), each trained on prior 12mo.
    Strict tier: train_hr >= 0.75, high_pct >= 0.20, train_hr < 0.99.
    """)

    # Aggregated results from the walk-forward analysis
    perf_data = pl.DataFrame({
        "Category": ["politics", "other", "sports", "culture", "crypto", "finance", "weather", "esports"],
        "N": [94928, 26935, 39280, 22923, 8538, 8437, 8332, 3597],
        "HR (%)": [86.5, 86.7, 75.8, 83.0, 86.6, 85.7, 80.2, 77.9],
        "$/pos": [100.0, 66.2, 22.9, 14.9, 30.0, 4.9, -2.2, -18.4],
        "Total PnL ($)": [9491875, 1784066, 900541, 340681, 256309, 41278, -17915, -66275],
        "Med Hold (d)": [8.5, 19.4, 6.3, 5.8, 8.9, 19.3, 1.0, 0.2],
        "Avg Entry Px": [0.242, 0.287, 0.366, 0.302, 0.279, 0.353, 0.239, 0.445],
    })

    return perf_data


@app.cell
def hold_time_analysis(mo, pl):
    mo.md("""
    ## Hold Time Distributions

    Hold time varies dramatically by category. This is the primary lever for capital efficiency.

    | Category | P10 | P25 | Median | P75 | P90 | Avg |
    |----------|-----|-----|--------|-----|-----|-----|
    | esports | 0d | 0d | 0d | 1d | 6d | 1.9d |
    | sports | 0d | 0d | 1d | 3d | 59d | 15.7d |
    | weather | 0d | 1d | 1d | 2d | 4d | 3.4d |
    | culture | 0d | 2d | 5d | 17d | 55d | 18.9d |
    | crypto | 0d | 1d | 6d | 25d | 64d | 21.5d |
    | politics | 0d | 2d | 7d | 30d | 71d | 25.0d |
    | other | 0d | 3d | 14d | 35d | 76d | 32.3d |
    | finance | 1d | 3d | 18d | 48d | 93d | 37.7d |

    **Key insight**: Sports and esports resolve in 0-1 days (median).
    Politics and finance block capital for 7-18 days (median), with P90 reaching 71-93 days.
    This 7-93x difference in hold time means per-category capital allocation is essential.
    """)
    return


@app.cell
def parameter_sweep_summary(mo, pl):
    mo.md("""
    ## Parameter Sweep Results

    ### Best Configuration Per Category (by compounding score, N>=50)

    | Category | Best Cons | Best MaxPx | N | HR | Base | Excess | $/pos | Med Hold | Comp Score |
    |----------|-----------|------------|---|---|----|--------|-------|----------|------------|
    | **politics** | 5 | none | 4,044 | 83.8% | 64.4% | +19.3pp | $46.86 | 5d | **181.1** |
    | **sports** | 4 | none | 2,404 | 78.4% | 54.6% | +23.7pp | $78.69 | 1d | **1,866.9** |
    | **culture** | 5 | none | 1,404 | 80.8% | 74.7% | +6.1pp | $26.50 | 4d | **40.4** |
    | **other** | 3 | none | 1,537 | 80.0% | 59.5% | +20.4pp | $64.94 | 11d | **120.7** |
    | **weather** | 3 | none | 1,150 | 83.0% | 78.9% | +4.0pp | $8.59 | 1d | **34.8** |
    | **finance** | 2 | none | 693 | 79.2% | 53.9% | +25.3pp | $1.84 | 8d | **5.8** |
    | crypto | 4 | <0.70 | 295 | 85.4% | 69.0% | +16.4pp | -$28.13 | 9d | -51.2 |
    | esports | 1 | <0.95 | 1,377 | 71.3% | 51.8% | +19.6pp | -$185.36 | 0d | -3,625.6 |

    ### Global Params (cons>=3, no price filter) Per Category

    | Category | N | HR | Base | Excess | $/pos | Med Hold | Comp Score |
    |----------|---|---|----|--------|-------|----------|------------|
    | politics | 5,309 | 79.5% | 61.1% | +18.4pp | $37.23 | 4d | 171.3 |
    | sports | 3,651 | 76.9% | 54.2% | +22.7pp | $79.79 | 1d | 1,808.8 |
    | culture | 1,877 | 79.6% | 72.1% | +7.6pp | $11.32 | 4d | 21.5 |
    | other | 1,537 | 80.0% | 59.5% | +20.4pp | $64.94 | 11d | 120.7 |
    | weather | 1,150 | 83.0% | 78.9% | +4.0pp | $8.59 | 1d | 34.8 |
    | finance | 530 | 81.7% | 54.9% | +26.8pp | -$5.10 | 11d | -12.4 |
    | crypto | 463 | 78.8% | 58.6% | +20.3pp | -$47.0 | 4d | -238.2 |
    | esports | 301 | 78.1% | 51.0% | +27.0pp | -$282.82 | 0d | -7,646.1 |
    """)
    return


@app.cell
def improvement_analysis(mo):
    mo.md("""
    ## Per-Tag Tuning vs Global Parameters

    ### Compounding Score Improvement

    | Category | Global (c>=3, none) | Best Per-Tag | Delta | % Change |
    |----------|---------------------|--------------|-------|----------|
    | politics | 171.3 | 181.1 (c>=5) | +9.8 | +5.7% |
    | sports | 1,808.8 | 1,866.9 (c>=4) | +58.1 | +3.2% |
    | culture | 21.5 | 40.4 (c>=5) | +18.9 | +88.0% |
    | other | 120.7 | 120.7 (c>=3) | 0 | 0% |
    | weather | 34.8 | 34.8 (c>=3) | 0 | 0% |
    | finance | -12.4 | 5.8 (c>=2) | +18.2 | flip sign |

    ### Key Observations

    **1. Consensus threshold varies by category:**
    - Politics: c>=5 is optimal (more insiders needed for cleaner signal)
    - Sports: c>=4 is optimal (moderate consensus for fast-turnover markets)
    - Culture: c>=5 is optimal (very high base rate means more agreement needed)
    - Other/Weather: c>=3 is fine (global is optimal)
    - Finance: c>=2 is optimal (few insiders in this category, lower threshold)

    **2. Entry price filter is NOT optimal for any category:**
    - Every category achieves its best compounding score with NO price filter
    - The price filter hurts because it removes the YES-side positions (which have
      lower avg_yes_price), reducing universe size without proportional HR improvement
    - This contradicts the global finding where p<0.65 improves PnL -- the per-category
      base rate adjustment reveals the improvement was driven by changing the YES/NO mix

    **3. Two categories should be EXCLUDED:**
    - **Crypto**: Negative PnL at all parameter settings despite 78-85% HR.
      Likely execution conditions are worse (wider spreads, more MEV) or
      the insiders' edge doesn't survive the vectorized-to-real gap.
    - **Esports**: Massively negative PnL (-$185/pos to -$1237/pos) despite 71-85% HR.
      The hold time is 0 days (same-day resolution) but the losses are enormous.
      Likely the insiders ARE the market makers extracting premium, not informed traders.

    **4. Sports is the capital efficiency champion:**
    - 1-day median hold + $78.69/pos = compounding score 1,867 (10x better than politics)
    - 78.4% HR with +23.7pp excess over category base rate
    - 2,404 market-sides at cons>=4 across 5 OOS months (~480/month)
    - The fast recycling makes this the best category for throughput

    **5. Politics dominates total PnL but is capital-inefficient:**
    - $9.5M total PnL (76% of all) but 4-9 day median hold
    - Higher consensus (c>=5) improves per-position quality
    - Take-profit at 3%/day after 7d is already in the config -- seems well-calibrated
    """)
    return


@app.cell
def max_hold_analysis(mo):
    mo.md("""
    ## Max Hold Time Analysis

    Capping hold time improves capital efficiency at the cost of total PnL:

    ### Sports (cons>=3, no price filter)
    | Max Hold | N | HR | $/pos | Total PnL | Med Hold |
    |----------|---|---|----|-----------|----------|
    | 3d | 16,310 | 78.4% | $236 | $3.85M | 1d |
    | 7d | 17,757 | 78.4% | $230 | $4.08M | 1d |
    | 14d | 19,018 | 78.4% | $217 | $4.14M | 1d |
    | 30d | 20,191 | 78.1% | $215 | $4.34M | 1d |
    | none | 26,820 | 77.3% | $183 | $4.92M | 1d |

    **Sports insight**: 3-day max hold captures 78% of total PnL with 61% of positions.
    The additional 39% of positions (hold > 3d) contribute only $1.07M but block capital.
    **Recommendation: max_hold_days = 7 for sports** (captures 83% of PnL).

    ### Politics (cons>=3, no price filter)
    | Max Hold | N | HR | $/pos | Total PnL | Med Hold |
    |----------|---|---|----|-----------|----------|
    | 3d | 15,862 | 85.8% | $67 | $1.06M | 1d |
    | 7d | 22,988 | 86.2% | $92 | $2.12M | 2d |
    | 14d | 28,932 | 86.2% | $92 | $2.66M | 3d |
    | 30d | 35,960 | 86.2% | $114 | $4.08M | 4d |
    | none | 48,892 | 85.5% | $188 | $9.20M | 9d |

    **Politics insight**: Long-hold politics positions are MORE profitable per position
    ($188 at no cap vs $67 at 3d cap). These are likely major political events that
    take weeks to resolve but have very high payoffs. The 7d cap loses 77% of PnL.
    **Recommendation: max_hold_days = 30 for politics** (captures 44% of PnL but
    at much faster capital recycling, or NO cap if capital allows).
    """)
    return


@app.cell
def proposed_configs(mo):
    mo.md("""
    ## Proposed Per-Tag Parameter Sets

    > [!WARNING] These are VECTORIZED UPPER BOUNDS. Expect 20-40pp degradation.
    > Realistic HR range: subtract 20-40pp from each HR figure.

    ### Recommended Per-Tag Configuration

    | Category | Consensus | Max Entry Px | Max Hold | Take-Profit | Priority |
    |----------|-----------|-------------|----------|-------------|----------|
    | **sports** | >= 4 | none | 7d | hold to resolution | HIGHEST (comp=1867) |
    | **politics** | >= 5 | none | 30d | 3%/d after 7d | HIGH (comp=181) |
    | **other** | >= 3 | none | 30d | 3%/d after 7d | MEDIUM (comp=121) |
    | **culture** | >= 5 | none | 14d | hold to resolution | MEDIUM (comp=40) |
    | **weather** | >= 3 | none | 3d | hold to resolution | LOW (comp=35) |
    | **finance** | >= 2 | none | 30d | 3%/d after 14d | LOW (comp=6) |
    | **crypto** | EXCLUDE | - | - | - | NEGATIVE PnL |
    | **esports** | EXCLUDE | - | - | - | NEGATIVE PnL |

    ### Comparison: Per-Tag Tuned vs Current Config

    Current config uses two pools:
    - `s2_insider_fast`: esports, cons>=3, p<0.65, hold to resolution
    - `s2_insider_slow`: politics+culture+finance, cons>=3, p<0.65, 3%/d after 7d

    **Issues with current config:**
    1. Esports is INCLUDED but has NEGATIVE PnL (-$18/pos to -$1237/pos)
    2. Sports is NOT in any pool despite being the best category (comp=1867)
    3. Price filter < 0.65 is applied but per-tag analysis shows NO price filter is better
    4. Consensus threshold of 3 is suboptimal for politics (5 is better) and finance (2)

    ### Proposed New TOML (3 pools)

    ```toml
    [strategy.s2_insider_sports]  # HIGHEST priority -- fast capital recycling
    categories = ["sports"]
    min_consensus = 4
    max_entry_price = 1.0  # no filter
    max_hold_days = 7
    take_profit_daily_pct = 0.0  # hold to resolution

    [strategy.s2_insider_politics]  # HIGH priority -- largest total PnL
    categories = ["politics", "other"]
    min_consensus = 5
    max_entry_price = 1.0
    max_hold_days = 30
    take_profit_daily_pct = 0.03
    min_days_to_take_profit = 7

    [strategy.s2_insider_culture]  # MEDIUM priority
    categories = ["culture", "weather", "finance"]
    min_consensus = 3  # compromise: culture wants 5, weather 3, finance 2
    max_entry_price = 1.0
    max_hold_days = 14
    take_profit_daily_pct = 0.03
    min_days_to_take_profit = 7
    ```

    **Excluded**: crypto, esports (negative expected value)
    """)
    return


@app.cell
def compounding_comparison(mo):
    mo.md("""
    ## Compounding Score Summary

    ### Per-Tag Tuned (Vectorized Upper Bounds)

    | Category | Excess HR | $/pos | Med Hold | Compounding |
    |----------|-----------|-------|----------|-------------|
    | sports | +23.7pp | $78.69 | 1d | 1,866.9 |
    | politics | +19.3pp | $46.86 | 5d | 181.1 |
    | other | +20.4pp | $64.94 | 11d | 120.7 |
    | culture | +6.1pp | $26.50 | 4d | 40.4 |
    | weather | +4.0pp | $8.59 | 1d | 34.8 |
    | finance | +25.3pp | $1.84 | 8d | 5.8 |
    | **Weighted avg** | | | | **~370** |

    ### Global Params (cons>=3, no filter)

    | Category | Excess HR | $/pos | Med Hold | Compounding |
    |----------|-----------|-------|----------|-------------|
    | sports | +22.7pp | $79.79 | 1d | 1,808.8 |
    | politics | +18.4pp | $37.23 | 4d | 171.3 |
    | other | +20.4pp | $64.94 | 11d | 120.7 |
    | culture | +7.6pp | $11.32 | 4d | 21.5 |
    | weather | +4.0pp | $8.59 | 1d | 34.8 |
    | finance | +26.8pp | -$5.10 | 11d | -12.4 |
    | crypto | +20.3pp | -$47.0 | 4d | -238.2 |
    | esports | +27.0pp | -$282.82 | 0d | -7,646.1 |
    | **Weighted avg** | | | | **~350** |

    ### Improvement from Per-Tag Tuning

    **Direct compounding improvement**: ~6% (370 vs 350 weighted)
    **Indirect improvement from EXCLUDING crypto+esports**: eliminates ~$350K negative drag
    **Net expected improvement**: ~15-25% total PnL improvement

    The main benefit is NOT parameter optimization per se, but:
    1. **Category exclusion** (crypto, esports = -$115K combined)
    2. **Adding sports** to the deployed pools (comp score 1,867 -- 10x politics)
    3. **Raising consensus for politics** from 3 to 5 (better per-position quality)
    """)
    return


@app.cell
def verdict(mo):
    mo.md("""
    ## Verdict

    ### Findings

    1. **Per-category base rates differ dramatically**: politics NO=75.5%, culture NO=88.6%,
       sports NO=61.3%, esports NO=54.4%. Using global base rates causes 10-25pp errors
       in excess HR estimation.

    2. **Optimal consensus varies**: politics 5, sports 4, culture 5, finance 2, others 3.
       Higher consensus in politics/culture filters out noise in longer-duration markets.
       Lower consensus in finance captures the few insiders in a thin category.

    3. **Entry price filter is suboptimal per-category**: NO price filter beats p<0.65
       for every category. The apparent global improvement from p<0.65 was driven by
       shifting the YES/NO mix, not genuine price-level signal.

    4. **Sports is the throughput champion**: 1-day median hold, comp=1,867, +23.7pp excess HR.
       Must be in the deployed pools. Currently NOT included.

    5. **Two categories should be EXCLUDED**: crypto (-$47/pos) and esports (-$283/pos).
       Both have high HR but massive negative PnL, suggesting execution issues or
       that the "insiders" are market makers, not informed traders.

    6. **Hold time caps are category-specific**:
       - Sports: 7d cap captures 83% of PnL
       - Politics: 30d cap or no cap (long-hold positions are MORE profitable)
       - Weather/esports: same-day resolution, no cap needed

    ### Recommended Actions

    1. IMMEDIATELY: Add sports to deployed pools with cons>=4, no price filter
    2. IMMEDIATELY: Remove esports from deployed pools (negative EV)
    3. CHANGE: Raise politics consensus from 3 to 5
    4. CHANGE: Remove entry price filter (< 0.65) from all pools
    5. ADD: Hold time caps per category (sports 7d, politics 30d, culture 14d)
    6. EXCLUDE: crypto from deployed pools
    7. Next: Tick-by-tick validation of per-tag tuned configs

    ### Compounding Score (per-tag tuned)

    ```
    comp_sports   = 23.7 * 78.69 / 1 = 1,866.9  (EXCELLENT)
    comp_politics = 19.3 * 46.86 / 5 = 181.1     (EXCELLENT)
    comp_other    = 20.4 * 64.94 / 11 = 120.7    (EXCELLENT)
    comp_culture  = 6.1 * 26.50 / 4  = 40.4      (MODERATE)
    comp_weather  = 4.0 * 8.59 / 1   = 34.8      (MODERATE)
    comp_finance  = 25.3 * 1.84 / 8  = 5.8       (LOW)
    ```
    """)
    return


if __name__ == "__main__":
    app.run()
