# Method Audit: tag-hr-copy — Temporal Leakage & Look-ahead

**Scope**: All code in `research/hypotheses/tag-hr-copy/` plus the underlying CH view definitions in
`docker/clickhouse/migrations/010_split_correction.sql` and `003_derived_views.sql`.

**Primary sweep audited**: `scripts/sweep2.py` (this is the file that produced `results_raw.json`).
`scripts/sweep.py` (v1) also reviewed — shares the same structure, same findings apply.

---

## 1. Train/Test Temporal Split

**PASS — with one leakage vector described in item 3.**

`sweep2.py` lines 63-65:
```python
train_end = test_start
train_start = subtract_months(train_end, 6)
```

The boundary is clean: `train_end = test_start`, which means the qualification filter uses
`resolved_at < test_start` strictly. Test evaluation uses `resolved_at >= test_start`. No interval
overlap at the date boundary.

The five folds are non-overlapping in test windows (Jan, Apr, Jul, Oct 2025; Jan 2026). Training
windows roll forward (always the 6 months immediately preceding the test month). Training windows
from adjacent folds DO overlap with each other — this is expected and correct for rolling walk-forward.

---

## 2. Qualification Timing

**PASS — with one structural ambiguity described in item 3.**

Trader qualification (`_tmp_thr_qual_buy`, `_tmp_thr_qual_dir`) uses `resolved_at < train_end` as
the filter (sweep2.py lines 90-93, 100-103). Only markets that **resolved before the test period
starts** contribute to a trader's qualification score. A trader whose markets resolved in the test
period cannot inflate their training HR. This is correct.

Test signal evaluation then filters `resolved_at >= test_start` and joins to the pre-built
qualification tables — no recalculation, no data from the test period bleeds into qualification.

---

## 3. Resolution Leakage — CRITICAL FINDING

> [!CRITICAL]
> **The `resolved_at` column used for temporal gating is NOT the trade timestamp. It is the market
> resolution timestamp. A trader's position record in `maker_positions_resolved_corrected` exists
> ONLY if the market has resolved.** Both training qualification AND test signal selection filter on
> `resolved_at`. This means the sweep NEVER sees open (unresolved) markets, which creates a
> resolution-conditioned universe at BOTH stages.
>
> **For training**: this is correct and expected — you need resolution to evaluate HR.
>
> **For testing (signal simulation)**: this is look-ahead contamination. The simulated strategy
> "enters" a market at `first_trade` (the trader's first trade time) but the market is only
> included in the test set if it resolved within `[test_start, test_end)`. In deployment, the
> strategy does not know which markets will resolve within the next 48 hours at signal time. The
> sweep implicitly selects only markets that DID resolve (and within the 48h hold gate) — this is
> a form of hindsight selection. Markets that the trader entered but that resolved AFTER the test
> window (or never resolved) are excluded silently.
>
> **Impact**: The test HR is computed only over markets that (a) had a qualified trader signal AND
> (b) resolved within the test month. Markets where the trader entered but the resolution slipped
> into the next month are excluded from BOTH the signal count and the HR denominator. This biases
> HR upward if short-resolution markets (resolved quickly) have different outcome distributions
> than long-resolution ones.
>
> **Severity**: For the 48h max-hold gate, this effect is partially mitigated — the gate already
> requires the market to resolve within 48h of the signal, which is a reasonable real-time filter.
> However, the test universe still excludes all markets where the trader entered but resolution fell
> outside the test month boundary, even if they would have resolved within 48h. The fold boundary
> artifact is real: a market entered on Jan 30 that resolves Feb 3 is excluded from the Jan fold
> and excluded from the Apr fold (too early). It is invisible to the walk-forward entirely.

---

## 4. Base Rate Computation

**PASS.**

Base rate query (sweep2.py lines 69-77):
```sql
SELECT countIf(yes_won=1), count()
FROM (SELECT condition_id, any(yes_won) AS yes_won
      FROM maker_positions_resolved_corrected
      WHERE condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
        AND toDate(resolved_at) >= '{train_start}'
        AND toDate(resolved_at) < '{train_end}'
      GROUP BY condition_id)
```

This computes the YES win fraction strictly within `[train_start, train_end)` using only resolved
training-period markets. No test data is involved. Correct.

---

## 5. Entry Price Filter

**N/A — no entry price filter exists in any sweep code.**

Neither `sweep.py` nor `sweep2.py` contains any `avg_entry_price`, `entry_price`, or price-based
filter. The `config.toml` does not specify one either. This item from the audit checklist does not
apply to this hypothesis.

---

## 6. Market Selection

**PARTIAL PASS — tag market universe is not time-bounded.**

`sweep2.py` lines 51-59:
```sql
CREATE OR REPLACE TABLE _tmp_thr_tag_mkts ENGINE = Memory AS
SELECT DISTINCT m.condition_id
FROM markets m JOIN events e ON m.event_id = e.id
JOIN event_tags et ON et.event_id = e.id
JOIN tags t ON t.id = et.tag_id
WHERE t.label = '{tag}'
```

This materializes ALL markets ever tagged with the label — including markets created after the test
period ends and markets created after 2026. In practice this is harmless because downstream queries
always filter by `resolved_at`, which excludes future-created markets. However, it could include
markets that were **re-tagged** after resolution, if the tag was applied retroactively.

> [!WARNING]
> **Tag assignment is not point-in-time.** The `event_tags` table reflects current tag state. If
> tags were added to events retroactively (e.g., Polymarket classified a 2024 event as "Esports"
> in 2025 after the fact), the sweep would include those markets in training even though the tag
> label was not available at the time the strategy would have needed to act. This is unlikely to be
> a major source of contamination for established tags (Basketball, Tennis) but is more concerning
> for "Esports" and "1H" which appear to be newer classifications. This cannot be verified from
> the code alone — it requires checking whether `event_tags` entries have a `created_at` timestamp.

---

## 7. Walk-Forward Integrity

**PASS for non-overlap. FAIL for `lookback_months` as reported in Round 1.**

The five test windows are:
- Jan 2025, Apr 2025, Jul 2025, Oct 2025, Jan 2026

These are non-overlapping. Each test month is evaluated exactly once. Training windows roll forward
correctly (6 months immediately preceding each test month).

However, `sweep.py` (v1) — NOT `sweep2.py` — has the dead `lookback_months` grid variable
(sweep.py lines 215-221), which caused 3x redundant computation and inflated n_folds counts.
**`sweep2.py` does NOT have this bug** — it iterates only over `(mt, ep)` pairs without a
lookback dimension. The `results_raw.json` is produced by `sweep2.py` (line 202), so the
published results are clean on this point.

---

## 8. Underlying View Integrity

**`maker_positions_resolved_corrected` is sound but has one subtle issue.**

The view definition (`010_split_correction.sql` lines 179-210) performs an `INNER JOIN` on
`markets_resolved`. This means a position row exists **only if the market has resolved**. The
`correct` column (`line 191`) is computed as:

```sql
(CASE WHEN mr.yes_won THEN p.net_yes ELSE p.net_no END) + p.net_usd > 0 AS correct
```

This is a position-level PnL sign, not a trade-level outcome. It is computed at query time from
the resolved outcome. There is no temporal leakage here — the correct flag is just a function of
net_yes, net_no, net_usd (lifetime trade aggregates), and the final resolution. The `first_trade`
column is `min(t.timestamp)` from `trades_raw`, which is the actual earliest trade timestamp for
that maker on that market. This is the correct "signal time" proxy.

**One subtle issue with `correct` as signal proxy**: `correct = 1` means the trader's net
**lifetime** position was profitable. A trader who was long YES, then fully exited (net_yes ≈ 0),
would have `position = 'CLOSED'` and be excluded from the view entirely (`WHERE NOT (net_yes <=
0.01 AND net_no <= 0.01)`). This means qualification HR only sees traders who still held a net
position at resolution — traders who correctly took profit and exited before resolution are
**excluded from the qualified pool**. This biases the qualified pool toward traders who held
through resolution rather than those who correctly read the market and exited early. It may
undercount skilled traders.

> [!WARNING]
> **Traders who correctly exited (net position ≈ 0 at resolution) are excluded from
> `maker_positions_resolved_corrected`** by the `WHERE NOT (net_yes <= 0.01 AND net_no <= 0.01)`
> filter in `010_split_correction.sql` line 210. Qualification HR is measured only over
> hold-to-resolution positions. This introduces selection bias toward position-holders vs. active
> traders, and may underrepresent the most skilled (exit-before-resolution) traders.

---

## 9. Hold Time Computation

**WARNING — `first_trade` is the wrong entry time for multi-trader signals.**

In both sweeps, hold time is computed as:
```sql
dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours
```

`max(t.first_trade)` across all qualified traders on a market is used as the "entry time" for
the deployed strategy. This is correct in concept (the strategy enters when the last qualifying
trader acts). However, `first_trade` is the trader's earliest trade on that market **over their
entire history** — not their first trade within the test window. If a trader began accumulating a
position in a market during the training window (before test_start) and that market resolved in
the test window, their `first_trade` could be months before test_start.

> [!CRITICAL]
> **`first_trade` is not bounded to the test window.** A qualified trader may have first traded a
> market in January (training window) with the market resolving in April (test window). The sweep
> would include this market in the April test fold, but `hold_hours = dateDiff(max(first_trade),
> resolved_at)` would compute from the January trade date — yielding `hold_hours` far exceeding
> the MAX_HOLD_HOURS=48 filter. This market is correctly excluded by the 48h gate. **But** — if
> ANY qualified trader entered the market within 48h of resolution, `max(first_trade)` pulls the
> latest entry time, which may be within 48h. That trader's late entry dominates, making the
> market appear as a valid 48h-hold signal even if other traders entered months earlier. The
> signal is attributed to the `max(first_trade)`, but the HR is measured over ALL qualified
> traders' positions on that market. The strategy would realistically only be copying the one late
> trader, not the earlier ones — but HR is measured pooling all of them.

---

## Summary of Findings

| Check | Status | Severity |
|-------|--------|----------|
| 1. Train/test split boundary | PASS | — |
| 2. Qualification timing | PASS | — |
| 3. Resolution-conditioned test universe | PARTIAL FAIL | WARNING |
| 4. Base rate from training only | PASS | — |
| 5. Entry price filter | N/A | — |
| 6. Tag universe time-bounded | PARTIAL FAIL | WARNING |
| 7. Walk-forward integrity (sweep2.py) | PASS | — |
| 8. View: exit-before-resolution exclusion | WARNING | WARNING |
| 9. Hold time: first_trade unbounded | FAIL | CRITICAL |

**Overall**: The sweep does not have classic look-ahead bias (future labels used in training, or
training/test overlap). However, two structural issues affect the validity of the results:

1. **CRITICAL** — `first_trade` is lifetime-unbounded. The 48h hold gate partially protects
   against this but does not fully prevent the signal attribution problem described in item 9.
   The fix is to also filter `first_trade >= test_start` when computing hold hours, or to use
   trade timestamps from within the test window only.

2. **WARNING** — The test universe includes only markets that resolved within the test month. Fold
   boundary leakage (markets that resolve just after the test window ends) systematically excludes
   certain markets. This is not classic look-ahead bias but introduces a selection effect.

3. **WARNING** — Tag classification is not point-in-time. Retroactive tagging cannot be ruled out
   from this code alone.

The Esports 77.2% HR and Tennis 55.7% HR may survive these corrections, but the `first_trade`
unbounded issue means hold time and signal attribution need verification before tick-by-tick replay
can be trusted to match the sweep.
