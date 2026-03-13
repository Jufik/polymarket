# Hypothesis: Crypto GBM Strategy Improvements

**Status**: `discovery`
**Created**: 2026-03-10
**Category**: crypto

## Statement

The crypto_gbm scalp strategy has proven edge (+$2.10/trade median) but can be improved
across several axes: dynamic position sizing based on signal strength, re-entry after
exit within the same window, fee-aware entry thresholds, late-entry size reduction,
and smarter entry pricing using orderbook depth.

## Improvement Axes

1. **Dynamic sizing** — scale bet size with lag magnitude (larger divergence → larger bet)
2. **Re-entry logic** — allow re-entering same window after exit if new GBM signal develops
3. **Fee-aware threshold** — PM fee is 0.25*(p*(1-p))^2, ~0 at extremes, 1.56% at 0.50.
   Threshold should be higher near p=0.50 and lower at extremes.
4. **Late-entry size reduction** — halve size for entries in final 3 min of 5-min window
5. **Smarter entry pricing** — use orderbook depth to set limit price more/less aggressively
6. **EWMA sigma** — exponentially weighted sigma for faster adaptation to volatility regime changes

## Success Criteria

- Net improvement in per-trade EV above current +$2.10 baseline
- No increase in max drawdown per position
- Re-entry signals maintain >55% convergence rate
- Sample size > 200 trades per improvement axis

## Scores

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | — | — | — |
| Sharpe | — | — | — |
| Avg Edge | — | — | — |
| Compounding | — | — | — |
| Trades/mo | — | — | — |

## Decision

Pending.

## Anti-Knowledge (if rejected)

N/A — pending.
