# Skeptic Review: score-axis-pool-construction (Round 1)

**Date**: 2026-03-11
**Reviewer**: Skeptic agent
**Artifacts reviewed**:
- `discovery/results.json`
- `discovery/analysis.md`
- `discovery/notes.md`
- `scripts/sweep_score_axis.py`
- `reviews/premortem.md`

---

## Checklist Results

### 1. Look-ahead Bias: CONDITIONAL PASS

The sweep correctly separates training and test windows using `TRAIN_CUTOFF = TEST_START = "2025-07-01"`. Pool construction uses `CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'` (lines 141-143 of sweep script), and test signal evaluation uses `CAST(p.first_trade AS DATE) >= '{TEST_START}'` (line 342). This prevents phantom test signals: a market where Pool A or Pool B traders entered before the test cutoff cannot contribute a test signal.

One residual concern: the price-level base rate computation (lines 482-496) uses all Sports YES positions in the test window without distinguishing pool membership. This is correct — it is the population HR, not the signal HR. No look-ahead detected here.

However, the pool quality metrics (`pool_a_avg_excess_hr`, `pool_b_avg_consistency_sharpe`) reported in `results.json` pool_stats block are computed from the *training* window traders. The issue is that the consistency_sharpe Sharpe formula uses a +0.05 additive dampener in the denominator (`mean_hr / (std_hr + 0.05)`, line 200-201). This is a free parameter that was presumably chosen before the sweep and is not varied in the sensitivity analysis. If chosen post-hoc to maximise Pool B quality differentiation, it constitutes hidden in-sample tuning.

> [!WARNING]
> The consistency_sharpe dampener constant (0.05) in `scripts/sweep_score_axis.py` line 200 is not swept as a parameter. If it was chosen to produce a "nice" separation between Pool A and Pool B traders, the pool construction has a hidden optimization layer that is not reflected in the fragility analysis. The sensitivity analysis only perturbs K — it should also perturb the dampener (e.g., 0.01, 0.05, 0.10) to confirm pool quality is not critically dependent on this constant.

### 2. Survivorship Bias: PASS

Markets are filtered by `p.resolved_at IS NOT NULL` and `p.correct IS NOT NULL` (line 344-345), which is standard for resolved-outcome evaluation. The universe is restricted to resolved Sports YES markets, which is necessary to compute hit rate. No post-hoc volume or liquidity filter is applied. The filter `count(*) < 10000` on training traders (line 162) is a noise guard (excludes bot-like accounts), not a post-hoc quality filter. The hold>=4h filter appropriately removes in-play contamination rather than selecting on resolution certainty.

The Sports market total of 275,617 is the full non-gambling universe, not a filtered subset. The signal universe collapses to 65 signals (K=50 N=1x1 directional) purely from the AND-gate, not from market selection criteria.

### 3. Edge Above Base Rate: FAIL

This is the central finding of the discovery, and the verdict correctly identifies the problem. The computation is reproduced here for verification.

**Best price-level-adjusted combo**: K=50 N=1x1 directional

- Reported HR: 70.77%
- Tag base rate: 33.29%
- Tag-level excess: +37.48pp (misleading, used in headline)
- Avg signal price: 0.6735
- Price-level base rate (population HR at [0.62-0.72]): 62.56%
- **Price-level excess: +8.21pp**

After the mandatory 20-40pp vectorized-to-tick degradation, the expected tick-by-tick excess is approximately -12pp to -32pp. Even the most optimistic assumption (only 20pp degradation) yields -12pp, which is well below break-even.

For all other viable combos:
- K=100 N=1x1 directional: +5.51pp price-adj excess (498 signals, but still sub-threshold after degradation)
- K=100 N=2x1 directional: +0.79pp price-adj excess (93 signals, effectively zero)
- K=100 N=2x2 directional: +1.43pp price-adj excess (65 signals)

The 5pp threshold for "likely surviving slippage" is not met by any combo except K=50 N=1x1 directional at +8.21pp, which still fails after degradation.

The BUY-only combos fare worse: the best is K=100 N=1x2 with +24.98pp price-adj excess, but this is computed on N=4 signals (2 months). A single trade flip changes HR by 25pp, making this figure meaningless.

> [!CRITICAL]
> No combo achieves price-level-adjusted excess HR that survives the 20-40pp vectorized-to-tick degradation. The best price-adjusted excess is +8.21pp (K=50 N=1x1 directional), giving an expected tick excess of -12pp to -32pp. The signal does not clear the edge threshold.

### 4. Sample Size: FAIL (multiple combos)

Signal counts across all combos:

| Combo | N signals | Assessment |
|-------|-----------|-----------|
| K=50 N=1x1 BUY-only | 2 | CRITICAL |
| K=100 N=1x2 BUY-only | 4 | CRITICAL |
| K=100 N=2x1 BUY-only | 2 | CRITICAL |
| K=50 N=1x2 directional | 16 | CRITICAL |
| K=50 N=2x1 directional | 0 | N/A |
| K=100 N=2x2 directional | 65 | borderline |
| K=100 N=2x1 directional | 93 | marginal |
| K=50 N=1x1 directional | 65 | borderline |
| K=100 N=1x1 directional | 498 | adequate |

Only K=100 N=1x1 directional (N=498) has adequate sample size for statistical claims. At K=100 each, the total pool covers 200 traders out of 1,195 qualified (16.7%), making the AND-gate nearly equivalent to "any qualified trader entered the market from either metric axis" — which is close to the unconstrained consensus signal, not a discriminating filter.

> [!CRITICAL]
> All BUY-only combos have N < 5 signals. The discovery correctly identifies this as a "universe collapse" but then continues to report HR, PnL, and compounding scores for these combos. The K=100 N=1x2 BUY-only +24.98pp price-adj excess cited as the "top BUY-only combo" (`buy_only_results.top_combos[0]`) is based on N=4 signals from 2 months. This figure should not appear in any ranking — it is statistical noise.

> [!WARNING]
> K=50 N=1x1 directional (N=65 signals over 8 months) is the "best" fragile directional combo. Monthly breakdown reveals N=18 in August 2025 with HR=44.4% and N=9 in January 2026 with HR=88.9%. With such month-to-month variance on small monthly N, the 70.8% aggregate HR is dominated by November (12 signals, 83.3%) and January (9 signals, 88.9%). These two months account for 32% of signals and likely reflect a specific sports season effect, not the strategy's general signal quality.

### 5. Walk-Forward: FAIL

The entire sweep is in-sample over a single 8-month test window (2025-07-01 to 2026-03-01). No walk-forward partition is applied. The sensitivity analysis varies K by ±25, which tests pool construction stability but is not a time-based hold-out.

The parameter sweep covers K ∈ {25, 50, 100}, N_a ∈ {1, 2}, N_b ∈ {1, 2}, and sell_mode ∈ {buy_only, directional} — 24 combinations. The "best" combo is selected by compounding score over the same period used to generate all signals. This is in-sample parameter selection. The subsequent sensitivity analysis using the same test window cannot compensate for this.

An additional concern: the fragility flag criterion is `n_signals < 30`, set in the script (line 549). This threshold was presumably chosen before the sweep, but it is consequential — K=50 N=1x1 directional (N=65) is marked `fragile: false` despite the K-sensitivity analysis showing it collapses to 0 signals at K=25. The fragility flag conflates sample size fragility with parameter fragility. These are separate risks.

> [!WARNING]
> Results are entirely in-sample. With 24 parameter combinations evaluated over the same 8-month window, the "best" combo (K=50 N=1x1 directional, CS=0.0592) is selected by fitting to this window. No walk-forward or time-based hold-out is applied. Expected in-sample optimism beyond the already-known 20-40pp vectorized degradation. A minimum 3-month hold-out (e.g., 2025-07 to 2025-12 train, 2026-01 to 2026-03 test) would reduce this risk, though the short history makes this impractical.

### 6. Degradation Band: N/A (Round 1)

No tick-by-tick validation has been performed. The discovery correctly deems validation unwarranted given the thin price-level excess. This checklist item is not applicable until Round 2.

---

## Additional Concerns

> [!CRITICAL]
> The verdict in `results.json` is set to `"marginal"` by the script's auto-logic (line 929-931 of sweep script). This verdict fires when `excess_hr_tag_pp >= 10pp AND n_signals >= 20`, which is satisfied by K=100 N=1x1 directional (+29.4pp tag excess, N=498). However, the auto-verdict logic does NOT check price-level excess — it only checks tag-level excess. A combo with +29.4pp tag excess and +5.5pp price-level excess would correctly be labeled "marginal" by the manual override in `results.json`, but the script's automated verdict path would label it "promising" if the CS threshold were met. The verdict is correct in the output file (manually overridden in `verdict_note`), but the auto-verdict script code at lines 929-931 has a bug: it will mis-classify any future hypothesis that has high tag-level excess but low price-level excess as "promising" without human review.

> [!CRITICAL]
> The `avg_pool_gap_hours` is negative for the best directional combos: K=50 N=1x1 dir has `avg_pool_gap_hours = -2.4h` and `med_pool_gap_hours = 0.0h`. A negative avg gap means Pool B (consistency_sharpe) traders are on average entering the market BEFORE Pool A (excess_hr) traders. This inverts the hypothesized temporal structure — the signal logic assumes Pool A fires first and Pool B confirms later, but the data shows Pool B entries often precede Pool A entries. The `pct_a_first = 0.508` (barely above 50%) confirms the relationship is near-random. This means the "dual-axis AND-gate" is not a sequential confirmation pattern — it is a coincidence detector. Both pools happen to have positions in the same market, with no reliable ordering. The temporal structure that would justify this as a "confirmation" signal is absent.

> [!WARNING]
> The November 2025 spike (12 signals, 83.3% HR in K=50 N=1x1 directional) warrants investigation before treating the 70.8% aggregate HR as representative. November/December are peak NBA and NFL season months in the US. If the strategy predominantly fires on US sports in these months, performance may be seasonal rather than structural. The Aug 2025 dropout (18 signals, 44.4% HR — below break-even at avg price 0.67) may reflect summer sports calendar where the signal has no edge. A seasonality decomposition should be a prerequisite for any further development.

> [!WARNING]
> The `compounding_score` formula used is `excess_hr_tag * avg_pnl_usd / med_hold_days` (script line 507-509). This mixes two different denominations: `excess_hr_tag` is expressed as a fraction (e.g., 0.37 for +37pp) while `avg_pnl_usd` is in dollars. The resulting score (0.0592 for best combo) has units of dollars and is dimensionally inconsistent with the standard compounding score definition (`excess_hr × avg_edge_usd / median_hold_days`). Furthermore, the README success criterion states CS > 5.0, but the best score is 0.0592 — three orders of magnitude below the threshold. This discrepancy is noted in `analysis.md` but the compounding score is still used as the primary ranking criterion for the sweep, creating a misleading ordering. A CS of 0.0592 vs threshold 5.0 is an unambiguous failure signal that should block further development.

> [!TIP]
> The directional mode "leaks" the sell-mode assumption into the signal semantics. SELL NO positions are Net-YES exposures, but they represent a different trader intent (market-making or hedging) than BUY YES positions (directional conviction). Including SELL NO routes in the signal increases N from 2 to 65 at K=50 N=1x1 but changes what "Pool A and Pool B both agree" means — it now means "both pools have net YES exposure via any route," not "both pools are actively buying YES because they believe it will resolve YES." The directional signal is arguably testing something different from the stated hypothesis. This ambiguity should be resolved before any validation attempt.

> [!TIP]
> The `is_gambling_market_sap` macro excludes crypto price markets (`%btc%`, `%eth%`, `%sol%`, etc.) based on slug patterns. This is correct in principle, but slug-pattern matching is fragile — markets titled "Bitcoin halving event" or "Ethereum upgrade" would not match the exclusion patterns and could contaminate the Sports universe. A tag-based exclusion (`primary_tag != 'Crypto'`) would be more robust than slug substring matching.

---

## Summary

This discovery correctly identifies that the score-axis dual-pool construction, as tested, does not produce viable alpha. The pre-mortem concerns were all verified: the prior +16pp finding from cross-pool-consensus was spurious (N=3, no hold filter, wrong base rate). After corrections, the best price-level-adjusted excess is +8.21pp (K=50 N=1x1 directional, N=65), which cannot survive the 20-40pp vectorized-to-tick degradation gap.

Beyond confirming the verdict, this review identifies four additional structural problems not fully addressed in the discovery write-up. First, the temporal ordering between Pool A and Pool B is near-random (pct_a_first ~50%, avg gap near zero or negative), invalidating the "sequential confirmation" rationale. Second, the November 2025 performance spike is a seasonal artifact that inflates the aggregate HR and should not be taken as evidence of a general signal. Third, the compounding score (best 0.0592) is three orders of magnitude below the stated success threshold of 5.0, yet this failure is buried in the analysis rather than stated as a headline blocker. Fourth, the auto-verdict script logic does not check price-level excess, which creates a systematic risk of mis-classifying future hypotheses. The discovery's recommendation to not proceed to tick validation is correct. The spawned idea `sports-yes-single-pool-price-gated` (single Pool A, price ceiling at 0.55) is the most promising reframe and should be prioritized over the dual-pool variants.
