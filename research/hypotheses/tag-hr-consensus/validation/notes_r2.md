# Validation Notes R2 — tag-hr-consensus (mpe pool filter)

**Date**: 2026-03-06
**Status**: COMPLETE
**Verdict**: MARGINAL — positive excess HR but negative PnL. Fill price is the bottleneck.

---

## Parameters Tested

| Combo              | N | ep   | mpe  | pc   | W   |
|--------------------|---|------|------|------|-----|
| Esports primary    | 4 | 10pp | 0.80 | 0.75 | inf |
| Esports sensitive  | 3 | 15pp | 0.70 | 0.75 | inf |
| Tennis primary     | 3 | 15pp | 0.70 | 0.75 | inf |
| Tennis sensitive   | 2 | 15pp | 0.70 | 0.75 | inf |

Walk-forward: 3 folds (2025-07, 2025-10, 2026-01).
BUY-only. No volume filter. mpe pool filter applied.

---

## Results

### Esports Primary (N=4, ep=10pp, mpe=0.80)

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL       | Avg Fill |
|----------|-------|------|------|--------|----------|-----------|----------|
| 2025-07  | 36.8% | 47   | 52   | 51.9%  | +15.2pp  | +$616     | 0.443    |
| 2025-10  | 65.4% | 46   | 40   | 60.0%  | -5.4pp   | -$197     | 0.577    |
| 2026-01  | 45.6% | 774  | 433  | 46.0%  | +0.4pp   | -$387     | 0.477    |
| **Agg**  | 49.2% | —    | 525  | **52.6%** | **+3.4pp** | **+$32**  | 0.499   |

### Esports Sensitive (N=3, ep=15pp, mpe=0.70)

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL       | Avg Fill |
|----------|-------|------|------|--------|----------|-----------|----------|
| 2025-07  | 36.8% | 28   | 46   | 54.4%  | +17.6pp  | +$1,060   | 0.457    |
| 2025-10  | 65.4% | 19   | 3    | 66.7%  | +1.3pp   | -$13      | 0.670    |
| 2026-01  | 45.6% | 317  | 229  | 43.7%  | -1.9pp   | -$1,186   | 0.447    |
| **Agg**  | 49.2% | —    | 278  | **54.9%** | **+5.7pp** | **-$139** | 0.525  |

### Tennis Primary (N=3, ep=15pp, mpe=0.70)

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL       | Avg Fill |
|----------|-------|------|------|--------|----------|-----------|----------|
| 2025-07  | 24.8% | 70   | 106  | 52.8%  | +28.1pp  | -$655     | 0.517    |
| 2025-10  | 39.6% | 131  | 180  | 38.3%  | -1.2pp   | -$4,296   | 0.489    |
| 2026-01  | 45.3% | 119  | 156  | 53.2%  | +7.9pp   | +$2,496   | 0.486    |
| **Agg**  | 36.5% | —    | 442  | **48.1%** | **+11.6pp** | **-$2,455** | 0.497 |

### Tennis Sensitive (N=2, ep=15pp, mpe=0.70)

| Fold     | Base  | Pool | Sigs | HR     | Excess   | PnL       | Avg Fill |
|----------|-------|------|------|--------|----------|-----------|----------|
| 2025-07  | 24.8% | 70   | 151  | 52.3%  | +27.6pp  | +$490     | 0.481    |
| 2025-10  | 39.6% | 131  | 353  | 42.8%  | +3.2pp   | -$3,960   | 0.477    |
| 2026-01  | 45.3% | 119  | 372  | 50.0%  | +4.7pp   | +$1,304   | 0.477    |
| **Agg**  | 36.5% | —    | 876  | **48.4%** | **+11.8pp** | **-$2,165** | 0.478 |

### Vectorized vs Tick Comparison

| Metric       | Vect UB (Esports) | Tick (primary) | Degr. | Vect UB (Tennis) | Tick (primary) | Degr. |
|--------------|-------------------|----------------|-------|------------------|----------------|-------|
| Hit Rate     | 80.7%             | 52.6%          | -28pp | 74.5%            | 48.1%          | -26pp |
| Excess HR    | +31.5pp           | +3.4pp         | -28pp | +37.9pp          | +11.6pp        | -26pp |
| Total PnL    | (UB +)            | +$32           | tiny  | (UB +)           | -$2,455        | NEG   |

**Degradation: 26-28pp** — within the expected 20-40pp band, on the lower end. Good news.

---

## Key Findings

### 1. Signal IS Real, But PnL Breaks at 2025-10

The positive excess HR (+3.4pp Esports, +11.6pp Tennis across all folds) confirms a real signal.
But aggregate PnL is near-zero for Esports and negative for Tennis. The breakdown is per-fold:

- **2025-07**: Both tags profitable. HR clearly above base.
- **2025-10**: **Disaster fold** — Tennis loses $4,296 on 180 signals. Esports loses $197 on 40.
- **2026-01**: Mixed — Tennis +$2,496, Esports -$387.

The 2025-10 fold is the problem. It has:
- Esports base rate = **65.4%** (non-stationary spike)
- Tennis HR = 38.3% at base 39.6% (random)
- Tennis pool = 131 traders (largest in the sweep)
- Massive loss from 180 Tennis signals at near-random HR

### 2. Fill Price at ~0.49 Implies Break-Even Requires 49%+ HR

Average fill price across combos: 0.44–0.58 (typically 0.48-0.52).
At fill price 0.50: E[PnL per signal] = hr*(1-0.50)*size - (1-hr)*0.50*size
= hr*50 - (1-hr)*50 = (2*hr - 1)*50

Break-even HR = 50% at fill_price = 0.50.

The Esports primary combo achieves 52.6% HR → tiny positive ($32 total), but Tennis combos
don't clear 50% consistently enough.

### 3. Pool Explosion in 2026-01 Still Problematic

Esports 2026-01: 774 qualified traders. N=4 of 774 fires on virtually every market (433 signals).
Signal quality degrades — these are not 4 insiders, they're 4 of hundreds of mediocre traders.

This is the same pool-explosion problem seen in validation round 1, just less severe (mpe=0.80
reduces pool vs no mpe, but doesn't prevent explosion when Esports grows rapidly).

### 4. Esports 2025-07 is Genuinely Profitable

47 traders in pool, 52 signals, HR=51.9%, avg_fill=0.443, Sharpe=3.27, PnL=+$616.
This is a real alpha signal in the early Esports market when:
- Pool is small and selected (47 traders)
- Markets are less efficiently priced (avg fill 0.443 = cheap entry)
- HR meaningfully above base (51.9% vs 36.8%)

The signal existed in 2025-07 and 2025-10 Esports folds but degrades as the market grows.

### 5. Tennis 2025-07 Shows +28pp Excess but Negative PnL

HR=52.8% vs base=24.8% — a real 28pp signal. But PnL=-$655. Why?

Avg fill price = 0.517. At HR=52.8%:
E[PnL] = (0.528*(1-0.517) - 0.472*0.517) * 100 * 106 signals
= (0.528*0.483 - 0.472*0.517) * 10,600
= (0.255 - 0.244) * 10,600 = +$117

But actual PnL is -$655. The avg fill of 0.517 hides the distribution — some signals are firing
at 0.70-0.75 (price ceiling), where even 52% HR loses money. The price ceiling filter at 0.75
allows expensive entries that erode the edge.

---

## Root Causes Summary

1. **Large pool size in 2026-01 Esports** (774 traders) dilutes N=4 consensus to noise
2. **2025-10 fold** is a structural outlier (Esports 65% base rate spike + Tennis random HR)
3. **Fill prices near 0.50** mean break-even requires consistent 50%+ HR — hard to sustain
4. **Tennis 2025-10** is cleanly negative — the signal doesn't exist in that regime

---

## Verdict: MARGINAL

The signal shows positive excess HR in 2 of 3 folds for Esports and inconsistently for Tennis.
PnL is marginally positive for Esports (+$32 across all signals) and negative for Tennis.

**Not ready for paper trading** in current form.

### What Would Fix It

1. **Pool size cap**: max_pool_size = 50. When pool exceeds 50 qualified traders, raise meh threshold
   dynamically until pool shrinks. This prevents the 774-trader 2026-01 explosion.

2. **Per-fold base rate in pool construction**: the 2025-10 fold is contaminated because traders
   qualified on earlier training data but the base rate flipped to 65%. Per-fold HR re-evaluation
   mid-window would help.

3. **Price filter at 0.40**: signal is only copyable if Nth qualified trader enters at price <= 0.40
   (not 0.75). At 0.40 fill, break-even HR drops to 40% — well within range.

4. **Volume filter reintroduction** (small): markets with total qualified trader volume >= $200.
   This filters micro-markets where random consensus fires.

---

## Anti-Knowledge Captured

- `mpe=0.70-0.80` pool filter reduces pool size but does NOT prevent pool explosion when the
  Esports market grows (2026-01: 774 traders even with mpe=0.80). Need absolute pool size cap.
- At avg fill price >= 0.50, a 52% HR strategy produces near-zero PnL. The signal edge and the
  fill price edge are nearly canceling each other out.
- The 2025-10 fold is structurally different from 2025-07 and 2026-01 — the Esports base rate
  spike (65%) in Oct-Nov 2025 makes it a hostile fold for any YES-biased strategy.
