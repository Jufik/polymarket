---
name: research-validate
description: "Tick-by-tick validation methodology — pm-harness execution, walk-forward windowing, vectorized vs tick comparison. Used by the Researcher agent during Phase 4."
user-invocable: false
---

# Validation Methodology (Tick-by-Tick)

You are performing tick-by-tick validation using the production replay harness.
This produces REALISTIC results (not upper bounds).

## Pre-Flight Checklist

Before running `pm-harness run`, verify ALL items in `checklist.md`:

1. **SELL handling decided** — strategy has explicit SELL policy (BUY-only, directional mapping, or weighted). See `pitfalls/sell_is_exit.md`.
2. **Unique-trader consensus** — counts unique traders (set), not trade events
3. **Asset-ID resolution** — uses asset_id for resolution, never string matching
4. **Settlement enabled** — `settlement_enabled = true` in config.toml
5. **Gambling excluded** — filter out susceptible markets
6. **RealisticFillSimulator** — `executor = "realistic"` (NOT "simulated")

If ANY item fails, fix before proceeding.

## Knowledge Context

Your dispatch prompt includes CRITICAL and WARNING admonitions from the Lead's Phase 0.
Verify each pre-flight item against these admonitions before proceeding.
If the knowledge context is missing from your prompt, load from `research/knowledge/` as fallback.

## Step 1: Run pm-harness

```bash
uv run pm-harness run \
  --config research/hypotheses/{slug}/config.toml \
  --period 2025-01-01:2026-01-01 \
  --output research/hypotheses/{slug}/validation/
```

This produces:
- `validation/ledger.parquet` — full trade ledger
- `validation/summary.json` — aggregated metrics
- `validation/replay_log.jsonl` — execution log

## Step 2: Walk-Forward (Optional)

If walk-forward is configured:
```bash
# Split period into train/test windows
# Train: 12 months, Test: 1 month (from config)
# Run each window separately, collect per-window metrics
```

## Step 3: Compare Vectorized vs Tick-by-Tick

Build comparison table:

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | X% | Y% | -Zpp |
| Sharpe | X | Y | -Z% |
| Avg Edge | $X | $Y | -Z% |
| Compounding | X | Y | -Z% |
| Trades/mo | X | Y | |

### Degradation Bands

- **20-40pp**: Expected. Normal simulation friction.
- **<10pp**: Suspicious. Likely look-ahead bias in strategy.
- **>40pp**: Excessive. Flag to Architect for harness investigation.

## Step 4: Add Validation Cells to Notebook

Add cells 7-11 to the marimo notebook:

```python
# Cell 7: Load ledger.parquet
# Cell 8: Equity curve (cumulative PnL over time)
# Cell 9: Monthly PnL breakdown
# Cell 10: Vectorized vs tick-by-tick comparison chart
# Cell 11: Hold time vs PnL scatter
```

## Step 5: Write Notes

Write to `validation/notes.md`:
- Tick-by-tick metrics vs vectorized comparison
- Degradation analysis (which metrics degraded most, why)
- Surprising findings for knowledge capture
- Verdict: exploitable / marginal / none

## Output Requirements

Return to Lead:
1. Validated metrics (HR, PnL, Sharpe, drawdown, compounding_score)
2. Vectorized vs tick comparison table
3. Monthly equity curve data
4. Knowledge captures (surprising findings)
5. Verdict: exploitable / marginal / none
