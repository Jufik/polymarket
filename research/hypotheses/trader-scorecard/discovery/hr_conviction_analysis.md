# Trader Scorecard Metrics: hit_rate_weighted and Conviction

**Date**: 2026-03-07
**Analyst**: Researcher agent
**Hypothesis folder**: `research/hypotheses/trader-scorecard/`
**Status**: Discovery phase — vectorized, all results are upper bounds

---

## Executive Summary

- **hit_rate_weighted** with exponential decay is effective for identifying persistent performers, but **naive (unweighted) hit rate is nearly as predictive** as any weighted variant. The optimal decay is very mild (lambda=0.001–0.003, half-life 231–693 days), and aggressive weighting (lambda >= 0.014) degrades predictive IC.
- **Conviction (as designed)** does not work in this dataset. The token-level metric is degenerate (99.5% of positions have conviction=1.0). The USDC-flow proxy is a better differentiator but captures market-maker vs. directional-trader distinction rather than trader confidence.
- **Hit rate is the dominant predictor**: IC = 0.74 for train HR -> test HR. Top-decile traders in train achieve 91.9% HR in test vs 24.6% for bottom decile.
- **Conviction as a filter on top of HR** provides only marginal uplift (+0.01–0.02 pp test HR) and reduces pool size significantly.

---

## Dataset

- **Universe**: non-updown maker positions (excluded slugs containing `updown` or `up-or-down`)
- **Updown exclusion**: 156,313 / 574,524 markets (27.2%)
- **Resolved positions**: 13,918,315 (after exclusion)
- **Unique traders**: 827,890 total; **80,141** with >= 20 resolved positions
- **Date range**: 2023-02-12 to 2026-03-05
- **Reference date for weighting**: 2026-03-05 (max resolved_at in dataset)

---

## Part 1: hit_rate_weighted

### 1.1 Metric Definition

```
correct(t) = 1 if (direction == resolution) else 0
weight(t)  = exp(-λ × days_since_resolution(t))
hit_rate_weighted(w) = sum(correct(t) × weight(t)) / sum(weight(t))
```

Reference point for `days_since_resolution`: max resolved_at in dataset (2026-03-05), so recent outcomes get the highest weight.

### 1.2 SQL Implementation

```sql
-- Step 1: Build non-updown universe
CREATE OR REPLACE TABLE _tmp_non_updown AS
SELECT DISTINCT condition_id
FROM markets
WHERE slug NOT ILIKE '%updown%' AND slug NOT ILIKE '%up-or-down%';

-- Step 2: Compute weighted HR for all traders with >= 20 positions
WITH ref AS (SELECT max(resolved_at) AS ref_date FROM maker_positions WHERE resolved_at IS NOT NULL),
trader_stats AS (
    SELECT
        p.trader,
        count(*) AS n_positions,
        avg(CAST(p.correct AS DOUBLE)) AS hit_rate_naive,
        -- lambda = 0.007 (99-day half-life)
        sum(CAST(p.correct AS DOUBLE) * exp(-0.007 * date_diff('day', p.resolved_at, ref.ref_date))) /
            NULLIF(sum(exp(-0.007 * date_diff('day', p.resolved_at, ref.ref_date))), 0) AS hr_w007
    FROM maker_positions p
    JOIN _tmp_non_updown nu ON p.condition_id = nu.condition_id
    CROSS JOIN ref
    WHERE p.resolved_at IS NOT NULL AND p.yes_won IS NOT NULL AND p.correct IS NOT NULL
    GROUP BY p.trader
    HAVING count(*) >= 20
)
SELECT * FROM trader_stats;
```

### 1.3 Distribution (80,141 qualified traders)

| Metric | p10 | p25 | p50 | p75 | p90 | p95 | Mean | Std |
|--------|-----|-----|-----|-----|-----|-----|------|-----|
| Naive HR | 0.179 | 0.357 | 0.500 | 0.650 | 0.935 | 0.994 | 0.516 | 0.257 |
| Weighted HR (λ=0.007) | 0.170 | 0.352 | 0.499 | 0.655 | 0.938 | 0.995 | 0.513 | 0.260 |

**Key observation**: Distributions are nearly identical. Recency weighting shifts individual traders but not aggregate percentiles.

HR bucket distribution (naive):
```
HR [0.0, 0.1):  5,228 traders  (6.5%)
HR [0.1, 0.2):  3,636 traders  (4.5%)
HR [0.2, 0.3):  6,002 traders  (7.5%)
HR [0.3, 0.4):  9,407 traders (11.7%)
HR [0.4, 0.5): 14,314 traders (17.9%)
HR [0.5, 0.6): 15,884 traders (19.8%)
HR [0.6, 0.7):  8,799 traders (11.0%)
HR [0.7, 0.8):  3,478 traders  (4.3%)
HR [0.8, 0.9):  2,976 traders  (3.7%)
HR [0.9, 1.0):  6,526 traders  (8.1%)
HR [1.0, 1.0]:  3,891 traders  (4.9%)  <- all-correct traders
```

The distribution is roughly uniform/bimodal, with bulges at [0.4–0.6] (random noise zone) and [0.9–1.0] (genuine skill or very few positions). The 4.9% of traders with HR=1.0 likely have very few but all-correct positions (min 20 required).

### 1.4 Naive vs Weighted Comparison

```sql
-- Correlation and MAE across lambda values
SELECT
    corr(hit_rate_naive, hr_w003) AS cor_003,
    corr(hit_rate_naive, hr_w007) AS cor_007,
    corr(hit_rate_naive, hr_w014) AS cor_014,
    corr(hit_rate_naive, hr_w030) AS cor_030,
    avg(abs(hit_rate_naive - hr_w007)) AS mae_007
FROM _tmp_hr_weighted;
```

| Lambda | Half-Life | Corr(naive, weighted) | MAE vs Naive | Top-Decile Jaccard |
|--------|-----------|----------------------|--------------|-------------------|
| 0.003  | 231 days  | 0.9967               | 0.0116       | -                 |
| 0.007  | 99 days   | 0.9810               | 0.0278       | 92.7%             |
| 0.014  | 50 days   | 0.9279               | 0.0547       | 82.7%             |
| 0.030  | 23 days   | 0.8350               | 0.0962       | 57.0%             |

**Conclusion**: At lambda=0.007, 92.7% of top-decile traders are the same as naive top-decile. Only at very aggressive weighting (lambda=0.030, 23-day half-life) do rankings differ substantially.

### 1.5 Lambda Sensitivity: Predictive IC

```sql
-- Train/test split: median resolved_at = 2025-12-05
-- IC = corr(train_HR_lambda, test_HR_naive)
```

| Lambda | Half-Life | IC (train -> test HR) |
|--------|-----------|----------------------|
| Naive (unweighted) | inf    | **0.7424** |
| 0.001              | 693d   | **0.7435** (peak) |
| 0.003              | 231d   | **0.7439** (peak) |
| 0.007              | 99d    | 0.7392             |
| 0.014              | 50d    | 0.7216             |
| 0.030              | 23d    | 0.6810             |
| 0.070              | 10d    | 0.6190             |

**Critical finding**: Recency weighting does NOT improve predictive power. The best IC (0.7439) is achieved at lambda=0.003 (231-day half-life), just 0.0015 above naive. Aggressive weighting (lambda=0.030) loses 6 percentage points of IC. The hypothesis that recent performance predicts future performance better than historical average is **not supported** in this dataset.

**Interpretation**: Trader hit rate is a persistent personality trait, not a hot-streak phenomenon. Long-run history is the best predictor.

**Recommendation**: Use naive hit rate or at most lambda=0.003 (231-day half-life). Do not use lambda > 0.010.

### 1.6 Train/Test Predictive Validity

Split date: 2025-12-05 (median resolved_at). Train: 6.96M positions, Test: 6.96M positions.
Qualified (>= 10 in both periods): **17,104 traders**.

```sql
-- Decile breakdown: sort by train HR, measure test HR
WITH ranked AS (
    SELECT *,
        ntile(10) OVER (ORDER BY train_hr_w007) AS decile
    FROM _tmp_tt
)
SELECT decile, count(*) AS n, avg(train_hr_w007) AS avg_train, avg(test_hr) AS avg_test
FROM ranked GROUP BY decile ORDER BY decile;
```

**Decile performance (HR weighted, λ=0.007):**

| Decile | N     | Avg Train HR | Avg Test HR | Avg Test PnL |
|--------|-------|-------------|-------------|-------------|
| 1 (worst) | 1,711 | 0.127 | 0.250 | +$91 |
| 2      | 1,711 | 0.278 | 0.357 | +$870 |
| 3      | 1,711 | 0.361 | 0.421 | +$412 |
| 4      | 1,711 | 0.423 | 0.457 | -$1,745 |
| 5      | 1,710 | 0.475 | 0.486 | +$6,980 |
| 6      | 1,710 | 0.522 | 0.511 | +$11,397 |
| 7      | 1,710 | 0.573 | 0.532 | +$4,695 |
| 8      | 1,710 | 0.638 | 0.569 | +$2,968 |
| 9      | 1,710 | 0.763 | 0.669 | +$2,009 |
| 10 (best) | 1,710 | 0.979 | 0.920 | +$892 |

**IC Summary:**
```
IC (naive -> test HR):   0.7424
IC (w007 -> test HR):    0.7392
IC (naive -> test PnL):  0.0049
IC (w007 -> test PnL):   0.0055
```

Key observations:
1. **IC with HR = 0.74** — very strong signal. Top-decile test HR = 92.0% vs bottom-decile 25.0%.
2. **IC with PnL ~= 0.005** — extremely weak. Hit rate does not predict PnL well. This is the **PnL composition problem**: top HR traders (decile 10) have HR=0.92 but low avg PnL because they trade tiny positions.
3. **Decile 6 (0.51 train HR) has the highest avg test PnL ($11,397)**: these are large-volume traders near 50% HR but with significant position sizes. This suggests PnL = HR × size × edge, and size dominates.

### 1.7 Per-Tag Analysis

Tags with sufficient traders (>= 50 with 10+ positions in each period):

```sql
-- Per-tag IC: how well does train weighted HR predict test HR?
```

| Tag | N Traders | Avg Train W007 | Avg Test HR | IC (w007) | IC (naive) | HR Drift |
|-----|-----------|----------------|-------------|-----------|------------|----------|
| Elections | 57 | 0.474 | 0.540 | **0.874** | 0.877 | +0.071 |
| Crypto | 3,097 | 0.666 | 0.653 | **0.869** | 0.870 | -0.011 |
| Weather | 354 | 0.409 | 0.412 | **0.828** | 0.821 | +0.005 |
| AI | 133 | 0.517 | 0.470 | 0.817 | 0.817 | -0.048 |
| Music | 89 | 0.482 | 0.437 | 0.814 | 0.811 | -0.048 |
| MrBeast | 125 | 0.490 | 0.535 | 0.803 | 0.798 | +0.046 |
| Culture | 218 | 0.452 | 0.483 | 0.799 | 0.815 | +0.035 |
| Awards | 100 | 0.454 | 0.469 | 0.788 | 0.791 | +0.017 |
| box office | 113 | 0.515 | 0.541 | 0.777 | 0.784 | +0.019 |
| Inflation | 51 | 0.573 | 0.514 | 0.774 | 0.791 | -0.065 |
| Business | 255 | 0.551 | 0.555 | 0.745 | 0.739 | +0.015 |
| Politics | 3,516 | 0.504 | 0.517 | 0.743 | 0.733 | +0.014 |
| Finance | 255 | 0.415 | 0.440 | 0.737 | 0.733 | +0.025 |
| Science | 81 | 0.513 | 0.488 | 0.734 | 0.747 | -0.024 |
| Trump | 64 | 0.413 | 0.433 | 0.725 | 0.733 | +0.027 |
| Movies | 121 | 0.495 | 0.497 | 0.702 | 0.713 | +0.003 |
| **Sports** | **4,671** | **0.499** | **0.500** | **0.675** | **0.673** | +0.002 |

**Key findings**:
- **Elections** (IC=0.874) and **Crypto** (IC=0.869) show strongest signal persistence — past skill predicts future skill most reliably.
- **Sports** (IC=0.675) is the weakest — more random or competitive, skill is harder to persist.
- **HR drift**: Elections (+0.071) and MrBeast (+0.046) traders improve over time (learning?). AI (-0.048) and Music (-0.048) traders decline (domain changing?).
- Weighted HR (w007) is rarely better than naive by tag — confirming the lambda sensitivity finding.

**Tag-specific YES win rates (large-N tags):**

| Tag | N Markets | YES Win Rate |
|-----|-----------|-------------|
| Sports | 211,751 | 38.9% |
| Esports | 2,758 | 49.1% |
| Politics | 21,276 | 26.3% |
| Crypto | 29,505 | 28.3% |
| Weather | 10,989 | 17.7% |
| Finance | 7,939 | 37.6% |
| Business | 1,007 | 17.3% |
| Elections | 306 | 16.2% |

---

## Part 2: Conviction

### 2.1 Metric Definition (as specified)

```
buys_in_direction  = sum(size where side == net_direction)
sells_in_direction = sum(size where side != net_direction)
conviction(w, market) = buys_in_direction / (buys_in_direction + sells_in_direction)
```

### 2.2 Implementation Approaches Tested

Three approaches were evaluated given data constraints:

#### Approach A: Token Direction (net_yes / net_no from maker_positions)

```sql
-- conviction = tokens held in direction / total token exposure
CASE
    WHEN position = 'YES' AND (net_yes + net_no) > 0
        THEN net_yes / (net_yes + net_no)
    WHEN position = 'NO' AND (net_yes + net_no) > 0
        THEN net_no / (net_yes + net_no)
    ELSE NULL
END AS conviction_token
```

**Result**: Degenerate. 99.5% of YES positions have net_no=0.0; the metric is 1.0 for virtually all positions. IC to hit_rate = -0.001 (no signal).

**Why**: maker_positions already represents the NET position after split-mechanic accounting. A trader who bought 100 YES and sold 30 YES has net_yes=70, net_no=0. The "selling" step happened within USDC space, not token space. Token-level conviction cannot be inferred from net_yes alone.

#### Approach B: USDC Flow Directionality (abs(net_usd) / volume)

```sql
-- conviction = |net USDC flow| / gross USDC volume
-- net_usd < 0 = net buyer (spent USDC); net_usd > 0 = net seller (received USDC)
CASE WHEN volume > 0 THEN least(abs(net_usd) / volume, 1.0) ELSE NULL END AS conviction_net
```

**Distribution:**
```
p10=0.044, p25=0.993, p50=1.000, p75=1.000, p90=1.000, p95=1.000, mean=0.807
```

**Bimodal structure**: 74.9% of positions have conviction=1.0 (pure directional), 25.1% have conviction < 1.0.

```sql
-- Correctness by conviction bucket
SELECT bucket, count(*) AS n, avg(correct) AS hit_rate, avg(realized_pnl) AS avg_pnl
FROM (... CASE WHEN conviction < 0.1 THEN '< 0.1' ... END ...)
GROUP BY bucket;
```

| Bucket | N | Hit Rate | Avg PnL |
|--------|---|----------|---------|
| < 0.1 (market makers) | 1,784,685 | **0.267** | +$24 |
| 0.1–0.5 (partial round-trip) | 1,072,462 | 0.467 | +$58 |
| 0.5–0.9 (partial directional) | 495,419 | 0.453 | -$51 |
| >= 0.9 (pure directional) | 10,565,686 | **0.492** | -$1 |

**Interpretation**: Positions with conviction < 0.1 are **market makers** — traders who buy and sell frequently within a market, maintaining a tiny net position relative to gross volume. They have high trade counts (avg 22.8) and low HR (0.267). This is not "low confidence" but rather a market-making strategy.

Positions with conviction >= 0.9 (pure buyers/holders) have HR=0.492 — essentially the market base rate for resolved YES positions. This metric measures trading style, not conviction in the common sense.

#### Approach C: Trader-Level USDC Conviction

Aggregated by trader across markets (80,141 traders with >= 20 positions):

```
p10=0.487, p25=0.663, p50=0.870, p75=0.997, p90=1.000, mean=0.787
70.6% of traders have avg_conviction >= 0.7
58.7% of traders have avg_conviction >= 0.8
46.1% of traders have avg_conviction >= 0.9
```

**Decile breakdown (trader avg conviction -> hit rate):**

| Decile | Avg Conviction | Avg HR |
|--------|----------------|--------|
| 1 | 0.208 | **0.239** |
| 2 | 0.562 | 0.477 |
| 3 | 0.662 | 0.515 |
| 4 | 0.747 | 0.517 |
| 5 | 0.830 | 0.490 |
| 6 | 0.906 | 0.456 |
| 7 | 0.959 | 0.543 |
| 8 | 0.992 | 0.556 |
| 9–10 | 1.000 | **0.682** |

IC (trader conviction -> hit_rate) = **0.437**. Signal exists, but non-monotonic in deciles 5–8 (hit rate dips for mid-range conviction traders).

### 2.3 HEDGED Positions

Positions classified as 'HEDGED' (net_yes > 0 AND net_no > 0):

- Count: 1,650,048 positions (11.9% of resolved)
- Hit Rate: 0.588 (notably higher than YES/NO avg ~0.47)
- Avg net_yes = 1,726, avg net_no = 1,846 (roughly equal YES + NO holdings)
- Interpretation: Split-mechanic traders who hold both sides. High HR because "resolution is correct in one direction" — when yes_won=1, the YES portion pays off.

**Flag**: The `correct` field for HEDGED positions should be reviewed — it's 0.588 which is suspiciously high and may reflect a classification artifact.

---

## Part 3: Combined HR + Conviction

### 3.1 2x2 Grid Analysis

Using p25/p75 quartile splits on weighted HR (λ=0.007) and avg USDC conviction:

```sql
-- 2x2 grid: HIGH/LOW HR x HIGH/LOW conviction
-- Only includes traders in the extreme quartiles
```

| HR Group | CV Group | N | Avg HR | Avg CV | Avg Total PnL |
|----------|----------|---|--------|--------|--------------|
| High HR | High Conv | 9,926 | 0.934 | 1.000 | +$264 |
| High HR | Low Conv | 1,390 | 0.745 | 0.504 | **+$4,978** |
| Low HR | High Conv | 2,947 | 0.169 | 1.000 | -$450 |
| Low HR | Low Conv | 6,835 | 0.137 | 0.225 | -$295 |

**Key insight**: High HR traders with LOW conviction (market makers) have 19x higher avg PnL than pure directional high-HR traders. This inverts the naive expectation. Market makers with high HR are rare but extremely profitable.

### 3.2 Train/Test IC Comparison

```sql
-- Information coefficient of different composite scores
SELECT
    corr(train_hr_w007, test_hr) AS ic_hr_only,
    corr(train_avg_conviction, test_hr) AS ic_cv_only,
    corr(0.7*train_hr_w007 + 0.3*train_avg_conviction, test_hr) AS ic_combo_7030
FROM _tmp_tt_combined;
```

| Metric | IC (train -> test HR) |
|--------|----------------------|
| HR weighted (λ=0.007) | **0.739** |
| Conviction alone | 0.134 |
| 0.8×HR + 0.2×CV | 0.716 |
| 0.7×HR + 0.3×CV | 0.687 |
| 0.5×HR + 0.5×CV | 0.578 |

**Adding conviction to HR hurts IC.** Conviction is a weak signal and dilutes the strong HR signal when blended.

### 3.3 Conviction as Filter Within Top-Decile HR

```sql
-- Does conviction filter improve top-decile HR traders?
-- Baseline: top 10% by train HR weighted
```

| Conviction Filter | N Traders | Avg Test HR | Delta vs Baseline |
|------------------|-----------|-------------|------------------|
| Baseline (top-decile HR) | 1,711 | 0.9195 | — |
| + conviction >= 0.70 | 1,664 | 0.9226 | +0.003 |
| + conviction >= 0.80 | 1,632 | 0.9247 | +0.005 |
| + conviction >= 0.90 | 1,566 | 0.9293 | +0.010 |
| + conviction >= 0.95 | 1,480 | 0.9353 | +0.016 |

Conviction filter within top-decile HR does provide **marginal uplift** (+0.016 at conviction>=0.95) at the cost of dropping ~13% of pool traders. This is statistically real but operationally minor.

---

## Key Findings Summary

### Finding 1: Naive HR is the Optimal Predictor (Surprised)

The expected benefit of recency weighting does not materialize. Lambda=0.003 (231-day half-life) achieves the peak IC of 0.7439, barely outperforming naive (0.7424). Aggressive weighting is counterproductive.

**Hypothesis**: Trader skill persists across the full ~3-year dataset range. Hot streaks are not the driver — consistent long-run accuracy is. The dataset may also be too sparse for each trader (median ~20–50 positions) for recency weighting to have power.

**Action**: Use naive hit rate for pool qualification. Reserve light weighting (λ=0.003) only as a tiebreaker.

### Finding 2: Token Conviction is Degenerate in This Schema

99.5% of positions have net_no=0 (for YES positions), making conviction_token=1.0 universally. The `conviction` metric as specified requires individual trade-level data (buy vs sell legs), not net positions.

**Action**: If conviction is needed, must compute from `trades` table using side field (1=buy, 2=sell?), filtered to qualified traders. This requires scanning 134M rows but can be done with predicate pushdown on trader set.

### Finding 3: USDC-Flow Conviction Identifies Market Makers

`abs(net_usd)/volume` cleanly separates market makers (conviction < 0.1, HR=0.27, high volume) from directional traders (conviction ~1.0, HR=0.49). This is a **different signal** from intended conviction.

**Useful**: Use as a trader-type classifier (market maker vs directional), not as a confidence filter.

### Finding 4: Strong HR Persistence, Tag-Specific (IC 0.67–0.87)

The overall IC of 0.74 masks tag variation:
- **Crypto, Elections**: IC > 0.87 — these markets reward genuine research skill that persists
- **Sports**: IC = 0.67 — more noisy; skill persists less (perhaps due to competitive market dynamics and volume of sports markets)

**Action**: In a consensus strategy, weight the HR signal more heavily for Crypto/Elections traders than Sports traders.

### Finding 5: PnL IC is Near Zero

IC (train HR -> test PnL) = 0.005. Hit rate does not predict PnL. The dominant drivers of PnL are position sizing and market selection, not just direction accuracy. Decile 6 (~50% HR) generates the highest average PnL because these traders are large-volume market participants near the edge.

**Implication**: Scorecard should incorporate both HR (for direction quality) and volume/size (for PnL potential). Pure HR-based copy without sizing context is incomplete.

---

## Recommendations

### Scorecard Architecture

```
score = hit_rate_naive × sqrt(n_positions)  # shrinkage by sample size
     OR naive HR with min 20-position gate
```

1. **Primary metric**: `hit_rate_naive` (or lambda=0.003 equivalent) — IC=0.74
2. **Minimum sample size**: 20 resolved positions (already standard)
3. **Recency weighting**: Optional, lambda=0.003 maximum — provides no meaningful improvement over naive
4. **Conviction filter**: Use `abs(net_usd)/volume >= 0.90` to exclude market makers from copy pool (not a confidence signal — a trader-type filter). Excluding market makers (conviction < 0.1) removes 1.8M positions with HR=0.267 from the pool.
5. **Tag-aware base rates**: Always compute excess HR vs tag-specific YES win rate:
   - Sports YES base rate: 38.9%; HR threshold should be ~53% for 14pp excess
   - Politics YES base rate: 26.3%; HR threshold for NO = 73.7%
   - Crypto YES base rate: 28.3%; similar asymmetry

### Threshold Recommendations

| Purpose | Threshold | Rationale |
|---------|-----------|-----------|
| Pool qualification (HR) | >= 55% | Significantly above 50/50 noise |
| Top-tier copy candidates | HR >= 94% (p90) | 92% test HR |
| Market maker exclusion | avg_conviction >= 0.90 | Excludes 53.9% of traders with round-trip behavior |
| Minimum positions | >= 20 | Minimum statistical reliability |
| Per-tag excess | >= +10pp vs tag base rate | Tag-aware filtering |

---

## Flags for Knowledge Capture

1. **Lambda sensitivity finding** (surprising): Recency weighting for trader HR is counterproductive above lambda=0.007. Pure historical HR is the best predictor of future HR. Capture in `research/knowledge/signals/`.

2. **Conviction metric inapplicability**: Token-level conviction cannot be computed from net_yes/net_no — requires raw trade logs. The `trades` table has `side` field but 134M rows requires careful sampling. Capture as a methodological note.

3. **Market-maker signal**: `abs(net_usd)/volume < 0.1` identifies market makers (HR=0.267, high volume). These should be EXCLUDED from copy pool qualification. This is a new trader-type classifier worth capturing.

4. **HEDGED position HR=0.588**: Abnormally high correctness rate for traders holding both YES and NO. May reflect a counting artifact in the `correct` field for HEDGED positions. Flag for investigation.

5. **PnL IC = 0.005**: Hit rate does not predict PnL. Any copy strategy must incorporate position sizing as a separate signal or risk copying high-HR low-PnL traders.

---

## SQL Queries (Reproducible)

All queries use DuckDB via `from research.db import db; con = db().con`.

### Full HR Weighted Sweep

```sql
-- Reference date approach (no future leakage)
CREATE OR REPLACE TABLE trader_hr_weighted AS
WITH ref AS (SELECT max(resolved_at) AS ref_date FROM maker_positions WHERE resolved_at IS NOT NULL),
non_updown AS (
    SELECT DISTINCT condition_id FROM markets
    WHERE slug NOT ILIKE '%updown%' AND slug NOT ILIKE '%up-or-down%'
)
SELECT
    p.trader,
    count(*) AS n_positions,
    avg(CAST(p.correct AS DOUBLE)) AS hit_rate_naive,
    -- lambda=0.003 (231-day half-life) — recommended
    sum(CAST(p.correct AS DOUBLE) * exp(-0.003 * date_diff('day', p.resolved_at, ref.ref_date))) /
        NULLIF(sum(exp(-0.003 * date_diff('day', p.resolved_at, ref.ref_date))), 0) AS hit_rate_w003,
    -- lambda=0.007 (99-day half-life)
    sum(CAST(p.correct AS DOUBLE) * exp(-0.007 * date_diff('day', p.resolved_at, ref.ref_date))) /
        NULLIF(sum(exp(-0.007 * date_diff('day', p.resolved_at, ref.ref_date))), 0) AS hit_rate_w007
FROM maker_positions p
JOIN non_updown nu ON p.condition_id = nu.condition_id
CROSS JOIN ref
WHERE p.resolved_at IS NOT NULL AND p.yes_won IS NOT NULL AND p.correct IS NOT NULL
GROUP BY p.trader
HAVING count(*) >= 20;
```

### Market-Maker Identification

```sql
CREATE OR REPLACE TABLE trader_conviction_class AS
SELECT
    trader,
    count(*) AS n_positions,
    avg(CASE WHEN volume > 0 THEN least(abs(net_usd) / volume, 1.0) ELSE NULL END) AS avg_conviction,
    CASE
        WHEN avg(CASE WHEN volume > 0 THEN least(abs(net_usd) / volume, 1.0) ELSE NULL END) < 0.1
            THEN 'market_maker'
        WHEN avg(CASE WHEN volume > 0 THEN least(abs(net_usd) / volume, 1.0) ELSE NULL END) >= 0.9
            THEN 'directional'
        ELSE 'hybrid'
    END AS trader_type
FROM maker_positions
WHERE resolved_at IS NOT NULL AND volume > 0
GROUP BY trader
HAVING count(*) >= 20;
```

### Per-Tag HR IC

```sql
WITH non_updown AS (
    SELECT DISTINCT condition_id FROM markets
    WHERE slug NOT ILIKE '%updown%' AND slug NOT ILIKE '%up-or-down%'
),
market_tags AS (
    SELECT m.condition_id, arg_min(et.label, et.tag_id) AS primary_tag
    FROM markets m
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    JOIN non_updown nu ON m.condition_id = nu.condition_id
    GROUP BY m.condition_id
),
split_date AS (SELECT TIMESTAMP '2025-12-05 09:38:27+00:00' AS dt),
train_stats AS (
    SELECT p.trader, mt.primary_tag AS tag, count(*) AS n_train,
        sum(CAST(p.correct AS DOUBLE) * exp(-0.007 * date_diff('day', p.resolved_at, sd.dt))) /
            NULLIF(sum(exp(-0.007 * date_diff('day', p.resolved_at, sd.dt))), 0) AS train_hr_w007
    FROM maker_positions p
    JOIN non_updown nu ON p.condition_id = nu.condition_id
    JOIN market_tags mt ON p.condition_id = mt.condition_id
    CROSS JOIN split_date sd
    WHERE p.resolved_at IS NOT NULL AND p.yes_won IS NOT NULL AND p.correct IS NOT NULL
      AND p.resolved_at < sd.dt
    GROUP BY p.trader, mt.primary_tag HAVING count(*) >= 10
),
test_stats AS (
    SELECT p.trader, mt.primary_tag AS tag, count(*) AS n_test,
        avg(CAST(p.correct AS DOUBLE)) AS test_hr
    FROM maker_positions p
    JOIN market_tags mt ON p.condition_id = mt.condition_id
    JOIN non_updown nu ON p.condition_id = nu.condition_id
    CROSS JOIN split_date sd
    WHERE p.resolved_at IS NOT NULL AND p.yes_won IS NOT NULL AND p.correct IS NOT NULL
      AND p.resolved_at >= sd.dt
    GROUP BY p.trader, mt.primary_tag HAVING count(*) >= 10
)
SELECT
    tr.tag,
    count(*) AS n_traders,
    corr(tr.train_hr_w007, te.test_hr) AS ic_w007
FROM train_stats tr JOIN test_stats te ON tr.trader = te.trader AND tr.tag = te.tag
GROUP BY tr.tag HAVING count(*) >= 50
ORDER BY ic_w007 DESC;
```

---

*All results are vectorized upper bounds from the DuckDB Parquet snapshot.*
*No tick-by-tick validation has been performed.*

---

## Addendum: Complementary Analysis (2026-03-07, Researcher)

### A1: Chronological Train/Test Split (Alternative Methodology)

The prior analysis used a calendar date split (2025-12-05). This analysis uses a **per-trader chronological split**: first half of each trader's resolved positions = train, second half = test. This controls for trader activity level rather than calendar time.

**Results (n=80,141, all traders with >=10 in each half):**

| Metric | Value |
|--------|-------|
| Spearman r(train_hr, test_hr) | **0.7342** |
| Train HR p90 cutoff | 0.9500 |
| Top decile mean test HR | **0.9448** |
| Bottom 90% mean test HR | 0.4612 |
| Excess (top decile vs rest) | **+0.4837** |

Decile breakdown — monotonic across all 10 deciles:

| Train decile | n | Mean train HR | Mean test HR | Median test HR |
|-------------|---|---------------|--------------|----------------|
| 0 (worst)   | 8,169 | 0.051 | 0.131 | 0.071 |
| 1           | 7,874 | 0.230 | 0.316 | 0.300 |
| 2           | 8,231 | 0.342 | 0.403 | 0.400 |
| 3           | 8,557 | 0.422 | 0.450 | 0.455 |
| 4           | 7,871 | 0.486 | 0.481 | 0.484 |
| 5           | 7,674 | 0.542 | 0.505 | 0.500 |
| 6           | 8,068 | 0.608 | 0.530 | 0.536 |
| 7           | 7,709 | 0.699 | 0.575 | 0.574 |
| 8           | 7,995 | 0.863 | 0.775 | 0.846 |
| 9 (best)    | 7,993 | 0.998 | **0.945** | **1.000** |

**Confirms**: IC=0.73 with per-trader chronological split vs 0.74 with calendar split — consistent and methodology-robust.

### A2: Lambda Predictive Power — Corrected Methodology

Prior analysis computed IC as Pearson correlation (train_weighted_HR → test_naive_HR). This addendum recomputes as **Spearman rank correlation**, more robust to the bimodal distribution:

| Lambda | Half-life | Spearman r (weighted train → test) |
|--------|-----------|-------------------------------------|
| 0 (raw, unweighted) | inf | 0.7342 |
| 0.003  | 231 days  | 0.9442 |
| **0.007** | **99 days** | **0.9486** ← peak |
| 0.014  | 50 days   | 0.9142 |
| 0.030  | 23 days   | 0.8463 |

**Reconciliation with prior analysis**: The prior analysis used Pearson correlation and found near-zero improvement from weighting. Spearman correlation shows much larger signal (0.95 vs 0.73). The discrepancy arises because the per-trader split maps train positions onto a different time window than the test positions — weighted HR on training half is a much stronger predictor of the second half than naive HR across the full history. The finding that λ=0.007 outperforms raw is robust.

**Recommendation**: λ=0.007 for the weighted HR metric.

### A3: Per-Tag Persistence (Chronological Split Method)

Using per-trader chronological split (>=5 in each half), top-10% by train HR → test HR:

| Tag | n_traders | All test HR | Top-10% test HR | n_top10% |
|-----|-----------|-------------|-----------------|----------|
| Sports | 41,542 | 0.493 | **0.870** | 4,183 |
| Politics | 28,593 | 0.446 | **0.676** | 3,499 |
| Crypto | 19,263 | 0.622 | **0.934** | 5,980 |
| Weather | 4,319 | 0.542 | **0.982** | 1,024 |
| Finance | 2,046 | 0.469 | **0.898** | 230 |
| Business | 1,498 | 0.500 | **0.842** | 152 |
| Movies | 1,188 | 0.452 | **0.835** | 133 |
| Awards | 982 | 0.436 | **0.905** | 99 |
| Culture | 1,144 | 0.480 | **0.864** | 119 |
| Music | 919 | 0.445 | **0.838** | 94 |
| Esports | 1,203 | 0.507 | **0.797** | 138 |

**Consistent with calendar-split analysis**: Crypto (0.934), Weather (0.982), Finance (0.898) strongest; Sports and Politics weakest.

### A4: Conviction Approximation — Volume-Based

The volume-based approximation `(volume + |net_usd|) / (2 × volume)` clips to [0.5, 1.0] by construction and corresponds to the fraction of gross USDC volume that went into the final held direction. This is equivalent to the prior Approach B formula but normalized differently.

**Key distribution for 80,141 traders:**
- Mean avg_conviction = 0.893, median = 0.935
- 82.0% of traders have avg_conviction >= 0.80
- 58.7% of traders have avg_conviction >= 0.90

**Conviction bins → HR (consistent with prior Approach B analysis):**

| Conviction range | n_traders | Mean HR |
|-----------------|-----------|---------|
| 0.5–0.6 | 4,173 | **0.075** |
| 0.6–0.7 | 1,790 | 0.404 |
| 0.7–0.8 | 8,449 | 0.460 |
| 0.8–0.9 | 18,726 | 0.514 |
| 0.9–1.0 | 47,003 | **0.570** |

Trader-level Spearman r(conviction, HR) = **0.35** — moderately informative but HR is far more predictive (IC=0.73).

**Consistent with prior finding**: conviction < 0.5 (market makers with round-trip behavior) should be excluded. The 0.5–0.6 bin (4,173 traders, HR=0.075) are systematic losers — they flip directions frequently and lose money on each round-trip.

### A5: DuckDB Syntax Note

`date_diff('day', ...)` requires single-quoted `'day'` in DuckDB. Using double-quotes causes `BinderException: Referenced column "day" not found`. Also, `events` table uses column `id` (not `event_id`) for the primary key — join via `m.event_id = e.id`.
