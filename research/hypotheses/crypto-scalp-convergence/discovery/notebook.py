"""BTC Up/Down Scalp Convergence — Discovery Notebook (Marimo)

Run with: PYTHONPATH=. uv run marimo run research/hypotheses/crypto-scalp-convergence/discovery/notebook.py
"""
import marimo as mo
import json
from pathlib import Path

app = marimo.App()

# Cell 0: Setup + Load Results
@app.cell
def setup():
    import json
    from pathlib import Path
    import polars as pl
    import numpy as np

    ROOT = Path("/mnt/nvme/git/polymarket/polymarket")
    results_path = ROOT / "research/hypotheses/crypto-scalp-convergence/discovery/results.json"
    results = json.loads(results_path.read_text())

    mo.md(f"""
    # BTC Up/Down Scalp Convergence — Discovery Results

    **Hypothesis**: PM BTC Up/Down prices lag Binance GBM fair value. A scalp strategy
    can buy the underpriced side during the lag and sell when prices converge.

    **Verdict**: `{results['verdict'].upper()}`

    **Note**: {results['note']}

    **Date range**: {results['universe']['date_range'][0]} → {results['universe']['date_range'][1]}
    """)
    return results, ROOT, mo


# Cell 1: Universe Overview
@app.cell
def universe(results, mo):
    u = results["universe"]
    df = results.get("dislocation_frequency", {})
    pctiles = df.get("deviation_pctiles_all_second_snapshots", {})

    mo.md(f"""
    ## Universe

    | Metric | Value |
    |--------|-------|
    | Total resolved windows (90d) | {u['total_resolved_windows_90d']:,} |
    | Windows with PM trade data | {u['windows_with_trade_data']:,} |
    | 5-minute windows | {u['duration_breakdown']['5min']:,} |
    | 15-minute windows | {u['duration_breakdown']['15min']:,} |

    ## GBM-PM Deviation Structure

    | Metric | Value |
    |--------|-------|
    | Windows with max |dev| > 5% | {df['pct_windows_max_dev_gt_5pct']:.1%} |
    | Windows with max |dev| > 10% | {df['pct_windows_max_dev_gt_10pct']:.1%} |
    | Windows with max |dev| > 15% | {df['pct_windows_max_dev_gt_15pct']:.1%} |

    **|GBM_P(Up) - PM_P(Up)| distribution** across all per-second snapshots:

    | p10 | p25 | **p50** | p75 | p90 | p95 | p99 |
    |-----|-----|---------|-----|-----|-----|-----|
    | {pctiles.get('p10',0):.3f} | {pctiles.get('p25',0):.3f} | **{pctiles.get('p50',0):.3f}** | {pctiles.get('p75',0):.3f} | {pctiles.get('p90',0):.3f} | {pctiles.get('p95',0):.3f} | {pctiles.get('p99',0):.3f} |

    The median per-second |deviation| is **{pctiles.get('p50',0):.1%}** — PM prices are
    consistently and substantially misaligned with GBM fair value.
    """)
    return ()


# Cell 2: Scalp Strategy Results by Threshold
@app.cell
def threshold_results(results, mo):
    bt = results["by_threshold"]

    rows = []
    for key, r in bt.items():
        thresh = float(key.split("_")[1])
        p = r.get("convergence_time_pctiles_sec", {})
        rows.append({
            "Entry Threshold": f"{thresh:.0%}",
            "Events": r["n_events"],
            "Conv. Rate": f"{r['convergence_rate']:.1%}",
            "Hold p10 (s)": p.get("p10"),
            "Hold p50 (s)": p.get("p50"),
            "Hold p90 (s)": p.get("p90"),
            "Median Gross PnL": f"{r['median_gross_pnl']:.1%}",
            "Median Net PnL": f"{r['median_net_pnl']:.1%}",
            "Hit Rate": f"{r['hit_rate']:.1%}",
            "Pct Profitable": f"{r['pct_profitable']:.1%}",
        })

    import polars as pl
    tbl = pl.DataFrame(rows)

    return mo.md(f"""
    ## Scalp Strategy Results (UPPER BOUNDS — vectorized, zero latency)

    {tbl.to_pandas().to_html(index=False)}

    ### Critical Observation: Hit Rate < 50%

    Hit rate (~42-48%) means GBM direction is **wrong about half the time**. The
    profit comes from **mean-reversion** (PM price snapping back), not from GBM
    correctly predicting the outcome.

    ### Feasibility Gate

    - **5% threshold**: median hold = 12s — BORDERLINE. Tight with websocket latency.
    - **10% threshold**: median hold = 22s — More workable. 12,564 events in 90 days (~140/day).
    - **15% threshold**: median hold = 42s — Comfortable. 10,999 events (122/day).

    Fee assumption: 3% per side = 6% round-trip.
    """)


# Cell 3: Regime Analysis — Duration Comparison
@app.cell
def regime_duration(results, mo):
    bt = results["by_threshold"]
    thresh_10 = bt.get("thresh_0.1", {})
    regime = thresh_10.get("regime_breakdown", {})

    d5 = regime.get("duration_5min", {})
    d15 = regime.get("duration_15min", {})

    return mo.md(f"""
    ## Regime Analysis — Entry Threshold = 10% (Best Combo)

    ### By Window Duration

    | Duration | Events | Median Net PnL | Median Hold | Hit Rate | Conv. Rate |
    |----------|--------|----------------|-------------|----------|-----------|
    | 5-min    | {d5.get('n',0):,} | +{d5.get('median_net_pnl',0):.1%} | {d5.get('median_hold_sec',0):.0f}s | {d5.get('hit_rate',0):.1%} | {d5.get('convergence_rate',0):.1%} |
    | 15-min   | {d15.get('n',0):,} | +{d15.get('median_net_pnl',0):.1%} | {d15.get('median_hold_sec',0):.0f}s | {d15.get('hit_rate',0):.1%} | {d15.get('convergence_rate',0):.1%} |

    5-min windows: faster convergence, similar PnL. Both viable.

    ### By Hour of Day (UTC)

    | Hours | n | Median Net PnL | Median Hold |
    |-------|---|----------------|-------------|
    {"".join(f"| {k} | {v['n']} | +{v['median_net_pnl']:.1%} | {v['median_hold_sec']:.0f}s |" + chr(10) for k, v in regime.get('by_hour_utc', {}).items())}

    No significant time-of-day effect. Strategy works 24/7.

    ### By Volatility Quartile

    | Quartile | n | Median Net PnL | Median Hold |
    |---------|---|----------------|-------------|
    {"".join(f"| {k} | {v['n']} | +{v['median_net_pnl']:.1%} | {v['median_hold_sec']:.0f}s |" + chr(10) for k, v in regime.get('by_volatility_quartile', {}).items())}

    Robust across all vol regimes. High vol = faster convergence.

    ### By BTC Direction

    | BTC Direction | n | Median Net PnL |
    |---------------|---|----------------|
    | Up (BTC rose) | {regime.get('btc_Up', {}).get('n', 0)} | +{regime.get('btc_Up', {}).get('median_net_pnl', 0):.1%} |
    | Down (BTC fell) | {regime.get('btc_Down', {}).get('n', 0)} | +{regime.get('btc_Down', {}).get('median_net_pnl', 0):.1%} |

    Slightly better when BTC is falling, but both directions are profitable.
    """)


# Cell 4: Execution Feasibility Analysis
@app.cell
def feasibility(results, mo):
    kf = results["key_findings"]
    spread = results.get("implied_half_spread_p50", 0.01)

    return mo.md(f"""
    ## Execution Feasibility Analysis

    ### Latency Budget

    | Component | Typical | Worst-case |
    |-----------|---------|------------|
    | PM trade event → websocket message | 50-200ms | 500ms |
    | Network + processing | 10-50ms | 100ms |
    | Order placement (REST) | 200-500ms | 1000ms |
    | Order confirmation | 100-300ms | 500ms |
    | **Total round-trip** | **0.4-1.1s** | **2.1s** |

    **Median convergence at 5% threshold: {kf['median_convergence_sec_at_5pct']:.0f}s**

    After entering (at ~1s into convergence), remaining window: ~11s at median.
    But 10% of events converge in under 4s — those would be complete misses.

    ### Market Impact

    - Implied half-spread: {spread:.1%} (from trade-to-trade price changes)
    - PM BTC Up/Down books are thin (short-duration binary markets)
    - For $200 notional: impact likely < 0.5% additional cost
    - For $1,000 notional: impact may approach 1-2%, potentially offsetting edge

    **Recommended position size**: $100-300 per scalp

    ### Theoretical Daily Capacity (10% threshold)

    - Events/day: ~140 (12,564 / 90 days)
    - Median net PnL per scalp: +8.7%
    - At $200/scalp: $17.40 × 140 = **$2,436/day UPPER BOUND**
    - Expected real fill: 40-60% of theoretical = **$975-1,460/day**

    ### Assessment: {kf['feasibility_assessment'].upper()}

    {kf['feasibility_assessment'].capitalize()} viability. The edge exists in principle
    but requires sub-second execution infrastructure. Not achievable with a naive
    REST-polling strategy. Requires:
    1. Websocket connection to PM CLOB orderbook feed
    2. Websocket connection to Binance 1s bars
    3. GBM computation on each price update (trivial)
    4. Automated order placement via CLOB API
    """)


# Cell 5: Key Critical Issues
@app.cell
def critical_issues(mo):
    return mo.md("""
    ## Critical Issues Before Deployment

    ### Issue 1: PM Price Proxy Quality

    This analysis uses **last trade price** as PM P(Up). In reality:
    - The last trade may be at an extreme (thin market, single large trade)
    - The real signal should come from the **orderbook mid-price**, not last trade
    - This inflates apparent deviation by unknown amount (est. 20-40%)

    **Required validation**: replay with actual orderbook snapshots to measure true
    mid-price deviation.

    ### Issue 2: GBM Model Accuracy

    GBM assumes log-normal returns with constant 24h sigma. BTC has:
    - Fat tails (10% of deviations > 15% — possibly just model error)
    - Jump risk (crashes/rallies within 15-min windows)
    - Intra-day sigma patterns

    **Required**: Test if replacing 24h sigma with 1h or 2h rolling sigma reduces
    apparent deviation while keeping exploitable signal.

    ### Issue 3: Execution Mode

    The SimulatedExecutor fills at `max_price` (a known artifact). This is NOT
    how this strategy works — we'd fill at the **current best ask** (to buy YES).
    The gap between GBM fair value and best ask determines actual PnL, not `max_price`.

    **Required**: Tick-by-tick replay with orderbook data to validate.

    ### Issue 4: Competing Arbitrageurs

    If this edge is this large (+8.7% per scalp at 10% threshold), sophisticated
    market-makers have likely already discovered it. The observed deviation may
    already be net of competitive pressure. We need to check if fill rates decline
    for large orders.

    ## SELL Dual-Test Note

    This strategy generates signals from **Binance price movements**, not from
    trader SELL/BUY behavior. The SELL dual-test requirement applies to trader-following
    strategies. This hypothesis is price-arbitrage based — SELL handling is not
    applicable to signal generation. Noted in discovery.
    """)


# Cell 6: Spawned Ideas
@app.cell
def spawned(results, mo):
    ideas = results.get("spawned_ideas", [])
    caps = results.get("knowledge_captures", [])

    ideas_md = "\n".join(f"- {idea}" for idea in ideas)
    caps_md = "\n".join(f"- {cap}" for cap in caps)

    return mo.md(f"""
    ## Spawned Ideas

    {ideas_md}

    ## Knowledge Captures

    {caps_md}

    ## Recommendation

    **MARGINAL at 5% threshold, potentially PROMISING at 10-15% threshold.**

    Proceed to tick-by-tick validation only if:
    1. Orderbook mid-price data is available (not just last trade)
    2. Binance 1s bars can be streamed in real-time
    3. Infrastructure can achieve <1.5s round-trip execution

    If infrastructure is not yet in place, **deprioritize** — the edge is time-sensitive
    and cannot be validated without low-latency data.
    """)


if __name__ == "__main__":
    app.run()
