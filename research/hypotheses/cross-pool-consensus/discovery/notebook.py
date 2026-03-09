"""Cross-Pool Consensus Discovery Notebook (Marimo).

Visualizes sweep results from sweep_cross_pool.py.
All results are UPPER BOUNDS — vectorized backtests are 20-40pp optimistic vs tick.

Usage:
    PYTHONPATH=. uv run marimo run research/hypotheses/cross-pool-consensus/discovery/notebook.py
"""

import marimo as mo
import json
from pathlib import Path

# ── Cell 0: Load results ──────────────────────────────────────────────────────

results_path = Path(__file__).parent / "sweep_results.json"
with open(results_path) as f:
    data = json.load(f)

results = data["results"]

mo.md(f"""
# Cross-Pool Consensus Discovery

**ALL VALUES ARE UPPER BOUNDS** — vectorized backtests are 20-40pp optimistic vs tick.

- Train cutoff: `{data['config']['train_cutoff']}`
- Test: `{data['config']['test_start']}` → `{data['config']['test_end']}` (8 months)
- Tag: Sports YES (primary), Politics YES (secondary)
- Base rate: Sports YES = **33.3%**, Politics YES = **18.8%**
""")

# ── Cell 1: Pool independence summary ────────────────────────────────────────

pool_stats = results["pool_stats"]

rows = []
for variant, stats in pool_stats.items():
    rows.append({
        "Variant": variant,
        "Pool A": stats["pool_a_size"],
        "Pool B": stats["pool_b_size"],
        "Jaccard": stats["jaccard"],
        "Overlap": stats["overlap_size"],
    })

mo.md("""
## Pool Independence

All variants achieved **Jaccard = 0.000** (fully disjoint pools).

| Variant | Pool A | Pool B | Jaccard | Overlap |
|---------|--------|--------|---------|---------|
""" + "\n".join(
    f"| {r['Variant']} | {r['Pool A']} | {r['Pool B']} | {r['Jaccard']:.3f} | {r['Overlap']} |"
    for r in rows
))

# ── Cell 2: Single-pool baselines ─────────────────────────────────────────────

baselines = results["baselines"]

def fmt_r(r):
    if "error" in r:
        return f"— ({r['error']})"
    return f"{r['n_signals']} sig | {r['hit_rate']:.1%} HR | {r['excess_hr']:+.1%} excess | {r['avg_pnl_usd']:+.4f} PnL | {r.get('med_hold_hours','?')}h"

mo.md(f"""
## Single-Pool Baselines (K=100)

| Config | Result |
|--------|--------|
| K=100 N=1 BUY-only | {fmt_r(baselines.get('k100_n1_buy_only', {}))} |
| K=100 N=2 BUY-only | {fmt_r(baselines.get('k100_n2_buy_only', {}))} |
| K=100 N=3 BUY-only | {fmt_r(baselines.get('k100_n3_buy_only', {}))} |
| K=100 N=1 Directional | {fmt_r(baselines.get('k100_n1_directional', {}))} |
| K=100 N=2 Directional | {fmt_r(baselines.get('k100_n2_directional', {}))} |
| K=100 N=3 Directional | {fmt_r(baselines.get('k100_n3_directional', {}))} |

**Key**: BUY-only collapses at N=2 (15 signals, +13.4pp). Directional N=2 has 552 signals (+38.8pp).
""")

# ── Cell 3: Cross-pool BUY-only comparison ────────────────────────────────────

cross_pool = results["cross_pool"]

def safe_r(d, key):
    r = d.get(key, {"error": "no data"})
    if "error" in r:
        return {"n_signals": 0, "hit_rate": 0, "excess_hr": 0, "avg_pnl_usd": 0, "med_hold_hours": None, "med_gap_hours": None}
    return r

mo.md("""
## Cross-Pool BUY-Only Results (N_a=1, N_b=1)

| Variant | Signals | HR | Excess HR | Avg PnL | Med hold h | Med gap h |
|---------|---------|-----|-----------|---------|-----------|-----------|
""" + "\n".join(
    f"| {v} | {safe_r(cross_pool[v], 'na1_nb1_buy_only')['n_signals']} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_buy_only')['hit_rate']:.1%} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_buy_only')['excess_hr']:+.1%} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_buy_only')['avg_pnl_usd']:+.4f} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_buy_only').get('med_hold_hours') or '?'} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_buy_only').get('med_gap_hours') or '?'} |"
    for v in cross_pool
))

# ── Cell 4: Cross-pool Directional comparison ─────────────────────────────────

mo.md("""
## Cross-Pool Directional Results (N_a=1, N_b=1)

> WARNING: Directional fires at avg price 0.86. Break-even HR = 86%. Near-certainty in-play markets.

| Variant | Signals | HR | Excess HR | Avg PnL | Med hold h | vs Baseline N=2 dir |
|---------|---------|-----|-----------|---------|-----------|---------------------|
""" + "\n".join(
    f"| {v} | {safe_r(cross_pool[v], 'na1_nb1_directional')['n_signals']} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_directional')['hit_rate']:.1%} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_directional')['excess_hr']:+.1%} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_directional')['avg_pnl_usd']:+.4f} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_directional').get('med_hold_hours') or '?'} | "
    f"{safe_r(cross_pool[v], 'na1_nb1_directional')['excess_hr'] - 0.388:+.1%} |"
    for v in cross_pool
))

# ── Cell 5: Monthly breakdown — best directional variant ──────────────────────

best_monthly = cross_pool.get("score_axis", {}).get("na1_nb1_directional", {}).get("monthly", [])

mo.md("""
## Monthly Breakdown — score_axis Directional (N_a=1 N_b=1)

Best directional variant: 8/8 months above base rate.

| Month | Signals | HR | Avg PnL |
|-------|---------|-----|---------|
""" + "\n".join(
    f"| {m['month']} | {m['n_signals']} | {m['hit_rate']:.1%} | {m['avg_pnl']:+.4f} |"
    for m in best_monthly
))

# ── Cell 6: SELL sensitivity summary ─────────────────────────────────────────

mo.md("""
## SELL Sensitivity Analysis

| Mode | Max signals | Max HR | Max excess HR | Break-even concern? |
|------|------------|--------|--------------|---------------------|
| BUY-only | 12 | 88.9% | +55.6pp | No (avg price 0.55-0.67) |
| Directional | 377 | 92.9% | +59.6pp | YES — avg price 0.85-0.90 |

**Delta**: 30x volume difference. This is NOT a minor sensitivity.

**Root cause**: Directional includes in-play Sports markets (SELL NO = bullish at near-certainty).
BUY-only excludes all SELL trades, missing the dominant Sports signal source.

**Implication**: Cross-pool BUY-only is non-deployable (too thin).
Cross-pool Directional is likely below break-even in tick validation.
""")

# ── Cell 7: Politics secondary tag ───────────────────────────────────────────

pol = results.get("cross_pool_politics", {})

mo.md("""
## Politics YES (Secondary Tag, max_price=0.80)

Base rate: 18.8%

| Variant | Mode | Signals | HR | Excess HR | vs v3 Politics N=3 (+43.5pp) |
|---------|------|---------|-----|-----------|-------------------------------|
""" + "\n".join(
    f"| {v} | {mode.replace('na1_nb1_', '')} | "
    f"{safe_r(pol[v], f'na1_nb1_{mode.replace(\"na1_nb1_\",\"\")}')['n_signals']} | "
    f"{safe_r(pol[v], f'na1_nb1_{mode.replace(\"na1_nb1_\",\"\")}')['hit_rate']:.1%} | "
    f"{safe_r(pol[v], f'na1_nb1_{mode.replace(\"na1_nb1_\",\"\")}')['excess_hr']:+.1%} | "
    f"{safe_r(pol[v], f'na1_nb1_{mode.replace(\"na1_nb1_\",\"\")}')['excess_hr'] - 0.435:+.1%} |"
    for v in pol for mode in ["na1_nb1_buy_only", "na1_nb1_directional"]
    if v in pol
))

# ── Cell 8: Verdict ───────────────────────────────────────────────────────────

mo.md("""
## Verdict

**MARGINAL → NO-GO** for the stated cross-pool-consensus hypothesis.

**Reasons**:
1. BUY-only mode: 98% throughput collapse (12 vs 741 baseline signals)
2. Directional mode: fires at avg price 0.86 — break-even HR = 86%, right at vectorized result
3. No temporal advantage: pools fire simultaneously (med gap ≈ 0h)
4. Politics YES: no improvement over existing v3 strategy

**Genuinely new finding**: Score-axis pool construction (excess_hr vs consistency_sharpe, disjoint)
achieves **+16pp HR improvement** over random split in directional mode. This is a real signal
from orthogonal skill dimensions. Worth exploring as a standalone signal quality filter.

**Spawned**:
- `score-axis-pool-construction` [MEDIUM]: use as meta-filter on existing v3 strategies
- `sequential-cross-pool` [LOW]: enforce Pool A fires > 2h before Pool B
""")
