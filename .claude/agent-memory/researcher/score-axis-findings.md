# Score-Axis Pool Construction Findings (2026-03-11)

## Key Verdict: MARGINAL — prior art was spurious

The cross-pool-consensus +33.4pp "+16pp over random" prior finding was based on N=3 signals,
no hold>=4h filter, and tag base rate (not price-level base rate).
After corrections: K=50 N=1x1 BUY-only → N=2 signals, excess_price = -5.96pp.

## Axis Correlation in Sports: 0.46 (NOT 0.95)
- excess_hr vs consistency_sharpe Spearman = 0.46 for Sports (genuinely orthogonal)
- Pool B (consistency) traders: avg HR ~60-62%, enter at near-certainty prices (0.67+)
- Orthogonality alone is insufficient if Pool B traders are "consistently mediocre"

## Price-Level Base Rate: Critical Correction
- Sports YES signals at avg price 0.63-0.69 → population HR at that price band = 57-67%
- True alpha = excess over price-level base (not tag base 33.3%)
- Best combo (K=50 N=1x1 dir): +8.2pp price-adjusted excess (UPPER BOUND)
- After 20-40pp tick degradation: expected tick excess = **-12pp to -32pp** (negative)

## Signal Collapse Pattern
- K=25: 0 signals in ALL combos after hold>=4h
- K=50 BUY-only: 2 signals/8mo (useless)
- K=50 directional: 65 signals/8mo (borderline but fragile)
- K=100 directional: 498 signals/8mo (adequate N, but near-zero PnL)

## FRAGILE: All top combos
- K-25 from any working K → 0 signals
- K+25 → HR drops 7-10pp (threshold 5pp)

## Viable Range for Signals: K = 80-120 directional only

## Spawned: sports-yes-single-pool-price-gated [HIGH]
Use single Pool A (top-100 excess_hr) with max_price=0.55 instead of dual-pool.
Forces signals into genuine uncertainty zone (40-55% price-level base rate).
