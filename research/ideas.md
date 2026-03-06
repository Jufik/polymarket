# Strategy Research Idea Backlog

## Queued

### Microstructure Calibration Upgrade
**Type**: Infra improvement (execution layer)
**Impact**: Close 10-20pp of vectorized-to-tick degradation gap
**Summary**: Replace static per-market spread/impact with time-varying, order-size-dependent estimates derived from the trade tape:
- `spread_curve(t)` — spreads widen pre-resolution
- `depth_estimate(t)` — from trade inter-arrival times + size distributions
- `fill_probability(size)` — P(fill) as f(order_size, market_liquidity)
- `impact_function(size)` — slippage = f(order_size, depth)
**Where**: Extend `strategies/execution/calibrate.py`, produce `MarketMicrostructure` per condition_id
**Priority**: After taxonomy layer is proven useful

## In Progress

(none)

## Tested

### tag-hr-copy
**Status**: rejected (individual signal — consensus gap)
**Tags tested**: Esports, 1H, Tennis (BUY-only, BUY+SELL directional)
**Vectorized (R3 UB)**: Esports HR=67.2%/CS=34.87, 1H HR=78.0%/CS=19.71, Tennis HR=72.4%/CS=9.67
**Tick-by-tick**: Esports HR=45.8%, 1H HR=49.8% (≈base), Tennis HR=40.6% — all negative PnL
**Root cause**: Vectorized measured N-trader consensus; tick strategy fired on individual trades
**Lesson**: See `pitfalls/individual_vs_consensus_signal.md`

## Queued — Spawned from tag-hr-copy

### tag-hr-consensus [HIGH]
**Spawned from**: tag-hr-copy
**Summary**: Same qualified pools (Esports/Tennis), but fire entry intent only when N distinct
qualified traders have entered the same market within a time window (e.g. n=3 within 4h).
This replicates what the vectorized sweep measured. Expected HR: 60-70%, expected signals: 300-800/year.
**Priority**: HIGH — direct fix of tag-hr-copy's structural failure

### esports-price-regime [MEDIUM]
**Spawned from**: tag-hr-copy price analysis
**Summary**: Target 0.60-0.75 fill price bucket in Esports specifically — observed 64% HR (458 fills).
Apply price floor AND ceiling, skip consensus requirement. Simpler signal, fewer parameters.
**Note**: See `signals/price_regime_hr_correlation.md`

### esports-sub-tag [MEDIUM]
**Spawned from**: tag-hr-copy Esports analysis
**Summary**: Decompose Esports by game (CS2, Dota2, LoL, Valorant). Different games may have
different base rates and trader pool characteristics. Per-game pools may improve signal quality.

### tennis-directional [MEDIUM]
**Spawned from**: tag-hr-copy R3 Tennis DIR sweep
**Summary**: Tennis DIR (BUY YES + SELL NO) showed HR=72.4%, excess=+30.5pp, CS=6.84 after R3 fix.
Up from R2's HR=52.4%. Gap between BUY and DIR is smallest in Tennis (1.4x vs 78x for 1H).
Worth validating independently with consensus filter.

## Parked

(none)
