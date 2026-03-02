# Position Settlement and Capital Recycling

> **TL;DR**: Positions must be settled mid-simulation to free capital. Without settlement, `cost_basis` grows monotonically and blocks all new entries after N fills.

> [!CRITICAL]
> Any simulation with capital limits MUST implement tick-by-tick settlement. Without it: 50 fills then 10,462 rejections — results are meaningless.

> [!WARNING]
> `BacktestRunner` does NOT settle mid-run. Only use it for strategies without capital constraints. Use `ReplayRunner` for capital-constrained replay.

## Finding

The `Position` dataclass has `cost_basis` which accumulates on every BUY fill but is never reduced by SELL fills. Only explicit settlement (setting `cost_basis=0`) frees capital for the risk gate.

The risk gate checks: `sum(p.cost_basis for all positions) + intent.size_usd > capital_usd`. Without settlement, this permanently blocks after `capital_usd / size_usd` fills.

Settlement must be **tick-by-tick**: at each simulated timestamp, check if any held markets have resolved. If so, zero their quantities and cost_basis, update realized_pnl.

The `ReplayRunner` (in `strategies/runners/replay.py`) handles this via:
- Pre-sorted resolution timeline: `[(resolved_at, condition_id)]`
- On each tick: scan timeline pointer, settle markets with `resolved_at <= current_time`
- Settlement uses asset_id-based resolution (never strings)
- `ExecutionGateway._strategy_spent` is a separate counter that never resets — set it very high for replays

## Evidence

```python
# LiveRunner.settle_resolved_market (production, strategies/runners/live.py:416)
new_pos = replace(old_pos,
    qty_yes=0.0, qty_no=0.0,
    cost_basis=0.0,  # THIS is what frees capital
    realized_pnl=old_pos.realized_pnl + pnl_delta,
)
```

Before fix: 50 fills then 10,462 rejections (all max_open_positions).
After fix: 355 fills, 0 rejections, 352 settlements (capital recycles).

## Impact

- **ReplayRunner**: Must have resolution data and settle mid-replay (not post-hoc)
- **BacktestRunner**: Does NOT settle — only use for simple strategies without capital constraints
- **Gateway budget**: Set to very high value for replays (`1_000_000`). The risk gate on `cost_basis` is the real constraint.
- **Any simulation with capital limits**: Must implement settlement or results are meaningless

## Related

- `execution/hold_time_capital.md` — Hold time determines how long capital is locked before settlement
- `data/resolution_mechanics.md` — Settlement uses asset_id-based resolution (correct approach)
- `pitfalls/vectorized_vs_tick.md` — Without settlement, tick-by-tick simulation produces zero results after N fills

## Tags

`settlement`, `capital`, `position-lifecycle`, `replay`, `cost-basis`, `critical`
