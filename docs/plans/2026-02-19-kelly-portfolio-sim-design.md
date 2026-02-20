# Kelly Portfolio Copy Simulation — Design

**Date**: 2026-02-19
**Capital**: $1,500 deployed
**Edge source**: Portfolio copy of 9-month consistent pure_taker traders (entry <= 0.80)

## Algorithm

Per-trader Kelly allocation, walk-forward monthly (Jan 2025 - Jan 2026).

### Per window:

1. **Build pool**: same filters as portfolio runner (9m consistent, pure_taker, 20+ mkts, entry <= 0.80)
2. **Estimate per-trader Kelly from training data**:
   - Monthly ROI series per trader (pnl / volume per calendar month in training period)
   - `mu_i` = mean monthly ROI, `sigma_i` = std monthly ROI
   - `f*_i = mu_i / sigma^2_i`, capped at `kelly_cap`
   - Drop traders with f* <= 0 or < min_training_months data points
3. **Allocate capital**:
   - Raw: `alloc_i = f*_i * bankroll`
   - Normalize so `sum(alloc) <= bankroll` (no leverage)
4. **Simulate holdout**:
   - Holdout ROI per trader = actual holdout pnl / holdout volume
   - Your PnL = sum(alloc_i * holdout_roi_i)
5. **Update bankroll**: bankroll += month_pnl (compounding)

### Parameters swept:
- `kelly_cap`: [0.10, 0.25, 0.50, 1.00]
- `min_training_months`: [4, 6]

### Output:
- Equity curve (per kelly_cap)
- Monthly PnL stream
- Sharpe, max drawdown, CAGR
- Comparison vs equal-weight (1/N)
- Per-trader Kelly fractions and contribution
