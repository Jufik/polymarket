# Discovery Notes — tag-hr-consensus Round 2

**Date**: 2026-03-06
**Status**: UPPER BOUNDS complete (Round 2 with pool entry price guardrail). Ready for validation gate.
**Verdict**: PROMISING (BUY-only variant only)

---

## Round 2 Changes vs Round 1

Round 2 adds `max_pool_entry_price` ({0.70, 0.80, 0.90}) as a pool qualification filter. This excludes
traders who primarily buy at very high prices (≥ threshold avg entry price) — the "sure-thing buyers"
whose inflated HR reflects bet timing rather than genuine informational edge. Parameters also updated:
min_trades >= 20 (from 5), bot_guard < 10,000.

---

## What Worked

### Signal Is Real (BUY-only)

The core hypothesis is validated vectorized: qualified trader consensus (N>=3-4) in Esports/Tennis
markets predicts YES outcomes with 76-94% HR vs 37-65% base rate. This is a strong upper-bound signal.

**Esports BUY top combos (UPPER BOUNDS)**:

| Rank | N | pc   | ep   | mpe  | HR     | Excess  | PnL      | Hold | CS     | n_folds |
|------|---|------|------|------|--------|---------|----------|------|--------|---------|
| 1    | 3 | 0.75 | 15pp | 0.70 | 84.4%  | +43.2pp | $36.99   | 2h   | 191.78 | 2       |
| 2    | 2 | 0.75 | 15pp | 0.80 | 75.9%  | +26.7pp | $54.07   | 2h   | 173.32 | 3       |
| 3    | 4 | 0.75 | 10pp | 0.80 | 80.7%  | +31.5pp | $45.81   | 2h   | 173.18 | 3       |

**Tennis BUY top combos (UPPER BOUNDS)**:

| Rank | N | pc   | ep   | mpe  | HR     | Excess  | PnL      | Hold | CS     | n_folds |
|------|---|------|------|------|--------|---------|----------|------|--------|---------|
| 1    | 4 | 0.75 | 15pp | 0.90 | 93.8%  | +51.3pp | $31.13   | 2h   | 191.76 | 2       |
| 2    | 4 | 0.75 | 15pp | 0.80 | 93.5%  | +51.1pp | $31.00   | 2h   | 190.21 | 2       |
| 3    | 5 | 0.75 | 10pp | 0.90 | 86.4%  | +44.0pp | $33.12   | 2h   | 174.78 | 2       |

### Directional Variant Is Dead

With pool entry price filter applied, the directional (BUY + SELL NO as YES) variant collapses to
near-zero excess HR:
- Esports DIR: best combo is N=2, ep=15pp, mpe=0.8 → HR=42.5%, excess=+0.35pp (CS=0.14) — effectively random
- Tennis DIR: best combo is N=2, ep=10pp, mpe=0.9 → HR=36.4%, excess=+8.5pp (CS=9.27) — marginal but too low

**Root cause**: When you exclude sure-thing buyers (mpe filter), the remaining pool is genuinely
informed. Their net YES positions (BUY-only) are meaningful. But including SELL NO traders brings
in heterogeneous entry types that dilute the signal. This is the opposite of Round 1's finding
("SELL sensitivity = 0pp") — the mpe filter reveals that the BUY-only pool is meaningfully different
from the directional pool.

### Pool Entry Price Filter Is a Signal Amplifier

Mpe=0.70 produces the highest CS for Esports (excludes even moderate-priced entries, keeps only
bargain hunters). Mpe=0.90 is sufficient for Tennis. This aligns with the hypothesis: traders who
buy YES at 0.60-0.70 have genuine uncertainty-based edge, while traders who buy at 0.85+ are
following public information.

---

## What Didn't Work / Issues Found

### Tennis Sample Size at N>=4 Is Very Small

Tennis N=4 top combo: only 44 signals across 2 folds (22/fold). N=5: 29-31 signals across 2 folds.
This is too few for reliable inference. The 93.8% HR at N=4 could be statistical noise on n=44.
The N=2-3 combos with 3 folds and 100-350 signals are more reliable.

Recommended validation focus: Tennis N=3 (48-131 signals, 3 folds) or N=2 with strict mpe=0.70
(209 signals, 3 folds). Skip N>=4 for Tennis — insufficient data.

### Esports Top Combo Has Only 2 Folds

The Esports #1 combo (N=3, mpe=0.70) has only 2 folds with 157 total signals. The #2 combo
(N=2, mpe=0.80) has 3 folds and 684 signals — better statistical foundation. The #3 combo
(N=4, mpe=0.80) also has 3 folds.

For validation, prefer the 3-fold combos over the 2-fold ones for robustness.

### Avg PnL Is Noisy (Use Median)

Several top combos show negative avg_pnl while median_pnl is positive (e.g., Esports #1:
avg=-$85 vs median=+$37). This is expected — avg is skewed by whale positions.
Compounding score uses median_pnl correctly. Do NOT use avg_pnl for decision-making.

---

## Surprising Findings

### 1. Mpe=0.70 Cuts Pool Size Dramatically But Improves Quality

Esports pool with mpe=0.70 is likely ~30-50% the size of mpe=0.90, yet produces higher HR.
This means the informational edge is concentrated in a small subset of traders who buy early
at low prices. Most "qualified" traders (by HR alone) are buying late at high prices.

### 2. Tennis Has Shorter Effective Hold at N>=4

Tennis N=4 shows avg_hold_hours=2.0 vs N=2 avg_hold_hours=3.3. Higher consensus = faster signal.
This makes intuitive sense: if 4 informed traders all entered before the market gets crowded,
resolution is already visible in the market dynamics.

### 3. Esports Base Rate Non-Stationarity Confirmed Again

2025-10 fold: 65.4% base rate vs 45.6% in 2026-01. Per-fold qualification is essential.
A global excess-hr threshold without per-fold base would qualify different traders across folds.

---

## Validation Recommendation

### Esports
- **Primary**: N=4, pc=0.75, ep=10pp, mpe=0.80 (3 folds, 259 signals, HR=80.7%, CS=173.18)
- **Sensitivity**: Test N=2 and N=3 alongside to see HR gradient
- Expected tick HR: 40-60% (after 20-40pp vectorized discount)
- **GO** if tick HR > Esports base rate + 5pp minimum

### Tennis
- **Primary**: N=3, pc=0.75, ep=15pp, mpe=0.70 (3 folds, 48 signals, HR=74.5%, CS=70.17)
- **Alternative**: N=2, pc=0.75, ep=15pp, mpe=0.70 (3 folds, 209 signals, HR=70.0%, CS=41.39)
- Expected tick HR: 30-50% (after 20-40pp discount from 74% vectorized)
- **GO** if tick HR > Tennis base rate + 5pp minimum
- **Note**: Skip Tennis N>=4 — insufficient signal count for reliable tick-by-tick test

### Both
- Directional variant: DO NOT validate — dead signal (near-zero excess HR with mpe filter)

---

## Spawned Ideas

1. **sure-thing-buyer-classification** [HIGH]: Identify traders with avg entry price >= 0.85 as
   "sure-thing buyers" and exclude from all consensus pools. This is now validated as signal-destroying.

2. **entry-price-timing-analysis** [MEDIUM]: Analyze whether mpe=0.70 traders enter earlier in
   the market's life than mpe=0.90 traders. If so, entry timing = signal, not just entry price.

3. **volume-weighted-entry** [LOW]: Weight consensus by position size instead of trader count.
   A trader with 10x larger position at 0.60 may dominate the signal.
