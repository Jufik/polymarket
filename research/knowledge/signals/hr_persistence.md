# Trader Hit Rate is Persistent, Not Streaky

> **TL;DR**: Naive (unweighted) hit rate is the best predictor of future HR (IC=0.744). Exponential recency weighting does not improve prediction and degrades above λ=0.007.

> [!CRITICAL]
> Do NOT use aggressive recency weighting (λ > 0.007) for trader HR. Peak IC is at λ=0.003 (+0.0015 above naive) — indistinguishable. Trader skill is a persistent personality trait over 3+ years, not a hot-streak phenomenon. Use naive HR or λ≤0.003 maximum.

## Finding

Across 80,141 traders with ≥20 resolved non-gambling positions (2023-02 to 2026-03), train/test split at median resolved_at:

| Lambda | Half-Life | IC (train → test HR) |
|--------|-----------|---------------------|
| Naive  | ∞         | **0.7424**          |
| 0.003  | 231d      | **0.7439** (peak)   |
| 0.007  | 99d       | 0.7392              |
| 0.014  | 50d       | 0.7216              |
| 0.030  | 23d       | 0.6810              |

Top-decile (by train HR) achieves 91.9% test HR vs 24.6% for bottom decile.

## Evidence

DuckDB sweep over maker_positions (gambling-excluded). Full SQL in `research/hypotheses/trader-scorecard/discovery/hr_conviction_analysis.md`.

## Impact

- Scorecard should use naive HR as primary metric (simplest, best)
- Recency weighting adds complexity for no gain
- Per-tag IC varies: Crypto/Elections (0.87) > Weather (0.83) > Sports (0.67)

## Related

- `data/tag_base_rates.md` — must compute excess HR per tag
- `pitfalls/vectorized_vs_tick.md` — all numbers are upper bounds

## Tags

`hit-rate`, `signal-quality`, `persistence`, `scorecard`, `lambda`
