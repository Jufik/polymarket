# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo", "polars", "plotly", "numpy"]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S2 Hit-Rate Copy: Tick-by-Tick Validation")


@app.cell
def setup():
    import marimo as mo
    import json
    from pathlib import Path

    OUTPUT_DIR = Path("research/output/s2_tick_hitrate")
    return mo, json, Path, OUTPUT_DIR


@app.cell
def hypothesis(mo):
    mo.md("""
    # S2 Hit-Rate Copy: Tick-by-Tick Validation

    ## Hypothesis

    **Signal**: Traders with excess hit rate (Bayesian) above direction-specific base rate.
    **Thesis**: Copying BUY trades from skilled traders with tiered conviction (seed + scale) builds an edge.
    **Null**: Strategy HR equals base rate after tick-by-tick degradation.

    ## Config (recommended from vectorized estimation)
    ```
    min_positions=30, min_excess_hr=0.10, recency_months=6
    direction=BOTH, max_entry_price=0.95
    exclude_tags=(Sports, Weather) -- tag-based, not m.category
    use_bayesian_hr=True, scale_threshold=3, seed_threshold=3
    position_size_usd=100, max_signal_price=0.85, max_hold_hours=168
    ```

    ## Success criteria
    - HR significantly above blended base rate (~50%)
    - Positive PnL after realistic slippage
    - Vectorized-to-tick gap within expected 20-40pp range
    """)
    return


@app.cell
def knowledge(mo):
    mo.md("""
    ## Knowledge Context

    > [!CRITICAL] Vectorized results are 20-40pp optimistic vs tick-by-tick.
    > Vectorized HR 83-86% implies realistic range 43-66%.

    > [!CRITICAL] SELL is exit, not signal. Filter `side != 'BUY'` in on_trade().

    > [!CRITICAL] Consensus counts unique traders (set.add), never trade events (counter+=1).

    > [!CRITICAL] Resolution uses asset_id (token_won), never string matching.

    > [!CRITICAL] Settlement must happen mid-replay or results are meaningless.

    > [!WARNING] NO base rate is 62-74%. A 55% NO HR is BELOW baseline.

    > [!WARNING] Entry price filter can INVERT between vectorized and tick-by-tick.

    > [!WARNING] markets.category is 99.3% NULL. Use tag chain for exclusion.
    """)
    return


@app.cell
def base_results(mo, json, Path, OUTPUT_DIR):
    # Load validation results
    results_path = OUTPUT_DIR / "validation_results.json"
    with open(results_path) as f:
        all_results = json.load(f)

    # Base config results
    base = [r for r in all_results if r["config_label"] == "base"]

    # Vectorized upper bounds
    vec = {
        "2025-04": {"hr": 0.829, "pnl_per_pos": 241.0},
        "2025-07": {"hr": 0.842, "pnl_per_pos": 145.0},
        "2025-10": {"hr": 0.855, "pnl_per_pos": 155.0},
    }

    # Period base rates
    period_br = {
        "2025-04": {"YES": 0.29, "NO": 0.71},
        "2025-07": {"YES": 0.26, "NO": 0.74},
        "2025-10": {"YES": 0.37, "NO": 0.63},
    }

    # Build comparison table
    rows = []
    for r in base:
        p = r["period"]
        v = vec[p]
        gap = v["hr"] - r["hit_rate"]
        flag = "FLAG (>40pp)" if gap > 0.40 else "OK (20-40pp)" if gap > 0.20 else "GOOD (<20pp)"
        rows.append({
            "Period": p,
            "Vec HR (UB)": f"{v['hr']:.1%}",
            "Tick HR": f"{r['hit_rate']:.1%}",
            "Gap (pp)": f"{gap*100:.1f}",
            "Vec PnL/pos": f"${v['pnl_per_pos']:.0f}",
            "Tick PnL/pos": f"${r['avg_edge']:.2f}",
            "Tick PnL Total": f"${r['total_pnl_net']:,.0f}",
            "Fills": r["total_fills"],
            "Sharpe": f"{r['sharpe']:.2f}",
            "Flag": flag,
        })

    mo.md(f"""
    ## Phase 1: Base Config (Vectorized vs Tick-by-Tick)

    | Period | Vec HR (UB) | Tick HR | Gap (pp) | Vec PnL/pos | Tick PnL/pos | Tick PnL | Fills | Flag |
    |--------|-------------|---------|----------|-------------|-------------|----------|-------|------|
    {"".join(f"| {r['Period']} | {r['Vec HR (UB)']} | {r['Tick HR']} | {r['Gap (pp)']} | {r['Vec PnL/pos']} | {r['Tick PnL/pos']} | {r['Tick PnL Total']} | {r['Fills']} | {r['Flag']} |" + chr(10) for r in rows)}

    **Assessment**: The 33-39pp degradation is within the expected 20-40pp range but at the upper end.
    All periods show marginal or negative PnL. The strategy does NOT survive tick-by-tick validation
    in its current form.
    """)
    return base, vec, period_br


@app.cell
def variant_results(mo, json, OUTPUT_DIR):
    results_path = OUTPUT_DIR / "validation_results.json"
    with open(results_path) as f:
        all_results = json.load(f)

    # Group by config_label
    configs = {}
    for r in all_results:
        label = r["config_label"]
        if label not in configs:
            configs[label] = []
        configs[label].append(r)

    mo.md(f"""
    ## Phase 2: Parameter Variants

    | Config | Period | Traders | Fills | HR | PnL | Sharpe | Avg Hold (h) | Rejected |
    |--------|--------|---------|-------|-----|-----|--------|-------------|----------|
    {"".join(f"| {r['config_label']} | {r['period']} | {r['n_traders']} | {r['total_fills']} | {r['hit_rate']:.1%} | ${r['total_pnl_net']:,.0f} | {r['sharpe']:.2f} | {r['avg_hold_hours']:.0f} | {r['n_rejected']} |" + chr(10) for r in all_results)}

    **Key observations:**
    - `hold72h` is IDENTICAL to `base` -- **ReplayRunner does not call on_timer()**,
      so max_hold_hours is non-functional. This is a simulation gap.
    - `scale4` (stricter consensus) shows slightly higher HR (47-51%) with fewer fills
      and closer to breakeven PnL
    - YES-only variant failed due to CH timeout (empty pool query issue)
    """)
    return configs


@app.cell
def direction_diagnosis(mo):
    mo.md("""
    ## Phase 3: Direction Diagnosis (Root Cause Analysis)

    The vectorized analysis computes HR per (trader, direction). A YES-qualified trader
    only has their YES positions counted. But the tick-by-tick strategy counts consensus
    per condition_id WITHOUT filtering by the trader's qualified direction.

    ### Direction-aware variants (manual simulation, Apr 2025)

    | Variant | Fills | Resolved | Won | HR | YES HR | YES excess | NO HR | NO excess |
    |---------|-------|----------|-----|-----|--------|-----------|-------|----------|
    | Naive (current) | 690 | 690 | 318 | 46.1% | 37.0% | +8.0pp | 56.3% | -14.7pp |
    | Directed consensus | 511 | 511 | 253 | 49.5% | 41.7% | +12.7pp | 61.4% | -9.6pp |
    | Same-dir consensus | 442 | 442 | 234 | 52.9% | 46.4% | +17.4pp | 63.9% | -7.1pp |

    ### All 3 periods: Same-direction consensus (best variant)

    | Period | HR | YES HR | YES excess | NO HR | NO excess | Fills |
    |--------|-----|--------|-----------|-------|----------|-------|
    | Apr 25 | 52.9% | 46.4% | +17.4pp | 63.9% | -7.1pp | 442 |
    | Jul 25 | 53.6% | 43.8% | +17.8pp | 60.5% | -13.5pp | 840 |
    | Oct 25 | 50.7% | 43.9% | +6.9pp | 62.5% | -0.5pp | 907 |

    ### Critical finding: NO direction destroys value

    NO HR is BELOW the NO base rate in all periods (except Oct which is barely above).
    This mirrors the S1 finding: NO HR collapses from vectorized to tick-by-tick (82% -> 34%
    for S1, -7 to -15pp below base for S2).

    YES direction shows genuine excess HR (+7 to +18pp above base). However, YES base rate
    is only 26-37%, so even 44-46% absolute HR means 54-56% of positions lose.
    """)
    return


@app.cell
def root_causes(mo):
    mo.md("""
    ## Root Causes (5 identified)

    ### 1. Direction-agnostic consensus (impact: -7pp HR)
    The strategy counts any qualified trader in consensus regardless of their qualified
    direction. A YES-qualified trader buying a NO token contributes to NO-direction
    consensus, but their NO positions are not what made them qualified.

    **Fix**: Filter consensus participants by direction. Only count a trader in consensus
    for direction X if they are qualified in direction X.

    ### 2. NO HR collapse in tick-by-tick (impact: -8 to -15pp below NO base)
    NO-qualified traders show excess HR in vectorized (aggregate net position) but not
    at the specific trade level. The vectorized analysis uses final net position (which
    averages across many trades). Tick-by-tick enters at the Nth qualifying trade,
    which is typically a worse entry point.

    This is a fundamental structural issue, not a bug. NO direction is inherently harder
    to copy because:
    - NO is the default outcome (62-74% base rate)
    - Entering at a specific trade price for NO is worse than the blended average
    - Many "NO wins" happen after price already moved to 0.90+

    ### 3. UNKNOWN outcomes (impact: 50-180 fills per period)
    Fills where the token_map lookup fails default to outcome="YES" incorrectly.
    These positions are settled using the wrong YES asset_id, causing systematic losses.

    **Fix**: Skip trades where asset_id is not in the inverted token_map.

    ### 4. Entry price shift (impact: ~-2pp PnL)
    RealisticFillSimulator fills at max_price = price + 0.02, which is worse than the
    trade price. Combined with slippage fees (~$2.50/fill), this erodes marginal edge.

    ### 5. on_timer() not called by ReplayRunner (impact: max_hold_hours is non-functional)
    The ReplayRunner only calls on_trade() in its main loop. It never calls on_timer(),
    so the hold timeout mechanism does nothing. All positions are held until resolution.
    """)
    return


@app.cell
def compounding_scores(mo, base):
    mo.md("""
    ## Compounding Scores

    Using blended base rate of ~50% for BOTH direction:
    """)

    rows = []
    for r in base:
        if r["avg_hold_hours"] > 0 and (r["win_count"] + r["loss_count"]) > 0:
            hold_days = r["avg_hold_hours"] / 24
            excess = r["hit_rate"] - 0.50
            cs = excess * r["avg_edge"] / hold_days if hold_days > 0 else 0
            rows.append(f"| {r['period']} | {r['hit_rate']:.1%} | {excess:+.1%} | ${r['avg_edge']:.2f} | {hold_days:.1f}d | {cs:.2f} |")

    mo.md(f"""
    | Period | HR | Excess HR | Avg Edge | Hold | Comp Score |
    |--------|-----|----------|----------|------|-----------|
    {"".join(r + chr(10) for r in rows)}

    All compounding scores are near zero or negative. The strategy has no edge
    in tick-by-tick validation.

    For comparison, S2 Insider Copy (validated): compounding scores 3.11-12.37.
    """)
    return


@app.cell
def verdict(mo):
    mo.md("""
    ## Verdict: FAIL

    The S2 Hit-Rate Copy strategy does NOT survive tick-by-tick validation.

    **Quality gate results:**
    - [x] Minimum 100 trades OOS (740-1579 fills per period)
    - [ ] HR significantly above base rate -- **FAIL** (45.9-50.6% vs ~50% blended)
    - [ ] Positive PnL after realistic slippage -- **FAIL** (2 of 3 periods negative)
    - [x] No look-ahead bias
    - [x] Robust to parameter perturbation (scale4 variant comparable)
    - [x] Documented in marimo notebook
    - [ ] Compounding score > 1.0 -- **FAIL** (near zero)

    **The core problem**: The vectorized analysis leverages per-trader per-direction HR,
    but tick-by-tick cannot replicate this because:
    1. We don't know which direction a trader will buy in advance
    2. NO direction HR collapses in tick-by-tick (structural, not a bug)
    3. Consensus is per-market, not per-direction

    **Possible salvage paths** (for future research):
    1. **Direction-aware consensus** (+7pp HR): Only count traders in consensus for
       the direction they're buying, and only if qualified in that direction.
       Still marginal (50-54% HR) but worth testing with PnL.
    2. **YES-only strategy**: Ignore NO direction entirely. YES shows +7 to +18pp
       excess but low absolute HR (43-46%). May be profitable with low entry prices.
    3. **Combine with Insider Copy**: Use hit-rate as a secondary filter on the
       insider pool (which already shows 57-67% tick-by-tick HR).

    **Status**: Moving to TESTED (REJECTED). Direction-aware consensus and YES-only
    variants queued as follow-up ideas.
    """)
    return


if __name__ == "__main__":
    app.run()
