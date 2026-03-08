# Entry Price Percentile Scoring — Discovery Results

**Date**: 2026-03-07
**Script**: `scripts/price_scoring.py`

## Executive Summary

All four approaches to "entry price quality" produce results **dominated by prediction markets' fundamental calibration property**: entry prices ARE the market's probability estimate, so higher entry prices = higher win rates. This creates a spurious correlation that masks true skill.

**Key finding**: "Sure-thing pilers" (entry > 0.80) have 79% HR vs 43% for value buyers (entry < 0.20). This is NOT because betting on sure things is skilled — it is tautological. The real question is whether a trader BEATS the market's implied probability, which requires the population-adjusted metric (Approach 1: bucket excess HR).

**Best approach for scorecard**: Bucket excess HR (Approach 1) is the only method that isolates skill from the baseline market calibration effect.

---

## Data Scope

- Total positions (filtered, price 0.01-0.99, gambling excluded): **8,896,728**
- Traders with >= 20 positions: **55,623**
- Markets filtered: updown/up-or-down excluded
- Entry price proxy: `abs(net_usd) / abs(net_yes or net_no)` by position type

---

## Population HR by Entry Price (5pp buckets)

The market is well-calibrated: prices track probabilities precisely.

| Price Range | N Positions | Pop HR | % of Total |
|---|---|---|---|
| 0.00-0.05 | 1,662,703 | 6.1% | 18.69% |
| 0.05-0.10 | 574,551 | 12.1% | 6.46% |
| 0.10-0.15 | 355,256 | 16.5% | 3.99% |
| 0.15-0.20 | 296,218 | 20.3% | 3.33% |
| 0.20-0.25 | 285,485 | 24.3% | 3.21% |
| 0.25-0.30 | 300,290 | 29.0% | ~3.4% |
| 0.30-0.35 | 305,434 | 33.5% | ~3.4% |
| 0.35-0.40 | 301,233 | 36.7% | ~3.4% |
| 0.40-0.45 | 395,034 | 43.9% | ~4.4% |
| 0.45-0.50 | 422,253 | 48.0% | ~4.7% |
| 0.50-0.55 | 487,590 | 52.4% | ~5.5% |
| 0.55-0.60 | 438,494 | 56.5% | ~4.9% |
| 0.60-0.65 | 358,383 | 63.9% | ~4.0% |
| 0.65-0.70 | 245,767 | 68.7% | ~2.8% |
| 0.70-0.75 | 262,054 | 73.0% | ~2.9% |
| 0.75-0.80 | 245,767 | 78.7% | 2.76% |
| 0.80-0.85 | 242,795 | 84.8% | 2.73% |
| 0.85-0.90 | 274,068 | 89.9% | 3.08% |
| 0.90-0.95 | 448,284 | 94.6% | 5.04% |
| 0.95-0.99 | 995,069 | 97.6% | 11.18% |

> CRITICAL: 18.69% of all positions are 0-5 cents (deep underdogs, 6.1% HR). 11.18% are 95-99 cents (near-certainties, 97.6% HR). Any metric that does not correct for price level is measuring market calibration, not trader skill.

---

## Approach 1: Price-Bucket Excess HR (RECOMMENDED)

**Method**: Per-trader HR within 10pp price buckets minus population HR in that bucket. Weighted average across buckets = `bucket_excess_hr`.

**Principle**: A trader who achieves 20% HR in the 5-15% bucket (pop HR = 9%) has +11pp excess — genuine skill finding cheap value.

### Decile Analysis (sorted by bucket_excess_hr)

| Decile | N Traders | Avg Bucket Excess HR | Avg Overall HR |
|---|---|---|---|
| 1 (worst) | 3,836 | -11.7% | 40.5% |
| 2 | 3,836 | -6.2% | 43.5% |
| 3 | 3,836 | -3.9% | 46.0% |
| 4 | 3,836 | -2.3% | 47.4% |
| 5 | 3,836 | -1.2% | 60.6% |
| 6 | 3,836 | -0.1% | 58.7% |
| 7 | 3,836 | +1.4% | 53.9% |
| 8 | 3,836 | +3.2% | 68.0% |
| 9 | 3,835 | +5.5% | 55.7% |
| 10 (best) | 3,835 | +13.5% | 60.1% |

**Observations**:
- D1 (bucket_excess_hr = -11.7%) has avg HR of 40.5%: consistent underperformers at their own price level
- D10 (bucket_excess_hr = +13.5%) has avg HR of 60.1%: genuine outperformers at their chosen price levels
- Relationship is noisy (D5 and D8 oddly high) — small samples per bucket per trader create noise
- Range: -53.7pp to +92.4pp (extreme values are small-sample artifacts needing min_positions_per_bucket gate)

**Limitation**: Requires >=5 positions per bucket per trader, which drops 31% of traders from analysis.

**Correlation with overall HR**: r = +0.918 (strong, but distinct from raw price-level effect).

---

## Approach 2: Calibration Score

**Method**: `calibration_gap = avg(correct) - avg(entry_price_proxy)` per trader.
- Positive = trader resolves more often than their entry prices imply (buying underpriced outcomes)
- Negative = trader resolves less often than prices imply (buying overpriced outcomes)

### Decile Analysis (sorted by calibration_gap)

| Decile | N Traders | Avg Calibration Gap | Avg Overall HR |
|---|---|---|---|
| 1 (most negative) | 5,563 | -0.1117 | 39.2% |
| 2 | 5,563 | -0.0517 | 46.9% |
| 3 | 5,563 | -0.0269 | 48.3% |
| 4 | 5,562 | -0.0100 | 51.2% |
| 5 | 5,562 | +0.0037 | 52.9% |
| 6 | 5,562 | +0.0173 | 53.9% |
| 7 | 5,562 | +0.0320 | 56.4% |
| 8 | 5,562 | +0.0504 | 55.6% |
| 9 | 5,562 | +0.0774 | 55.7% |
| 10 (most positive) | 5,562 | +0.1531 | 60.5% |

**IC with future HR: +0.082** (weak but positive)

**Observations**:
- Monotonically increasing HR from D1 to D10
- D10 traders (avg gap +0.153) achieve 60.5% HR: they buy positions priced 15pp below their actual resolution rate
- D1 traders (avg gap -0.112): HR only 39.2% — buying positions priced 11pp ABOVE resolution rate
- Calibration gap and bucket excess HR are nearly identical in theory (r = 0.918 between them)

**Limitation**: Does not correct for which markets a trader participates in. A trader who exclusively operates in highly-liquid high-certainty markets can have positive calibration gap from market drift alone.

---

## Approach 3: Weighted Edge Metric — ANTI-SIGNAL, AVOID

**Method**: `avg_payoff_on_wins = avg(1 - entry_price WHERE correct=1)`. Higher = winning at cheaper prices.

### Decile Analysis (sorted by avg_payoff_on_wins)

| Decile | N Traders | Avg Payoff on Wins | Avg Overall HR |
|---|---|---|---|
| 1 (cheapest wins) | 5,548 | 0.038 | **80.0%** |
| 2 | 5,548 | 0.084 | 65.0% |
| 3 | 5,548 | 0.166 | 60.6% |
| 4 | 5,547 | 0.255 | 55.6% |
| 5 | 5,547 | 0.323 | 50.8% |
| 6 | 5,547 | 0.374 | 49.3% |
| 7 | 5,547 | 0.415 | 47.8% |
| 8 | 5,547 | 0.451 | 46.3% |
| 9 | 5,547 | 0.503 | 39.6% |
| 10 (most expensive wins) | 5,547 | 0.656 | **27.2%** |

**IC with future HR: -0.585** (strong NEGATIVE — this metric predicts HR in the wrong direction)

**Explanation of the inversion**: `avg_payoff_on_wins = avg(1 - entry_price | correct=1)`. Low payoff on wins means winning at high entry prices (near-certain bets). This just measures entry price level — not whether the trader beat the market's implied probability. D1 traders (payoff=0.038) buy at 0.96 and win — the market was right to price them at 0.96. Their "cheap payoff on wins" is an artifact of their high entry price strategy.

**Conclusion**: Do NOT use as a positive signal. It is the strongest predictor of entry price level, not skill.

---

## Approach 4: Sure-Thing Concentration — INVERTED RESULT

**Method**: `sure_thing_ratio = count(entry > 0.80 AND correct) / count(correct)`.

Hypothesis: high ratio = penalize. **Result: the opposite — high ratio predicts higher HR.**

### Decile Analysis (sorted by sure_thing_ratio)

| Decile | N Traders | Avg Sure-Thing Ratio | Avg Overall HR |
|---|---|---|---|
| 1 (no sure-thing wins) | 5,548 | 0.000 | **42.9%** |
| 2 | 5,548 | 0.015 | 43.3% |
| 3 | 5,548 | 0.103 | 42.1% |
| 4 | 5,547 | 0.197 | 42.4% |
| 5 | 5,547 | 0.300 | 44.4% |
| 6 | 5,547 | 0.419 | 49.3% |
| 7 | 5,547 | 0.567 | 53.6% |
| 8 | 5,547 | 0.758 | 59.5% |
| 9 | 5,547 | 0.921 | 65.3% |
| 10 (all wins are sure-things) | 5,547 | 1.000 | **79.3%** |

**IC with future HR: +0.552** (strong POSITIVE — in the wrong direction for the hypothesis)

**Explanation**: D10 traders who bet only on >0.80 price outcomes win 79.3% because the population HR at 0.80-1.00 is ~88-97%. Their HR UNDERPERFORMS the implied probability — they are not generating alpha.

**The key insight from segmentation**:

| Trader Type | N | Avg HR | Avg Entry Price | Avg Calib Gap |
|---|---|---|---|---|
| sure_thing_piler | 1,882 | 68.4% | 0.751 | -0.067 |
| trend_follower | 7,136 | 57.2% | 0.608 | -0.035 |
| mixed | 12,150 | 54.7% | 0.564 | -0.017 |
| value_oriented | 31,035 | 50.0% | 0.472 | +0.028 |
| skilled_cheap_buyer | 3,270 | 43.6% | 0.298 | **+0.138** |

Sure-thing pilers have avg_entry_price=0.751, calibration_gap=-0.067 (they UNDERPERFORM their implied probability). Skilled cheap buyers enter at 0.298 and win 43.6% — OUTPERFORMING the 29.8% implied probability by +13.8pp.

---

## Cross-Metric Correlations

| Metric Pair | r |
|---|---|
| HR vs Avg Entry Price | **+0.932** (tautological confound) |
| HR vs Bucket Excess HR | **+0.918** |
| HR vs Sure-Thing Ratio | +0.550 |
| HR vs Calibration Gap | +0.273 |
| HR vs Avg Payoff on Wins | **-0.663** (anti-predictor of HR) |
| Payoff on Wins vs Sure-Thing Ratio | -0.898 (near-perfect inverse — same underlying variable) |
| Calibration Gap vs Bucket Excess HR | +0.918 (approaches 1 and 2 nearly identical) |

---

## Out-of-Sample IC (Train < 2024-07-01 → Test >= 2024-07-01)

Note: severe attrition — only 354 traders met both training (>=20 pos) and test (>=5 pos) thresholds.

| Metric | OOS IC with Future HR |
|---|---|
| Train HR (baseline) | **+0.660** |
| Sure-Thing Ratio | +0.552 (price level proxy) |
| Calibration Gap | +0.082 |
| Avg Payoff on Wins | -0.585 (anti-signal) |

Train HR dominates. Calibration gap adds marginal IC above baseline. Sure-thing ratio's strong IC is the price-level confound again.

---

## Conclusions

### What the data shows

Polymarket is well-calibrated. The four metrics proposed in the hypothesis all fail to isolate skill from price-level effects:

1. **Bucket excess HR** (Approach 1): the CORRECT approach — controls for price level by comparing within-bucket performance. Achieves r=+0.918 with HR (legitimate, not tautological).
2. **Calibration gap** (Approach 2): a reasonable proxy, but still partially confounded by which markets a trader participates in. IC=+0.082 vs future HR.
3. **Avg payoff on wins** (Approach 3): INVERTED signal. IC=-0.585 with future HR. Do not use.
4. **Sure-thing ratio** (Approach 4): INVERTED prediction direction. IC=+0.552 but for the wrong reason (price-level proxy).

### Recommended scorecard component

**Primary**: `bucket_excess_hr` — weighted average excess HR across 10pp price buckets.

**Hard exclusion gate**:
- `avg_entry_price > 0.85` AND `bucket_excess_hr < +2pp`: systematic sure-thing pilers with no alpha (exclude from copy pool; they have no upside room)

**Soft penalty**: `calibration_gap < -5pp`: these traders systematically overpay relative to their outcome rate

### Composite formula

```
entry_price_score = bucket_excess_hr        # primary metric (controls for price level)
```

Or if bucket data is sparse (fewer than 5 markets per bucket):
```
entry_price_score_fallback = calibration_gap  # simpler approximation
```

Do NOT use sure_thing_ratio or avg_payoff_on_wins as scorecard inputs.

### Open questions for synthesis task

1. Does bucket excess HR add IC on top of raw HR after controlling for n_positions?
2. Is bucket excess HR stable across different market categories (politics vs sports vs crypto)?
3. What is the right minimum positions per bucket threshold to avoid small-sample noise?
4. Can bucket excess HR serve as a pool EXCLUSION criterion (e.g., bottom quintile = exclude) rather than a ranking metric?
