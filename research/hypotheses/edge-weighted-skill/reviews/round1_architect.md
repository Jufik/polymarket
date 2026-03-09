# Architect Review — Round 1: edge-weighted-skill

**Date**: 2026-03-09
**Reviewer**: architect
**Scope**: config.toml correctness, harness fidelity, discovery script methodology, simulation gaps

---

## 1. Config.toml Assessment

### Status: INCOMPLETE — config.toml is a stub, not strategy-specific

The config at `research/hypotheses/edge-weighted-skill/config.toml` is the blank template with `[strategy.RENAME_ME]` and `[provider.RENAME_ME_provider]`. It has NOT been filled in for the actual validation configuration. The harness cannot run as-is.

### Required fields (correctly set in template):

| Field | Value | Status |
|-------|-------|--------|
| `executor` | `"realistic"` | CORRECT |
| `fill_model` | `"calibrated_slippage"` | CORRECT |
| `bootstrap_hours` | `168` (7 days) | NEEDS REVIEW — see below |
| `settlement_enabled` | `true` | CORRECT |
| `resolution_source` | `"asset_id"` | CORRECT |
| `walk_forward.train_months` | `12` | CONSISTENT with discovery (12m folds) |
| `walk_forward.test_months` | `1` | MATCHES discovery test period (Jan 2026) |

### Missing strategy-specific configuration:

1. **No multi-track definition**: The hypothesis explicitly proposes 3 distinct tracks:
   - Track A: Edge-copy (K=50, N=1, mid-price, longer hold ~18h)
   - Track B: Consensus pooling (K=200, N=2, mid-price, hold ~5h)
   - Track C: In-play dedicated (K=25, N=1, hold <4h, latency-sensitive)

   The current config.toml has one `[strategy.RENAME_ME]` block. Multi-track requires either:
   - Three separate `[strategy.X]` blocks with separate capital allocations, OR
   - One strategy that internally routes by regime (single capital pool, harder to audit)

2. **Capital allocation unspecified**: `capital_usd = 1000` is the template default. With $100 max position and 20 max open positions, this is capped at $2,000 deployed simultaneously — may be fine for January 2026 (766 signals/month for K=50), but at K=200 N=2 with 761 signals and 5.4h avg hold, concurrent positions could hit the cap.

3. **Provider not specified**: The provider needs to know which pool (K, method, tags) to load. No `[provider.X.params]` are filled in.

### Bootstrap hours analysis:

`bootstrap_hours = 168` (7 days) covers 1 week of tick history before replay starts. Assessment:

- **Consensus track (mid-price)**: Consensus N=2 fires when a 2nd pool trader enters a market. With 761 signals over January (31 days) = ~24/day, 7-day bootstrap gives ~170 qualifying events to warm up the provider's market state. Adequate.
- **In-play track**: In-play signals are time-sensitive (58-min lead time per prior research). Bootstrap just needs the trader pool loaded, not a long history. 168h is more than sufficient.
- **Walk-forward concern**: If the provider uses wall-clock `datetime.now()` as `train_end` (as seen in the TagHR provider bug documented in memory), the training window will contaminate OOS results. **Must verify the provider implementation uses `replay_start` as `train_end`.**

---

## 2. Discovery Script Methodology Assessment

### copy_vs_pooling.py

**Positive findings:**
- Correctly uses `first_trade >= test_start` filter to eliminate phantom signals (non-copyable)
- Market-level aggregation enforced (1 signal per condition_id) — counting_unit pitfall avoided
- Training period strictly `< 2026-01-01`, test period Jan 2026 — clean train/test split
- Gambling markets excluded via slug-pattern matching
- BUY-only (YES positions) — SELL pitfall avoided
- Uses `yes_entry_data` inner join, which correctly excludes split-route traders

**Methodology concerns:**

1. **In-play consensus N trigger approximation**: The script approximates consensus entry as `avg pool entry price`. For N=2 consensus, the correct trigger price is the price at the moment the Nth distinct trader enters, not the average. This is noted as a limitation, but it means tick-by-tick will see a different entry price than vectorized assumed. Degradation from this source alone could add 5-10pp.

2. **PnL model gap**: The vectorized script uses `$100 stake, fill at avg pool entry + 1pp slippage`. The realistic executor uses calibrated slippage from actual trade-to-trade price changes. These can diverge significantly in illiquid markets (longshot regime). This is expected and correct — the 20-40pp degradation estimate accounts for this.

3. **Longshot regime HR in test period**: Vectorized shows 5.6% base rate for longshot (<0.30). The in-play track (K=10, N=1) shows 90.9% HR — this is the sure-thing regime dominating the signal count (19/100 signals at sure-thing/100%). The longshot component is only 3 signals. This is too sparse for reliable tick validation.

4. **No capital constraint in vectorized**: Acknowledged limitation. With 766 signals/month (K=50) and hold ~18h, peak concurrent positions at $100/each would need ~$9,200 (766 signals × 18h / 24h / day × ~7 overlapping). With only $1,000 capital and 20 max_open_positions, tick-by-tick will reject a large fraction of signals. This is the primary source of expected PnL degradation beyond the normal 20-40pp band.

   **Capital rejection risk is HIGH for this hypothesis.** Recommendation: set `capital_usd = 5000` and `max_open_positions = 50` for the replay, or use separate capital pools per track.

5. **Pool scored on ALL tags combined** (Part A): The edge-copy pool uses global training (all tags combined) to rank traders. But the walk-forward analysis shows tag-specific scoring is more stable (e.g., Politics composite σ=0.059 vs edge_primary σ=0.058). For tick validation, tag-specific pools are recommended.

### walkforward_stability.py

**Positive findings:**
- Train/test boundary is strict (train_end = test_start, no overlap)
- OOS evaluation uses `first_trade >= test_start` phantom filter
- Market-level aggregation in OOS evaluation
- Gambling exclusion consistent with copy_vs_pooling.py

**Methodology concerns:**

1. **Fold 3 test window is open-ended**: `test_end = 2026-04-01` but current date is 2026-03-09. This means Fold 3 only has ~2 months of OOS data vs 3 months expected. HR and signal counts for Fold 3 are underestimates relative to what the full window would show. Do not treat Fold 3 metrics as comparable to Folds 1-2.

2. **Elections tag has 1 signal in Fold 1**: The entire Elections Fold 1 result (HR=1.000) rests on 1 signal. This is statistically meaningless and should be excluded from stability comparisons. The Spearman correlation is reported as "N/A" for F1-F2 (correct) but the HR=1.000 propagates into the best-method selection table, distorting results.

3. **Conviction filter inconsistency**: `walkforward_stability.py` uses `avg_conviction >= 0.90` to qualify traders, while `copy_vs_pooling.py` uses `conviction >= 0.50` (from the global scorecard where `AVG(ABS(mp.net_usd) / NULLIF(mp.volume, 0)) >= 0.50`). These filters may select different trader populations. The walk-forward pool may be more conservative than the copy_vs_pooling pools used in discovery.

4. **Spearman rank correlation is negative in most cases**: Negative Spearman (e.g., Sports hr_only F2-F3: -102.591) indicates rank reversal — the worst performers in Fold 2 become the best in Fold 3. Values outside [-1, 1] indicate a numerical issue in the Spearman formula when n is very small (check: formula uses `n*(n^2-1)`, which explodes for tiny n). Values like -102 and -141 are invalid and indicate insufficient overlap between folds (< 3 traders in common). The Spearman metric should not be used for stability ranking until this is fixed.

---

## 3. SyncReplayRunner Fidelity Assessment

### Consensus N trigger: NOT directly supported

The SyncReplayRunner processes trades one at a time. Implementing consensus (N distinct pool traders enter a market before signal fires) requires the strategy to maintain per-market trader entry counts. This is implementable in strategy state, but:

- The strategy must track `{condition_id: set(traders_seen)}` and only emit TradeIntent when `len(traders_seen) >= N`
- This means the strategy must inspect the `maker` field of each trade and compare against the pool list
- This is doable but requires the strategy to hold the full pool list as state, not just a feature from the provider

**No harness change required** — the runner handles per-trade state correctly. This is a strategy implementation concern.

### In-play latency: harness cannot model 58-minute lead time

The tick-by-tick runner uses `trade.published_at` as the clock. The in-play strategy would copy a trade at the moment it appears in the feed. In production, there's a 58-minute observed lead time (bot enters, then market moves). The harness correctly simulates the copy trigger at the moment the observed trade arrives — no look-ahead bias — but:

- The vectorized analysis implicitly assumes the copy fires at the same advantageous price the original trader got
- In tick-by-tick, we fill at the next available price after the signal fires (which may be worse due to price impact)
- For in-play (sure-thing regime), the market is already at 0.85-0.99, so there's little room for slippage. But for longshot regime (<0.30), the gap between bot entry and our copy can be significant

**Expected extra degradation for in-play track: 10-20pp beyond the normal 20-40pp band** (consistent with copy_vs_pooling.md note of 50-60pp total degradation). The results doc states this explicitly and correctly.

### Settlement: correct

`_settle_market()` fires when `res_time <= now` (chronological loop). Resolution rows loaded from `data/research/` Parquet snapshot. Settlement rate should be >95% for a well-constructed universe. If the universe is built from the test-period condition_ids, all should have resolutions in the snapshot.

### Capital recycling: correct in harness.py

`strategy_budgets=None` is already set in `run_fast_backtest()` — the `ExecutionGateway` won't block fills due to cumulative spend tracking. Capital gates are enforced via `check_risk_gate()` per the StrategyConfig. This is correct.

---

## 4. Critical Issues for Tick Validation

### Issue 1 (BLOCKING): config.toml is not filled in

The config must specify actual strategy names, provider names, and parameters before `pm-harness` can run. This is not a harness bug — the researcher must complete the config.

### Issue 2 (HIGH): Capital allocation likely too tight

With `capital_usd=1000` and `max_open_positions=20` for K=50 N=1 (766 signals/month, 18h hold), the position cap will reject ~70-80% of signals. This will produce artificially low fill counts and poor PnL. Recommend:
- Per-track capital: $3,000-$5,000 per track
- max_open_positions: 50 per track
- Run tracks as separate strategy instances

### Issue 3 (MEDIUM): Provider training window look-ahead risk

If the provider uses `datetime.now()` to bound training data (as the TagHRProvider did), the walk-forward contamination is severe — 42% of training data would be post-test-period. The researcher must confirm the provider implementation is passed `replay_start` as `train_end`.

### Issue 4 (MEDIUM): In-play expected degradation is 50-60pp, not 20-40pp

The standard degradation band does not apply to in-play copy. If tick results show 35-40pp degradation for in-play, flag it as suspicious (potentially optimistic) rather than normal. If >60pp, investigate latency/slippage model.

### Issue 5 (LOW): Walk-forward Spearman values are numerically invalid

Values outside [-1, 1] indicate insufficient intersection between fold pools. The stability ranking derived from Spearman should be disregarded for Elections and Sports high-K scenarios.

---

## 5. Recommendations for Validation Setup

1. **Complete config.toml** with 3 separate strategy blocks (one per track), each with appropriate capital allocation and params
2. **Verify provider does not use `datetime.now()`** — must accept `train_end` parameter from harness
3. **Set test universe** to January 2026 condition_ids only (same as discovery test period)
4. **Validate first for K=200 N=2 consensus** (the highest-signal, highest-excess-HR candidate with $3,582/month vectorized PnL) — this is the most likely to survive degradation
5. **Validate in-play track separately** with explicit expectation of 50-60pp degradation
6. **Do not validate Elections tag** — insufficient signal count (K=25: 66 signals in Fold 3, only 1 in Fold 1)

---

## 6. Harness Changes Required

**None.** The current `harness.py` and `sync_replay.py` are correctly configured:
- `strategy_budgets=None` (capital recycling works)
- `executor = SimulatedExecutor` in `run_fast_backtest` — this needs to be overridden to `RealisticFillSimulator` for validation (the config says `executor="realistic"` but `run_fast_backtest` always uses `SimulatedExecutor`)

**Wait — this is a harness bug that needs investigation:**

`run_fast_backtest()` in `harness.py` (line 250) hardcodes `SimulatedExecutor(fee_pct=0.0)` regardless of the config's `executor` setting. The `config.toml` says `executor = "realistic"` and `fill_model = "calibrated_slippage"` but the fast backtest path ignores these fields entirely.

The `pm-harness` CLI (which reads config.toml) may handle this correctly — but if the researcher calls `run_fast_backtest()` directly (as the harness.py docstring suggests), they will silently get simulated fills, not realistic fills. This is a **harness fidelity gap**.

**Recommendation**: Add a warning when config specifies `"realistic"` executor but `run_fast_backtest()` is called without a `fill_config`. Or accept a `harness_config` parameter that auto-selects the executor.

This is a generic improvement (not strategy-specific) and within architect's owned files scope. Will flag to team-lead for prioritization — not implementing without explicit request.
