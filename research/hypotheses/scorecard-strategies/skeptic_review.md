# Skeptic Review: scorecard-strategies (Round 2)

**Date**: 2026-03-07
**Reviewer**: Skeptic agent
**Sources reviewed**:
- `tick_validation_results.md` — semi-tick validation output (task #2)
- `strategy1_tag_consensus.md` — vectorized baseline, Tag-Expert Consensus
- `strategy2_smart_pool.md` — vectorized baseline, Smart Money Pool
- `strategy3_elite_copy.md` — vectorized baseline, Elite Copy
- `strategy4_smart_pool_pm.md` — vectorized baseline, Smart Pool + Position Management
- `architect_audit.md` — harness configuration audit
- `synthesis.md` — combined vectorized synthesis
- `research/knowledge/pitfalls/` — all pitfall entries

---

## Checklist Results

### 1. Look-ahead Bias: PARTIAL FAIL

The vectorized baselines are mostly clean for the primary signal, but several issues remain:

**Vectorized: PASS for consensus counting.** All four strategy files use `count(DISTINCT trader)` rather than event counts, and the SQL queries correctly use `CAST(first_trade AS DATE) >= test_start` to exclude pre-period entries. The consensus-dedup pitfall is nominally addressed.

**Tick validation: CRITICAL FAIL — the "semi-tick" method has a subtle look-ahead problem.**

The tick validator (`tick_validation_results.md:22`) explains that it loads `maker_positions` ordered by `first_trade` and fires the signal when the Nth qualified trader is observed. However, `maker_positions` is a **resolved-positions table** — it already contains the final `correct`, `yes_won`, and `resolved_at` columns for each position. The strategy code that consumes these rows never acts on `resolved_at` directly, but the pool qualification step runs on training data that is separated by date. The issue is elsewhere:

The semi-tick approach processes rows from `maker_positions` — a table where each row is a completed, resolved position. It does NOT simulate the strategy receiving individual raw trades. It processes one row per trader-market pair, with `first_trade` as the timestamp. This means **the Nth row in a market's ordered sequence is only in the positions table because that trader's position eventually resolved** (i.e., the market eventually closed). Markets that have pool-trader entries but whose positions are still open at the time of the snapshot are excluded. This is a **mild survivorship bias in the tick simulation itself** — markets that resolve quickly are over-represented.

> [!WARNING]
> The semi-tick simulation uses `maker_positions` (resolved positions only). Any market where pool traders entered but the market has not yet resolved by snapshot date is invisible to the validation. This biases the tick simulation toward faster-resolving markets, which may have systematically different HR than slower-resolving ones. Magnitude: unknown, but likely <5pp given 3-month test window mostly covered.

**Scorecard pool construction: PASS for look-ahead on training data.** All strategies correctly build pools from `resolved_at < 2025-12-05` and score test signals from `first_trade >= 2025-12-05`. The train/test split is respected.

**CRITICAL: The "base rate" used to compute excess_hr in tick validation is inconsistent.**

`tick_validation_results.md` lines 47-48 report for Esports Candidate A:
```
Test base rate (YES wins): 6.9%
Tick excess HR (vs YES BR): +93.1pp
Tick excess HR (vs NO BR = 93.1%): -0.0pp
```

For Crypto Candidate B (lines 75-77):
```
Vectorized excess HR (vs YES BR): +59.0pp
Tick excess HR (vs NO BR = 85.8%): -11.3pp
```

The reviewer selectively switches between YES and NO base rate depending on which number looks better. For Crypto, reporting "+60.3pp excess vs YES BR" when the NO base rate is 85.8% is misleading: the strategy direction is not always YES. If the strategy predominantly bets NO in a period where NO wins 85.8% of the time, the +60.3pp is largely base-rate chasing, not skill.

> [!CRITICAL]
> The tick validation conflates direction-specific HR with overall HR. For Crypto, the 749 tick signals mix YES and NO signals. The overall 74.5% HR is NOT +60pp excess over anything meaningful — it is compared to the YES win rate (14.2%) while the strategy presumably bets NO most of the time (consistent with the 85.8% NO base rate period). The document itself flags this: "Need to check: how many tick signals were YES vs NO direction?" — but this check was NEVER DONE. Until YES-direction and NO-direction signals are decomposed and their HR compared against respective directional base rates, the Crypto result is uninterpretable.

---

### 2. Survivorship Bias: FAIL (Mild — structural, but present)

The tick validation correctly restricts to resolved markets (unavoidable for HR computation) and applies `first_trade >= 2025-12-05` to prevent phantom signals. However, two survivorship issues remain:

**Issue A: Pool qualification requires minimum trade history.**
The qualified pool requires `n_markets >= 10` (or `n_markets >= 20` in Strategy 1) from training data. Traders who became active or skilled after the training cutoff are excluded from the pool. This is methodologically correct (you cannot use post-cutoff data), but it means the pool composition represents traders who were skilled in a specific historical period. If the Dec 2025 – Mar 2026 market environment is structurally different (it is — see Finding 5 in tick_validation_results.md), the pool may contain systematically different traders than those who are actually skilled in the test period.

> [!WARNING]
> Pool qualification is performed on training data (pre-2025-12-05), but the test period shows massive regime shifts: Esports YES rate dropped from 45.4% to 6.9%, Crypto from 47.9% to 14.2%. A trader who specialized in correctly identifying rare YES outcomes in training may not be the right expert in a high-NO-rate period. Pool calibration was not re-validated against test-period expert accuracy — only test-period HR is reported (not pool member HR).

**Issue B: Vectorized compounding scores use "all resolved markets" but report only 3 months of test data.**
The test window is Dec 2025 – Mar 2026 — approximately 3 months. All signal counts and compounding scores are extrapolated from this window. Seasonal patterns (sports seasons, election calendars, crypto volatility regimes) are not controlled for.

> [!WARNING]
> Three months of test data (Dec 2025 – Mar 2026) is a specific, heavily event-loaded period (post-US-election, crypto bear phase, major sports seasons). Reported signal counts and excess HR may not generalize to other periods. No seasonal sensitivity analysis was performed.

---

### 3. Edge Above Base Rate: PARTIAL FAIL

**Crypto (Candidate B — tick validation)**: The 74.5% tick HR is compared to a 14.2% YES base rate, yielding "+60pp excess." But this is the wrong comparison frame. The strategy uses vol-weighted direction which bets YES or NO depending on pool consensus. In a period where NO wins 85.8%, a pure-NO strategy would have 85.8% HR. The strategy's 74.5% HR is actually BELOW the naive NO base rate.

**Actual excess for Crypto: -(85.8 - 74.5) = -11.3pp below the naive NO base rate.** This is correctly stated on line 77 of tick_validation_results.md but is buried and contradicts the headline "+60.3pp" framing in the summary table.

> [!CRITICAL]
> The Crypto tick HR of 74.5% is BELOW the naive NO base rate of 85.8% for the test period. The strategy is underperforming a "bet NO on everything" baseline by 11.3pp. This is not positive excess HR — it is a signal that either (a) the strategy is misfiring YES signals in a bearish period, or (b) the NO signals are lower quality than the market has already priced in. The +60pp headline figure is computed vs YES base rate (14.2%), which is not the relevant comparison since a smart copier would always choose the majority direction in a lopsided period.

**Politics NO (Candidate C — tick validation)**: 88.9% tick HR vs NO base rate of 82.7% = +6.2pp excess. This is correctly measured. However, 6.2pp excess is below the knowledge base's minimum threshold of 5pp to "survive slippage" (pitfalls/vectorized_vs_tick.md warning). Given that:
- The tick simulation has zero fill friction (SimulatedExecutor per architect_audit.md §1.1)
- Real slippage is estimated at 1-3pp (architect_audit.md §4.2)
- Politics fills are near 0.90 for high-confidence signals (strategy4_smart_pool_pm.md, vol_conf bucket 0.9-1.0 fill=90.6¢)

The 6.2pp excess in tick simulation would reduce to 3-5pp after real fills. This is at or below breakeven.

> [!WARNING]
> Politics NO K=50 tick excess HR of +6.2pp over NO base rate (82.7%) is the entire edge. Tick simulation uses zero-friction fills (SimulatedExecutor). Real Polymarket fills at 0.88-0.91 entry price with 1-2pp spread eat 1-3pp of edge. Effective live excess may be 3-5pp — barely positive. Compounding score = 0.06 (tick_validation_results.md line 173). This strategy is NOT deployable at small sizes.

**Sports base rate contamination**: strategy4_smart_pool_pm.md reports Sports vol-conf >= 0.80, N=5 at 85.6% vectorized HR, but never applies the hold >= 4h filter that is flagged as mandatory. The expected post-filter count was estimated at "10-20% of raw signals." At 20% survival rate, the 5,710 signals become ~1,140 signals with an unknown (likely lower) HR.

> [!CRITICAL]
> Strategy 4 (Smart Pool with Position Management) reports Sports excess HR of +56.6pp (vectorized, N>=5, vol_conf>=0.80) without applying the hold >= 4h in-play filter. The synthesis document explicitly states "most Sports signals (80%+) in this dataset are short-hold in-play signals" (`synthesis.md`). The Sports CS=1,190 upper bound is built on contaminated data and is not comparable to the filtered estimates from Strategy 1. This strategy's Sports results are unusable until filtered.

---

### 4. Sample Size: PARTIAL FAIL

**Esports (Candidate A)**: 4 tick signals, 100% HR.

> [!CRITICAL]
> 4 tick signals is statistically meaningless. A binomial test of HR=100% with N=4 yields a 95% CI of [0.40, 1.00]. The lower bound (40%) is below the 6.9% YES base rate by enough to be positive, but the upper bound covers the full range. The "INSUFFICIENT DATA" verdict in tick_validation_results.md is correct, but the summary table still reports "100% HR" and "+93.1pp excess" — these numbers should not be in any table that drives decisions.

**Crypto K=50 configuration (strategy2_smart_pool.md)**: The K=50 Esports pool (N>=3, conf>=0.80) shows 403 test signals at 100% HR in the vectorized baseline. The tick validation ran a DIFFERENT configuration (all-pool, N=3, C=0.80 → 4 signals). The K=50 Esports configuration that drove the highest vectorized CS (628) was never validated in tick. This is a gap.

> [!WARNING]
> The tick validator chose to test the "all excess>0 pool" (535 traders, N=3) for Esports, not the K=50 pool configuration that produced the strongest vectorized signal (403 signals, 100% HR). The K=50 restriction concentrates signal in the top traders. The validation does not cover the highest-priority candidate as specified in task #2.

**Politics K=50 (Candidate C)**: 1,563 tick signals — statistically adequate. The supplemental all-pool result (3,188 signals) also adequate.

**Crypto (Candidate B)**: 749 tick signals — adequate, but see direction decomposition concern above.

**Strategy 4 Sports N>=5, vol_conf>=0.80**: 5,710 vectorized signals (N=3: 9,172), no tick validation performed. Unknown sample size after hold filter.

---

### 5. Walk-Forward vs In-Sample: FAIL

All vectorized results are in-sample or pseudo-out-of-sample on a single test window. There is no walk-forward validation.

The "test period" (Dec 2025 – Mar 2026) was designated before the sweep was run (correct), so the train/test split is pre-committed. However:

1. **Parameter sweeps were performed over the full test window.** Strategy 1 sweeps K={10,20,30,50,100} × N={2,3,4,5} × tag. Strategy 2 sweeps min_traders × min_conf × K_cap. Strategy 4 sweeps vol_conf thresholds × N. The "best" configurations reported are the best-of-sweep on the same test window used for evaluation.

> [!WARNING]
> All parameter selections (K, N, vol_conf threshold, hold filter cutoff) were optimized on the test window used to report HR. This is in-sample parameter selection even though the test period data was held out from pool qualification. The "best config" in each table is the best ex-post, not the best ex-ante. A separate hold-out period or walk-forward fold is required to validate parameter choices. The reported "best" numbers are inflated by selection bias.

2. **The train/test split was chosen at 2025-12-05** (3 months ago). There is no multi-fold cross-validation or rolling window. One period of 3 months is insufficient to distinguish signal from noise for parameters that might perform differently across election cycles, sports seasons, or crypto regimes.

> [!WARNING]
> Single train/test split with one 3-month test window. For Politics, the test window covers a post-election period (NO-dominant, HR=82.7%). The "edge" may be regime-specific, not generalizable. No alternative test windows were evaluated.

---

### 6. Degradation Band: CRITICAL FAIL

The expected degradation from vectorized to tick-by-tick is 20-40pp per the knowledge base. What actually happened is structurally different and requires explanation.

**Observed degradation**:
- Esports: 0pp (4 signals — invalid comparison)
- Crypto: -1.3pp (tick BETTER than vectorized — unexpected direction)
- Politics K=50: +1.0pp (essentially flat)

**Expected**: 20-40pp degradation per `pitfalls/vectorized_vs_tick.md`.

**The 0-1pp degradation is the wrong direction and magnitude.** The architect audit (§6) modeled expected degradation at 20-40pp for these strategies. The tick result showing <2pp degradation is a red flag per the checklist:

> [!CRITICAL]
> Tick degradation of <2pp (Crypto: -1.3pp, Politics K=50: +1.0pp) is far outside the expected 20-40pp band. Per the skeptic checklist, <10pp degradation is a signal of look-ahead bias or a fundamentally different simulation methodology. The tick validator used a "semi-tick" approach via the positions table rather than raw trades and a proper SyncReplayRunner integration. This non-standard methodology explains the anomalous result — it is NOT evidence that the signal has zero consensus gap.

**Why the semi-tick shows no degradation:**

The tick validation document itself explains (lines 22-31): "tick fires EARLIER than vectorized" because vectorized uses `max(first_trade)` across ALL eventual traders while tick fires at the Nth observation. This means the tick simulation generates **more signals** (749 vs 127 for Crypto) with **earlier trigger times** and **longer hold periods**. This is arithmetically correct but does not represent a proper tick-by-tick simulation:

1. The "semi-tick" never simulates capital constraints. There are no concurrent position limits. The architect audit (§P5) identified that the budget gate bug would suppress tick fills when using the real harness. The semi-tick bypasses all of this.

2. The "semi-tick" uses `first_trade` from the positions table — not the actual trade arrival sequence in the raw trades. Multiple positions within the same second are not tiebroken by `trade_id`.

3. The "semi-tick" does not simulate fill execution. It assumes perfect fills at the moment of signal with zero friction.

4. Most importantly: the semi-tick produces MORE signals than vectorized (749 vs 127). The knowledge base says tick should produce fewer signals due to capital constraints and missed signals (positions after capital is exhausted). More signals at equal or better HR is only possible if the simulation is less constrained than vectorized — which means it cannot be a valid upper bound test.

> [!CRITICAL]
> The tick validation using the "semi-tick" / positions-table approach is NOT equivalent to a proper SyncReplayRunner tick-by-tick simulation. It:
> (a) does not simulate capital constraints (no position limits, no budget gate),
> (b) does not simulate fill execution (no slippage, no spread),
> (c) does not use raw trade-level arrival timestamps (uses first_trade from resolved positions table),
> (d) produces more signals than vectorized (impossible for a properly-constrained simulator).
> The reported <2pp degradation is an artifact of the simulation methodology, not evidence of a low-degradation signal. The actual SyncReplayRunner tick-by-tick degradation is unknown. Do not interpret these results as validation that the signal is robust.

---

## Additional Concerns

> [!CRITICAL]
> Crypto strategy excess HR is actually negative vs relevant base rate. The 749-signal, 74.5% tick HR reported as "+60.3pp excess" is compared to the YES base rate (14.2%) rather than the directional base rate. In a period where NO wins 85.8%, the strategy's 74.5% HR means it is performing **11.3pp worse than a naive NO bet**. The strategy is either betting YES when it shouldn't be, or its NO calls are lower quality than the market price implies. This must be decomposed by direction before any deployment discussion.

> [!CRITICAL]
> The "semi-tick" approach generates more signals than the vectorized baseline (749 vs 127 for Crypto, 1,563 vs 932 for Politics). This is impossible for a properly constrained simulation — vectorized already has no capital constraints. If tick finds more signals, the tick methodology is LESS constrained than vectorized, making it an invalid validation. More signals + similar HR = the tick simulation is measuring something different from a real deployment scenario.

> [!CRITICAL]
> Strategy 4 (Smart Pool with Position Management) reports Sports CS=1,190 as the headline result without applying the mandatory in-play hold filter (>= 4h). The synthesis.md correctly identifies this as a critical pitfall. Using the Sports Binary_80_N5 result to anchor any ranking or capital allocation decision is methodologically wrong until the hold-filtered Sports signal is measured.

> [!WARNING]
> Parameter sweep overfitting: all K, N, vol_conf, and hold thresholds were selected by maximizing HR on the same test window. The K=50 Esports result (100% HR) compared to K=All Esports (92%) implies that the "right" K was discovered in-sample. Similarly, Politics K=50 (+6.2pp excess) vs all-pool (-0.6pp) means the entire edge comes from 50 specific traders selected by optimizing on... the same test period data? The report does not clarify whether the K=50 selection was pre-committed or post-hoc. If post-hoc, the +6.2pp excess is not reliable.

> [!WARNING]
> Crypto fills in the positions table average 83-96 cents for the direction traded (strategy4_smart_pool_pm.md). Kelly fractions at these fills are 0.01-0.02 — essentially zero edge per dollar risked in a live setting. Yet the tick simulation uses zero-friction fills (strategy2_smart_pool.md uses `SimulatedExecutor` per architect_audit.md §1.1). The +60pp excess headline for Crypto will collapse entirely once realistic fills (1-2pp spread at 90¢ entry = actual 0.5-1¢ edge) are accounted for. This is not a small correction.

> [!WARNING]
> All-pool vs K=50 comparison for Politics reveals a critical fragility: expanding from 50 to 4,739 qualified traders drops tick HR from 88.9% to 82.0% — essentially at the NO base rate. The entire Politics edge is concentrated in 50 traders. If any of those 50 traders change behavior, retire, or shift to different markets in the deployment period, the edge collapses. No robustness test across pool definitions or trader turnover scenarios was performed.

> [!WARNING]
> The test period (Dec 2025 – Mar 2026) shows dramatic base rate shifts vs training in ALL tags (Esports: 45.4% → 6.9%, Crypto: 47.9% → 14.2%, Politics: 24.6% → 17.3%). These shifts suggest a bearish/NO-dominant regime. Pool traders who excelled at predicting NO outcomes in a historically balanced market are rewarded in this period simply for maintaining their directional bias. The training-period pool selection may be a proxy for "chronic NO bettors" who happened to be correct during a bearish regime. This hypothesis is not tested.

> [!TIP]
> The architect audit (§P5) identified a critical bug: the `ExecutionGateway` with cumulative budget tracking would block fills after the strategy has deployed its total capital_usd in fills — even when prior positions have settled and capital is freed. This bug was identified but NOT verified to be fixed in the tick validation (which bypassed ExecutionGateway entirely using the semi-tick approach). Before any SyncReplayRunner integration, this bug must be fixed and confirmed.

> [!TIP]
> Strategy 4 (Phase 4) shows that for Crypto, the optimal entry point is after the 2nd qualified trader, NOT the 5th. HR peaks at N=2 (70.8%) and degrades to 61.5% at N=8. Validating Candidate B at N=5 (tick_validation_results.md) may miss the optimal parameter entirely. The N=5 validation result may understate the achievable HR at N=2. Separate N=2 vs N=5 tick validation for Crypto is warranted.

> [!TIP]
> The visionary and architect agents both surface the "early vs late pool direction" finding from Strategy 4: early-majority direction (first 3 traders) is near-random (50-63% HR), while late-majority (all N traders) is 67-79%. This implies a dynamic entry rule (wait for final consensus, exit if pool flips) would significantly outperform the static N-threshold rule. No tick-by-tick simulation of this dynamic rule has been run.

---

## Summary

The scorecard-strategies research has identified genuine signals in the vectorized phase, but the tick validation as executed is not a valid substitute for proper SyncReplayRunner integration. The semi-tick simulation bypasses capital constraints, uses resolved-position data rather than raw trades, and produces more signals than the vectorized baseline — making it more permissive, not less. The reported <2pp degradation band (expected 20-40pp) is an artifact of the methodology, not evidence of low degradation.

Three blocking issues must be resolved before any deployment decision:

1. **Crypto direction decomposition**: the 74.5% tick HR must be decomposed into YES signals and NO signals with separate excess HR vs directional base rates. The current headline is misleading (likely -11pp vs relevant NO base).

2. **Proper SyncReplayRunner integration**: implement `SmartPoolStrategy` and `TagConsensusStrategy` as protocol-compliant classes, run through `run_fast_backtest()` with the budget gate fix (P5), capital constraints (`max_open_positions=20`), and track `rejected_intents`. Only then can the vectorized→tick degradation be correctly measured.

3. **Sports in-play filter**: all Sports results in Strategy 4 must be re-run with the mandatory hold >= 4h filter before any Sports signal can be reported or ranked.

The most promising surviving signal is **Politics NO, K=50 pool** — it shows stable vectorized-to-tick behavior (+1pp degradation by the semi-tick measure), genuine multi-day hold times (156h median tick), and high signal volume (1,563 signals). However, the excess HR is thin (+6.2pp above NO base rate), the pool is fragile (50 traders carry all the edge), and zero-friction simulation overstates the live edge. Compounding score of 0.06 is not commercially viable at small position sizes. It may be viable at $1,000+ per signal but requires proper SyncReplayRunner validation first.

**Crypto is the highest-risk finding**: extraordinary headline numbers that collapse when measured against the correct directional base rate. Do not promote.
