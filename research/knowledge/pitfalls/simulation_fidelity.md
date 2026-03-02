# Simulation Engine Fidelity Gaps

> **TL;DR**: The tick-by-tick engine correctly models settlement, capital, and resolution but approximates fills, timing, and impact. Total estimated gap: -15 to -20pp beyond the user-facing consensus/SELL gaps.

> [!WARNING]
> The fill model uses linear market impact with no orderbook depth. For large orders (>10% of market volume), PnL will be overestimated.

> [!WARNING]
> No signal aggregation window exists. Each trade triggers an immediate strategy decision. Real strategies should batch signals over 100-1000ms. Estimate: ~3pp impact.

> [!TIP]
> Use the `sim-fidelity-auditor` agent after any tick-by-tick validation where degradation exceeds 40pp. It diagnoses which simulation component is responsible.

## Finding

The simulation engine has 10+ identified gaps between simulated and real Polymarket execution. The gaps fall into three categories:

1. **User-responsibility gaps** (0-48pp): consensus dedup, SELL filtering — documented in separate pitfalls entries. Strategy code must handle these.

2. **Engine structural gaps** (~15pp total):
   - Fill model: linear impact, no depth, no partial fills (~5pp)
   - Timing: no aggregation window, no latency distribution (~5pp)
   - Calibration: spreads from trade prices not orderbook, static per-market (~3pp)
   - Accounting: no MTM, annualization assumes constant frequency (~2pp)

3. **Fixed gaps** (0pp): settlement, risk gates, asset_id resolution, fees.

## Evidence

Scoreboard from code audit (2026-03-02):

| Component | Fidelity | Estimated Gap |
|-----------|----------|--------------|
| Fill price | Medium | -3pp |
| Spread calibration | Medium | -2pp |
| Market impact | Low | -3pp |
| Signal timing | Low | -3pp |
| Latency model | Low | -2pp |
| Partial fills | None | -2pp |
| Mark-to-market | None | -1pp |
| Capital settlement | High | 0pp |
| Risk gates | High | 0pp |
| Resolution | High | 0pp |

## Impact

- **When interpreting tick-by-tick results**: subtract an additional ~5pp from HR and multiply PnL by 0.7-0.8 for conservative real-world estimate
- **Engine improvements**: signal aggregation window and partial fills would close ~5pp of the gap
- **Strategy design**: strategies that depend on exact timing (HFT-like) are poorly simulated; strategies with patient entries (minutes-to-hours) are well simulated

## Related

- `pitfalls/vectorized_vs_tick.md` — the 9 gaps between vectorized and tick (user-side)
- `execution/position_settlement.md` — settlement correctly modeled
- `execution/hold_time_capital.md` — capital model correctly modeled

## Tags

`simulation`, `fidelity`, `fill-model`, `timing`, `market-impact`, `engine`
