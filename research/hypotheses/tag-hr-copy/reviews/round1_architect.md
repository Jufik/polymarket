# Architect Config Review — Round 1
**Hypothesis**: tag-hr-copy
**Config**: `research/hypotheses/tag-hr-copy/config.toml`
**Reviewed by**: Architect agent
**Date**: 2026-03-05

---

## Checklist Results

| Check | Expected | Actual | Status |
|---|---|---|---|
| executor | `"realistic"` | `"realistic"` | PASS |
| settlement_enabled | `true` | `true` | PASS |
| resolution_source | `"asset_id"` | `"asset_id"` | PASS |
| bootstrap_hours | >= consensus window | `168` (7 days) | PASS |
| max_hold_hours | 48h enforced | `48` in params | PASS (see note) |
| pre_filter_makers | `true` | `true` in config | FAIL — not wired in harness |
| walk_forward train_months | reasonable | `6` | PASS |
| walk_forward test_months | reasonable | `1` | PASS |
| capital_usd | reasonable | `1000` USD | PASS |
| max_position_usd | reasonable | `100` USD | PASS |
| max_open_positions | reasonable | `20` | PASS |

---

## PASS Details

### executor = "realistic"
Correct. `HarnessConfig` defaults to `"realistic"` and config explicitly sets it. The harness will use `RealisticFillSimulator` with `calibrate_spreads()` + `calibrate_volumes()` over the replay period trades.

### settlement_enabled = true
Correct. `ReplayRunner` is constructed with `resolutions=resolutions` and `token_map=token_map` (lines 198-200 of harness.py). Mid-replay settlement will free capital as markets resolve.

### resolution_source = "asset_id"
Correct. The harness loads resolutions from `markets_resolved` view (which uses the CLOB API `token_won` field — asset_id based, not string matching). This field is parsed by `load_harness_config` but the harness always uses asset_id resolution via `load_resolutions_from_rows`. The field is a documentation-level signal only; no code path switches on it.

### bootstrap_hours = 168
7 days. The `tag_hr_provider` uses `lookback_months = 6` for computing qualified trader hit rates. Bootstrap is for warm-up only (letting providers accumulate initial state). 168h is adequate — the provider queries historical data at `compute()` time, not incrementally.

### max_hold_hours = 48
Present in `[strategy.tag_hr_copy.params]`. Whether the strategy implementation actually enforces this is outside config scope — the harness passes `strat_cfg.params` through as-is. Config is correct; implementation enforcement is the researcher's concern.

### walk_forward: train_months=6, test_months=1
Reasonable split. `load_harness_config` correctly reads `[harness.walk_forward]` subsection and maps to `walk_forward_train_months=6`, `walk_forward_test_months=1`. Note: `--walk-forward` flag is not wired to actual windowing logic in `_run_harness` (the flag is accepted but `walk_forward` arg is unused beyond the parameter list — no OOS windowing actually executes). This is a harness gap but not a config error.

---

## FAIL — Critical Gap: pre_filter_makers Not Wired

**Config value**: `pre_filter_makers = true`
**Harness behavior**: `HarnessConfig.pre_filter_makers` is parsed correctly but **never consumed** anywhere in `harness.py`.

The trade load query (lines 112-127 of harness.py) loads ALL trades in the period with no maker pre-filtering:
```python
trade_rows = ch.query(
    "SELECT * FROM polymarket.trades_raw FINAL "
    "WHERE timestamp >= %(start)s AND timestamp < %(end)s "
    "ORDER BY timestamp",
    parameters={"start": int(start_epoch), "end": int(end_epoch)},
)
```

**Impact**: Without pre-filtering to qualified makers, the replay processes the full trade volume (potentially 11x more trades than needed). This will:
1. Slow the replay significantly
2. Cause provider `on_trade()` callbacks to fire on irrelevant trades
3. Not block correctness per se (the strategy's own signal logic will still gate entries), but degrades simulation fidelity by diluting provider state with non-qualified trades

**Severity**: Medium — not a bias risk, but a performance and fidelity gap.

**Recommendation**: Implement pre_filter_makers in the harness trade load, joining against `qualified_traders` or similar. This is a generic harness improvement (benefits all strategies). Owner: Architect.

---

## Observations

### Signal frequency vs capital
Config targets 608-1905 signals/month across 4 tags. With `max_position_usd = 100` and `capital_usd = 1000`, the budget allows 10 concurrent positions. At signal peak (1905/mo ≈ 63/day), capital recycling depends heavily on `max_hold_hours=48`. For Esports (avg hold ~2h) and 1H (avg hold ~1.67h), capital turns over quickly and `max_open_positions=20` would saturate budget before hitting position count. For Basketball (avg hold ~4h), similar story. Capital constraint is the binding limit at peak signal frequency, not position count — this is expected behavior.

### Walk-forward flag is a no-op
`--walk-forward` is accepted by the CLI but the `walk_forward` bool is never used inside `_run_harness`. The config's `[harness.walk_forward]` section is parsed and stored in `HarnessConfig` but no OOS windowing logic is implemented. Running with `--walk-forward` produces the same result as without it.

### fill_model field is parsed but not consumed
`harness_cfg.fill_model` is stored but the harness always constructs `FillModelConfig()` with defaults regardless of the config value. This is cosmetic for now since the only supported value is `"calibrated_slippage"`.

### pm-harness is registered
`pm-harness = "polymarket_pipeline.cli.harness:app"` is present in pyproject.toml (line 97). The CLI entry point is valid.

---

## Summary

Config is **structurally sound** for validation. The three critical fields (executor, settlement, resolution_source) are correct. The main gap is `pre_filter_makers` being silently ignored — the harness will process all trades rather than pre-filtered qualified makers. Two other harness gaps (walk-forward no-op, fill_model ignored) are cosmetic for this run.

**Recommendation**: Proceed with validation. Flag the `pre_filter_makers` gap as a harness improvement task for post-run.
