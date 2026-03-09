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

## Tick-Validated Strategy Results (2026-03-09, v3 with BEH gate)

> [!CRITICAL]
> v3 adds a BEH (bucket_excess_hr) qualification gate at 0.02 minimum. This removes near-certainty traders who achieve high raw HR simply by betting into already-resolved markets. N=2 vs N=3 triples fills at the cost of ~9.8pp excess HR.

| Strategy | Pool Config | Tick HR | Base Rate | Tick Excess HR | Fills | Sharpe | Vectorized UB | Verdict |
|----------|-------------|---------|-----------|---------------|-------|--------|--------------|---------|
| Sports YES v3 | K=25, N=2 | 63.3% | 33.3% | **+30.0pp** | 2023 | 5.23 | +53.4pp | PROMOTE |
| Politics NO v3 | K=100, N=2 | 83.0% | 73.6% | **+9.3pp** | 347 | 0.55 | +8.8pp | MARGINAL |
| Sports InPlay v3 | K=25, N=1 | 60.2% | 33.3% | **+26.9pp** | 5936 | 0.27 | +34.2pp | VIABLE (risk limits) |

Test period: 2025-07-01 — 2026-03-01 (8 months).
Script: `research/hypotheses/scorecard-v3-strategies/scripts/run_tick_v3.py`

### v2 vs v3 Comparison (Sports YES)

- v2 (K=25, N=3): +39.8pp excess, 612 fills, Sharpe=11.94
- v3 (K=25, N=2): +30.0pp excess, 2023 fills, Sharpe=5.23
- Trade-off: N=2 delivers 3.3x fills at -9.8pp excess HR cost and -6.7 Sharpe
- BEH gate changes pool composition (removes ~5% of v2 traders who were near-certainty bettors)
- For capital deployment, v3 is superior — more fills = faster capital compounding

### N=2 vs N=3 Trade-off (general principle)

Lowering consensus threshold from N=3 to N=2:
- Fill count: 3x increase (more markets reach threshold)
- Excess HR: -9.8pp reduction (signal diluted by lower-conviction markets)
- Sharpe: decreases significantly (more variance from higher fill volume)
- Capital recycling: faster (more concurrent positions)

Rule of thumb: prefer N=2 for capital deployment (volume), N=3 for signal purity.

### Politics NO — First Validated NO-Direction Strategy

- 83.0% tick HR vs 73.6% NO base rate = +9.3pp excess
- Tick BEATS vectorized by +0.6pp (consensus fires before population base rate shifts)
- 54-day avg hold — extremely slow capital recycler (Sharpe=0.55)
- YES/NO pool overlap: Jaccard=0.031 (NO specialists are distinct traders)
- Verdict: MARGINAL — thin edge, slow recycling. Monitor for deterioration.

### Sports InPlay N=1

- 60.2% HR, +26.9pp excess, 5936 fills over 8 months (742 fills/month)
- 37% of fills complete within 4h (in-play dominates)
- Requires position limits to control concurrent exposure (max_open_positions=20)
- Production WS latency (~50ms) << elite trader lead time (58 min median) — no latency penalty

## BEH Gate: bucket_excess_hr >= 0.02

The BEH (Bucket Excess Hit Rate) gate is applied BEFORE composite scoring as a qualification filter, not a ranking signal.

**What it filters**: Traders whose high raw HR is explained by price-level effects. A trader who only bets at 0.90+ YES wins 90% of the time by luck — not skill. BEH subtracts the population base rate within each 0.10-wide price bucket.

**Effect on Crypto pool**: BEH gate removes 26% of traders from the Crypto pool (those betting into near-certainties). This is the right behavior — it prevents the pool from filling with traders who bet "BTC is above $40k next month" when it's trading at $60k.

**BEH as filter vs ranker**: At the top of the distribution (composite top-100), BEH and excess_hr converge (Jaccard=1.0). BEH's power is in the gate (removing bottom 20-30% of qualifiers), not in re-ranking the top.

## Pool Building Reference

Training cutoff: `2025-07-01`. Test period: `>= 2025-07-01`.
- v2 implementation: `research/hypotheses/scorecard-v2-strategies/scripts/build_pools.py`
- v3 implementation (with BEH gate + NO pools): `research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py`

Production provider must replicate this scoring in ClickHouse SQL with rolling training window.

## Related

- `signals/hr_persistence.md` — HR is primary signal (IC=0.744, persistent trait)
- `signals/stability_bonus.md` — consistency_sharpe as anti-luck filter
- `signals/entry_price_quality.md` — calibration_gap finding, bucket_excess_hr
- `pitfalls/direction_decomposition.md` — YES-only for Sports/Crypto
- `pitfalls/in_play_contamination.md` — max_price filter for Crypto/Politics

## Tags

`scorecard`, `composite`, `trader-grading`, `pool-building`, `walk-forward`, `direction-filter`, `production`, `critical`
