# Forward Pricing Backtest: Tautology-Free Results

**Date**: 2026-02-17
**Engine**: `strategies/consistency_copy/backtester/` with forward asof join
**Data**: 70.9M trader-market PnL rows, 340M price records, 390K resolved markets

---

## Two Bugs Found & Fixed

### Bug 1: Resolution Value Semantics

**Problem**: The CLOB API uses `resolution_value=1` for ALL resolved markets (both YES-won and NO-won), `resolution_value=0` for unresolved, and `-1` for voided. The sweep code (`sweep.py:110`) treated `resolution_value=1` as "YES won" and `resolution_value=0` as "NO won" — this made ALL YES-only signals "win" because every resolved market has `resolution_value=1`.

**Fix**: Added `yes_won` boolean column to `markets_resolved.parquet`, computed from `token_map.parquet` by checking if `token_index=0` (affirmative side) has `winner=True`. The sweep now uses `yes_won` instead of `resolution_value` to determine bet outcomes.

| Metric | Before Fix | After Fix |
|--------|-----------|-----------|
| YES-only avg hit rate | 100% (tautological) | 18.5% |
| NO-only avg hit rate | 0% (tautological) | 45.3% |
| Meaningful? | No | Yes |

### Bug 2: Direction Inference Tautology

**Problem**: `_infer_bet_yes(market_pnl, resolution_value)` determined trader direction from PnL + outcome. A trader with positive PnL on a YES-won market was classified as "bet YES" — but this is circular because profitable traders on YES-won markets MUST have bet YES by definition.

**Fix**: Added `net_yes_tokens` column to `trader_market_pnl.parquet`. This is the net position in token_index=0 (affirmative) tokens, computed from actual trade data. `bet_yes = net_yes_tokens > 0` uses real trade direction, not outcomes.

### Revert: Resolution Filter

The previous "fix" changing `resolution_value == 1` to `>= 0` was incorrect. The 47,679 rows with `resolution_value=0` are **unresolved markets**, not NO-resolved markets. Reverted to `== 1` (390,211 truly resolved markets).

---

## Entry Price Model: Forward Asof (t+dt)

When the copy signal fires (Nth skilled trader enters a market), the realistic entry price is the **next available trade** after the signal time — not the backward-looking last trade before.

### Forward Price Diagnostics

| Window | Coverage | Median dt | Mean dt | P95 dt | P99 dt |
|--------|----------|-----------|---------|--------|--------|
| 2024 H1 | 72.4% | 45s | 542s | 2,642s | 3,378s |
| 2024 H2 | 90.8% | 0s | 274s | 1,775s | 3,080s |
| 2025 H1 | 95.6% | 0s | 146s | 992s | 2,648s |
| Dec 2025 | 99.0% | 0s | 28s | 0s | 1,056s |
| Jan 2026 | 98.7% | 0s | 31s | 0s | 1,204s |

Coverage improves from 72% (2024 H1) to 99% (late 2025) as market liquidity grows. Median dt=0s in recent periods — most markets have a trade in the same second.

---

## Market Base Rates

Critical context: 38.1% of resolved markets have `yes_won=True` (affirmative token wins), 61.9% have `yes_won=False`. This means the NO side wins nearly 2:1 overall — likely because most Polymarket questions are "Will X happen?" and most events don't happen.

---

## Non-Tautological Backtest Results

**5,683 configs** swept across 5 rolling windows, $100 fixed bet, 2% fee.

### Direction-Level Summary

| Direction | Count | Avg HR | Med HR | Avg Sharpe | Avg PnL |
|-----------|-------|--------|--------|------------|---------|
| NO-only | 2,077 | 45.3% | 43.8% | -3.3 | -$5,013 |
| YES-only | 1,422 | 18.5% | 18.2% | -10.6 | -$12,741 |
| both | 2,184 | 39.0% | 38.5% | -7.1 | -$13,205 |

### Direction + MVF Band

| Direction | MVF Band | Count | Avg HR | Avg Sharpe | Avg PnL |
|-----------|----------|-------|--------|------------|---------|
| NO-only | pure_taker | 510 | 50.3% | -0.04 | -$277 |
| NO-only | informed_taker | 694 | 46.1% | -2.7 | -$3,280 |
| NO-only | all | 873 | 41.8% | -5.5 | -$9,158 |
| both | pure_taker | 533 | 46.6% | -2.2 | -$1,551 |
| both | informed_taker | 721 | 40.3% | -7.5 | -$7,871 |
| both | all | 930 | 33.7% | -9.5 | -$24,019 |
| YES-only | all | 786 | 22.5% | -8.5 | -$18,170 |
| YES-only | pure_taker | 219 | 16.7% | -10.4 | -$2,627 |
| YES-only | informed_taker | 417 | 11.9% | -14.9 | -$7,820 |

### Key Findings

1. **NO-only, pure_taker is the only viable combination** — near-zero avg Sharpe, 50.3% avg HR vs 61.9% base rate. The signal selects markets where the NO side has less edge than the base rate, but entry prices compensate.

2. **YES-only is strongly anti-predictive** — 18.5% HR vs 38.1% base rate. When skilled traders consensus points YES, the market is LESS likely to resolve YES. This may be because consistently profitable traders earn most of their PnL from being contrarian on popular markets.

3. **Informed taker (MVF<0.30) is NOT the best filter** for this signal. Pure takers (MVF<0.10) show better results, possibly because pure takers are genuine price-takers who act on directional conviction, while mixed-MVF traders may have hedged positions.

4. **17.2% of configs have positive Sharpe** — 596 NO-only, 361 both, only 20 YES-only.

---

## Top 10 Configurations

All top configs are **NO-only**:

| # | Sharpe | HR | PnL | Dir | MVF | Months | Mkts | Min T | Agree | Band | Win | Pool |
|---|--------|-----|-----|-----|-----|--------|------|-------|-------|------|-----|------|
| 1 | 5.0 | 57.9% | $465 | NO | informed | 6 | 30 | 10 | 60% | wide | 2 | 1216 |
| 2 | 4.8 | 46.5% | $1444 | NO | informed | 9 | 30 | 5 | 100% | wide | 2 | 578 |
| 3 | 4.8 | 46.5% | $1444 | NO | informed | 9 | 30 | 5 | 90% | wide | 2 | 578 |
| 4 | 4.5 | 59.9% | $1444 | NO | pure | 6 | 30 | 7 | 70% | wide | 2 | 988 |
| 5 | 4.3 | 46.3% | $1280 | NO | informed | 9 | 20 | 5 | 90% | wide | 2 | 856 |
| 6 | 4.3 | 44.5% | $1272 | NO | informed | 9 | 20 | 5 | 100% | wide | 2 | 856 |
| 7 | 4.3 | 59.0% | $1456 | NO | pure | 6 | 30 | 7 | 60% | wide | 2 | 988 |
| 8 | 4.1 | 62.1% | $1516 | NO | pure | 6 | 10 | 7 | 70% | wide | 2 | 4180 |
| 9 | 3.5 | 62.8% | $1280 | NO | pure | 6 | 20 | 7 | 70% | wide | 2 | 1998 |
| 10 | 3.5 | 53.2% | $4499 | NO | informed | 6 | 10 | 5 | 60% | wide | 2 | 4934 |

### Pattern: The best configs share

- **Direction**: NO-only (unanimous)
- **MVF**: pure_taker or informed_taker (NOT "all")
- **Price band**: [0.05, 0.95] (WIDE — not the [0.20, 0.80] from the tautological run)
- **Min traders**: 5-10 (higher threshold than the tautological top which was 2)
- **Agreement**: 60-100% (full range)
- **Windows**: Only 2 windows had enough signal (Dec 2025 + Jan 2026)

### Per-Window Stability (Best Pattern: NO-only, pure_taker, 7 traders, 70% agree, wide band)

| Window | HR | Sharpe | PnL | Bets | Pool |
|--------|-----|--------|-----|------|------|
| Dec 2025 (mkts=10) | 45.7% | 1.2 | $488 | 46 | 4,455 |
| Dec 2025 (mkts=20) | 48.6% | 1.6 | $633 | 35 | 2,312 |
| Dec 2025 (mkts=30) | 47.8% | 2.9 | $964 | 23 | 1,046 |
| Jan 2026 (mkts=10) | 78.5% | 6.9 | $2,545 | 65 | 3,905 |
| Jan 2026 (mkts=20) | 77.1% | 5.4 | $1,927 | 48 | 1,685 |
| Jan 2026 (mkts=30) | 71.9% | 6.0 | $1,924 | 32 | 931 |

The Jan 2026 window shows dramatically better performance — likely from higher market liquidity (99% forward price coverage) and larger trader pools. However, this is only 1 month of holdout.

---

## Edge Decomposition

The positive Sharpe for NO-only with below-base-rate hit rates (46-50% vs 62% base) is explained by entry price advantage:

- Binary bet PnL: `won → bet * (1-p)/p - fee; lost → -bet - fee`
- At price p=0.30 (NO side cheap): win pays $233, loss costs $102 → breakeven at 30.4%
- The signal selects markets where skilled traders disagree with the affirmative side AND the NO entry price offers positive EV even with <50% win rate

This means the consensus copy signal works through **price selection** (finding favorable entry prices when skilled traders take contrarian positions) rather than pure directional prediction.

---

## Caveats

1. **Only 2 holdout windows** contributed to the top configs (Dec 2025 + Jan 2026) — earlier windows had too few consistent traders to generate enough bets
2. **Small bet counts** — top configs have 20-65 bets per window, susceptible to noise
3. **Recency bias** — Jan 2026 performance (Sharpe 5-7) may not generalize
4. **NO base rate dominance** — 61.9% of markets resolve NO, inflating apparent NO signal quality
5. **No execution delay** — results assume instant execution (delay=0s); real execution will lag

## Next Steps

1. **Execution delay sweep** — test robustness at 30s, 60s, 300s delays
2. **Longer holdout windows** — validate across 6-12 month holdouts, not 1-month
3. **Out-of-sample validation** — reserve Jan 2026 as pure test set, never optimize on it
4. **Base rate adjustment** — normalize hit rates against per-window YES/NO base rates
5. **Position sizing** — explore Kelly or edge-weighted sizing now that we have genuine hit rates
