# Dynamic Entry Quality Research

**Date**: 2026-03-07
**Scope**: 6,301,097 positions across 55,623 qualified traders (>=20 positions)
**Data**: maker_positions (split-corrected), markets join (gambling excluded), entry_price BETWEEN 0.01–0.99

---

## Key Findings Summary

| Signal | IC vs Hit Rate | Direction | Notes |
|--------|---------------|-----------|-------|
| `avg_payoff_correct` (1-ep on wins) | **-0.663** | Negative | INVERTED: high payoff = cheap entry = low HR |
| `cheap_entry_ratio` (frac ep<0.40) | **-0.801** | Negative | INVERTED: consistent cheap buyers have low HR |
| `expensive_entry_ratio` (frac ep>0.75) | **+0.786** | Positive | Sure-thing buyers have high HR |
| `rr_ratio` (payoff_correct/loss_incorrect) | **-0.349** | Negative | INVERTED |
| `entry_lag_hours` (market timing) | **+0.024** | Near zero | Timing essentially irrelevant |
| Composite (payoff + cheap ratio) | **-0.813** | Negative | Best predictor but inverted |

> **CRITICAL INTERPRETATION**: Entry price is a proxy for market consensus, NOT individual skill. Traders who consistently buy cheap (ep<0.40) are betting on long shots. Their low HR is not a reflection of skill — it is the actuarially fair outcome. This is the **base rate problem** at the trader level.

---

## Approach 2: Average Payoff on Correct Trades

**Formula**: `avg_payoff_correct = avg(1.0 - entry_price) WHERE correct=1`

| Decile | Med Payoff | Med Entry Price | Med HR |
|--------|-----------|----------------|--------|
| D1 (lowest payoff) | 0.037 | 0.952 | **0.917** |
| D2 | 0.083 | 0.598 | 0.609 |
| D3 | 0.165 | 0.592 | 0.600 |
| D4 | 0.256 | 0.555 | 0.569 |
| D5 | 0.323 | 0.506 | 0.517 |
| D6 | 0.375 | 0.490 | 0.500 |
| D7 | 0.416 | 0.479 | 0.487 |
| D8 | 0.450 | 0.473 | 0.476 |
| D9 | 0.500 | 0.380 | 0.400 |
| D10 (highest payoff) | 0.622 | 0.233 | **0.263** |

**IC = -0.663**

The gradient is **perfectly monotonic and inverted**. This is the base rate at work:
- D1: traders who buy at ep=0.95 win 91.7% of the time — because ep=0.95 is near-certain outcomes
- D10: traders who buy at ep=0.23 win 26.3% of the time — consistent with ep itself

**avg_payoff_correct is simply capturing entry price, not skill.** To find genuine value hunters, we need to compare trader HR against the market-implied base rate at the prices they enter.

---

## Approach 3: Payoff Asymmetry (Risk/Reward Ratio)

**Formula**: `rr_ratio = avg(1-ep | correct) / avg(ep | wrong)`

| Decile | Med RR | Med Payoff | Med Loss | Med HR |
|--------|--------|------------|----------|--------|
| D1 (rr=0.067) | 0.067 | 0.049 | 0.886 | **0.909** |
| D2 | 0.531 | 0.195 | 0.390 | 0.621 |
| D3 | 0.721 | 0.332 | 0.461 | 0.573 |
| D4 | 0.841 | 0.366 | 0.428 | 0.538 |
| D5 | 0.998 | 0.352 | 0.353 | 0.517 |
| D6 | 1.177 | 0.373 | 0.316 | 0.485 |
| D7 | 1.418 | 0.387 | 0.272 | 0.458 |
| D8 | 1.780 | 0.419 | 0.235 | 0.417 |
| D9 | 2.445 | 0.465 | 0.187 | 0.356 |
| D10 (rr=4.725) | 4.725 | 0.579 | 0.105 | **0.235** |

**IC = -0.349**

Same inversion: high RR traders are cheap buyers with low HR. Their EV = payoff × HR - loss × (1-HR):
- D10: 0.579×0.235 - 0.105×0.765 = 0.136 - 0.080 = **+0.056 EV** (positive but thin)
- D1: 0.049×0.909 - 0.886×0.091 = 0.045 - 0.081 = **-0.036 EV** (negative)

**RR × HR (expected value proxy) is more informative than either alone.** The best EV traders appear in D6-D8 range (rr≈1.2-1.8, HR≈45-49%).

---

## Approach 4: 2D Grid — Price Bucket x Hold Time

Key findings from the grid (position-level, not trader-level):

| Price Bucket | 1-24h HR | 1-3d HR | 7-30d HR | 30d+ HR |
|-------------|----------|---------|---------|---------|
| <0.10 (deep cheap) | 0.070 | 0.118 | 0.142 | 0.112 |
| 0.10-0.25 | 0.203 | 0.226 | 0.241 | 0.217 |
| 0.25-0.40 | 0.330 | 0.360 | 0.374 | 0.361 |
| 0.40-0.55 | 0.484 | 0.500 | 0.518 | 0.520 |
| 0.55-0.70 | 0.623 | 0.638 | 0.661 | 0.672 |
| 0.70-0.85 | 0.784 | 0.794 | 0.805 | 0.844 |
| 0.85+ (sure thing) | 0.944 | 0.951 | 0.961 | 0.973 |

**Hold time has a small but consistent positive effect within each price band** (+3-7pp from 1-24h → 30d+). Longer holds slightly outperform — consistent with information advantage persisting over time.

**But price band dominates hold time.** A cheap position held 30d+ (ep<0.25, HR=0.217) still underperforms a mid-price position held <24h (ep=0.40-0.55, HR=0.484).

### 2D Grid Interpretation for Strategy

The 2D grid is useful for **calibrating fair HR**:
- A trader buying at ep=0.30 consistently winning 45%+ is genuinely skilled (vs 33-36% base rate)
- A trader buying at ep=0.90 winning 95%+ is NOT skilled — that's just base rate
- The **excess HR vs grid-implied base rate** is the correct skill metric

---

## Approach 1: Entry Timing (Market Lifecycle)

**Formula**: `entry_lag_hours = hours since market created_at`

| Decile | Med Lag | Med HR | Med Entry Price |
|--------|---------|--------|----------------|
| D1 (fastest) | 26h | 0.487 | 0.502 |
| D2 | 56h | 0.457 | 0.446 |
| D3 | 99h | 0.500 | 0.501 |
| D4 | 131h | 0.500 | 0.493 |
| D5 | 151h | 0.556 | 0.535 |
| D6 | 158h | 0.588 | 0.561 |
| D7 | 212h | 0.482 | 0.467 |
| D8 | 324h | 0.479 | 0.465 |
| D9 | 1070h | 0.542 | 0.522 |
| D10 (latest) | 2508h | 0.538 | 0.529 |

**IC = +0.024** — essentially zero. Entry timing is not a skill signal.

The apparent non-monotonicity (D5-D6 spike at 151-158h) and confound with entry price (later entrants buy at higher prices because markets drift toward certainty) make this metric unreliable.

### Early vs Late x Cheap/Mid/Expensive cross

| Timing | Price Zone | n | HR |
|--------|-----------|---|--|
| Early | Cheap (<0.40) | 629,354 | 0.149 |
| Early | Mid (0.40-0.75) | 459,138 | 0.542 |
| Early | Expensive (>0.75) | 162,062 | 0.909 |
| Late | Cheap (<0.40) | 2,329,438 | 0.188 |
| Late | Mid | 1,526,693 | 0.578 |
| Late | Expensive (>0.75) | 1,194,411 | 0.922 |

Within each price zone, **late entrants slightly outperform early entrants** (e.g., cheap: 0.149 → 0.188; mid: 0.542 → 0.578). This is the **anti-early-entry finding**: late entrants have more information and buy at prices already corrected by the market. Early cheap buys are often just guesses.

**Recommendation: discard entry timing as a signal.** It's dominated by price.

---

## Approach 5: Bargain Hunting Consistency

**Formula**: `cheap_entry_ratio = frac(entry_price < 0.40)` per trader

| Quintile | Med Cheap Ratio | Med Expensive Ratio | Med HR |
|----------|----------------|--------------------|----|
| Q1 (least cheap) | 0.000 | 0.556 | **0.750** |
| Q2 | 0.250 | 0.333 | 0.607 |
| Q3 | 0.400 | 0.333 | 0.542 |
| Q4 | 0.528 | 0.233 | 0.447 |
| Q5 (most cheap) | 0.754 | 0.069 | **0.278** |

**IC (cheap_ratio → HR) = -0.801** — strongest signal, and inverted.
**IC (expensive_ratio → HR) = +0.786** — mirror of cheap, positive.

The quintile Q1 traders (cheap_ratio=0, expensive_ratio=0.556) achieve HR=0.750 because they predominantly buy expensive (near-certain) outcomes. This is not skill — it is selection of near-certainties.

**Key takeaway**: cheap_entry_ratio and expensive_entry_ratio are measuring the **composition of a trader's market portfolio** (favorites vs underdogs), not their calibration skill.

---

## Approach 6: Tag-Specific Entry Quality

Entry price difference between correct and incorrect positions:

| Tag ID | HR | EP Correct | EP Wrong | Diff |
|--------|-----|-----------|---------|------|
| 101757 | 0.495 | 0.747 | 0.174 | **+0.573** |
| 102169 | 0.470 | 0.746 | 0.163 | **+0.583** |
| 102264 | 0.523 | 0.808 | 0.165 | **+0.643** |
| 1312 | 0.548 | 0.778 | 0.201 | **+0.578** |
| tag=2 | 0.509 | 0.694 | 0.215 | +0.479 |
| tag=1 | 0.435 | 0.601 | 0.278 | +0.323 |

**Consistent across all tags**: correct positions are entered at significantly higher prices than incorrect positions. This is the base rate effect again — when a trader is right, the market was already pricing it as likely.

The `ep_correct - ep_wrong` gap varies substantially by tag (0.28-0.64). Tags with larger gaps have more "sure-thing" trading (ep=0.80 wins vs ep=0.17 losses). This could be useful for **tag-specific baseline calibration**.

---

## Root Cause: The Entry Price / Hit Rate Identity

The fundamental issue with all these approaches:

> **entry_price ≈ market's implied probability of correct**
> **hit_rate ≈ actual probability of correct**
> **If markets are well-calibrated: hit_rate ≈ entry_price**

This means:
- `avg_payoff_correct = avg(1-ep | correct)` ≈ `1 - avg(ep | correct)` ≈ `1 - hit_rate` (for calibrated markets)
- Everything derived from entry price without subtracting the market's base rate is measuring **market calibration**, not **trader skill**

The correct skill metric is **excess HR** = `hit_rate - avg_entry_price` (or vs tag-specific base rate).

---

## Recommended Metric: Price-Calibrated Excess Return

### For individual positions:
```
position_edge = correct - entry_price
```
Expected value 0 for a calibrated bettor. Positive = outperformed market.

### Per-trader aggregation:
```
avg_edge = mean(correct - entry_price) across all positions
```
This is equivalent to: `hit_rate - avg_entry_price`.

Interpretation:
- avg_edge = 0.00 → perfectly calibrated (no alpha)
- avg_edge = +0.05 → consistently finds markets priced 5pp cheap
- avg_edge = -0.05 → consistently buys overpriced positions

### Decile analysis needed: `avg_edge → future HR`

This would be the "true" IC. The prior research on `striking_score` used a similar approach (edge = |yes_won - entry_price|) but was non-monotonic because it used absolute value. The signed version `avg_edge = mean(correct - entry_price)` should be strictly monotonic.

---

## Composite Score Recommendation

| Approach | Verdict | Reason |
|----------|---------|--------|
| avg_payoff_correct | REJECT as standalone | Proxy for (1-entry_price), not skill |
| cheap_entry_ratio | REJECT as standalone | Proxy for underdog-buyer tendency |
| expensive_entry_ratio | REJECT as standalone | Proxy for favorite-buyer tendency |
| rr_ratio | REJECT as standalone | Measures bet structure, not skill |
| entry_lag (timing) | REJECT | IC≈0, dominated by price |
| **price-calibrated excess** `avg(correct - ep)` | **RECOMMENDED** | True measure of trader alpha |
| hold_time × price grid | **USEFUL** for calibration | Sets context-specific base rates |

### Implementation for Scorecard

```python
# Per trader:
avg_edge = mean(correct - entry_price)   # signed excess return

# Use 2D price x hold grid to set position-level expected value,
# then sum excess returns
calibrated_edge = sum(correct - grid_expected_hr[price_bucket][hold_bucket])
```

The 2D grid (Approach 4) provides baseline HR by (price_bucket, hold_bucket) which can be used to compute **context-adjusted excess return** — a cleaner separation of skill from bet composition.

---

## Next Steps for Scorecard

1. **Compute `avg_edge = mean(correct - entry_price)` per trader** — this is the primary new signal
2. **Test IC of avg_edge vs future HR** across train/test folds (this research only ran on full history)
3. **Compute grid-adjusted edge** using 2D price x hold buckets as context base rates
4. **Composite**: `0.7 * hit_rate_weighted + 0.3 * avg_edge_normalized` — avg_edge acts as a tilt away from sure-thing buyers toward genuine long-shot hunters
5. **Hard filter**: exclude traders with `cheap_entry_ratio > 0.70` who have HR < 30% — these are pure gambler profiles, not alpha generators

---

## Data Notes

- 29.9M raw maker positions → 8.9M after filter (resolved, valid entry_price, gambling excluded) → 6.3M for qualified traders (>=20 positions)
- 39% of positions have ep<0.10 with HR≈7-14% — deep long-shot volume dominates but has negligible edge (consistent with prior research memory note)
- Tag-specific EP gaps are large enough to warrant tag-stratified analysis in scoring
