# Skeptic Review: tag-hr-copy (Round 1)

## Checklist Results

### 1. Look-ahead Bias: PASS (with one structural concern)

Walk-forward is correctly implemented: trainer qualifies traders on `[train_start, train_end)`, signals are evaluated on `[test_start, test_end)` with no overlap. `resolved_at` is the resolution timestamp, not trade time — this is a boundary use: resolved markets were visible to traders during the training window, which is legitimate.

One structural concern: the `get_base_rate()` function computes the tag YES base rate from `maker_positions_resolved_corrected` filtered by `resolved_at` within the training window. This is correct (training-window-only), and is passed as the threshold in `qualify_traders_*`. Confirmed clean.

**Directional correctness computation is fragile but not look-ahead**: In `compute_signals_directional()`, `any(position)` picks an arbitrary position for the market-level direction aggregation when multiple qualified traders entered opposite sides of the same market (line 156-160). This is not look-ahead; it is incorrect aggregation — see Additional Concerns below.

### 2. Survivorship Bias: PASS (minor concern flagged)

Only resolved markets (`resolved_at` exists) are included, which is appropriate for HR evaluation. The Esports universe is a nascent tag (most markets post-2025). The 13%-YES base rate in Jan 2025 and 51% in Jan 2026 indicate rapid market growth and composition shift, not retroactive filtering.

One concern: requiring `min_trades >= 50` in an early-stage tag necessarily selects traders who were active very early and had already accumulated 50+ resolved Esports positions in a thin market. These traders may have structural advantages (market maker relationships, informational edge during low-competition phase) that will not persist as the tag matures and competition increases.

### 3. Edge Above Base Rate: PASS for Esports/Tennis/1H; FAIL for Basketball

**Esports BUY-only (top combo)**: HR=77.2%, base rate varies 13-51%. In the reported period, average base_rate ≈ 43%. Excess ≈ +34pp. This is extremely large — see Additional Concerns.

**Tennis BUY-only**: HR=55.7%, Tennis base rate reported as 27% YES. Excess ≈ +28.7pp. Well above 5pp threshold.

**1H BUY-only**: HR=73.4%, base rate 50%. Excess ≈ +23.4pp. Clear excess on a near-random baseline.

**Basketball BUY-only**: HR=41.4%, base rate 45%. **Excess = -3.6pp**. The strategy is BELOW the Basketball YES base rate. Despite appearing in the top 5, this is a losing directional bet. The compounding score of 73.7 is entirely driven by `avg_pnl = $86.52`, which the notes themselves flag as whale-dominated. On median_pnl=$0.86, the compounding score collapses to ~0.73. This combo should NOT be in the top 5.

> [!CRITICAL]
> **Basketball BUY-only has negative excess HR (-3.6pp) against the 45% YES base rate** (`scripts/sweep.py` line 78: `base_rate + excess_threshold / 100.0`). The qualification threshold of `hr >= 0.45 + 0.15 = 0.60` is being applied to the training data, but the test HR of 41.4% is below base rate. This means the signal is not generalizing. The "rank 2" position for Basketball BUY-only is driven entirely by whale PnL contamination in `avg_pnl`. Basketball BUY-only must be excluded or demoted to INVESTIGATE status.

> [!CRITICAL]
> **Compounding score uses `avg_pnl` not `median_pnl` as the edge measure** (`scripts/sweep.py` line 274: `cs = compounding_score(avg_excess, avg_pnl, avg_hold)`). For Basketball, avg_pnl=$86.52 vs median_pnl=$0.86 — a 100x difference. The compounding score of 73.7 is a statistical artifact of whale outliers. All compounding scores computed with `avg_pnl` are upper-bounded by the right tail, not the expected deployable edge. This affects all tags, not just Basketball.

### 4. Sample Size: WARNING

**Esports top combo**: n_folds=3 (not 5 — two folds skipped, presumably because Esports was thin pre-2025). Total signals = 1,825 across 3 folds. 608 signals/fold. This is adequate for overall HR, but:

**Sensitivity.json reveals a deeper problem**: The Esports_50_15 sensitivity uses only `n_folds=2` for all perturbations. With 2 folds, stability assessment is unreliable. The reported fragile=false in results.json (max_hr_drop 7.7pp) contradicts sensitivity.json which marks `fragile: true` with `max_hr_drop: 0.0766`. There is a **discrepancy** between the two files.

**Basketball top combo**: n_folds=2 only. Two-fold averages are not stable estimates. Any single bad month wipes the signal.

> [!WARNING]
> **Esports and Basketball top combos use n_folds=2 in sensitivity analysis**, which is insufficient to assess stability. The fragile/not-fragile label in results.json may be incorrect. The sensitivity baseline HR=82.3% (sensitivity.json Esports_50_15) does not match the reported avg_hr=77.2% in results.json for the same combo. These files appear to have been produced by different runs or parameter configurations.

> [!WARNING]
> **Basketball top combo has n_folds=2** across all evaluated combos. Any two-fold result is highly sensitive to which two months were selected. With Basketball's seasonal structure (NBA playoffs, off-season), two folds may represent the same season phase twice.

### 5. Walk-Forward: PASS (with implementation bug)

Walk-forward is correctly structured: 5 monthly folds with non-overlapping train/test splits, 6-month lookback windows. The lookback_months parameter in the grid (3, 6, 12) is defined but **not actually used** to adjust the training window — `FOLDS` are hardcoded and `lookback_months` only appears as a combo key dimension. All reported results at `lookback_months=3` vs `lookback_months=12` use the same training data (the hardcoded `FOLDS`). This means the lookback sensitivity sweep is meaningless — it produced the same results three times.

> [!WARNING]
> **`lookback_months` parameter in the grid is non-functional** (`scripts/sweep.py` line 215-221). The comment says "FOLDS already define 6-month windows; just use excess_pp threshold" but this means 3x redundant computation per combo, and results.json aggregates across these duplicate folds as if they were independent. Total signal counts are tripled. `signals_per_fold` is inflated by 3x for all combos where `n_folds` is a multiple of 3.

### 6. Degradation Band: N/A (Round 1)

No tick-by-tick validation exists yet.

---

## Additional Concerns

> [!CRITICAL]
> **Directional aggregation bug in `compute_signals_directional()`** (`scripts/sweep.py` lines 156-160). When multiple qualified traders hold different positions on the same market (one YES, one NO), `any(position)` returns an arbitrary trader's position. The market-level `correct_dir` is then computed as if ALL qualified traders held that one arbitrary position. This corrupts directional HR. The correct approach: aggregate YES and NO positions separately, count a market as "correct" if the majority-direction traders were correct, or count each (trader × market) separately then deduplicate at market level. For BUY-only (position='YES' filter), this bug does not apply — only affects the directional variant.

> [!CRITICAL]
> **Esports base rate non-stationarity creates qualification threshold drift**. The `get_base_rate()` uses the training window to compute the threshold. In fold 1 (train Jan 2024-Jan 2025), base_rate=13% → threshold = 13%+15% = 28%. In fold 5 (train Jul 2025-Jan 2026), base_rate=51% → threshold = 51%+15% = 66%. Traders qualified in fold 1 needed only 28% YES HR; in fold 5, they needed 66%. The same trader population produces wildly different qualification outcomes across folds. This is the correct behavior for a walk-forward — but it means the 77.2% avg_hr is averaging across three fundamentally different market regimes and qualification thresholds. The individual-fold HRs must be inspected to verify this is not averaging a strong early signal with a weaker later one.

> [!WARNING]
> **BUY-only selection bias warrants explanation, not just a recommendation**. Esports BUY-only HR=77.2% vs directional HR=42.2% is a 35pp gap. The notes attribute this to "SELL trades are exits, not directional." That is correct — but the 35pp gap is also consistent with another interpretation: the YES base rate in Esports recently shifted from 13% to 51%. Traders who accumulated YES positions early (when YES rarely won) and survived to qualify at mt=50 are being tested in a period when YES base rate is 51%. The "edge" partially reflects a base rate regime change, not persistent skill. This must be verified by checking per-fold excess HR against per-fold base rate.

> [!WARNING]
> **NCAA anti-predictive signal is unexplained**. BUY-only HR=18.6% against a 30% base rate is -11.4pp excess — far below random. The notes conclude "qualified traders win on NO, not YES." But this is structurally odd: the qualification criterion already required traders to have positive HR in the training window. If they had high HR on YES in training but 18.6% on YES in test, this is massive out-of-sample degradation, not just a direction issue. Either (a) NCAA YES base rate is much lower than 30% in the test period, (b) the training HR reflects very few markets and is noise, or (c) there is a look-ahead artifact specific to NCAA that deserves investigation before classifying this as a clean no-go.

> [!TIP]
> **`compounding_score()` uses `avg_pnl` throughout** (`scripts/sweep.py` line 274). Replace with `median_pnl` for a deployment-realistic estimate. This would correctly demote Basketball (CS collapses from 73.7 to ~0.73) and provide a stable ordering of the remaining tags.

> [!TIP]
> The sensitivity baseline for Esports_50_15 in `sensitivity.json` shows `avg_hr=0.8226` vs `results.json` `avg_hr=0.7718`. This 5pp discrepancy suggests sweep.py and the sensitivity analysis (sweep2.py?) were run against different data slices or fold sets. Both files should be reproduced from the same run.

---

## Summary

The walk-forward structure is sound and look-ahead bias is not present in the core train/test split. However, four issues require resolution before this can advance to tick-by-tick validation.

**BLOCKING**: (1) Basketball BUY-only has negative test excess HR (-3.6pp) and must be removed from the go list — its ranking is driven entirely by whale `avg_pnl`. (2) The directional aggregation bug corrupts directional HR when multiple traders hold opposite positions on the same market; directional results are unreliable. (3) The `lookback_months` parameter is non-functional, tripling redundant computation and potentially inflating `n_folds` counts.

**HIGH CONCERN**: The Esports 77.2% BUY-only signal is plausible but the per-fold breakdown is missing from the report — averaging across three folds with base rates of 13%, 34%, and 51% may be masking a decaying signal or a structural regime flip rather than persistent skill. The sensitivity discrepancy (82.3% vs 77.2%) between files must be resolved.

**PROCEED**: Esports BUY-only (after per-fold verification), Tennis BUY-only, and 1H BUY-only all show positive median_pnl and positive excess HR. These three are candidates for tick-by-tick validation, with the caveat that 20-40pp vectorized degradation is expected and Esports may land below zero edge post-tick.
