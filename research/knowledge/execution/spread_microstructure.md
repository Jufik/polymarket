# Polymarket Spread Microstructure

> **TL;DR**: Median half-spread is 0.01 (1 cent) globally, 0.00 for Sports. MAC estimator is correct for slippage; Roll overestimates 17x. Fill model contributes <1pp of the vectorized-to-tick HR gap.

> [!WARNING]
> Do NOT use the Roll estimator for slippage calibration. It captures fundamental price moves (17x MAC), not just bid-ask bounce. Use `calibrate_spreads(method="median_abs_change")`.

> [!TIP]
> For $10 trades in Sports markets, slippage is effectively zero ($0.00-$0.10). The fill model is not the bottleneck — consensus dedup and SELL filtering dominate the simulation gap.

## Finding

Comprehensive trade tape analysis (Nov 2025, 27M trade pairs) reveals:

**Spread estimates** (101K markets, Oct-Dec 2025):
- MAC median: 0.01 (1 cent), Roll median: 0.19 (19 cents) — 17x ratio
- 44% of consecutive trades have exactly zero price change
- Minimum tick size is 0.01 (1 cent), accounting for 45% of all non-zero changes

**By tag** (MAC / Roll):
- Sports: 0.000 / 0.148 (tightest)
- Crypto: 0.010 / 0.201
- Politics: 0.001 / 0.219
- Weather: 0.002 / 0.252 (widest)

**By volume tier**: Monotonically decreasing. >$100K markets have half the spread of <$10K markets.

**Lifecycle dynamics**: Last 10% of market life has 53% wider average spread (sports: 0.130 vs 0.085 mid-life). 54% of sports trades occur in this final phase.

**Market impact**: Non-monotonic with trade size. Large trades ($1K+) show LOWER median impact than mid-sized ($50-100) because they cluster in liquid markets. Directional: only 14% of $1K+ BUY trades see continued upward movement (mean reversion).

**PnL impact**: For $10 trades at 0.35 entry price, slippage ranges from $0.00 (SimulatedExecutor) to $0.10 (MAC sports) to $1.50 (Roll sports). Monthly PnL degradation: 1% (MAC) to 13% (Roll).

## Evidence

```sql
-- MAC and Roll estimator comparison (DuckDB over Parquet snapshot)
WITH step1 AS (
    SELECT condition_id, timestamp,
        price - lag(price) OVER (PARTITION BY condition_id ORDER BY timestamp) as dp
    FROM trades WHERE timestamp >= '2025-11-01' AND timestamp < '2025-12-01'
),
step2 AS (
    SELECT condition_id, dp,
        lag(dp) OVER (PARTITION BY condition_id ORDER BY timestamp) as dp_lag
    FROM step1 WHERE dp IS NOT NULL
),
per_market AS (
    SELECT condition_id, count(*) as n,
        median(abs(dp)) as mac_spread,
        avg(dp * dp_lag) - avg(dp) * avg(dp_lag) as serial_cov
    FROM step2 WHERE dp_lag IS NOT NULL
    GROUP BY condition_id HAVING n >= 30
)
SELECT avg(mac_spread), median(CASE WHEN serial_cov < 0 THEN sqrt(-serial_cov) END)
FROM per_market
-- Result: MAC avg=0.011, Roll median=0.191
```

## Impact

- **Fill model is NOT the bottleneck**: <1pp HR impact, 1-5% PnL impact with MAC calibration
- **SimulatedExecutor is fine** for tick-by-tick validation of small-trade strategies ($10)
- **If PnL precision needed**: use `RealisticFillSimulator` with MAC calibration (2 lines)
- **Time-varying spreads**: only worthwhile when running multi-tag strategies simultaneously
- **Roll estimator**: useful for research (upper bound on friction) but NOT for simulation

## Related

- `pitfalls/simulation_fidelity.md` — simulation engine gap inventory
- `execution/hold_time_capital.md` — capital efficiency (the real bottleneck)
- `pitfalls/vectorized_vs_tick.md` — the 9 gaps (fill model is gap #10, smallest)

## Tags

`microstructure`, `spread`, `slippage`, `fill-model`, `calibration`, `Roll-estimator`, `MAC`
