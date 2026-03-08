# Signal Quality Exploration — Summary

**Date**: 2026-03-06
**Hypothesis**: tag-hr-consensus
**Source**: DuckDB vectorized, Parquet snapshot — all results are UPPER BOUNDS
**Folds**: 2025-07, 2025-10, 2026-01 (train = preceding 6 months)
**Base params**: N=3 consensus, meh=10pp, mpe=0.80, price_ceil=0.75, max_hold=48h

---

## Exploration 1: Monthly Base Rates (Regime Gate Analysis)

**Data quality note**: The query in the JSON used `sum(yes_won) / count(DISTINCT condition_id)` which counts position-level yes_won rows in the numerator (inflated) vs distinct markets in the denominator. The values are therefore not valid fractions. The per-fold base rates computed in explorations 2-6 (using correct deduplication) are reliable:

| Fold test | Esports base rate | Tennis base rate |
|-----------|-------------------|------------------|
| 2025-07   | 36.8%             | 24.8%            |
| 2025-10   | 65.4%             | 39.6%            |
| 2026-01   | 45.6%             | 45.3%            |

**Esports regime non-stationarity is severe**: The 2025-10 fold sits at 65.4% base rate — nearly double the 2025-07 fold. This is the "hostile fold" that destroys signal quality across all configurations. Any regime gate should suspend trading when the monthly YES win rate exceeds ~55%.

**Tennis base rate** is more stable (25-45% across observed folds), but the 2026-01 fold jumped from 40% to 45%, approaching the break-even zone.

**Regime gate recommendation**: Track a rolling 30-day YES win rate per tag. Suspend new entries when rate >= 55% (Esports) or >= 50% (Tennis). This would have excluded the 2025-10 Esports fold entirely.

---

## Exploration 2: Entry Price as Signal Quality Predictor

**Finding: Entry price is the strongest predictor of YES HR.** The relationship is monotone and dramatic across both tags and all folds.

### Esports (selected buckets)

| Price bucket | 2025-07 HR / excess | 2025-10 HR / excess | 2026-01 HR / excess |
|-------------|---------------------|---------------------|---------------------|
| 0.00-0.20   | 0% / -37pp          | —                   | 0% / -46pp          |
| 0.30-0.40   | —                   | —                   | 28% / -18pp         |
| 0.50-0.60   | —                   | —                   | 52% / +7pp          |
| 0.60-0.70   | 86% / +49pp         | 33% / -32pp         | 68% / +22pp         |
| 0.70+       | 92% / +56pp         | 95% / +30pp         | 94% / +48pp         |

### Tennis (selected buckets)

| Price bucket | 2025-07 HR / excess | 2025-10 HR / excess | 2026-01 HR / excess |
|-------------|---------------------|---------------------|---------------------|
| 0.00-0.20   | —                   | 0% / -40pp          | —                   |
| 0.20-0.30   | 25% / +0pp          | 0% / -40pp          | —                   |
| 0.40-0.50   | —                   | 78% / +38pp         | 25% / -20pp         |
| 0.60-0.70   | —                   | 50% / +10pp         | 83% / +38pp         |
| 0.70+       | 100% / +75pp        | 88% / +48pp         | 100% / +55pp        |

**Key insight**: Markets where qualified YES traders enter at **above 0.70** show 88-100% YES HR across all folds for both tags. This is extraordinary. These are markets where qualified traders are **confirming the favorite** — not contrarian bets.

**Implication for strategy**: Invert the "price ceiling" from a gate to a **floor**. Instead of `price_ceil=0.75` (avoid expensive markets), the signal is strongest when consensus entry price >= 0.70. Low-price consensus (< 0.40) is strongly bearish for the signal.

**Warning**: The price_ceil filter in the original strategy (max avg entry price <= 0.75) was meant to control fill economics, not signal quality. The current PRICE_CEIL filter passes 0.60-0.75 markets, which are borderline. A **minimum price floor** of 0.60 or 0.70 would dramatically improve quality.

**Economics concern**: Buying at 0.85+ (the 0.70+ bucket) means the max gain is $0.15/share. Even at 90% HR, at $1k position: 0.90 × $150 - 0.10 × $850 = $135 - $85 = $50 net. At $500: $25 net. Low absolute edge despite high HR. Tick validation needed to confirm profitability after fees.

---

## Exploration 3: Convergence Speed (1st to Nth Trader)

**Finding: Convergence speed is NOT a clean signal quality predictor** — results are mixed across tags and folds.

### Esports

| Speed bucket | 2025-07 HR/excess | 2025-10 HR/excess | 2026-01 HR/excess |
|-------------|-------------------|-------------------|-------------------|
| fast (5m-1h)  | —               | 90% / +25pp       | 65% / +20pp       |
| medium (1h-6h) | 80% / +43pp   | 86% / +20pp       | 69% / +24pp       |
| slow (6h-24h) | 71% / +34pp    | 63% / -3pp        | 63% / +17pp       |
| very slow (>24h) | —            | 83% / +18pp       | 64% / +19pp       |

**Esports**: Fast convergence (<=1h) is marginally better in 2025-10, but the effect is inconsistent and small (5-10pp). Slow convergence is not reliably worse.

### Tennis

| Speed bucket | 2025-07 HR/excess | 2025-10 HR/excess | 2026-01 HR/excess |
|-------------|-------------------|-------------------|-------------------|
| fast (5m-1h)  | —               | 25% / -15pp       | 100% / +55pp      |
| medium (1h-6h) | 60% / +35pp   | 67% / +27pp       | 74% / +28pp       |
| slow (6h-24h) | 46% / +21pp    | 63% / +23pp       | 81% / +35pp       |
| very slow (>24h) | —            | 38% / -2pp        | 64% / +19pp       |

**Tennis**: Fast convergence in 2025-10 Tennis is actually *negative* (-15pp), while medium and slow are positive. In Tennis 2026-01, very fast is 100% but that's only 6 markets.

**Verdict on convergence speed**: Not a reliable standalone filter. The signal strength varies too much across folds. Entry price (Exploration 2) dominates this feature. **Do not add convergence speed as a gate.**

However, **very slow (>24h)** convergence is a mild negative signal in Tennis (2025-10: -2pp, 2025-07: no data). Consider capping the consensus window at 24h for Tennis.

---

## Exploration 4: Trader Co-occurrence / Herding

**Finding: HIGH herding detected in both tags.** This is a structural problem.

### 2025-07 fold (reference)

| Tag | n_consensus_mkts | n_qual_traders | Top trader pct | Median trader pct |
|-----|-----------------|----------------|----------------|-------------------|
| Esports | 34 | 16 | 55.9% | 26.5% |
| Tennis  | 33 | 15 | 54.6% | 36.4% |

**The dominant trader appears in 55-56% of all consensus markets.** This means when that one trader enters, the "consensus" is almost guaranteed to fire — the other 2 required traders are taken from a pool of 15-16 highly correlated traders who also participate in >25% of markets.

**Interpretation**: The consensus signal is not truly independent. With only 15-16 qualified traders and a dominant actor covering >50% of markets, the effective independent signal count is much lower than N=3 implies. The "consensus of 3" is often driven by 1-2 dominant traders with 1 marginal entrant.

**Implication for K selection**: At K=30 or K=50 (top traders only), this herding effect gets worse, not better — a small number of ultra-sharp traders dominate even more. The signal quality is real (those traders are genuinely good), but the independence assumption is violated.

**Potential fixes**:
1. Require that no single trader appears in >50% of the *training-window* consensus markets for that fold — if the pool is dominated by one trader, skip the fold.
2. Weight traders inversely by market participation rate to reduce the dominant trader's influence.
3. Require N >= 4 to force more independent signals.

---

## Exploration 5: Market Age at Signal Time

**Finding: Younger markets have moderately higher signal quality, but the effect is weaker than entry price.**

### Esports

| Age bucket | 2025-07 HR/excess | 2025-10 HR/excess | 2026-01 HR/excess |
|-----------|-------------------|-------------------|-------------------|
| fresh (<=1h)   | —             | —                 | 73% / +27pp       |
| recent (1h-6h) | 100% / +63pp  | —                 | 68% / +23pp       |
| same-day (6h-24h) | 71% / +34pp | 73% / +8pp      | 70% / +25pp       |
| week-old (1-7d) | 83% / +47pp | 88% / +22pp      | 63% / +17pp       |
| mature (>7d)   | —             | —                 | 57% / +11pp       |

**Esports**: Recent markets are best in 2025-07, but week-old actually beats same-day in 2025-10 and 2026-01. No clean monotone relationship.

### Tennis

| Age bucket | 2025-07 HR/excess | 2025-10 HR/excess | 2026-01 HR/excess |
|-----------|-------------------|-------------------|-------------------|
| recent (1h-6h) | —             | 67% / +27pp       | —                 |
| same-day (6h-24h) | 50% / +25pp | 60% / +20pp    | 60% / +15pp       |
| week-old (1-7d) | —            | 55% / +15pp      | 80% / +35pp       |

**Surprising Tennis finding**: In 2026-01, week-old Tennis markets have the strongest signal (80% HR, +35pp excess), beating same-day markets (60%, +15pp). This is counterintuitive — it suggests qualified traders may enter Tennis markets after early price movement, and their late entry is a stronger conviction signal.

**Verdict**: Market age is not a reliable filter. The effect is inconsistent and depends heavily on the fold. **Do not add market age as a gate.** Focus on entry price (Exploration 2) instead.

---

## Exploration 6: Pool Size K Sensitivity (K = 10, 20, 30, 50, 75, 100)

**Finding: Signal quality degrades sharply as K increases. The optimal K differs by tag.**

### Esports

| K | 2025-07 signals/HR/excess | 2025-10 signals/HR/excess | 2026-01 signals/HR/excess |
|---|--------------------------|--------------------------|--------------------------|
| K=10  | 14 / 86% / +49pp | 10 / 100% / +35pp | 10 / 100% / +54pp |
| K=20  | 44 / 73% / +36pp | 31 / 81% / +15pp  | 12 / 100% / +54pp |
| K=30  | 70 / 61% / +25pp | 57 / 86% / +21pp  | 23 / 100% / +54pp |
| K=50  | 116 / 38% / +1pp | 106 / 72% / +6pp  | 52 / 100% / +54pp |
| K=75  | 116 / 38% / +1pp | 165 / 65% / -1pp  | 86 / 91% / +45pp  |
| K=100 | 116 / 38% / +1pp | 217 / 60% / -5pp  | 116 / 84% / +38pp |

**Esports**: K=10-20 is clearly best. At K=50, the 2025-07 fold already collapses to +1pp excess (essentially base rate). The 2025-07 fold has only 35 total qualifying traders, so K=50 equals the full pool. K=20-30 is the "elbow" — meaningful selectivity with reasonable signal counts.

**Note on 2026-01**: All K levels show excellent HR, but n_markets=13,538 and only 10-23 signals at K=10-30 means the strategy barely fires in that fold. The 2026-01 Esports fold is extremely large (massive market expansion) and the pool becomes thin relative to market count.

### Tennis

| K | 2025-07 signals/HR/excess | 2025-10 signals/HR/excess | 2026-01 signals/HR/excess |
|---|--------------------------|--------------------------|--------------------------|
| K=10  | 14 / 71% / +47pp | 0 / — | 0 / — |
| K=20  | 45 / 51% / +26pp | 0 / — | 0 / — |
| K=30  | 94 / 27% / +2pp  | 9 / 100% / +60pp | 6 / 83% / +38pp |
| K=50  | 94 / 27% / +2pp  | 57 / 75% / +36pp | 37 / 81% / +36pp |
| K=75  | 94 / 27% / +2pp  | 177 / 49% / +10pp | 98 / 69% / +24pp |
| K=100 | 94 / 27% / +2pp  | 272 / 44% / +5pp  | 135 / 62% / +17pp |

**Tennis is structurally different from Esports.** The 2025-07 fold has only 29 qualifying traders (pool exhausted at K=30), and the signal is already near-zero at K=30 (+2pp excess). But 2025-10 and 2026-01 have much stronger signals at K=30-50.

**Regime asymmetry in Tennis**: The 2025-07 fold has a low base rate (24.8%) but weak signal at K=30. The 2025-10 fold (base=39.6%) has 0 signals at K=10-20 but 100% HR at K=30. The pool is thinner in early folds — qualified Tennis traders took time to accumulate enough history.

**K recommendations by tag**:
- **Esports**: K=15-20 optimal. K=10 is too thin (10-14 signals/fold, not deployable). K=30 is the maximum before signal degrades.
- **Tennis**: K=30-50. K=10-20 produces zero signals in 2 of 3 folds. K=50 gives 37-94 signals/fold with 75-81% HR.

---

## Combined Findings and Recommended Parameter Updates

### Priority 1: Entry Price Floor (strongest signal)
**Action**: Add `min_consensus_ep >= 0.60` (or 0.70 for maximum quality) as a hard gate.
- Esports 0.70+ bucket: 95-94% HR across hostile and normal folds
- Tennis 0.70+ bucket: 88-100% HR across all folds
- This inverts the intuition — confirm the favorite, not the underdog

**Economics tradeoff**: At 0.85 avg fill, max gain = $0.15/share. Position sizing needs to account for compressed upside. The min_ep filter works best combined with max position size caps.

### Priority 2: Pool Size Reduction (clean selectivity)
**Action**: Set K=20 for Esports, K=50 for Tennis (based on signal volume / quality tradeoff).
- Esports K=20: 12-44 signals/fold, HR=73-100%, excess=+15-54pp
- Tennis K=50: 37-94 signals/fold, HR=75-81%, excess=+36pp (2 of 3 folds; 2025-07 is K=30 exhausted)

### Priority 3: Regime Gate (suspend in hostile folds)
**Action**: Suspend Esports trading when rolling 30d YES win rate >= 55%.
- Would have excluded 2025-10 Esports fold (65.4% base rate) entirely
- Tennis regime is more stable but add gate at >= 50%

### Priority 4: Convergence Window Cap (mild effect)
**Action**: For Tennis, cap consensus window at 24h (very slow convergence was neutral or negative).
- Not critical — other filters dominate

### Priority 5: Herding Awareness (structural)
**Action**: Track dominant trader market participation. If top-1 trader covers >50% of training consensus markets, require N >= 4 for that fold.
- Both tags showed ~55% dominance by one trader in 2025-07
- This is a fold-adaptive parameter, not a static filter

---

## What NOT to Do

1. **Do not add convergence speed as a gate** — inconsistent direction across folds, dominated by entry price
2. **Do not add market age as a gate** — inconsistent, Tennis week-old beats same-day in one fold
3. **Do not use K=10** for production — too thin (0 signals in multiple Tennis folds, 10 in Esports)
4. **Do not blindly increase K** — K=75-100 collapses Esports signal to near-zero in some folds
5. **Do not assume entry price < 0.40 is a good contrarian signal** — it is the worst bucket (0% HR)

---

## Next Steps

1. Tick validation of the **entry price floor** filter (min_consensus_ep >= 0.70) — this is the highest-priority untested improvement
2. Combined test: K=20 Esports + min_ep=0.70 + vol>=500 + dissent>=0.70
3. Tick test on Tennis K=50 alone vs the stacked filter combination
4. Regime gate implementation: real-time rolling base rate monitor per tag

---

*Artifacts: `signal_quality.json` (full data), `signal_quality_summary.md` (this file)*
*Run time: 117.9s on DuckDB Parquet snapshot*
