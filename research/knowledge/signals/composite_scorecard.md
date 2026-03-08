# Composite Scorecard: 4-Signal Trader Grading System

> **TL;DR**: The composite scorecard (excess_hr × 0.45 + consistency_sharpe × 0.25 + avg_edge_usd × 0.15 + bucket_excess_hr × 0.15) provides dramatically better walk-forward stability than HR-only ranking, at a modest cost in peak excess HR. HR-only pools collapse in walk-forward (1 signal in fold 3); composite pools stay stable (117-148 signals).

> [!CRITICAL]
> Always use composite ranking for production pools — HR-only ranking leads to walk-forward collapse. The composite pool selects fundamentally different traders (Jaccard overlap 0.10-0.21 with HR-only). Gained traders have 2-5x higher consistency_sharpe and 5-12x higher avg_edge_usd.

> [!CRITICAL]
> Direction decomposition is the single most impactful filter. Sports and Crypto are YES-only (NO signals have -3.7pp and +0.4pp excess respectively — structural bias). Politics works in both directions but YES is stronger (+67.6pp vs +14.5pp vectorized). Always filter YES-only for Sports and Crypto.

> [!WARNING]
> Do NOT use calibration_gap as an exclusion gate. It actively hurts performance across all tags. Crypto: 96% of elite pool has negative cal_gap — filtering destroys the pool entirely. Politics: strict gate drops HR from 91.1% to 86.8% and loses 23% of signals. Use tighter K (20-30) instead of calibration gates.

## Scorecard Components

| Component | Weight | Role | IC / Evidence |
|-----------|--------|------|---------------|
| **excess_hr** | 0.45 | Primary quality signal | IC=0.744 (naive HR, persistent) |
| **consistency_sharpe** | 0.25 | Walk-forward stability / anti-luck | r=+0.498 with HR, prevents pool collapse |
| **avg_edge_usd** | 0.15 | Profitability signal (median PnL) | Composite traders 5-12x higher than HR-only |
| **bucket_excess_hr** | 0.15 | Entry quality control (price-adjusted skill) | IC=+0.918 within price buckets |

```
composite = 0.45 * percentile(excess_hr)
           + 0.25 * percentile(consistency_sharpe)
           + 0.15 * percentile(avg_edge_usd)
           + 0.15 * percentile(bucket_excess_hr)
```

All components are percentile-rank normalized to [0,1] within each tag before weighting.

## Qualification Filters (Pre-Scoring)

Before computing scores, traders must pass:
- `n_markets >= 20` (sufficient sample)
- `avg_conviction >= 0.90` (real positions, not dust)
- `n_trades < 10000` (exclude bots)
- `excess_hr > 0` (positive edge)
- For consistency_sharpe: `>= 6 months` with `>= 5 positions/month`
- For bucket_excess_hr: `>= 2 price buckets` with positions

## Rejected Components

| Signal | Why Rejected |
|--------|-------------|
| **calibration_gap** (exclusion gate) | Hurts across all tags — removes high-excess_hr traders |
| **sure_thing_penalty** | Inverted — high sure_thing_ratio = +13.6pp excess HR |
| **value_hunter copy** | Genuine alpha but $0.50-$5.00 positions = not commercially copyable |
| **recency weighting** | λ>0.003 degrades IC; naive HR is best (skill is persistent) |

## Tick-Validated Strategy Results (2026-03-07)

| Strategy | Pool Config | Tick Excess HR | Fills | Sharpe | Verdict |
|----------|-------------|---------------|-------|--------|---------|
| Sports YES Composite | K=25, N=3 | **+39.8pp** | 612 | 11.94 | PROMOTE |
| Politics YES Composite | K=100, N=5 | **+41pp** (price≤0.80) | 125 | - | PROMOTE |
| Crypto YES HR-only | K=50, N=2 | **+30.9pp** (price<0.70) | 98 | - | RERUN max_price=0.65 |

Vectorized-to-tick degradation: Sports -7pp, Politics -11pp (below expected 20-40pp — consensus gate is natural filter).

## Pool Building Reference

Training cutoff: `2025-07-01`. Test period: `>= 2025-07-01`.
Implementation: `research/hypotheses/scorecard-v2-strategies/scripts/build_pools.py`

Production provider must replicate this scoring in ClickHouse SQL with rolling training window.

## Related

- `signals/hr_persistence.md` — HR is primary signal (IC=0.744, persistent trait)
- `signals/stability_bonus.md` — consistency_sharpe as anti-luck filter
- `signals/entry_price_quality.md` — calibration_gap finding, bucket_excess_hr
- `pitfalls/direction_decomposition.md` — YES-only for Sports/Crypto
- `pitfalls/in_play_contamination.md` — max_price filter for Crypto/Politics

## Tags

`scorecard`, `composite`, `trader-grading`, `pool-building`, `walk-forward`, `direction-filter`, `production`, `critical`
