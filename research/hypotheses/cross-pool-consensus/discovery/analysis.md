# Cross-Pool Consensus — Discovery Analysis

> **ALL VALUES ARE UPPER BOUNDS** — vectorized backtests are 20-40pp optimistic vs tick.

- Train cutoff: 2025-07-01 (resolved before this date)
- Test window: 2025-07-01 to 2026-03-01 (8 months)
- Primary tag: Sports YES
- Secondary tag: Politics YES (max_price=0.80)

---

## Key Finding: SELL Handling Is The Signal

The most important result from this sweep is unexpected: **all strong cross-pool signal comes from the directional SELL mode, not BUY-only.**

| Mode | Sports signals | Sports HR | Excess HR |
|------|---------------|-----------|-----------|
| BUY-only (any variant) | 3–12 | 50–89% | +17–56pp |
| Directional (incl. SELL) | 13–377 | 68–88% | +35–55pp |

BUY-only cross-pool signals are too thin (3–12 over 8 months) to be deployable. The directional mode reveals what's happening: **the directional SELL data is capturing in-play sports markets** where traders enter near certainty (avg signal price 0.70–0.86). This is the same contamination pattern seen in earlier research.

---

## Pool Independence

All 4 variants achieved Jaccard = 0.000 (fully disjoint pools) because the recency/score-axis splits excluded already-selected traders. This is the ideal scenario for cross-pool testing.

| Variant | Pool A | Pool B | Jaccard | Description |
|---------|--------|--------|---------|-------------|
| style_split | 50 high-vol | 50 selective | 0.000 | n_markets ≥100 vs ≤30 |
| recency_split | 50 recent-3mo | 50 long-term | 0.000 | different time horizons |
| score_axis | 50 top excess_hr | 50 top consistency | 0.000 | different skill axes |
| random_split | 50 rank-even | 50 rank-odd | 0.000 | baseline null hypothesis |

---

## Single-Pool Baselines (K=100, reference)

| N | Mode | Signals | HR | Excess HR | Avg PnL/trade | Med hold |
|---|------|---------|-----|-----------|--------------|----------|
| 1 | BUY-only | 741 | 68.3% | +35.0pp | +$0.025 | 3.5h |
| 1 | Directional | 3,134 | 69.6% | +36.3pp | -$0.003 | 3.8h |
| 2 | BUY-only | 15 | 46.7% | +13.4pp | -$0.066 | 4.1h |
| 2 | Directional | 552 | 72.1% | +38.8pp | -$0.016 | 4.2h |
| 3 | BUY-only | 0 | — | — | — | — |
| 3 | Directional | 211 | 69.2% | +35.9pp | -$0.043 | 9.3h |

**Key observation**: BUY-only single-pool collapses at N=2 (only 15 signals, -9pp HR vs N=1). This is the same structural fragility that drove the hypothesis — single-pool N=2 BUY-only is nearly empty.

---

## Cross-Pool Results — BUY-Only Mode

### Summary Table (BUY-only, N_a=1 N_b=1)

| Variant | Signals | HR | Excess HR | vs Baseline N=2 | Avg Gap h |
|---------|---------|-----|-----------|----------------|-----------|
| style_split | 9 | 88.9% | +55.6pp | +42pp improvement | -0.4h |
| recency_split | 5 | 60.0% | +26.7pp | +13pp improvement | -0.0h |
| score_axis | 3 | 66.7% | +33.4pp | +20pp improvement | +2.8h |
| random_split | 12 | 50.0% | +16.7pp | +3pp improvement | -0.2h |

**Style split BUY-only is the top performer (+55.6pp excess)** — but has only 9 signals in 8 months (~1/month). Too thin for deployment.

Compared to the single-pool N=2 BUY-only baseline (15 signals, +13.4pp excess), style split delivers higher excess HR but slightly fewer signals. The independence structure helps HR but costs volume.

### N_a=1 N_b=2 BUY-only

All variants return ≤1 signal or zero. Too thin — cross-pool confirmation at N_b=2 effectively kills BUY-only throughput entirely.

---

## Cross-Pool Results — Directional Mode

### Summary Table (Directional, N_a=1 N_b=1)

| Variant | Signals | HR | Excess HR | vs Baseline N=2 dir | Med hold |
|---------|---------|-----|-----------|---------------------|----------|
| style_split | 233 | 67.8% | +34.5pp | -4pp vs baseline | 3.5h |
| recency_split | 213 | 86.9% | +53.6pp | +15pp vs baseline | 2.8h |
| score_axis | 239 | 87.9% | +54.6pp | +16pp vs baseline | 3.3h |
| random_split | 377 | 71.4% | +38.1pp | -1pp vs baseline | 4.8h |

**Score-axis and recency splits dramatically outperform random split in directional mode.**
- score_axis: 87.9% HR vs 71.4% random (16pp better, same volume scale)
- recency_split: 86.9% HR vs 71.4% random (15pp better)

This suggests the independent scoring axes (excess_hr vs consistency) and timing (recent vs long-term) capture genuinely different information that adds signal when both pools agree.

### N_a=1 N_b=2 Directional

| Variant | Signals | HR | Excess HR | Med hold |
|---------|---------|-----|-----------|----------|
| score_axis | 69 | 89.9% | +56.6pp | 3.2h |
| recency_split | 14 | 92.9% | +59.6pp | 2.7h |
| random_split | 149 | 71.1% | +37.9pp | 11.6h |

Score-axis N_a=1 N_b=2 directional is the top performer by compounding score potential, with 69 signals at 89.9% HR.

---

## SELL Variant Comparison

| Metric | BUY-only | Directional | Delta |
|--------|---------|-------------|-------|
| Max HR (best variant) | 88.9% | 92.9% | +4pp directional |
| Max signals (best variant) | 12 | 377 | 30x more |
| Avg signal price (best dir) | ~0.67 | 0.85–0.90 | Directional fires near-certainty |
| Avg PnL (best dir) | +0.21 | +0.018 | BUY-only better per-trade |

**Interpretation**: Directional mode's higher HR comes from including near-certainty markets (avg price 0.85+). Sports directional signals at 0.86 average price need HR > 86% just to break even. The vectorized PnL is close to zero even at 87.9% HR because the edge is consumed by fill price.

**SELL handling is highly sensitive**: >30x signal volume difference means this is NOT a minor sensitivity. The appropriate label is "SELL mode fundamentally changes what markets the signal fires on."

> [!WARNING] FRAGILE: Directional mode fires predominantly on near-certainty in-play markets (avg price 0.86). At these prices, break-even HR is 86%. Actual tick HR for in-play Sports is known to be 70-80% (InPlay Track A memory). Directional Sports YES cross-pool directional is likely below break-even in tick validation.

---

## Timing Gap Analysis

The med_gap_hours between Pool A and Pool B is close to zero or negative across all BUY-only variants:

| Variant | Med gap (BUY-only) | Pool A first % |
|---------|--------------------|--------------------|
| style_split | -0.4h | 44% |
| recency_split | -0.0h | 40% |
| score_axis | +2.8h | 100% |
| random_split | -0.2h | 42% |

**Pools fire nearly simultaneously** in BUY-only mode. This makes cross-pool confirmation less of a "sequential confirmation" and more of a "both pools happened to be in the same rare market." There is no timing structure where Pool A fires predictably before Pool B.

For score_axis BUY-only, Pool A (excess_hr) fires first 100% of the time with +2.8h gap. This is the one case with temporal structure — the excess_hr traders (hot hand) entering before consistency traders is interpretable.

---

## Politics YES Results (Secondary Tag)

### Directional mode N_a=1 N_b=1 (max_price=0.80)

| Variant | Signals | HR | Excess HR | Baseline v3 ref |
|---------|---------|-----|-----------|----------------|
| random_split | 183 | 63.9% | +45.1pp | v3 N=3: +43.5pp |
| score_axis | 134 | 61.9% | +43.1pp | v3 N=3: +43.5pp |

Cross-pool Politics YES directional achieves ~same as the existing v3 Politics YES strategy (N=3, +43.5pp). No improvement over the existing deployed approach.

### BUY-only N_a=1 N_b=1

| Variant | Signals | HR | Excess HR |
|---------|---------|-----|-----------|
| random_split | 15 | 33.3% | +14.5pp |
| score_axis | 11 | 36.4% | +17.5pp |

Below the existing v3 baseline. Cross-pool BUY-only Politics is not competitive.

---

## Top 5 Combos Summary (BUY-only, upper bounds)

| Rank | Variant | N_a | N_b | Signals | HR | Excess HR | Avg PnL | Med hold h | CS approx |
|------|---------|-----|-----|---------|-----|-----------|---------|-----------|-----------|
| 1 | style_split | 1 | 1 | 9 | 88.9% | +55.6pp | +$0.215 | 2.5h | 0.53 |
| 2 | score_axis | 1 | 1 | 3 | 66.7% | +33.4pp | +$0.003 | 4.3h | 0.02 |
| 3 | recency_split | 1 | 1 | 5 | 60.0% | +26.7pp | +$0.174 | 3.0h | 0.15 |
| 4 | random_split | 1 | 1 | 12 | 50.0% | +16.7pp | -$0.055 | 3.2h | neg |
| 5 | style_split | 1 | 2 | 1 | 100.0% | +66.7pp | +$0.169 | 3.0h | n/a (N=1) |

Compounding score = excess_hr * avg_pnl / (med_hold / 24). Style_split rank 1 has highest but only 9 signals over 8 months — very thin.

## Top 5 Combos Summary (Directional, upper bounds)

| Rank | Variant | N_a | N_b | Signals | HR | Excess HR | Avg PnL | Med hold h | CS approx |
|------|---------|-----|-----|---------|-----|-----------|---------|-----------|-----------|
| 1 | score_axis | 1 | 2 | 69 | 89.9% | +56.6pp | +$0.018 | 3.2h | 0.10 |
| 2 | recency_split | 1 | 2 | 14 | 92.9% | +59.6pp | +$0.026 | 2.7h | 0.14 |
| 3 | score_axis | 1 | 1 | 239 | 87.9% | +54.6pp | +$0.018 | 3.3h | 0.09 |
| 4 | recency_split | 1 | 1 | 213 | 86.9% | +53.6pp | +$0.015 | 2.8h | 0.09 |
| 5 | random_split | 1 | 1 | 377 | 71.4% | +38.1pp | -$0.026 | 4.8h | neg |

---

## Sensitivity Analysis

### Parameter Sensitivity (BUY-only, style_split N_a=1 N_b=1)

The 9-signal result is too thin to meaningfully test sensitivity. A single market shift of ±1 signal changes HR by 11pp.

**FRAGILE**: All BUY-only cross-pool results with < 15 signals should be treated as noise-dominated. Sampling variance alone accounts for 20-30pp HR swings at N=9.

### Parameter Sensitivity (Directional, score_axis)

Score-axis is consistent across 8/8 months (lowest month: 59.4% HR in Aug 2025, highest: 96.9% in Jan 2026). This monthly variation is reasonable for directional Sports markets.

Perturbing pool split (K=40 vs K=60 each): not tested in this sweep, would need a re-run. Hypothesis: HR will be insensitive to K because the signal comes from WHICH AXIS the pools select on, not the exact K.

---

## Root Cause Diagnosis

### Why BUY-only is Thin

Sports YES BUY-only at market level: only ~741 signals across all of K=100 in 8 months (93/month). Splitting K=100 into two K=50 pools halves the per-pool signal rate. Cross-pool requires BOTH pools to see the same market. With 50% lower signal probability per pool, cross-pool overlap is approximately 50%^2 = 25% of K=100 single-pool volume, which matches the observed ~12 signals vs 741 baseline.

### Why Directional Mode Has More Signal

Including SELL trades (SELL NO = bullish signal) approximately 4x's the raw event count. These are predominantly in-play sports markets where the outcome is near-certain, hence the high HR and high avg price.

### Why Score-Axis Outperforms Random Split in Directional Mode

Score-axis puts excess_hr traders (sharp, directional) in Pool A and consistency traders (steady, reliable) in Pool B. When both agree on a market, you have convergence across two independent dimensions of skill. The 16pp HR improvement vs random (87.9% vs 71.4%) suggests this is a real structural effect, not noise.

### Why Timing Gap is Near Zero

Sports markets are fast (hold ~3h). If both pools are active in a market, they both enter within minutes of each other. There is no temporal "Pool A fires, then we wait for Pool B confirmation" — it's simultaneous agreement, not sequential.

---

## Conclusion and Verdict

**Cross-pool consensus vs single-pool consensus shows modest improvement in HR but critical throughput problem in BUY-only mode.**

1. **BUY-only mode**: too thin (3-12 signals/8 months) to deploy. Cross-pool filtering is too aggressive.
2. **Directional mode**: score_axis and recency_split variants show +15-16pp HR improvement over random split, which is real signal from the pool construction. However, these fire on near-certainty in-play markets (avg price 0.86) where break-even HR is 86% — right at the edge.
3. **vs Single-pool K=100 N=2 BUY-only baseline**: Cross-pool style_split shows +42pp excess vs baseline's +13pp, but at 9 signals vs 15. Both are too thin. The hypothesis was trying to solve single-pool BUY-only thinness — cross-pool doesn't solve it, it makes it worse.
4. **vs Single-pool directional**: Cross-pool score_axis directional (+54.6pp excess) significantly outperforms single-pool K=100 N=2 directional (+38.8pp). But this is at avg price 0.86 — may be negative EV after tick degradation.
5. **Politics YES**: No improvement over existing v3 strategy.

**VERDICT: MARGINAL** — The cross-pool construction solves the structural independence problem (Jaccard=0 achieved cleanly) and score_axis shows a real +16pp HR improvement signal in directional mode. However:
- BUY-only is non-deployable (too thin)
- Directional fires at near-certainty prices likely below break-even in tick
- The "cross-pool confirmation" doesn't provide sequential timing benefit (pools fire simultaneously)

**GO/NO-GO decision**: This is a **NO-GO for the stated hypothesis** but spawns two interesting sub-ideas that may be worth separate investigation.

---

## Spawned Ideas

1. **Score-axis pool disagreement signal**: If Pool A (excess_hr) fires but Pool B (consistency) does NOT, this could be a "hot hand" signal. The inverse cross-pool (only one pool fires) may carry different information. Test: condition_ids where Pool A enters but Pool B doesn't in 24h.

2. **BUY-only volume filter**: The 9-signal BUY-only style_split result (88.9% HR) suggests high-vol traders and selective traders rarely agree — but when they do, it's predictive. Instead of cross-pool consensus, try: single-pool but require BOTH a high-volume trader AND a selective trader to agree in the same market.

3. **Sequential cross-pool**: Enforce temporal ordering: Pool A must fire BEFORE Pool B (Pool A_entry < Pool B_entry by ≥ 2h). Test if the subset of cross-pool signals where Pool A truly "predicts" Pool B are higher quality.

---

## Knowledge Captures

- **Cross-pool BUY-only throughput collapse**: Splitting K=100 into two K=50 pools reduces cross-pool BUY-only signals to ~1.6% of original (12/741). This is geometric, not arithmetic, because cross-pool requires simultaneous overlap.
- **Score-axis pool construction**: Top-K by excess_hr vs top-K by consistency_sharpe gives fully disjoint pools and shows real +15-16pp HR improvement in directional mode. This construction method is worth preserving.
- **In-play contamination in directional mode**: Sports YES directional includes near-certainty in-play markets. Avg price 0.86+ means break-even HR is 86%, right at the vectorized result. Always add hold>=4h filter for Sports YES to remove in-play markets.
