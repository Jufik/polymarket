# Validation Notes — tag-hr-consensus

**Date**: 2026-03-06
**Status**: COMPLETE
**Verdict**: NONE — signal does not survive tick-by-tick validation at N=5

---

## Parameters Tested

| Tag     | N | W    | meh  | pc   |
|---------|---|------|------|------|
| Esports | 5 | inf  | 10pp | 1.00 |
| Tennis  | 5 | 8h   | 10pp | 1.00 |

Walk-forward: 3 folds (2025-07, 2025-10, 2026-01).
BUY-only. No volume filter. SimulatedExecutor (zero slippage).

---

## Results

### Esports — Per-Fold

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL      |
|----------|-------|------|------|--------|----------|----------|
| 2025-07  | 36.8% | 18   | 23   | 17.4%  | -19.4pp  | -$194    |
| 2025-10  | 65.4% | 24   | 39   | 76.9%  | +11.5pp  | +$17     |
| 2026-01  | 45.6% | 320  | 387  | 47.0%  | +1.4pp   | -$2,805  |
| **Agg**  | 49.2% | —    | 449  | **47.1%** | **-2.1pp** | **-$2,982** |

### Tennis — Per-Fold

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL      |
|----------|-------|------|------|--------|----------|----------|
| 2025-07  | 24.8% | 17   | 4    | 50.0%  | +25.2pp  | -$104    |
| 2025-10  | 39.6% | 80   | 93   | 38.7%  | -0.8pp   | -$1,191  |
| 2026-01  | 45.3% | 86   | 42   | 59.5%  | +14.2pp  | -$69     |
| **Agg**  | 36.5% | —    | 139  | **49.4%** | **+12.9pp** | **-$1,365** |

### Vectorized vs Tick Comparison

| Metric          | Vectorized (UB) | Tick (Esports) | Degradation | Tick (Tennis) | Degradation |
|-----------------|----------------|----------------|-------------|---------------|-------------|
| Hit Rate        | 82.3%          | 47.1%          | -35.2pp     | 49.4%         | -35.4pp     |
| Excess HR       | +33.0pp        | -2.1pp         | -35.1pp     | +12.9pp       | -35.1pp     |
| Signals/fold    | 209            | 149.7          | -28%        | 27            | +71% (noise)|
| Total PnL       | (UB +)         | -$2,982        | NEGATIVE    | (UB +)        | -$1,365     |

**Degradation**: 35pp in both tags — at the upper end of the expected 20-40pp band, but squarely within it.

---

## Root Cause Analysis

### 1. Degradation Is Exactly the Consensus Gap

The vectorized sweep fires when the Nth qualified trader has EVER entered during the test window (vectorized: `max(first_trade)` across all N traders per market). The tick strategy fires when the Nth unique qualified trader's BUY trade arrives in real-time.

These are the same in theory. But in practice:
- **Pool contamination from large 2026-01 fold**: The 2026-01 Esports fold has 320 qualified traders (vs 18-24 in earlier folds). With N=5 and 320 qualified traders in the pool, consensus fires on almost any large Esports market — including ones where the "consensus" is 5 random qualified traders who happen to have traded the same market, not 5 traders who all have private information about the same outcome.
- **The large pool dilutes signal quality**: 320 qualified traders at 10pp excess HR means many of them have HR ≈ base_rate + 10pp ≈ 55-56%. N=5 of these produces 50-55% HR, close to base.

### 2. Esports 2025-07 Fold Is a Disaster

Pool of only 18 traders with N=5 threshold means consensus fired in markets where the 5 who entered happened to be the worst performers in that specific market. HR=17.4% is *below* random — this fold is pure noise with n=23 signals.

### 3. Tennis 2025-10 Fold is Noisy

93 signals in one fold but HR=38.7% — below base of 39.6%. The 8h window filter didn't prevent low-quality consensus in this fold.

### 4. Positive Excess But Negative PnL

Tennis aggregate shows +12.9pp excess (49.4% HR vs 36.5% base) but negative PnL. This happens because:
- Positive outcome pays (1 - fill_price) per token
- If signals fire at high prices (e.g., 0.65+), even a 50% HR loses money: E[PnL] = 0.5*(1-0.65) - 0.5*0.65 = 0.5*0.35 - 0.5*0.65 = -0.15
- The Tennis 2025-07 fold: 2/4 wins at presumably high prices → -$104 loss

---

## Structural Issues

### Issue 1: N=5 Is Too Restrictive at Large Pool Size

At 320 qualified traders, N=5 fires constantly (387 signals in one month). The signal is no longer "5 insiders agree" — it's "5 out of 320 mediocre traders happened to enter the same market." The vectorized sweep measured the BEST N traders by consensus, but the tick strategy picks the first N regardless of quality ordering.

**Fix**: Require higher N when pool is large, OR add minimum excess_hr per consensus trader at signal time (not just pool membership). Round 3 sweep showed meh=20pp is the sweet spot.

### Issue 2: Esports Non-Stationarity Is Lethal

The 2025-07 fold's base rate (37%) vs 2025-10 (65%) is a 28pp swing. A trader qualified by HR in the 2025-07 training window (when base=10-37%) may have 65% base-rate-adjusted HR in the 2025-10 test window. The pool quality degrades when the market regime flips.

### Issue 3: Signal Fires at Market Price, Not Consensus Price

The tick strategy fires the signal at the Nth qualified trader's current trade price. In efficient Esports markets, by the time N=5 traders have entered YES, the price is already 0.65-0.90. The edge is priced in.

---

## Verdict

**NONE** for these exact parameters (N=5, meh=10pp, no vol filter).

However the signal structure is not dead — it's the parameter selection that is wrong:

1. **meh=10pp is too loose** at large pool sizes (2026-01 fold). Round 3 sweep showed meh=15-20pp is the correct range.
2. **N=5 with no pool size control is wrong**: need N proportional to pool size, or hard floor on per-trader HR at consensus time.
3. **No volume filter hurts**: the 2025-07 fold fired on micro-markets (8 micro-market signals at near-zero HR). Even a $100 volume filter would remove the worst of these.

---

## Recommended Next Validation

Based on Round 3 sweep results (which showed CS>100 with meh=15-20pp):

**Esports**: N=2, meh=15pp, mpe=0.80, no vol filter
- Round 3 vectorized: HR=69.2%, excess=+20pp, median_pnl=$206, CS=424
- Expected tick: 29-49% HR (after 20-40pp discount)
- GO if tick excess > 0pp and PnL > 0

**Tennis**: N=3, meh=20pp, mpe=0.90, no vol filter
- Round 3 vectorized: HR=82.0%, excess=+45.5pp, median_pnl=$21.72, CS=78.99
- Expected tick: 42-62% HR
- GO if tick excess > 5pp and PnL > 0

**Key change**: meh=10pp → meh=15-20pp. This is the critical lever.
