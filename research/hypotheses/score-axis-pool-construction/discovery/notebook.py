"""Score-Axis Pool Construction — Discovery Notebook.

Marimo notebook for exploring the vectorized discovery results.
Run with: marimo run notebook.py  (or marimo edit notebook.py for interactive mode)

All results are UPPER BOUNDS — vectorized backtests 20-40pp optimistic vs tick.
"""

import marimo as mo

# ─── Cell 0: Imports + data load ─────────────────────────────────────────────

import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

RESULTS_PATH = Path(__file__).parent / "results.json"
with open(RESULTS_PATH) as f:
    results = json.load(f)

mo.md(f"""
# Score-Axis Pool Construction — Discovery Results

**Hypothesis**: Pool A (top-K excess_hr) AND Pool B (top-K consistency_sharpe, disjoint) must
agree on direction before signaling. Pools are disjoint by construction (Jaccard = 0).

**Tag**: Sports YES | **Test window**: 2025-07-01 to 2026-03-01 | **Train cutoff**: 2025-07-01

> [!WARNING]
> **ALL RESULTS ARE UPPER BOUNDS** — vectorized backtests are 20-40pp optimistic vs tick.

**Verdict**: `{results['verdict']}` — {results['verdict_note']}
""")


# ─── Cell 1: Prior art verification ──────────────────────────────────────────

prior = results["prior_art_verification"]

mo.md(f"""
## Prior Art Verification

The parent hypothesis (cross-pool-consensus) claimed **+33.4pp excess HR** at K=50 N=1x1 BUY-only.

| Filter | Prior art applied? | Impact |
|--------|-------------------|--------|
| hold >= 4h | **NO** | 1/3 signals dropped |
| first_trade >= test_start | YES | No additional impact |
| Price-level base rate | **NO** | Excess becomes −5.96pp |

| | Without hold filter | With hold>=4h |
|-|---------------------|---------------|
| N signals | {prior['without_hold_filter']['n_signals']} | {prior['with_hold_ge_4h']['n_signals']} |
| HR | {prior['without_hold_filter']['hit_rate']:.1%} | {prior['with_hold_ge_4h']['hit_rate']:.1%} |
| Excess (tag) | +{prior['without_hold_filter']['excess_hr_tag_pp']:.1f}pp | +{prior['with_hold_ge_4h']['excess_hr_tag_pp']:.1f}pp |
| Excess (price) | N/A | **{prior['with_hold_ge_4h']['excess_hr_price_pp']:+.2f}pp** |

**Conclusion**: The prior +33.4pp was based on N=3 signals and is **spurious**.
""")


# ─── Cell 2: Universe + axis correlation ─────────────────────────────────────

uni = results["universe"]
pool_stats = results["pool_stats"]

mo.md(f"""
## Universe

- **Qualified traders**: {uni['qualified_traders_total']:,} (n_markets >= 20, excess_hr > 0, conviction >= 0.90)
- **Axis correlation** (Spearman approx): **{uni['axis_correlation_spearman_approx']:.3f}**
  — genuinely orthogonal (< 0.5 = independent axes)
- **Tag base rate** (test window): {uni['tag_base_rate_test_window']:.1%}
- **Sports markets**: {uni['sports_markets_total']:,}

### Pool Composition by K

| K | Pool A (excess_hr) avg HR | Pool B (consistency) avg HR | Pool B avg sharpe |
|---|--------------------------|----------------------------|-------------------|
| 25 | {pool_stats['k25']['pool_a_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k25']['pool_b_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k25']['pool_b_avg_consistency_sharpe']:.1f} |
| 50 | {pool_stats['k50']['pool_a_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k50']['pool_b_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k50']['pool_b_avg_consistency_sharpe']:.1f} |
| 100 | {pool_stats['k100']['pool_a_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k100']['pool_b_avg_excess_hr'] + uni['tag_base_rate_test_window']:.1%} | {pool_stats['k100']['pool_b_avg_consistency_sharpe']:.1f} |

Note: Pool B (consistency_sharpe) selects traders with stable but moderate HR (~60-62%),
significantly lower than Pool A (~97-108% of pool A's raw HR). The AND-gate is between
a sharp pool and a mediocre-consistent pool.
""")


# ─── Cell 3: BUY-only top combos ─────────────────────────────────────────────

buy_combos = results["buy_only_results"]["top_combos"]
rows_buy = []
for r in buy_combos:
    price_adj = f"{r.get('excess_hr_price_pp', 'N/A'):+.1f}pp" if isinstance(r.get('excess_hr_price_pp'), (int, float)) else "N/A"
    rows_buy.append(
        f"| K={r['k_each']} N={r['n_a']}x{r['n_b']} | {r['n_signals']} | {r['hit_rate']:.1%} | "
        f"+{r['excess_hr_tag_pp']:.1f}pp | {price_adj} | {r['avg_signal_price']:.3f} | "
        f"${r['avg_pnl_usd']:.4f} | {r['med_hold_hours']:.1f}h | "
        f"{r.get('compounding_score', 'N/A'):.4f} | {'FRAGILE' if r.get('fragile') else 'OK'} |"
    )

mo.md(f"""
## BUY-Only Results (UPPER BOUNDS)

Only direct BUY YES trades counted. Pool B must have a direct BUY YES position in the market.

| Combo | N | HR | Excess(tag) | Excess(price) | Avg price | PnL/trade | Med hold | CS | Status |
|-------|---|----|-------------|--------------|-----------|-----------|----------|----|--------|
{chr(10).join(rows_buy) if rows_buy else '| None | — | — | — | — | — | — | — | — | — |'}

> [!CRITICAL]
> All BUY-only combos have N < 30 — **insufficient statistical power**.
> K=100 N=1x2 with N=4 signals is meaningless. **BUY-only is NOT VIABLE.**
""")


# ─── Cell 4: Directional top combos ──────────────────────────────────────────

dir_combos = results["directional_results"]["top_combos"]
rows_dir = []
for r in dir_combos:
    price_adj = f"{r.get('excess_hr_price_pp', 'N/A'):+.1f}pp" if isinstance(r.get('excess_hr_price_pp'), (int, float)) else "N/A"
    rows_dir.append(
        f"| K={r['k_each']} N={r['n_a']}x{r['n_b']} | {r['n_signals']} | {r['hit_rate']:.1%} | "
        f"+{r['excess_hr_tag_pp']:.1f}pp | {price_adj} | {r['avg_signal_price']:.3f} | "
        f"${r['avg_pnl_usd']:.4f} | {r['med_hold_hours']:.1f}h | "
        f"{r.get('compounding_score', 'N/A'):.4f} | {'FRAGILE' if r.get('fragile') else 'OK'} |"
    )

mo.md(f"""
## Directional Results (SELL NO = bullish) — UPPER BOUNDS

Includes SELL NO routes (split-entry) as YES exposure from maker_positions net.

| Combo | N | HR | Excess(tag) | Excess(price) | Avg price | PnL/trade | Med hold | CS | Status |
|-------|---|----|-------------|--------------|-----------|-----------|----------|----|--------|
{chr(10).join(rows_dir)}

### Key Observation: Price-Level-Adjusted Excess

The real edge measure is **excess(price)** — how much better than any trader at the same entry price.

- K=50 N=1x1: +8.2pp price-adj (best, but **FRAGILE** — K-25 kills signal entirely)
- K=100 N=1x1: +5.5pp price-adj (not fragile, but near-zero PnL = $0.001/trade)
- K=100 N=2x1: +0.8pp price-adj (essentially no alpha above base)

After 20-40pp vectorized→tick degradation:
- K=50 N=1x1: expected tick excess = 8.2pp - 20 to 40pp = **-12pp to -32pp** (negative)
""")


# ─── Cell 5: Monthly breakdown (best combo) ──────────────────────────────────

best_dir = dir_combos[0] if dir_combos else None
if best_dir and best_dir.get("monthly"):
    monthly_rows = []
    for m in best_dir["monthly"]:
        monthly_rows.append(
            f"| {m['month']} | {m['n_signals']} | {m['hit_rate']:.1%} | ${m['avg_pnl']:.4f} |"
        )
    monthly_table = "\n".join(monthly_rows)
    mo.md(f"""
## Monthly Breakdown — Best Combo (K=50 N=1x1 Directional)

| Month | N | HR | PnL/trade |
|-------|---|----|-----------|
{monthly_table}

**Aug 2025 problem**: 18 signals at 44.4% HR — BELOW break-even at 0.67 entry (BE=67%).
Drags down the overall 8-month HR. Last 5 months show stronger performance (82% avg).

Possible explanations:
1. Seasonal effect: fall/winter sports (NBA/NFL) different signal profile than summer
2. Training data bias: model trained to Jul 2025, some leakage in early test period
3. Random variance: N=18 is still small — could be statistical noise
""")


# ─── Cell 6: Hold time distribution ──────────────────────────────────────────

mo.md(f"""
## Hold Time Distribution

All combos (hold >= 4h filter applied):

| Combo | Med hold | P25 | P75 | Comment |
|-------|----------|-----|-----|---------|
| K=50 N=1x1 dir | 5.2h | 4.4h | 8.8h | Most signals at 4-9h |
| K=50 N=1x2 dir | 9.2h | 5.1h | 13.3h | Pool B wait adds ~4h |
| K=100 N=1x1 dir | 5.6h | 4.6h | 11.2h | Similar to K=50 |
| K=100 N=2x1 dir | 7.2h | 4.8h | 12.9h | Pool A wait adds ~2h |

All combos: same-day resolution (~5-9h from signal to resolution).
Capital recycling is fast, but avg PnL/trade is very small ($0.001-$0.034).
Monthly dollar return = signals/month × avg_pnl = 8.1 × $0.034 = **$0.28/month** (per $1 position).

With $100 position size: ~$28/month gross → **not economically meaningful**.
""")


# ─── Cell 7: Sensitivity analysis ────────────────────────────────────────────

sens_rows = []
for s in results["sensitivity_analysis"]:
    p = s["params"]
    base_label = f"K={p['k_each']} N={p['n_a']}x{p['n_b']} {p['sell_mode'][:3]}"
    for pert in s["perturbations"]:
        if "error" in pert:
            sens_rows.append(f"| {base_label} | K={pert['k_perturb']} | 0 signals | — | FRAGILE |")
        else:
            sens_rows.append(
                f"| {base_label} | K={pert['k_perturb']} | {pert['n_signals']} signals | "
                f"{pert['hr_delta_pp']:+.1f}pp | {'FRAGILE' if pert.get('fragile') else 'OK'} |"
            )

mo.md(f"""
## Sensitivity Analysis

For each top-3 combo, K varied ±25:

| Base combo | K perturbed | N signals | HR delta | Status |
|-----------|-------------|-----------|----------|--------|
{chr(10).join(sens_rows)}

> [!WARNING] **ALL TOP COMBOS ARE FRAGILE**
> Every working combo collapses to 0 signals at K-25, and drops 7-10pp HR at K+25.
> The FRAGILE threshold is 5pp — all three exceed it. These results should NOT
> be extrapolated or treated as robust signals.

The fragility means the effective K-range for viable signals is roughly K=80-120
for directional mode. Below K=80, no cross-pool overlap in test markets.
""")


# ─── Cell 8: SELL sensitivity comparison ─────────────────────────────────────

mo.md(f"""
## SELL Mode Comparison

| Mode | N (K=50 N=1x1) | HR | Excess(price) | Avg price |
|------|---------------|----|---------------|-----------|
| BUY-only | 2 | 50.0% | -5.96pp | 0.618 |
| Directional | 65 | 70.8% | +8.21pp | 0.674 |

**SELL sensitivity = CRITICAL** (>5pp HR gap).

The 30x signal count difference reveals:
- BUY-only: requires explicit BUY YES from both pools (very rare in Sports w/ hold>=4h)
- Directional: includes SELL NO (split-entry to YES) — adds 63/65 signals

The strategy in directional mode is measuring "any qualified trader with net YES exposure"
not "skilled traders who explicitly bought YES". This semantic shift may explain
why the price-level excess is only +8.2pp — the SELL NO positions are entered
at market prices (not carefully timed entries).

{results['sell_sensitivity_note']}
""")


# ─── Cell 9: Spawned ideas ────────────────────────────────────────────────────

ideas = results.get("spawned_ideas", [])
ideas_md = "\n".join(
    f"### {i['name']} [{i.get('priority', 'N/A')}]\n{i['description']}\n"
    for i in ideas
)

mo.md(f"""
## Spawned Ideas

{ideas_md}

### Additional idea from analysis: sports-yes-single-pool-price-gated [HIGH PRIORITY]
Instead of dual-pool construction, use single Pool A (top-100 excess_hr) with max_price=0.55.
Forces signals to fire in genuine uncertainty zone (40-55% base rate) where real alpha
from skilled traders is visible. The dual-pool AND-gate adds little when Pool B
is "consistently mediocre" traders entering at near-certainty prices.
""")
