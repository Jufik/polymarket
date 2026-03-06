# Training Window Look-Ahead in Walk-Forward Validation

> [!CRITICAL]
> Provider qualification pools computed with `datetime.now()` as train_end create look-ahead bias:
> test signals early in the replay period overlap with the training window. In tag-hr-copy,
> Sep-Dec 2025 signals (42% of 1,546 fills) were partially in-sample because the pool was trained
> on data through Mar 2026. Fix: pass `test_start` per fold into provider `compute()`.

## Explanation

Walk-forward validation requires that the qualification pool (which traders are "qualified") be
computed using only data available at the start of the test period. If the pool is computed using
future data, the strategy is effectively selecting traders whose past performance was evaluated
using information not available at trade time.

The bug pattern:
```python
# WRONG — uses wall-clock now() which is after the replay period
train_end = datetime.datetime.now(datetime.timezone.utc)

# CORRECT — train_end is the start of the test fold
train_end = fold_test_start  # e.g., "2025-01-01"
train_start = fold_test_start - 6 months
```

In tag-hr-copy, the provider was trained on Sep 2025 – Mar 2026 data for a Jan 2025 – Jan 2026
replay. Signals from Sep-Dec 2025 used a pool trained on data from the future relative to signal
time. Only the Jan-Aug 2025 signals (n≈890 of 1,546, ~58%) were truly OOS.

## Data / Evidence

From validation_audit.md (2026-03-06):
- Training window: Sep 2025 – Mar 2026 (wall-clock)
- Replay period: Jan 2025 – Jan 2026
- Overlap: Sep-Dec 2025 signals in-sample (656 of 1,546 = 42%)
- True OOS window: Jan-Aug 2025 only

Bug was discovered post-run. Full walk-forward recomputation (per-fold pool) was not completed —
results are suggestive but not clean OOS.

From vectorized discovery (R3): `first_trade >= test_start` filter in market aggregation removed
31.9% of test-window positions that were phantom pre-test entries (69,477 / 217,895 across all
tags, 2025-07 fold). This is a related but separate look-ahead issue — see `phantom_test_signals.md`.

## Fix

Provider `compute()` must accept `train_end_date` parameter. Harness must pass `fold.test_start`
as `train_end_date` for each fold:

```python
async def compute(self, backend, train_end_date: str):
    train_start = (datetime.fromisoformat(train_end_date) - timedelta(days=180)).isoformat()
    # query uses: resolved_at < train_end_date AND resolved_at >= train_start
```

## Related

- `pitfalls/phantom_test_signals.md` — `first_trade >= test_start` filter in vectorized sweeps
- `pitfalls/vectorized_vs_tick.md` — simulation fidelity gaps

## Tags

`look-ahead`, `walk-forward`, `training-window`, `provider`, `validation`, `in-sample`
