# Pre-Validation Config Checklist

Before running `run_fast_backtest()` or `SyncReplayRunner`, verify ALL of the following:

## Strategy Config
- [ ] `mode = ExecutionMode.REPLAY` (not "paper_dev" or "live")
- [ ] `capital_usd` matches research budget (typically 1000)
- [ ] `max_position_usd` is reasonable (100 for $1000 capital)
- [ ] `cooldown_s = 0` for replay (no cooldown needed)

## Provider Config
- [ ] Provider `params` match discovery sweep parameters exactly
- [ ] `refresh_interval_s` set (ignored in replay but good practice)

## Execution Config
- [ ] Fill model chosen: `SimulatedExecutor(fee_pct=0.0)` for speed, `RealisticFillSimulator` for accuracy
- [ ] Settlement is automatic (SyncReplayRunner settles resolved markets as clock advances)
- [ ] Resolution source: asset_id-based via `load_replay_resolutions()` from Parquet snapshot
- [ ] `bootstrap_hours` sufficient for strategy's consensus building time (if using providers)

## Strategy Code
- [ ] `on_trade()` or `on_trade_sync()` has explicit SELL policy (BUY-only, directional mapping, or weighted — see `pitfalls/sell_is_exit.md`)
- [ ] Consensus counts unique traders (set, not counter)
- [ ] No look-ahead: features use only data available at trade time
- [ ] Gambling markets excluded (check susceptibility or question text)

## Data
- [ ] Parquet snapshot exists (`data/research/`) and is current (`research/export_snapshot.py`)
- [ ] Period has sufficient resolved markets for the category
- [ ] Universe filter (condition_ids) correctly scoped
- [ ] `load_replay_resolutions()` returns token_map and resolutions covering the full period
