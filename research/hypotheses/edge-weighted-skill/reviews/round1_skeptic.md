# Skeptic Review: edge-weighted-skill (Round 1)

> **ALL RESULTS ARE UPPER BOUNDS** (vectorized). Tick-by-tick validation has NOT been run yet.

---

## Checklist Results

### 1. Look-ahead Bias: PARTIAL FAIL

**Walk-forward script** (`scripts/walkforward_stability.py`): Clean. `train_end` equals `test_start` on all three folds (lines 44-65). The phantom-signal filter `first_trade >= test_start` is applied in `evaluate_oos()` (line 431, 455). Base rate for test period is computed from the test window, not training window. **PASS for walk-forward.**

**Decomposition sweep** (`scripts/decomposition_sweep.py`): The base rate grid is built over ALL resolved positions with no temporal split (lines 170-195). Trader scores are then compared against that same all-time grid. This is acceptable for a global characterisation sweep but it means bucket_excess_hr values are computed in-sample relative to a population that includes the trader's own trades in the base rate denominator. For prolific traders (e.g. 0xfc25f... with 3,533 positions), their own history inflates the base rate they are measured against, systematically biasing their bucket_excess_hr towards zero. The bias direction disfavours top traders, so it is conservative — but the magnitude is unknown and uncorrected.

**Copy vs pooling script** (`scripts/copy_vs_pooling.py`): Pool is built from positions with `first_trade < 2026-01-01` (line 175). Test evaluates `first_trade >= 2026-01-01 AND first_trade < 2026-02-01` (lines 369-370). The train/test split is correct.

> [!WARNING]
> `decomposition_sweep.py` lines 170-195: base rate grid uses ALL-TIME data to score ALL-TIME trader performance. A trader with 3,533 positions (`0xfc25f...`) influences their own base rate bucket, suppressing their apparent edge. The bias direction is conservative but introduces unknown measurement error in bucket_excess_hr. The decomposition results should be treated as approximate rankings, not precise edge estimates.

**Consensus entry price** (`copy_vs_pooling.py`, line 626): Signal price is `AVG(entry_price)` across all pool traders in the market, not the Nth trader's price. The script acknowledges this (line 1225) but uses it anyway for PnL simulation. For K=200 N=2 — the best-performing configuration — the "signal" fires when the 2nd trader enters, but PnL is computed at the average of ALL 200 pool traders' prices, including those who entered before and after the signal. This is a look-ahead: traders who enter after the signal trigger are included in the fill price calculation.

> [!CRITICAL]
> `copy_vs_pooling.py` lines 626, 647-649: Consensus PnL uses `AVG(entry_price)` over all pool traders in the market, not the Nth-trader trigger price. For K=200 N=2, this averages prices of traders who may have entered days or weeks before or after the actual signal. The fill price is partly determined by future information (traders who enter after the signal fires). The K=200 N=2 result ($3,582/month, 63.7% HR) is the sole positive-PnL consensus configuration and rests on this flawed fill assumption.

### 2. Survivorship Bias: PASS with caveats

All analyses require `resolved_at IS NOT NULL` — markets without resolution are excluded. This is appropriate: unresolved markets cannot be evaluated. The test period is January 2026 with `resolved_at` in that window, ensuring markets resolved after the test window are excluded.

However, the qualification filter `count(*) >= 20` (decomposition) or `count(DISTINCT condition_id) >= 20` (walk-forward) selects only traders who have already accumulated sufficient history. Traders who started trading in Q4 2025 would be excluded from all pools even if they have genuine skill. This is reasonable but slightly biases the pool toward established accounts.

> [!TIP]
> The minimum 20-position filter systematically excludes new high-skill traders. Consider a smaller minimum (10 positions) for in-play specialists who concentrate heavily in a specific tag.

### 3. Edge Above Base Rate: MIXED

**Copy strategies (Edge-weighted, K=50/100):**
- edge_copy_k50: 49.9% HR, base rate 38.8% → excess = +11.1pp
- edge_copy_k100: 51.1% HR, base rate 38.8% → excess = +12.3pp

Both exceed the 5pp threshold. However PnL is marginal ($359 and $665/month) and the methodology note says "+1pp slippage" is the entire cost model. No market impact, no bid-ask spread, no failed fills at the stated price. The real slippage for copying strategies is likely higher.

**Consensus K=200 N=2:** 63.7% HR, excess = +24.9pp. This is the headline number. But see Critical above — the fill price is contaminated by look-ahead averaging.

**In-play K=10:** 90.9% HR, excess = +52.1pp. But only 121 signals, average hold = 3.1h. This excess is dominated by the sure-thing regime (see below).

> [!CRITICAL]
> The in-play copy track computes excess HR against the overall YES base rate (38.8%) but the distribution shows sure-thing positions (>=0.85) at 96.3% base rate make up a large fraction of in-play signals. The copy_vs_pooling_results.md (Part C) shows inplay_copy_k10_n1 has "8/100%" in the "Sure" column format, meaning sure-thing count/HR. Without explicit decomposition of in-play signals by regime, the +52.1pp excess for inplay_k10 is an artifact of the pool being composed almost entirely of sure-thing traders trading in-play markets — a pool that will have 96%+ HR regardless of skill. This is the "HR baseline pool = in-play sure-thing pool" problem noted in the team context.

**Walk-forward:** All methods show positive excess across all folds for Politics, Sports, Crypto (all excess values in walkforward_results.md are positive). This is genuine.

> [!WARNING]
> Walk-forward base rate is computed from YES positions only (line 179: `p.position = 'YES'`). The decomposition sweep explicitly shows NO-skill dominates (51% of traders are NO-skilled vs 12.6% YES-skilled). The walk-forward analysis exclusively evaluates YES direction and thus ignores the dominant signal direction. All walk-forward stability conclusions apply only to YES-direction, which is the weaker signal.

### 4. Sample Size: MIXED

**Walk-forward fold 1 (Elections tag):** F1 Signals = 1 for all methods and K values (walkforward_results.md lines 71-79). A single-signal fold produces HR = 1.000 or 0.000 and makes the Elections stability analysis meaningless.

> [!CRITICAL]
> Elections tag in walk-forward fold 1 has exactly 1 signal across ALL methods and K values. The Elections HR σ = 0.283-0.331 is driven entirely by this single-signal fold collapsing. No stability conclusions can be drawn for Elections. The "best method = composite (σ=0.273)" for Elections K=25 is statistically meaningless.

**In-play K=10:** 121 signals for one month. Marginally above the 100-trade minimum. The 90.9% HR from 121 signals has a 95% CI of approximately ±5pp, meaning the true HR could be 85.9%-95.9% — still impressive but noisier than reported.

**edge_inplay_k25_n1:** 16 signals. This is far below 100.

> [!CRITICAL]
> `edge_inplay_k25_n1` reports 16 signals in the test month. This is a critical minimum violation (16 << 100). The 81.2% HR and $652 PnL from 16 signals have no statistical validity. This configuration should not appear in any conclusion or strategy recommendation.

**Edge copy K=5 and K=10:** 165 and 167 signals respectively. Adequate count but both show negative PnL (-$1,859 and -$1,940).

**Consensus K=50 N=3:** 177 signals. Borderline adequate.

### 5. Walk-Forward: PARTIAL PASS

Walk-forward is implemented and correctly separated (train_end = test_start). Three folds covering July 2025 to April 2026 (open-ended for fold 3).

However, a critical methodological flaw undermines the stability comparison:

> [!CRITICAL]
> The walk-forward script (`walkforward_stability.py` lines 159-203) computes `bucket_excess_hr` using price bucket base rates derived from within-fold training data (lines 253-295). However, the training query at lines 159-176 uses `CAST(p.yes_won AS DOUBLE) AS correct` mapped from `yes_won` — which is the resolution outcome. There is no check that `resolved_at < train_end` in the base rate computation for the `_wf_bucket_` table. If any positions in the training join have `first_trade < train_end` but `resolved_at >= train_end` (positions open at fold boundary), their resolution outcome is included in the training score. These are futures-resolved positions evaluated in training, creating subtle look-ahead.

> [!WARNING]
> The walk-forward summary (walkforward_results.md) reports "Best stability = hr_only" for Politics K=25 but the detailed Key Findings section (lines 104-115) says "Best stability = composite" — the summary table and the key findings text disagree for Politics K=25. This inconsistency suggests the stability ranking function has a bug: the summary table uses `sigma_score + retain_score + spearman_score` (line 821) but the Key Findings text uses only `min(hr_std)`. The "best method" conclusion depends on which metric is prioritised and the two metrics give different answers.

**Spearman rank correlations** are concerning: many are negative (e.g. Politics K=25 hr_only: -1.191; Sports K=100 hr_only: -102.591). The Spearman implementation in `compute_spearman_rank_correlation()` (lines 508-528) uses a simplified formula `1 - 6*sum(d^2) / (n*(n^2-1))` that can exceed [-1, +1] for tied ranks or small n. Values of -102 are clearly impossible and indicate a bug in the Spearman computation or the rank lists passed to it.

> [!CRITICAL]
> `walkforward_stability.py` lines 508-528: The Spearman formula `1 - 6*sum(d^2) / (n*(n^2-1))` produces values outside [-1, +1] (e.g. -102.591 for Sports K=100 F2-F3, -141.5 for Elections K=100 F2-F3). This is mathematically impossible and indicates a bug — likely that `ranks_a` and `ranks_b` contain different traders (low overlap), so the formula's assumption that both lists cover the same n traders is violated. The correlation is computed between sets with different members, making `rank_a[t]` and `rank_b[t]` inconsistent relative to n. All Spearman values are unreliable and should not be used for stability conclusions.

### 6. Degradation Band: N/A (Round 1, no tick validation yet)

---

## Additional Concerns

> [!CRITICAL]
> **NO-direction completely excluded from copy and consensus tests.** The decomposition confirms 51% of qualified traders are NO-skilled vs 12.6% YES-skilled. The entire copy_vs_pooling analysis evaluates only YES direction (`p.position = 'YES'`, line 175 in copy_vs_pooling.py). The walk-forward is also YES-only. Half the available alpha signal is uncharacterised. Any strategy recommendation based on this discovery work is incomplete for production deployment, where NO-direction signals are arguably stronger.

> [!CRITICAL]
> **Single test month for all PnL conclusions.** Every PnL figure in copy_vs_pooling_results.md is derived from January 2026 only. January 2026 has a specific YES base rate (38.8%) and market composition. There is no cross-month PnL stability analysis. The K=200 N=2 configuration showing $3,582/month is a single-month observation. One month is insufficient to distinguish edge from noise at the signal counts available (761 signals for K=200 N=2).

> [!WARNING]
> **Conviction filter inconsistency.** The decomposition sweep uses `abs(net_usd) / volume >= 0.10` (10% conviction, line 154). The copy_vs_pooling script uses `conviction >= 0.50` (50% conviction, line 273) for pool building, then evaluates test positions from `maker_positions` without applying the same conviction filter. Positions in the test set may include low-conviction positions that would not qualify for the pool-building phase. The test universe is therefore broader than the training universe in terms of position quality.

> [!WARNING]
> **`avg_entry_price` for NO-direction positions.** In decomposition_sweep.py lines 134-137: NO entry price is computed as `1.0 - COALESCE(ye.avg_entry_price, 0.5)`. This assumes the YES entry price from `yes_entry_data` is available for every NO position. For traders who only trade NO and never touch YES tokens, `ye.avg_entry_price` will be NULL and entry price defaults to 0.5. The distribution of NO entry prices will be artificially clustered near 0.5 for pure-NO traders, distorting their bucket_excess_hr computation.

> [!WARNING]
> **`consistency` formula is ill-defined.** `decomposition_sweep.py` line 351: `consistency = 1 - hr_std / overall_hr`. For a trader with overall_hr = 0.05 and hr_std = 0.2 (possible for low-HR traders), this gives consistency = -3.0. There is no clamp. The walk-forward script uses a different consistency formula (monthly Sharpe). The two scripts use incompatible consistency definitions, so traders ranked by composite score in decomposition cannot be directly compared to traders ranked by composite score in walk-forward.

> [!TIP]
> The Jaccard=1.000 between HR-primary and Edge-primary is presented as a finding that "scoring method overlap: HR-primary and Edge-primary lists overlap 100%." The finding should be stated more precisely: the overlap is 100% because the top-100 are dominated by traders with 100% HR and very small N (55-28 positions). Any scoring that includes HR at any weight will select these traders. The Jaccard=1.0 is a sampling artifact of the 20-position minimum threshold, not evidence that the two methods are equivalent signals.

> [!TIP]
> The walk-forward's Elections tag should be dropped from all stability conclusions. With 1 signal in fold 1 and fewer than 132 signals in folds 2-3, it has insufficient power to evaluate any method. Including it in the "best method per tag" summary table misleads interpretation.

---

## Summary

The edge-weighted-skill hypothesis is structurally sound but the discovery phase has four blocking issues. First, the sole positive-PnL consensus configuration (K=200 N=2, $3,582/month) uses look-ahead in its fill price computation — AVG price across all pool traders rather than the Nth trigger price. Second, the Spearman rank correlation implementation is buggy and produces impossible values (-102), invalidating all pool stability conclusions based on that metric. Third, the in-play sub-results include configurations with 16 signals (edge_inplay_k25_n1) that are being compared with configurations with 121+ signals as if they are comparable. Fourth, the entire analysis evaluates only YES direction despite the decomposition showing NO-skill is 4x more prevalent. Before tick validation, the researcher must: (a) re-compute consensus PnL using the Nth trader's actual price, not the pool average; (b) fix the Spearman computation; (c) exclude sub-100-signal configurations from conclusions; and (d) run the parallel NO-direction analysis. The walk-forward does demonstrate positive OOS excess HR across all three folds for Politics, Sports, and Crypto, which is the genuinely useful finding — but it applies only to YES direction and uses the weaker signal (YES-skill, 12.6% of traders).
