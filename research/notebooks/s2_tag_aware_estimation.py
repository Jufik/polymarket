# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "polars", "clickhouse-connect", "plotly", "numpy"]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S2 Hit-Rate Copy: Tag-Aware Mode Estimation")


@app.cell
def setup():
    import marimo as mo
    import clickhouse_connect
    import polars as pl
    import numpy as np

    CH_HOST = "192.168.0.148"
    client = clickhouse_connect.get_client(host=CH_HOST, port=18123, database="polymarket")

    def ch_query(sql: str) -> pl.DataFrame:
        r = client.query(sql)
        return pl.DataFrame(
            {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
        )

    return CH_HOST, client, ch_query, mo, np, pl


@app.cell
def hypothesis(mo):
    mo.md("""
    # S2 Hit-Rate Copy: Tag-Aware Mode Estimation

    ## Hypothesis

    Using TAG-SPECIFIC base rates (instead of global 38%/62%) to qualify traders
    should identify more skilled traders by measuring excess HR against the correct
    baseline. Direction-specific consensus (YES and NO tracked separately) should
    avoid the -7pp penalty from direction-agnostic consensus.

    ## Design

    - `tag_aware=True` mode in S2HitRateCopy
    - Per-(tag, direction) qualified pools via `tag_qualified_traders_query()`
    - Direction-specific consensus counting
    - Tag-specific Bayesian priors (alpha = tag_base * 10)
    - 16 tags from TAG_BASE_RATES (9% to 73% YES rate)

    ## Success Criteria

    - OOS tick-by-tick HR > BEFORE global (50.6% for Jul 25)
    - Positive tick-by-tick PnL
    - Compounding score > 0 (any positive edge)

    ## Key Knowledge Constraints

    > [!CRITICAL] Vectorized backtests are 20-40pp optimistic vs tick-by-tick.

    > [!CRITICAL] NO direction collapses in tick-by-tick copy strategies.
    > Tag-aware mode shifts to YES-dominant (expected improvement).

    > [!WARNING] Absolute HR is misleading without base rate context.
    > Must compute per-tag excess HR.
    """)
    return


@app.cell
def pool_analysis(mo):
    mo.md("""
    ## Pool Analysis (current, no as_of_date)

    Tag-aware pool: **3,433 unique traders** (vs 1,634 global = 2.1x larger)

    | Tag | Dir | Pool Size | Tag Base | Mean Excess |
    |-----|-----|-----------|----------|-------------|
    | Crypto | YES | 2,501 | 28.9% | +28.9pp |
    | Crypto | NO | 502 | 71.1% | +15.7pp |
    | Culture | YES | 328 | 11.7% | +19.3pp |
    | Trump | YES | 242 | 34.1% | +22.2pp |
    | Geopolitics | YES | 141 | 27.0% | +22.9pp |
    | Movies | YES | 111 | 9.7% | +22.5pp |
    | Elections | YES | 78 | 9.0% | +21.1pp |
    | Trump | NO | 77 | 65.9% | +15.5pp |
    | Finance | YES | 44 | 35.3% | +24.8pp |
    | AI | YES | 44 | 10.0% | +21.8pp |

    **Key insight**: Pool is **85% YES-direction** because tag-specific base rates
    are mostly <40% for YES, meaning many traders beat the low YES bar. Global pool
    was 60-70% NO because NO base rate of 62% is a higher bar.

    Overlap with global pool: **640 traders** (19% of tag-aware, 39% of global).
    **2,793 traders** are ONLY qualified at the tag level.
    """)
    return


@app.cell
def vectorized_results(mo):
    mo.md("""
    ## Vectorized Walk-Forward OOS (consensus >= 3)

    > [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.
    > Realistic range: HR 17.8% to 37.8% (for tag-aware).

    ### Tag-Aware vs Global Fixed (per-position evaluation)

    | Period | Global HR | Global N | Global Avg$ | Tag HR | Tag N | Tag Avg$ | HR Delta |
    |--------|-----------|----------|-------------|--------|-------|----------|----------|
    | Apr 25 | 82.9% | 8,512 | $241 | 52.7% | 3,458 | $210 | -30.2pp |
    | Jul 25 | 84.2% | 14,532 | $145 | 57.8% | 16,662 | $40 | -26.4pp |
    | Oct 25 | 85.5% | 15,227 | $155 | 61.0% | 74,673 | $22 | -24.5pp |

    ### Why Tag-Aware Vectorized HR Is Lower

    1. **YES dominance** (90-95%): YES HR ranges 45-59% vs 74-78% for NO in global
    2. **Tag-aware pool admits weaker traders**: beating 9-29% YES base is easier
       than beating 38% global base
    3. **Global NO positions inflate vectorized**: 62% base + aggregate net position
       = 85-90% vectorized HR, but collapses in tick-by-tick
    4. **Crypto dominates** (67% of Jul positions): single-tag concentration risk

    ### Per-Tag Vectorized (Jul 25, C>=3, unique markets)

    | Tag | N | HR | YES | NO | PnL | Avg$ |
    |-----|---|-----|------|-----|------|------|
    | Crypto | 2,599 | 66.4% | 2,433 | 166 | $36K | $14 |
    | Culture | 578 | 30.1% | 578 | 0 | -$4K | -$8 |
    | Trump | 477 | 62.5% | 444 | 33 | $23K | $48 |
    | Movies | 126 | 28.6% | 126 | 0 | -$8K | -$63 |
    | Politics | 60 | 28.3% | 60 | 0 | $6K | $104 |
    """)
    return


@app.cell
def tick_results(mo):
    mo.md("""
    ## Tick-by-Tick Validation: Jul 2025

    ### All Consensus Thresholds

    | Config | Fills | Wins | Losses | Pending | HR | PnL | Avg Edge | Sharpe | Hold |
    |--------|-------|------|--------|---------|-----|------|----------|--------|------|
    | C>=1 | 2,095 | 527 | 918 | 650 | 36.5% | -$27,614 | -$19.11 | -0.54 | 286h |
    | C>=2 | 5,095 | 1,402 | 1,629 | 2,064 | 46.3% | -$16,101 | -$5.31 | -0.30 | 101h |
    | C>=3 | 4,870 | 1,294 | 1,474 | 2,102 | 46.7% | -$6,486 | -$2.34 | -0.14 | 68h |
    | C>=4 | 3,419 | 900 | 1,037 | 1,482 | 46.5% | -$10,924 | -$5.64 | -0.43 | 46h |
    | C>=5 | 2,534 | 669 | 766 | 1,099 | 46.6% | -$4,836 | -$3.37 | -0.33 | 33h |

    ### Comparison with BEFORE (global pool, Jul 2025)

    | Metric | BEFORE (Global C>=3) | Tag-Aware C>=3 | Delta |
    |--------|---------------------|----------------|-------|
    | HR | 50.6% | 46.7% | -3.9pp |
    | PnL | -$5,835 | -$6,486 | -$651 |
    | Fills | 1,317 | 4,870 | +3,553 |
    | Sharpe | -0.14 | -0.14 | 0 |
    | Avg Edge | -$4.43 | -$2.34 | +$2.09 |

    ### Vectorized vs Tick-by-Tick Gap

    | Metric | Vectorized (UB) | Tick-by-Tick | Gap |
    |--------|----------------|--------------|-----|
    | HR | 57.8% | 46.7% | -11.1pp |
    | Avg PnL | $40 | -$2.34 | -$42 |
    | N | 16,662 | 4,870 | -71% |

    Gap of 11.1pp is BELOW the expected 20-40pp range. This is because the
    tag-aware vectorized HR was already lower (57.8% vs 84.2% global), indicating
    less vectorized inflation to begin with.
    """)
    return


@app.cell
def direction_analysis(mo):
    mo.md("""
    ## Direction Breakdown (Manual Signal Analysis, C>=3)

    ### Almost Entirely YES Signals

    | Direction | Wins | Losses | Unknown | HR (resolved) |
    |-----------|------|--------|---------|---------------|
    | YES | 1,294 | 1,472 | 149 | 46.8% |
    | NO | 0 | 2 | 0 | 0.0% |

    **NO consensus>=3 almost never reached** because NO pools are too small
    (max 45 traders for Crypto NO). The strategy effectively operates as YES-only.

    ### Per-Tag YES HR (C>=3 signals, resolved)

    | Tag | YES Wins | YES Losses | YES HR | YES Base | Excess |
    |-----|----------|------------|--------|----------|--------|
    | Crypto | 1,212 | 1,152 | 51.3% | 28.9% | +22.4pp |
    | Trump | 51 | 80 | 38.9% | 34.1% | +4.8pp |
    | Culture | 19 | 128 | 12.9% | 11.7% | +1.2pp |
    | Movies | 7 | 44 | 13.7% | 9.7% | +4.0pp |
    | Elections | 2 | 20 | 9.1% | 9.0% | +0.1pp |
    | AI | 1 | 10 | 9.1% | 10.0% | -0.9pp |
    | Politics | 1 | 32 | 3.0% | 23.2% | -20.2pp |
    | Music | 1 | 4 | 20.0% | 8.8% | +11.2pp |

    ### Key Findings

    1. **Crypto YES is the ONLY tag with meaningful signal** (51.3% HR, +22.4pp excess, 2,364 signals)
    2. **Extreme-NO-bias tags are terrible for YES**: Culture 12.9%, Politics 3.0%
       -- these traders beat the aggregate base rate but not at the trade level
    3. **Aggregate excess HR != trade-level excess HR**: This is the SAME structural
       gap as vectorized-vs-tick, but WITHIN the tag-aware framework
    4. **Small sample sizes** outside Crypto: Trump has 131 signals, Culture 147,
       Movies 51 -- too few for reliable inference
    """)
    return


@app.cell
def verdict(mo):
    mo.md("""
    ## Verdict: TAG-AWARE DOES NOT IMPROVE HIT-RATE COPY

    ### Assessment: **REJECT**

    | Criterion | Result | Pass? |
    |-----------|--------|-------|
    | HR > BEFORE global (50.6%) | 46.7% | FAIL |
    | Positive PnL | -$6,486 | FAIL |
    | Compounding score > 0 | -0.28 (C>=3) | FAIL |
    | HR significantly above base | 46.7% vs ~29% YES base | PASS (but below 50%) |

    ### Root Causes

    1. **Tag-specific qualification is TOO PERMISSIVE**: Beating a 9-29% YES base
       rate is easy. Many traders who beat their tag base are still unskilled at
       predicting individual trade outcomes.

    2. **Aggregate-to-trade-level gap persists**: The structural problem of
       vectorized-vs-tick is not solved by tag-aware base rates. A trader with
       55% aggregate YES HR on Culture (11.7% base) still enters bad YES trades
       at the individual level because most Culture markets resolve NO.

    3. **Crypto concentration risk**: 81% of resolved signals are Crypto YES.
       The strategy is effectively "copy Crypto YES traders" with some noise
       from other tags.

    4. **NO direction is eliminated**: The tag-aware pool is so YES-heavy that
       NO consensus never builds. This removes the NO-collapse problem but also
       removes any NO alpha.

    5. **Direction-specific consensus helps but not enough**: The -7pp penalty
       from direction-agnostic consensus is avoided, but the fundamental
       problem is that trade-level YES predictions in NO-biased tags are poor.

    ### Comparison Summary

    | Variant | Vec HR | Tick HR | Vec-Tick Gap | Tick PnL | Compounding |
    |---------|--------|--------|--------------|----------|-------------|
    | Global (BEFORE) | 84.2% | 50.6% | -33.6pp | -$5,835 | -0.14 |
    | Tag-aware | 57.8% | 46.7% | -11.1pp | -$6,486 | -0.28 |

    Tag-aware has a SMALLER vectorized-to-tick gap (11pp vs 34pp) but WORSE
    absolute tick performance. The vectorized was already more honest (lower)
    but the tick-by-tick still can't profit.

    ### What Would Work Instead

    - **Crypto-YES-only copy**: 51.3% HR with +22.4pp excess. Positive PnL if
      entry prices are favorable. But need larger sample (only 1 month tested).
    - **Insider copy** (already validated): 57-67% HR, positive PnL. The
      multi-feature scoring IS the edge, not just HR.
    - **Tag-aware as pre-filter for insider**: Use tag base rates to contextualize
      insider excess HR (queued idea).
    """)
    return


@app.cell
def knowledge_captures(mo):
    mo.md("""
    ## Knowledge Captures

    ### 1. Tag-Specific Qualification Is Too Permissive (pitfalls/)

    > [!WARNING] Using tag-specific base rates for trader qualification admits
    > 2x more traders but does NOT improve trade-level prediction. Beating a
    > 9-29% YES base rate is too easy -- most qualified traders still cannot
    > predict individual outcomes.

    ### 2. Aggregate Excess HR Does Not Transfer to Trade Level (reinforces existing)

    > [!CRITICAL] This is a refinement of vectorized_vs_tick.md and
    > excess_hr_vs_absolute_hr.md. The gap exists EVEN when using correct
    > tag-specific base rates. The structural issue is NET POSITION HR
    > vs INDIVIDUAL TRADE HR -- these are fundamentally different.

    ### 3. Crypto YES Is the Signal, Not "Tag-Aware"

    > [!TIP] If pursuing tag-aware copy, restrict to Crypto YES only.
    > 51.3% tick HR vs 28.9% base = +22.4pp excess. But this is really
    > just a subset of the insider copy strategy with a different filter.
    """)
    return


if __name__ == "__main__":
    app.run()
