# Skeptic Review: tag-hr-consensus (Round 1)

Reviewer: Skeptic agent
Artifacts reviewed:
- `discovery/results.json`
- `discovery/notes.md`
- `discovery/notebook.py`
- `scripts/sweep_duckdb.py`
- `README.md`

---

## Checklist Results

### 1. Look-ahead Bias: PARTIAL FAIL

Two look-ahead issues found, one critical and one latent.

**Issue A — Volume filter uses post-hoc market volume (CRITICAL).**

The sweep filters markets by `total_vol >= threshold` (vol_filter_usd = 1000). The `total_vol` in `_build_mkt_stats` is computed as `sum(abs(net_usd))` across all qualified-trader positions in the test window (`xs` to `xe`). At signal time — the moment the Nth qualified trader enters — the strategy does not know what the final accumulated volume will be. Later traders who enter after the signal fires are included in the volume total. A market that eventually reaches $1k total qualified-trader volume may only have $300 at signal trigger time.

This is not a minor filter: the volume filter is described as "the key unlocking variable" driving HR from ~30% to 75%+. If the filter is a look-ahead, the entire reported uplift from volume filtering is inflated.

Concretely: `_build_mkt_stats` aggregates over `tp_{variant}` which contains ALL qualified-trader positions in the test window for that market — including those that arrive after the Nth consensus trigger (i.e., `n_traders >= consensus_n + k` late arrivals). Their `net_usd` is summed into `total_vol` before the vol filter is applied in `_batch_combo_query` via `s.n_traders >= c.consensus_n`. There is no cap saying "only count volume from the first N traders as of signal time."

> [!CRITICAL]
> `sweep_duckdb.py` lines 225-240: `_build_mkt_stats` sums `abs(net_usd)` over ALL qualified traders in the test window, not just those present at signal time (max(first_trade) of the Nth trader). The vol filter in `_batch_combo_query` (line 136) is applied against this post-hoc total. Markets that were below the vol threshold at signal time but eventually crossed it due to later entrants are incorrectly included. Since volume filter is the dominant HR predictor (+45pp uplift reported), this inflates the entire volume-filtered result set. Must recompute volume as of the signal timestamp.

**Issue B — Entry price uses blended average across the full test window (WARNING-level look-ahead).**

`ep_{variant}` in `_build_mkt_stats` (lines 212-221) computes `avg_ep` as a volume-weighted average across all of the trader's trades in the test window. At signal time, the strategy only knows prices from trades that have already occurred. For traders who entered in multiple tranches, later-tranche prices contaminate the blended average. The `price_ceil` filter (signal-level: `avg_ep <= price_ceil`) is therefore applied to a price that is partially future. This is a weaker look-ahead than Issue A but still present.

---

### 2. Survivorship Bias: PASS (with note)

The sweep uses `maker_positions` filtered by `resolved_at` date bands — only resolved markets are included, which is correct and necessary. The universe definition via `tag_mkts` pulls ALL markets for the tag without additional post-hoc filtering beyond resolution.

One note: the notebook cell `hold_time_distribution` adds `AND hold_h <= 48` as an additional filter (line 139). This is a MAX_HOLD_HOURS filter on the test set, not training. This correctly represents deployment intent — positions that don't resolve within 48h would be closed — so it is not survivorship bias. However, markets resolved at exactly 0h hold (instantaneous resolution) are included (`hold_h >= 0`). These are degenerate cases where `max(first_trade) == resolved_at` and should be verified they are not data artifacts.

---

### 3. Edge Above Base Rate: PASS (but non-stationary base rate is a structural problem)

The reported excess HR is large enough that even substantial measurement error would leave a positive signal. For the recommended Esports combo (N=5, W=inf, vol>=1k, ep>=10pp): reported HR = 82.3%, excess = 33pp. Even discarding the volume look-ahead (which we cannot yet quantify), 33pp excess is not explained by trivial errors.

Tennis is thinner: reported HR = 84.8%, excess = 48.3pp, but on only 82 total signals across 3 folds (27/fold). This is too thin to estimate excess reliably.

The base rate non-stationarity is a structural threat to the excess calculation:

> [!CRITICAL]
> The Esports base rate varies from 10% (2025-01 fold, n=10) to 65.4% (2025-10 fold, n=1441) across folds. The sweep computes `base` from the TEST window only (lines 334-351 in `sweep_duckdb.py`). This is correct in principle — per-fold base rates avoid leaking future class distributions. However, the 2025-10 fold with base=65.4% is extraordinary. At that base rate, any strategy that enters YES positions has a 65% unconditional HR. The 87% HR in that fold may reflect only a 22pp excess over a very high base — not the 38pp excess implied by pooling across folds. The results.json reports avg_hr and avg_excess pooled across folds without weighting by base rate variance. A fold where base=65% is not comparable to a fold where base=37%. Cross-fold excess arithmetic is suspect.

Additionally, the `correct` field used in pool qualification (`sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)`) is derived from `maker_positions_resolved.parquet`. Per `pitfalls/split_position_blind_spot.md`, `realized_pnl` (and therefore the `correct` flag) is wrong for ~12% of positions due to invisible split-minted tokens. This biases pool membership toward traders who happen not to use the split route — not toward genuinely skilled traders.

> [!WARNING]
> Pool qualification in `_build_qual_table` (line 156) uses `p.correct` from `maker_positions_resolved.parquet`. Per `pitfalls/split_position_blind_spot.md`, `correct = realized_pnl > 0` is unreliable for ~12% of maker (trader, asset_id) pairs due to split-route PnL miscalculation. Bots using the split route may be incorrectly excluded; informed traders using the split route may be incorrectly included (or vice versa). The pool composition is partially random noise for split-route traders.

---

### 4. Sample Size: PARTIAL FAIL

**Esports recommended combo**: 628 signals across 3 folds = ~209/fold. PASS at the aggregate level.

**Tennis recommended combo (vol>=2k)**: The results.json reports this as the Tennis recommendation with `expected_hr_upper_bound: 90.9%` but this specific combo does not appear in the top-5 Tennis results. It is described only in notes.md with `HR=90.9% (6pp higher than vol>=1k)`. No signal count is given for this combo. The closest reported Tennis result is N=5, W=8h, vol>=1k with 82 signals (27/fold).

> [!CRITICAL]
> The Tennis validation recommendation uses vol>=2k as params (results.json, `recommended_for_validation.Tennis`) with `expected_hr_upper_bound: 90.9%`, but this combo's signal count is not reported anywhere in the artifacts. The vol>=1k Tennis combo already has only 82 signals (27/fold). Doubling the vol threshold will further reduce this count — potentially below 50 total signals across 3 folds. A sensitivity analysis shows vol*2 adds 6.1pp HR, but if it halves the market count, the statistical basis collapses. A 90.9% HR on 30-40 markets is not a robust estimate.

**Early folds excluded**: Only 3 of 5 folds have data. The fold exclusion threshold of n<10 (line 349) is applied correctly, but this means all statistics rest on a 29-month window starting effectively mid-2025. The premortem correctly flagged this; no resolution is offered in the discovery artifacts.

> [!WARNING]
> Esports fold 2025-07 (n=223) carries equal weight to fold 2026-01 (n=13,538) in the cross-fold average. The smallest included fold is 61x smaller than the largest. If the avg_hr is a simple fold-average (which it is — see `sweep_duckdb.py` lines 411-416), the 223-market fold has 61x the influence per signal. The reported HR of 82.3% could be dominated by the high-base-rate 2025-10 fold and the smallest data fold. Should weight by signal count or report per-fold HR alongside the aggregate.

---

### 5. Walk-Forward: PASS (with structural caveat)

The sweep implements a clean walk-forward design: 6-month train window, 1-month test window, pool built exclusively on training data, evaluated on out-of-sample test data. The critical constraint (`first_trade >= xs` in test positions, line 208) correctly prevents phantom signals from positions that resolved in the test window but were entered before it. This addresses a known pitfall.

One structural caveat: the fold windows overlap in training. Folds share training data:

```
Fold 3: train 2025-01 to 2025-07, test 2025-07 to 2025-08
Fold 4: train 2025-04 to 2025-10, test 2025-10 to 2025-11
```

Folds 3 and 4 share the training period 2025-04 to 2025-07. Qualified traders identified in fold 3's training window partially overlap with those in fold 4. This is a rolling-window design (not expanding), which is reasonable, but means folds are not independent. The reported "3 folds" of evidence represents fewer independent data points than it appears. This does not invalidate the approach but overstates confidence.

---

### 6. Degradation Band: N/A

Round 1 — no tick-by-tick validation exists yet. The discount estimate of 20-40pp is applied qualitatively in expected_hr_tick_estimate_range but cannot be verified. Flag for Round 2 review.

---

## Additional Concerns

> [!CRITICAL]
> **Volume filter look-ahead is the most serious threat.** The entire HR uplift attributed to the volume filter (the dominant finding: 30% HR without filter vs 75-87% with filter) is measured on post-hoc accumulated volume, not volume available at signal time. The true deployable signal may show no relationship between live-observable volume at signal time and eventual HR. This must be fixed before tick-by-tick validation: compute `signal_volume` as `sum(abs(net_usd))` over only the first N qualified-trader entries (those whose `first_trade <= max_first_trade_of_Nth_entrant`), not all entrants in the test window.

> [!CRITICAL]
> **Tennis volume>=2k recommendation is unsupported by reported data.** The recommended validation params for Tennis (vol>=2k, ep>=10pp) claim 90.9% HR upper bound but provide no signal count. Before moving to tick-by-tick validation with these params, the sweep must be rerun with vol=2000 and results reported including n_signals. If n_signals < 50 across 3 folds, the Tennis arm should be parked.

> [!CRITICAL]
> **Esports base rate non-stationarity is not handled in the aggregation.** The fold with base=65.4% (2025-10) is an outlier and inflates pooled excess HR. The strategy must demonstrate positive excess HR in EACH fold independently, not just on average. The results.json does not report per-fold HR — only averages. Per-fold HR tables must be produced before the signal is validated.

> [!WARNING]
> **The SELL variant "insensitive" conclusion is premature.** results.json reports `sell_sensitivity_pp: 0.0` and concludes BUY-only and directional are "mathematically equivalent." This is correct IF `maker_positions.position = 'YES'` captures net long YES regardless of route. However, per `pitfalls/split_position_blind_spot.md`, net position accounting is corrupted for ~12% of positions. A trader who went long YES via SELL NO (split route) may have `position = 'NO'` or `position = 'HEDGED'` due to the invisible split cost, and would be excluded from the BUY-only filter. The 0pp difference may reflect data corruption masking a real difference rather than genuine insensitivity.

> [!WARNING]
> **Bot guard threshold of 10,000 is extremely permissive.** `BOT_GUARD = 10_000` (line 54) excludes only traders with 10,000+ positions in a tag during the training window. But the notes.md describes the top 10 bots as trading 1,557–3,341 markets per month. A 6-month training window at 3,341/month = ~20,000 positions — these bots ARE caught. However, bots trading 500–1,500 markets/month (6-month total: 3,000–9,000) pass the bot guard. The proposed `esports_bot` classification uses a 500-trade threshold with HR<20%, which is substantially stricter. The current sweep's bot guard does not match the proposed classification.

> [!WARNING]
> **The `coalesce(ep.avg_ep, 0.75)` default for traders without entry price data** (lines 157, 176, 234) silently assigns a 0.75 entry price to any trader who has no records in `yes_entry_data`. This default is above the most permissive `max_pool_ep` threshold of 0.90, so such traders are included in all pool variants. If traders missing from `yes_entry_data` are systematically different (e.g., they entered via split route only, making them invisible to YES-side trade aggregation), this default admits a potentially biased sub-pool without flagging it.

> [!WARNING]
> **Compounding score denominator instability.** `cs = excess_hr * avg_mp / hold_days` where `hold_days = avg_hold_h / 24`. When `avg_hold_h = 2.0h`, `hold_days = 0.083`, making the CS divisor tiny and the score astronomically sensitive to small changes in hold time. A 30-minute increase in average hold time (2.0h -> 2.5h) drops hold_days from 0.083 to 0.104 — a 25% CS reduction. The reported CS values (57-87) are dominated by this denominator and should not be used for ranking without hold-time confidence intervals.

> [!TIP]
> The `_batch_combo_query` uses `greatest(count(), 1)` in the HR denominator (line 127) to avoid division by zero, but this means a combo with 0 signals gets HR=0.0, not NULL. The aggregation function `agg()` then filters `ns < 5` (line 394), which should catch most zero-signal combos. Confirm the `ns >= 5` guard is applied before any combo appears in the output to prevent zero-signal combos from polluting averages.

> [!TIP]
> The fold aggregation averages `avg_hr` as a simple mean of fold HRs (line 412), but fold sizes differ by 61x. A signal-count-weighted average would be more representative of true out-of-sample performance. Consider also reporting the standard deviation of per-fold HR as a robustness indicator alongside the mean.

---

## Summary

The core hypothesis is sound and addresses the root cause of tag-hr-copy's failure correctly. The walk-forward design is properly implemented with out-of-sample evaluation. However, three issues are severe enough to block promotion to tick-by-tick validation in their current form.

The most critical is the volume filter look-ahead: the entire +45pp HR uplift from the vol>=1k filter is measured on total volume accumulated after the signal fires, not volume observable at trade time. Since volume is described as "the key unlocking variable," this single flaw could eliminate the signal entirely if corrected. The sweep must be rerun computing signal-time volume only (sum over traders with `first_trade <= signal_time`).

Second, the Esports base rate non-stationarity means the pooled 82.3% HR figure conflates folds with base rates ranging from 37% to 65%. Without per-fold excess HR tables, it is impossible to verify the signal is positive in each fold independently rather than averaging across a high-base-rate outlier.

Third, the Tennis validation recommendation (vol>=2k, 90.9% HR) rests on an unreported signal count that is almost certainly below 50 — too thin for statistical inference. The Tennis arm requires a separate sweep run with vol=2000 before it can be validated.

The `correct` field contamination from split-route PnL errors, the permissive bot guard, and the coalesce default for missing entry prices are secondary concerns that bias pool composition but are unlikely to reverse the signal entirely if the volume look-ahead is fixed.

**Recommendation: Do not proceed to tick-by-tick validation until the volume filter look-ahead is corrected and per-fold HR tables are produced. Esports can proceed after those fixes. Tennis requires an additional sweep run with vol=2000 reporting signal counts.**
