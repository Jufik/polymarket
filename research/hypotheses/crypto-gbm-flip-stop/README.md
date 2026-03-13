# Hypothesis: GBM Flip Stop-Loss Optimization

**Status**: `discovery`
**Created**: 2026-03-10
**Category**: crypto

## Statement

The GBM flip stop-loss (exit when GBM P(our side) < 0.35) produces 3x more exits than
the trailing stop (238 vs 81 in paper trading), suggesting many are false stops triggered
by temporary BTC oscillations. Optimizing the threshold, adding confirmation delays, or
making the stop adaptive to hold time should reduce false stops and improve net PnL.

## Research Questions

1. What fraction of flip exits are false stops (GBM would recover within 30-60s)?
2. What's the optimal flip threshold (currently 0.35)?
3. Does a confirmation delay (require N consecutive ticks below threshold) help?
4. Does time-in-position adaptation help (widen stop the longer you've held)?
5. What's the PnL impact of each improvement?

## Success Criteria

- Reduce false flip exits by >30%
- Net PnL improvement per trade
- No increase in max drawdown per position
- Sample size > 500 simulated windows

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
