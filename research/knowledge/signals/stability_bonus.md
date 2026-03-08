# Stability Bonus: Monthly HR Consistency as Skill Filter

> **TL;DR**: Monthly HR consistency (Sharpe-like ratio) monotonically predicts trader quality: D1=26% HR → D10=75% HR. Stability is the strongest anti-luck filter and is uncorrelated with striking score (r=-0.006).

> [!WARNING]
> Only 5,471 traders qualify for stability scoring (≥6 months with ≥5 positions/month). This is a restrictive but meaningful filter — it selects committed, long-horizon participants.

## Finding

For traders with ≥6 months of ≥5 resolved non-gambling positions each:

| Decile | Stability | Median HR | Monthly Std | Avg Monthly PnL |
|--------|-----------|-----------|-------------|-----------------|
| D1 (lowest) | 1.39 | 26.3% | 18.3% | -$99 |
| D5 | 3.05 | 48.4% | 14.9% | -$32 |
| D10 (highest) | 8.89 | 75.0% | 6.5% | +$3,956 |

Correlation with HR: r=+0.498. Correlation with striking: r=-0.006 (independent, non-redundant).

## Quadrant Analysis

| Quadrant | HR | Avg PnL |
|----------|-----|---------|
| High stability only | **68.2%** | +$23,377 |
| High both (stability + striking) | 55.7% | +$26,056 |
| High striking only | 42.3% | -$5,123 |

High-stability-only traders are the real gems.

## Evidence

DuckDB analysis in `research/hypotheses/trader-scorecard/discovery/striking_stability_analysis.md`.

## Impact

- Use stability as PRIMARY gate (≥3.0) after HR qualification
- Hard disqualifier: stability < 2.0 (HR < 40%, money-losing)
- XP variant (× log(1+months)) adds negligible signal (r=0.986 with plain)
- Best stable tags: CS2, Bundesliga, Ligue 1 — weekly-cadence sports

## Related

- `signals/hr_persistence.md` — HR is the dominant predictor; stability is anti-luck filter
- `data/tag_base_rates.md` — use per-window excess HR for base-rate-corrected stability

## Tags

`stability`, `consistency`, `scorecard`, `anti-luck`, `signal-quality`
