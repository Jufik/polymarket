# Architect Config Review — Round 1
**Hypothesis**: score-axis-pool-construction
**Reviewer**: Architect agent
**Date**: 2026-03-11
**Status**: HOLD — do not run validation

---

## Checklist Results

| Check | Value | Result |
|-------|-------|--------|
| executor | `"realistic"` | PASS |
| fill_model | `"calibrated_slippage"` | PASS |
| settlement_enabled | `true` | PASS |
| resolution_source | `"asset_id"` | PASS |
| pre_filter_makers | `true` | PASS |
| bootstrap_hours | `168` (7 days) | PASS — adequate for 12-month training window |
| walk_forward.train_months | `12` | PASS — reasonable |
| walk_forward.test_months | `1` | ACCEPTABLE — tight but standard |
| hold_min_hours | `4` | PASS — in-play guard present |
| max_price | `0.80` | FLAG (see below) |
| mode | `"replay"` | PASS |

---

## Harness Config: PASS

All five mandatory harness fields are correctly set:

- `executor = "realistic"` — uses `RealisticFillSimulator` with calibrated slippage and impact. This is correct for validation.
- `settlement_enabled = true` — `ReplayRunner` will call `_settle_market()` as the clock advances, freeing capital mid-run. Required for this universe (Sports markets resolve frequently).
- `resolution_source = "asset_id"` — correct. The harness resolution query uses `asset_id` from `markets_resolved` and `load_resolutions_from_rows()` builds a boolean lookup keyed on `asset_id`. No string-matching path.
- `pre_filter_makers = true` — harness will build `_tmp_harness_makers` memory table and filter trades to qualified pool only. This reduces trade volume and speeds the run.
- `bootstrap_hours = 168` — 7 days of warm-up trades before the test window starts. Given the provider uses 12-month training, bootstrap is only for the executor's `calibrate_spreads()` pass. Adequate.

---

## Walk-Forward Config: ACCEPTABLE WITH CAVEAT

```toml
[harness.walk_forward]
train_months = 12
test_months = 1
```

The config loader reads these correctly into `HarnessConfig.walk_forward_train_months` and `walk_forward_test_months`. However, **the harness does not implement walk-forward slicing** — the `--walk-forward` flag exists in `pm-harness run` but `_run_harness()` accepts it as a parameter and does nothing with it (not implemented beyond the flag). The walk-forward section in the TOML is parsed but the harness runs a single flat period regardless of this flag.

This means:
- If the caller passes `--walk-forward`, the harness silently runs as a single-window replay.
- The `train_months` / `test_months` values have no runtime effect.
- The provider's `train_months = 12` param in strategy params is used by the provider itself at `compute()` time, not by the harness.

**Implication for this hypothesis**: The provider will train on a fixed lookback (12 months before `datetime.now()` based on my memory notes). For a Sports replay covering e.g. 2025-01 to 2026-01, this creates look-ahead contamination — the provider sees trader scores from the full 12-month window including the test period. This is the same wall-clock training bug documented for tag-hr-copy.

**Severity**: MEDIUM. The harness config itself is not broken, but the provider's `compute()` call will need to receive `train_end_date = replay_start` to be clean OOS. This is a provider-side issue, not a harness config error — but the Architect should flag it.

---

## Signal Economics: BLOCK

> [!CRITICAL]
> The config comment block is self-defeating. The discovery team has already noted: "Price-level-adjusted excess = +8.2pp only. Likely negative after tick degradation." and "DO NOT validate without addressing price ceiling (max_price=0.55)." Yet the config sets `max_price = 0.80`, which is higher than the discovery recommendation. Running validation at `max_price = 0.80` will almost certainly produce a negative-PnL result and waste a full harness run.

Current economics:
- Vectorized price-level-adjusted excess: **+8.2pp** (upper bound)
- Expected tick degradation: **20-40pp**
- Expected tick-level excess: **-12pp to -32pp**
- Sample (8 months): **65 signals** at K=50, N_a=1, N_b=1 — ~8/month

The recommendation embedded in the config's own comments is to lower `max_price` to 0.55. At a price cap of 0.55, the breakeven HR is 55% (for YES bets). This would filter out the high-price structural-alpha entries (entry at 0.86) that inflate vectorized HR. Whether enough signals remain at max_price=0.55 is unknown — K=50 at 0.86 avg entry likely collapses to near-zero signals below 0.55.

---

## Fragility Flag

The config comments document: "K-25 → 0 signals, K+25 → -7pp HR." This is extreme parameter sensitivity. A K=50 pool that collapses to 0 signals at K=25 suggests the pool is not stable — it is being fitted to a very small set of traders in a single discovery sweep. This is not a harness concern but it means validation results (if any fill) will have very high variance and low replication probability.

---

## Cooldown Config: NOTE

```toml
cooldown_s = 0
```

Zero cooldown is intentional for this strategy type (each market is independent). No issue.

---

## Capital Config: NOTE

```toml
capital_usd = 1000
max_position_usd = 100
max_open_positions = 20
```

At 8 signals/month average, capital utilization will be very low. With `settlement_enabled = true`, capital will recycle after each resolution. No capital constraint issues expected IF signals occur.

---

## Summary Verdict: HOLD

**Do not run harness validation yet.** Two issues must be resolved first:

### Issue 1 (BLOCKING): Price ceiling contradiction
The config's own comments recommend `max_price = 0.55` but the config sets `max_price = 0.80`. Running at 0.80 will almost certainly produce negative PnL (expected excess: -12pp to -32pp at tick level). Validate only after lowering the price cap to the recommended level AND confirming sufficient signal count remains.

**Required action**: Discovery team must re-run the sweep with `max_price <= 0.55` and report signal count. If signals drop below 20 over the test period, the hypothesis is not viable and should be closed.

### Issue 2 (MEDIUM — harness limitation): Provider training window
The harness does not pass `train_end_date` to providers at `compute()` time. The provider will use wall-clock `datetime.now()` as the training cutoff, contaminating the OOS window. This is not fixable in the config — it requires the provider to accept a `train_end_date` parameter and the harness to pass `replay_start` as that value.

**Required action before clean validation**: Confirm `score_axis_pool_provider.compute()` accepts `train_end_date`. If not, add it. The harness fix (passing `replay_start` to `compute()`) is a generic improvement that belongs in `harness.py` — but it cannot be implemented until the provider interface supports it.

### Issue 3 (INFORMATIONAL): Walk-forward not implemented in harness
`--walk-forward` flag is a no-op in the current harness. The `train_months`/`test_months` TOML values are parsed but unused at runtime. If the research team expects walk-forward folding, it will not happen. Single-window replay only.

---

## Harness Fidelity Assessment

No fidelity issues detected in the harness itself. All 7 previously identified bugs are confirmed fixed in the current `harness.py`. The issues above are:
- Config/strategy mismatch (Issue 1)
- Missing provider interface convention (Issue 2)
- Unimplemented harness feature (Issue 3)

None require harness code changes at this time. If Issue 2 is addressed by the strategy team, a generic `train_end_date` injection in `_run_harness()` would be the appropriate harness-side fix.
