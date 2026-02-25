# S1: Proportional Copy of Longshot YES Specialists

**Status**: PRIMARY strategy, HIGH confidence
**Capital allocation**: $1,000 of $1,500 initial
**Direction**: Follows individual trader positions (sizing-weighted)
**Source insights**: copy/01-14, Stratregfinement.md

---

## Edge Summary

Consistent traders' edge is in **position sizing**, not direction prediction. Their directional accuracy is only 37-54%, but they bet 2.4x larger on winners than losers. Proportional copy preserves this sizing signal; fixed-bet copy destroys it.

The best traders to copy are **longshot YES specialists** — traders who buy YES at <50c and are right far more often than the market expects. This inverts the intuition from consensus copy (where NO-only dominates) because proportional copy amplifies ROI, and ROI is mechanically much higher on cheap tokens.

---

## Trader Pool Construction

### Selection Criteria (layered filters)

| Filter | Value | Source | Effect |
|--------|-------|--------|--------|
| Consistency | 9 consecutive profitable months | copy/02 | 87.8% forward win rate |
| MVF | < 0.10 (pure taker) | copy/03 | 74% OOS profitable, 0.555 train-test corr |
| Min markets/month | >= 20 | copy/02 | Raises median fwd PnL from $0.07 to $4,691 |
| Entry price | Median directional entry <= 0.80 | copy/12 | Removes 83% near-certainty traders |
| **Grading** | **longshot_yes_fraction > 15%** | **copy/14** | **Nearly doubles returns** |
| Recency | Active within 2 months | copy/05 | Ensures continued trading |

### Expected Pool Size

~20-44 traders (grows over time as more traders achieve consistency).

### Why Each Filter Matters

**Consistency (copy/02)**: Traders with N consecutive profitable months have dramatically higher future win rates than the 47.1% baseline. 9-month consistency gives 87.8% forward win rate with ~8-10 month signal half-life. Signal is real — survives volume-matching (not just a proxy for high volume). 600x enrichment vs random walk expectation.

**MVF filter (copy/03)**: Pure takers (MVF < 0.10) have the highest OOS profitability (74%) and strongest train-test correlation (0.555). Profitability inverts at MVF 0.48. Makers lose money collectively (-$220M) despite high win rates — classic market-making trap.

**Min markets (copy/02)**: Without it, median forward PnL is near zero (driven by whale outliers). At min_markets=20, median PnL jumps to $4,691 — a 67,000x improvement. Win rate stays the same; the filter ensures signal is broadly shared, not one-whale dependent.

**Entry price filter (copy/12)**: 83% of the consistent pure_taker pool has median entry price > $0.90. These traders "win" by buying near-certainties right before resolution — not genuine skill. Removing them concentrates on traders with real edge.

**Longshot YES grading (copy/14)**: The single strongest predictor of holdout copy profitability. Spearman r = +0.578 with forward ROI. Traders with >15% longshot YES fraction produce +26.4%/month vs +17.3% for full pool. 9/9 win months in walk-forward.

---

## Sizing & Allocation

### Equal-Weight Beats Kelly (copy/09)

Equal-weight (1/N) across traders: $7,695 final from $1,500 (+413%), Sharpe 0.97, 8.1% max DD.
Best Kelly variant (cap=10%): $4,872 (+225%), Sharpe 0.81, 12.3% max DD.

**Why Kelly fails here**: The pool is already filtered to uniformly skilled traders. Kelly tries to concentrate on "obviously better" traders but there aren't any — the consistency filter already did the heavy lifting. Kelly's sensitivity to noisy sigma estimates amplifies errors.

### Position Sizing Signal (copy/08)

Winners are 2.4x larger than losers by volume. The average win is bigger because more capital was committed, not better directional calls. Fixed $100 bets destroy this signal — copy win rate drops to 33-40%.

**Rule**: Replicate relative sizing. Track each trader's recent average position size. A trader betting 5x their average is a much stronger signal than 0.5x.

### Three Allocation Approaches ($10K capital, 9 months — copy/08)

| Approach | Monthly PnL | ROI/mo |
|----------|----------:|-------:|
| Vol-weighted (all traders) | $1,424 | 14.2% |
| Vol-weighted (top-5 by volume) | $2,723 | 27.2% |
| Equal-weight (1/N by ROI) | $2,094 | 20.9% |

Apply 50% haircut for realistic expectations.

---

## Contradiction Handling (copy/10)

When pool traders disagree on a market (A buys YES, B buys NO):

| Policy | 9-Month PnL | vs Copy-All |
|--------|----------:|:-----------:|
| **Skip contradicted** | **$7,529** | **+22%** |
| Majority-wins | $6,540 | +6% |
| Vol-weighted net | $6,320 | +2% |
| Copy all (both sides) | $6,195 | baseline |

**Before entry**: Skip markets where pool is already contradicted.
**After entry**: HOLD existing position if contradiction develops later. Neither first nor second mover is reliably right (49-53% each). Contradicted markets still contribute $120-352/month.

---

## Equity Curve (copy/11)

$1,500 initial, equal-weight, skip contradicted, longshot YES >15% grade.

| Month | Compound End | ROI |
|:-----:|----------:|----:|
| 2025-05 | $1,714 | +14.3% |
| 2025-06 | $2,052 | +19.7% |
| 2025-07 | $1,894 | -7.7% |
| 2025-08 | $2,302 | +21.5% |
| 2025-09 | $2,954 | +28.3% |
| 2025-10 | $3,399 | +15.1% |
| 2025-11 | $4,575 | +34.6% |
| 2025-12 | $6,003 | +31.2% |
| 2026-01 | $7,695 | +28.2% |

**With longshot YES >15% grade (copy/14)**: $11,834 final (+689%). 9/9 win months. Pool grows from 7 to 44 traders, concentration decreases (top trader 86% -> 27%).

### Realistic Haircuts

| Scenario | Compound Final |
|----------|----------:|
| Upper bound | $7,695 |
| 30% haircut | $4,812 |
| **50% haircut** | **$3,389** |
| 70% haircut | $2,382 |

---

## Two Types of Consistent Traders (copy/14)

| | Longshot Specialists | Near-Certainty Buyers |
|---|:---:|:---:|
| Mean entry price | <0.55 | >0.70 |
| Win rate | 36% | 78% |
| ROI per trade | 50% | 22% |
| **Holdout ROI** | **+22-26%/mo** | **-13%/mo** |

Near-certainty buyers look great on paper (78% WR) but are terrible to copy. They pay $0.92 for $1.00 tokens — every win is +8% but every loss is -100%.

---

## Grading Feature Importance (copy/14)

| Feature | Spearman r with holdout ROI |
|---------|:---:|
| **longshot_yes_fraction** | **+0.578** |
| win_rate | -0.504 (counterintuitive: lower training WR = better) |
| mean_entry | -0.478 (cheaper = better) |
| roi | +0.411 |
| no_fraction | **-0.398** (more NO bias = WORSE) |

---

## Position-Level Evidence (copy/14)

Within the graded pool, May 2025 - Jan 2026:

| Direction | Entry Bin | Win Rate | ROI | $/bet |
|-----------|-----------|:--------:|:---:|------:|
| **YES** | **<40c** | **60.3%** | **72.5%** | **$681** |
| NO | <40c | 49.5% | 34.0% | $165 |
| NO | >85c | 30.9% | 20.6% | $104 |

YES at <40c is the dominant trade: 60.3% win rate at 20-30% implied probability = ~30pp of edge.

---

## Risk Profile

- **Single losing month**: Jul 2025 at -7.7%, recovered next month
- **Max drawdown**: 8.1% (shallow, brief)
- **No catastrophic risk**: Diversified across 20-44 traders
- **Concentration risk**: Pool grows over time, top trader share decreases from 86% to 27%

---

## Key Differences: Proportional vs Consensus Copy

| Context | Best Signal | Mechanism |
|---------|-------------|-----------|
| **Proportional copy** (this) | YES longshot specialists | ROI matters; 60% WR at 20c = 72% ROI |
| **Consensus copy** (S3) | NO-only fixed bets | Direction matters; NO base rate 62% |

Both are valid for different allocation approaches. S1 is the primary strategy; S3 is supplemental.
