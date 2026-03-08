---
name: research-validate
description: "Tick-by-tick validation methodology — SyncReplayRunner execution, walk-forward windowing, vectorized vs tick comparison. Used by the Researcher agent during Phase 4."
user-invocable: false
---

# Validation Methodology (Tick-by-Tick)

You are performing tick-by-tick validation using the fast replay infrastructure.
This produces REALISTIC results (not upper bounds).

## Pre-Flight Checklist

Before running validation, verify ALL items in `checklist.md`:

1. **SELL handling decided** — strategy has explicit SELL policy (BUY-only, directional mapping, or weighted). See `pitfalls/sell_is_exit.md`.
2. **Unique-trader consensus** — counts unique traders (set), not trade events
3. **Asset-ID resolution** — uses asset_id for resolution, never string matching
4. **Settlement enabled** — SyncReplayRunner always settles (built-in)
5. **Gambling excluded** — filter out susceptible markets
6. **Fill model chosen** — SimulatedExecutor for speed, RealisticFillSimulator for accuracy

If ANY item fails, fix before proceeding.

## Knowledge Context

Your dispatch prompt includes CRITICAL and WARNING admonitions from the Lead's Phase 0.
Verify each pre-flight item against these admonitions before proceeding.
If the knowledge context is missing from your prompt, load from `research/knowledge/` as fallback.

## Step 1: Run Fast Backtest (Preferred)

Use `run_fast_backtest()` from the research harness — fully synchronous, no asyncio needed:

```python
from research.harness import run_fast_backtest, print_summary
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

config = StrategyConfig(
    name="test", enabled=True, mode=ExecutionMode.REPLAY,
    capital_usd=1000, max_position_usd=100,
    max_open_positions=20, cooldown_s=0,
)

result, summary = run_fast_backtest(
    strategy, config,
    universe=set_of_condition_ids,      # filter by market universe
    start_month=202501,                  # optional YYYYMM
    end_month=202512,                    # optional YYYYMM
)
if summary:
    print_summary(summary, "my_strategy")
```

This uses:
- **Parquet snapshot** (`data/research/trades/`) with Polars predicate pushdown
- **ReplayTick** lightweight dataclass (~0.5 μs vs ~10 μs for NormalizedTrade)
- **SyncReplayRunner** with zero-async overhead and built-in settlement
- **ParquetLedger** for trade-level records

### Direct SyncReplayRunner (for custom setups)

For more control (custom providers, realistic fills, etc.):

```python
from research.fast_replay import load_replay_trades, load_replay_resolutions
from research.sync_replay import SyncReplayRunner

# Load trades (Polars predicate pushdown — skips 95%+ of row groups)
ticks = load_replay_trades(universe=universe, start_month=202501, end_month=202512)

# Load resolutions from Parquet snapshot
resolutions, token_map = load_replay_resolutions()
resolutions = {k: v for k, v in resolutions.items() if k in universe}

# Setup
runner = SyncReplayRunner(
    strategy=strategy, ctx=ctx, gateway=gateway, config=config,
    resolutions=resolutions, token_map=token_map, ledger=ledger,
    providers=[my_provider],  # optional feature providers
)
result = runner.run(ticks)  # synchronous — no asyncio.run() needed
```

### Research Server (for interactive exploration)

The research server (`research/server.py`, port 9999) provides HTTP endpoints for replay:

```bash
# Start server
PYTHONPATH=. uv run python research/server.py

# Run replay via HTTP
curl -X POST http://localhost:9999/replay -H 'Content-Type: application/json' -d '{
  "universe": ["0xabc...", "0xdef..."],
  "threshold": 0.30,
  "capital_usd": 1000.0,
  "max_position_usd": 50.0
}'
```

## Step 2: Walk-Forward (Optional)

Split period into train/test windows:
```python
# Train: 12 months, Test: 1 month (from config)
# Run each window separately, collect per-window metrics
for train_start, train_end, test_start, test_end in folds:
    # Qualify traders in training window (DuckDB or CH)
    universe = get_qualified_universe(train_start, train_end)
    # Replay test window
    result, summary = run_fast_backtest(
        strategy, config,
        universe=universe,
        start_month=int(test_start.strftime("%Y%m")),
        end_month=int(test_end.strftime("%Y%m")),
    )
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

Add cells to the marimo notebook:

```python
# Cell N: Load ledger.parquet
import polars as pl
ledger_df = pl.read_parquet("research/output/ledger_strategy_name.parquet")

# Cell N+1: Equity curve (cumulative PnL over time)
# Cell N+2: Monthly PnL breakdown
# Cell N+3: Vectorized vs tick-by-tick comparison chart
# Cell N+4: Hold time vs PnL scatter
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
