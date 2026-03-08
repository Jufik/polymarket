# PnL IC from Hit Rate is Near Zero

> **TL;DR**: Train HR → Test PnL information coefficient is 0.005. Hit rate does not predict profitability. Position sizing and market selection dominate PnL outcomes.

> [!WARNING]
> A pure HR-based copy strategy will select accurate but potentially unprofitable traders. Decile 6 (~51% HR) generates the highest avg PnL ($11,397) because these are large-volume traders. The scorecard must include avg_edge_usd or profit_factor to capture economics, not just accuracy.

## Finding

Across 17,104 traders with ≥10 positions in both train and test periods:
- IC (train HR → test HR): **0.744** (very strong)
- IC (train HR → test PnL): **0.005** (essentially zero)
- Top-decile HR traders (92% test HR): avg PnL = +$892
- Decile 6 traders (51% test HR): avg PnL = **+$11,397** (highest)

PnL = HR × size × edge. Size dominates for mid-HR traders.

## Evidence

Train/test decile analysis in `research/hypotheses/trader-scorecard/discovery/hr_conviction_analysis.md`.

## Impact

- Scorecard must include economic metrics (avg_edge_usd, profit_factor), not just HR
- Composite: 0.45 HR + 0.20 avg_edge_usd + 0.10 profit_factor (30% economics weight)
- Copy strategy especially needs economic signal — following high-HR tiny traders is unprofitable

## Related

- `signals/hr_persistence.md` — HR is the best predictor of future HR, just not PnL
- `execution/hold_time_capital.md` — capital efficiency matters for PnL

## Tags

`pnl`, `hit-rate`, `position-sizing`, `scorecard`, `signal-quality`
