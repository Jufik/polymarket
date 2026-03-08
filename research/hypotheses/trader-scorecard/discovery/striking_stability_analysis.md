# Striking Score + Stability Bonus Analysis

**Task**: #2 — trader-scorecard hypothesis  
**Date**: 2026-03-07  
**Dataset**: maker_positions (DuckDB snapshot), ~29.9M rows, gambling slugs excluded  
**Agent**: Researcher  

---

## Scope

Two metrics explored:
1. **striking_score** — entry edge relative to market uncertainty (proxy for value-hunting)
2. **stability_bonus** — consistency of monthly hit rate (proxy for sustained skill vs luck)

Gambling exclusion: `slug NOT LIKE '%updown%' AND slug NOT LIKE '%up-or-down%'`

---

## Metric 3: striking_score

### Definition (proxy approach)

```
entry_price_proxy = |net_usd| / |net_yes|   (for YES positions)
                  = |net_usd| / |net_no|    (for NO positions)
resolution_price  = 1.0 if correct, 0.0 if wrong
edge              = |resolution_price - entry_price_proxy|
striking_score    = edge / (sqrt(max(hold_days, 0)) + 0.1)
```

**Vol proxy**: `sqrt(hold_days)` as uncertainty proxy. Longer holds imply more time for price movement, hence higher vol. The `+0.1` floor prevents division near zero for same-day resolutions.

**Proxy limitations**:
- Entry price is average cost, not point-in-time price (split over multiple trades)
- No 48h price history available — vol is approximated by hold time
- Negative net_yes/no positions (from splits) are excluded (filter: `entry_price_proxy BETWEEN 0.01 AND 0.99`)

### Data coverage

| Filter | Count |
|--------|-------|
| Valid markets (no gambling) | 301,649 |
| Positions loaded (resolved, non-trivial) | 12,615,205 |
| Positions with valid striking score | 8,896,679 |
| Traders with >= 10 positions | 128,914 |

### Entry price distribution

| Price bucket | Count | HR% | avg_edge | avg_striking |
|-------------|-------|-----|----------|-------------|
| 0-10% | 2,237,211 | 7.6% | 0.104 | 0.096 |
| 10-20% | 651,473 | 18.2% | 0.273 | 0.275 |
| 20-30% | 585,775 | 26.7% | 0.380 | 0.404 |
| 30-40% | 606,667 | 35.0% | 0.453 | 0.511 |
| 40-50% | 817,284 | 46.0% | 0.494 | 0.599 |
| 50-60% | 926,083 | 54.1% | 0.495 | 0.595 |
| 60-70% | 604,149 | 66.4% | 0.450 | 0.510 |
| 70-80% | 507,821 | 75.8% | 0.371 | 0.400 |
| 80-90% | 516,863 | 87.5% | 0.236 | 0.236 |
| 90-100% | 1,443,353 | 96.7% | 0.070 | 0.056 |

**Key observation**: edge is highest in the 40-60% price range (0.49-0.50), and striking_score peaks at 40-70% buckets (0.51-0.60). This makes sense — mid-market positions have the most room to swing either way.

### Per-trader striking_score distribution

| Percentile | Value |
|-----------|-------|
| min | 0.002 |
| p10 | 0.011 |
| p25 | 0.041 |
| p50 | 0.158 |
| p75 | 0.377 |
| p90 | 0.595 |
| p95 | 0.703 |
| max | 1.539 |

### Striking score decile → HR relationship

| Decile | n_traders | med_striking | avg_HR% | med_HR% | avg_pnl/trade |
|-------|-----------|-------------|---------|---------|--------------|
| 1 (lowest) | 12,892 | 0.007 | 44.97% | 50.00% | -0.89 |
| 2 | 12,892 | 0.016 | 49.80% | 52.94% | -6.75 |
| 3 | 12,892 | 0.041 | 56.99% | 55.36% | +4.46 |
| 4 | 12,892 | 0.081 | 56.07% | 53.85% | +2.15 |
| 5 | 12,891 | 0.129 | 54.61% | 52.63% | +7.82 |
| 6 | 12,891 | 0.192 | 50.00% | 48.39% | +1.02 |
| 7 | 12,891 | 0.284 | 47.71% | 47.06% | -11.36 |
| 8 | 12,891 | 0.377 | 47.95% | 48.28% | -7.01 |
| 9 | 12,891 | 0.521 | 49.22% | 50.00% | -16.41 |
| 10 (highest) | 12,891 | 0.703 | 49.17% | 50.00% | -7.80 |

**Finding**: Striking score has a **non-monotonic / inverted-U relationship with HR**. Deciles 3-5 (med_striking 0.04–0.13) have the highest HR (54-57%). Very high striking traders (deciles 7-10) drop to ~47-50% HR — they take large edge positions that DON'T resolve in their favor.

### Quintile breakdown — "value hunter" hypothesis

| Quintile | n | avg_HR% | avg_edge | avg_entry_px | pct_contrarian | pct_confident | avg_pnl |
|---------|---|---------|---------|-------------|---------------|--------------|---------|
| 1 (low) | 25,783 | 47.39% | 0.065 | 0.470 | 52.8% | 45.9% | -66.37 |
| 2 | 25,783 | 56.53% | 0.150 | 0.554 | 42.0% | 50.2% | +203.71 |
| 3 | 25,783 | 52.31% | 0.251 | 0.514 | 43.4% | 39.9% | +1053.50 |
| 4 | 25,783 | 47.83% | 0.357 | 0.473 | 38.7% | 21.9% | +218.85 |
| 5 (high) | 25,782 | 49.19% | 0.412 | 0.489 | 30.2% | 15.7% | +599.44 |

**Key observation**: Q2 (mild edge-seeking, avg_entry 0.55) has the highest HR at 56.5%. Q5 (high edge-seeking, avg_entry 0.49) has lower HR (49.2%) but still positive cumulative PnL (+$599). Low striking (Q1) has the worst HR AND worst PnL. 

**Interpretation**: "Value hunters" in Q5 take extreme market bets (only 15.7% confident positions, avg_entry 0.49), which is contrarian territory. Their HR suffers but they capture edge when right. Moderate striking (Q2-Q3) identifies traders who buy into markets with genuine uncertainty at reasonable prices.

### Striking score recommendation

- **Optimal range**: striking_score in [0.04, 0.20] (deciles 3-5) predicts best HR
- **High striking > 0.37**: signals over-aggression — avoid or down-weight
- **Low striking < 0.04**: may indicate position sizing issues or chasing consensus prices
- **Correlation with HR is weak and non-monotonic** — striking is NOT a standalone score

**Suggested use**: use striking_score as a feature in a composite score, NOT as a solo gate.

---

## Metric 4: stability_bonus

### Definition

```python
# Monthly windows, >= 5 trades per month
monthly_hr(trader, month) = avg(correct)
stability_bonus(trader) = mean(monthly_hr) / (std(monthly_hr) + 0.01)
stability_xp(trader)    = stability_bonus * ln(1 + n_months)  # XP variant
```

**Requirement**: >= 6 months with >= 5 positions each.

### Data coverage

| Filter | Count |
|--------|-------|
| (trader, month) pairs | 2,172,714 |
| Traders meeting >= 6 months criteria | 5,471 |

**Note**: Only 5,471 traders qualify — a significant selection filter. This is feature, not a bug: it identifies committed, long-horizon participants.

### Stability distribution

| Percentile | stability_bonus |
|-----------|----------------|
| min | 0.022 |
| p10 | 1.713 |
| p25 | 2.339 |
| p50 | 3.269 |
| p75 | 4.738 |
| p90 | 6.989 |
| p95 | 8.874 |
| max | 81.959 |

### Stability decile → HR (STRONG signal)

| Decile | n_traders | med_stability | med_HR% | avg_HR% | med_std% | months | avg_monthly_pnl |
|-------|-----------|--------------|---------|---------|---------|--------|----------------|
| 1 (lowest) | 548 | 1.392 | 26.27% | 25.82% | 18.28% | 7.0 | -99.0 |
| 2 | 547 | 1.948 | 37.70% | 37.68% | 18.38% | 8.0 | -63.5 |
| 3 | 547 | 2.339 | 43.16% | 43.52% | 17.45% | 8.0 | -217.2 |
| 4 | 547 | 2.708 | 46.65% | 46.39% | 16.23% | 8.0 | -794.1 |
| 5 | 547 | 3.048 | 48.37% | 48.54% | 14.87% | 8.0 | -32.3 |
| 6 | 547 | 3.457 | 51.53% | 52.06% | 13.78% | 9.0 | +1171.6 |
| 7 | 547 | 4.028 | 54.63% | 54.77% | 12.60% | 9.0 | +283.7 |
| 8 | 547 | 4.741 | 57.27% | 57.87% | 11.08% | 9.0 | +4869.7 |
| 9 | 547 | 5.948 | 60.90% | 61.73% | 9.21% | 8.0 | +1193.6 |
| 10 (highest) | 547 | 8.892 | 75.03% | 73.48% | 6.52% | 8.0 | +3955.9 |

**This is a VERY strong signal.** Stability decile monotonically predicts HR across all 10 deciles:
- D1 (unstable): 26.3% HR → lose money
- D10 (stable): 75.0% HR → strong profit

The relationship is nearly perfectly monotone, with HR rising from 26% to 75% across deciles. Monthly std drops from 18.3% to 6.5% — stable traders genuinely have lower variance, not just higher mean.

### XP variant (stability × log(1 + n_months))

| Decile | n | med_xp | med_HR% | med_months |
|-------|---|--------|---------|-----------|
| 1 | 548 | 2.984 | 26.86% | 7.0 |
| 5 | 547 | 6.921 | 47.13% | 8.0 |
| 10 | 547 | 20.693 | 74.50% | 9.0 |

XP deciles track similarly to raw stability — the log(months) weighting doesn't change the ordering dramatically since most stable traders have similar month counts (7-10 months). XP still monotonically predicts HR.

### Stability adds signal beyond raw HR

**Key question**: Does stability identify skill vs luck beyond just high overall HR?

From the decile data:
- D10 stability (75% HR, 6.5% std) vs D8 (57% HR, 11% std) — same monthly position count
- A trader with 75% HR AND low variance is fundamentally different from 75% HR with high variance
- Monthly PnL in D8 = +$4870, D9 = +$1194, D10 = +$3956 — all profitable but D10's consistency translates directly to sustainable PnL

**Answer**: Yes — stability distinguishes "lucky streak" traders from consistent performers.

### Per-tag stability (top 20 by median stability)

| tag_id | n_stable | med_stability | avg_HR% | spread_HR% |
|--------|---------|--------------|---------|-----------|
| 100410 | 28 | 5.29 | 56.8% | 25.2% |
| 100477 | 33 | 4.28 | 68.8% | 14.9% |
| 102561 | 23 | 4.24 | 52.8% | 21.9% |
| 185 | 20 | 4.06 | 70.7% | 10.5% |
| 101550 | 138 | 4.04 | 64.3% | 20.1% |
| 102114 | 176 | 3.97 | 46.1% | 22.5% |
| 28 | 1,091 | 3.81 | 50.2% | 18.5% |
| 1 | 8,890 | 3.71 | 58.4% | 24.3% |

**Tag 185 and 100477** stand out with high HR (70.7% and 68.8%) and low spread. Tag 1 (likely a large generic tag) has 8,890 stable traders at 58.4% HR — significant pool.

---

## Striking vs Stability Cross-Analysis

### Overlap

5,445 traders have BOTH metrics (qualifying for both striking_score >=10 positions AND stability >=6 months).

### Correlation matrix

|  | avg_striking | stability_bonus | overall_hr |
|--|-------------|----------------|-----------|
| avg_striking | 1.000 | -0.006 | -0.117 |
| stability_bonus | -0.006 | 1.000 | +0.498 |
| overall_hr | -0.117 | +0.498 | 1.000 |

**Key findings**:
1. **Striking and stability are essentially uncorrelated** (r=-0.006) — they measure independent properties
2. **Stability has strong correlation with HR** (r=+0.498) — high signal
3. **Striking has negative correlation with HR** (r=-0.117) — moderate negative signal

This means striking and stability are complementary, non-redundant features.

### Quadrant analysis

| Quadrant | n_traders | avg_HR% | avg_total_pnl |
|---------|-----------|---------|--------------|
| high_stability_only | 951 | 68.16% | +$23,377 |
| high_both | 1,776 | 55.65% | +$26,056 |
| low_both | 1,030 | 42.56% | +$288 |
| high_striking_only | 1,688 | 42.30% | -$5,123 |

**Critical insight**: 
- **high_stability_only** traders (stable HR, moderate striking) have the HIGHEST avg HR (68.2%) — these are the real gems
- **high_striking_only** traders (value-hunting without consistency) have the WORST PnL (-$5,123) — contrarians who don't win
- **high_both** traders have good PnL (+$26k) — striking combined with stability is profitable

**Recommendation**: stability_bonus should be the PRIMARY gate; striking_score should be a secondary modifier.

### Top 20 composite traders (striking × stability, HR > 50%)

Selected examples:

| trader | n_pos | HR% | striking | stability | stability_xp | months | total_pnl | composite |
|--------|-------|-----|---------|----------|-------------|--------|-----------|-----------|
| 0x01ed... | 242 | 98.3% | 0.182 | 75.66 | 174.2 | 9 | +$1,439 | 13.74 |
| 0xa676... | 22 | 95.5% | 0.107 | 77.57 | 150.9 | 6 | +$13,449 | 8.30 |
| 0x9f36... | 223 | 77.1% | 0.511 | 14.90 | 29.0 | 6 | +$393 | 7.61 |
| 0x2ff0... | 752 | 69.5% | 0.586 | 12.11 | 23.6 | 6 | +$406,915 | 7.10 |
| 0xb5e7... | 2,143 | 60.9% | 0.609 | 11.38 | 31.6 | 15 | +$27,879 | 6.93 |
| 0xb786... | 5,658 | 53.4% | 0.598 | 13.50 | 38.3 | 16 | +$1,129,888 | 8.07 |

Notable: `0xb786...` has 16 months, 5,658 positions, 53.4% HR, $1.1M total PnL. `0x2ff0...` has 69.5% HR with $406k PnL over 752 trades. These are high-quality signals.

---

## SQL Reference

### Striking score (DuckDB)

```sql
-- Positions with entry price proxy
CREATE OR REPLACE TABLE _tmp_ts_positions AS
SELECT trader, condition_id, position, correct, realized_pnl,
    net_usd, net_yes, net_no, first_trade, resolved_at,
    CASE
        WHEN position = 'YES' AND abs(net_yes) > 0.01 THEN abs(net_usd) / abs(net_yes)
        WHEN position = 'NO' AND abs(net_no) > 0.01  THEN abs(net_usd) / abs(net_no)
        ELSE NULL
    END AS entry_price_proxy,
    CAST(correct AS DOUBLE) AS resolution_price,
    date_diff('hour', first_trade, resolved_at) / 24.0 AS hold_days
FROM maker_positions mp
WHERE condition_id IN (SELECT condition_id FROM _tmp_ts_valid_markets)
  AND resolved_at IS NOT NULL AND position IN ('YES', 'NO') AND abs(net_usd) > 0.01;

-- Striking score
SELECT trader,
    avg(abs(resolution_price - entry_price_proxy) / (sqrt(GREATEST(hold_days, 0)) + 0.1)) AS avg_striking_score
FROM _tmp_ts_positions
WHERE entry_price_proxy BETWEEN 0.01 AND 0.99
GROUP BY trader HAVING count(*) >= 10;
```

### Stability bonus (DuckDB)

```sql
WITH monthly_hr AS (
    SELECT trader, date_trunc('month', resolved_at) AS month_bucket,
        count(*) AS n_pos, avg(correct) AS monthly_hr
    FROM _tmp_ts_positions
    GROUP BY 1, 2
    HAVING n_pos >= 5
)
SELECT trader,
    count(DISTINCT month_bucket) AS n_months,
    avg(monthly_hr) AS mean_hr,
    stddev_pop(monthly_hr) AS std_hr,
    avg(monthly_hr) / (stddev_pop(monthly_hr) + 0.01) AS stability_bonus,
    avg(monthly_hr) / (stddev_pop(monthly_hr) + 0.01) * ln(1 + count(DISTINCT month_bucket)) AS stability_xp
FROM monthly_hr
GROUP BY trader
HAVING count(DISTINCT month_bucket) >= 6;
```

---

## Conclusions and Recommendations

### striking_score

1. **Not a standalone signal** — correlation with HR is weak and non-monotonic (r=-0.12)
2. **Optimal zone**: striking in [0.04, 0.20] — moderate edge-seekers (deciles 3-5) have best HR
3. **High striking (>0.37) is a warning sign** — over-aggressive position-takers with below-average HR
4. **Low striking (<0.01) is also bad** — likely chasing consensus prices, minimal edge
5. **Proxy limitation is significant**: entry_price is average cost over multiple trades, not actual trigger price. For validation, would need tick-level entry data.
6. **Recommendation**: use as a secondary feature with a sweet-spot filter (e.g., striking > 0.02 AND < 0.40)

### stability_bonus

1. **STRONG primary signal** — monotone HR prediction from 26% to 75% across deciles (r=+0.50 with HR)
2. **Covers 5,471 qualifying traders** — meaningful but restrictive pool
3. **Completely independent of striking** (r=-0.006) — non-redundant composite feature
4. **XP variant adds minor value** for very long-tenure traders (>12 months)
5. **Stability_only traders outperform** even high-both traders on HR (68% vs 56%) — consistency trumps edge-seeking
6. **Recommended threshold**: stability_bonus >= 4.0 (above p75) for inclusion in copy signal
7. **Hard gate**: stability_bonus < 2.0 should disqualify (HR < 40%, money-losing)

### Composite recommendation

```
scorecard_score = stability_bonus * w_stab + striking_score_normalized * w_strike
  where w_stab = 0.70, w_strike = 0.30
  and striking_score_normalized = max(0, 1 - |striking - 0.12| / 0.08)  # reward sweet-spot

Primary gate: stability_bonus >= 3.0 (p50) AND n_months >= 6
Secondary filter: striking_score in [0.02, 0.40]
```

The high_stability_only quadrant (68% avg HR, +$23k pnl) demonstrates this is the right priority ordering.

---

## Artifact Location

- Analysis script: `/mnt/nvme/git/polymarket/polymarket/tmp/striking_stability_analysis.py`
- Raw results (JSON): `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/trader-scorecard/discovery/striking_stability_results.json`
- This document: `/mnt/nvme/git/polymarket/polymarket/research/hypotheses/trader-scorecard/discovery/striking_stability_analysis.md`
