# Skeptic Review — Tick Validation Round 1
## Scorecard V2 Strategies (Politics Composite, Crypto Elite, Sports Composite)

**Date**: 2026-03-07
**Reviewer**: Skeptic agent
**Scope**: All three tick-by-tick validation files + synthesis.md
**Validation files reviewed**:
- `validation/politics_composite.md` (Task #2)
- `validation/crypto_elite.md` (Task #3)
- `validation/sports_composite.md` (Task #4)

---

## 6-Point Checklist Summary

| Check | Politics | Crypto | Sports |
|-------|----------|--------|--------|
| 1. Look-ahead / future info | WARNING | CRITICAL | CRITICAL |
| 2. Survivorship bias | WARNING | WARNING | WARNING |
| 3. Edge above base rate | PASS (YES-only) | PASS (genuine) | PASS |
| 4. Sample size adequacy | PASS | WARNING | WARNING |
| 5. Walk-forward vs in-sample | WARNING | Not tested | PASS |
| 6. Degradation in 20-40pp band | WARNING | CRITICAL | CRITICAL |

---

## Point 1 — Look-Ahead / Future Information Bias

### Politics

> [!WARNING]
> **NO pool trained on YES positions only — but NO signals fired anyway.**
> `politics_composite.md` line 58 states: "Pool qualification was done on YES positions only (training used `position = 'YES'`). The top-K traders are selected for their YES hit rate performance." The tick runner then fires NO signals from this pool. This is not look-ahead in the temporal sense, but it IS a methodological inconsistency: the pool was NOT selected for NO accuracy. The 312 NO signals with -5.0pp excess HR confirm this. **This is not a backtest artifact — it's a design error that the validation correctly identified.** The skeptic concern is: did the vectorized UB use the same mis-specified direction, and was the -5pp already present in vectorized? If YES excess was isolated in vectorized as well, the comparison is apples-to-apples. If the vectorized used combined direction but reported +62.5pp, it is meaninglessly inflated by base rate.

> [!WARNING]
> **Late-entry contamination: 44.3% of Politics fills at price > 0.90.** At this price, the market has already partially resolved. The consensus firing at >0.90 is not predictive — it is lagging. This is not strictly look-ahead (the price is available at signal time), but it raises concern: are these signals coincidental with near-resolution reporting on external sources? If pool traders buy NO at 0.90 because they have already read the result, this is economic look-ahead even if the backtest mechanics are clean. See `pitfalls/in_play_contamination.md`.

### Crypto

> [!CRITICAL]
> **67.5% of fills at price = 0.99 — structural in-play artifact.**
> `crypto_elite.md` lines 28-47 confirm: 322/477 fills at fill_price=0.99. At this price, the market is effectively resolved. Pool traders buy YES at $0.99 because the outcome is already known (Bitcoin price outcomes, crypto election results). This is **economic look-ahead**: the signal fires on information that is publicly available (outcome is known) but not yet settled on-chain. The validation correctly identifies this but does not flag it as look-ahead — it flags it as "in-play." The skeptic position is stronger: **any backtest that includes price=0.99 fills is contaminated by post-resolution noise, regardless of whether the timestamp technically precedes settlement.** The -$6,455 PnL at price=0.99 despite 95% HR confirms the EV is negative (-$20.25/fill), which means even if deployable, these fills destroy value.

> [!CRITICAL]
> **November 2025 outlier: $27k PnL from 15 genuine fills.**
> Of the total $57,206 PnL from genuine signals (price<0.70), November 2025 contributed $27,237 (47.6%) from just 15 fills. This is one-month concentration in 98-signal sample. The remaining 83 genuine signals generated $29,969 over 7 months — approximately $4,281/month. November likely contained one or two large-edge markets (e.g., a crypto event with known early outcome). **If November 2025 is excluded, the genuine-signal strategy generates ~$30k over 7 months — still positive but ~2× lower than reported.**

### Sports

> [!CRITICAL]
> **Hold filter (`min_hold_hours=4.0`) was never implemented.**
> `sports_composite.md` lines 44-47 confirm: "`min_hold_hours=4.0` is stored but never read in `_maybe_fire()`." The filter was intended to exclude post-resolution noise (markets resolving within hours of signal). Without it, any in-play contamination that was supposed to be excluded by the ≥4h gate passes through unchecked. The validation reassures that N=3 consensus naturally filters in-play (only 1.8% fills < 1h), but this is an empirical observation — not a mechanical guarantee. **The fact that the intended filter was silently absent invalidates the stated "with ≥4h hold filter" configuration. The result is uncontrolled for what the designer intended.**

---

## Point 2 — Survivorship Bias

> [!WARNING]
> **Pool construction look-ahead not explicitly verified for tick validation.**
> All three strategies rely on a pool constructed with `train_cutoff = 2025-07-01`. The validation documents assert this cutoff is respected, but there is no explicit confirmation that the tick runner's `FeatureProvider` correctly passes `train_end_date = test_start` (per `pitfalls/training_window_lookahead.md`). The discovery phase had a known bug where `datetime.now()` was used, inflating the pool with 42% in-sample traders. The validation scripts should have fixed this, but the validation files do not explicitly confirm the fix was applied. **If the same bug persists in the tick validation's pool construction, all results are inflated by in-sample leakage.**

> [!WARNING]
> **Market universe filtering not verified.** The phantom test signal pitfall (`pitfalls/phantom_test_signals.md`) requires `first_trade >= test_start` for market inclusion. The Politics validation mentions 11,944 markets in test period but does not confirm this filter was applied. Without it, ~32% of "test" markets may be pre-test entries that inflate HR by up to 12pp.

> [!WARNING]
> **Composite scorecard stability over 8-month test window.** The top-K composite pool was frozen at `train_cutoff`. If some of these traders became market-makers, went dormant, or changed strategies mid-test, the live performance would diverge. No trader attrition analysis is presented. The walk-forward folds in discovery (3 folds) suggest the composite pool is stable, but the tick validation uses a single contiguous 8-month window — not a rolling fold.

---

## Point 3 — Edge Above Base Rate

### Politics YES-only

The YES direction has +51.6pp excess HR (70.6% HR vs 19.0% base rate) over 262 fills. This is genuine edge — well above what base-rate riding would produce.

The NO direction has -5.0pp excess HR (76.0% HR vs 81.0% base rate). This is **NOT edge**. The synthesis (`synthesis.md` line 65) shows Politics NO at +14.5pp in vectorized, but the tick result shows -5.0pp. This is a 19.5pp reversal between vectorized and tick — larger than the 20-40pp degradation band for NO (i.e., the vectorized NO signal is entirely explained by simulation artifacts).

> [!CRITICAL]
> **Politics NO has negative real-world alpha. Synthesis.md still shows +14.5pp for Politics NO.** The tick result conclusively refutes the synthesis claim that "both directions work" for Politics. The synthesis must be updated: Politics is YES-only, consistent with Sports and Crypto.

### Crypto genuine signals (price < 0.70)

45.9% HR vs 15% base rate = +30.9pp excess on 98 fills. Genuine edge confirmed if sample is clean (see Point 4).

### Sports

73.0% HR vs 33.2% base rate = +39.8pp excess on 612 fills. Genuine edge confirmed at high sample size.

---

## Point 4 — Sample Size Adequacy

### Politics YES-only

262 YES fills over 8 months. At ~147/yr (price≤0.70 subset), this is adequate for trend detection but marginal for precise confidence intervals. The subset of 98 signals (price≤0.70) is borderline — standard error on HR is ±4.9pp at N=98, 95% CI. The +38.1pp excess at 95% CI floor is still ~+28pp, so the edge claim survives even with uncertainty.

### Crypto genuine signals

> [!WARNING]
> **98 genuine signals is an underpowered sample.** The standard error of a proportion at N=98, HR=45.9% is ±5.0pp. The 95% CI for genuine excess HR is approximately +20.9pp to +40.9pp. At the lower bound, the strategy still has meaningful edge. However:
> - The sample spans 8 months unevenly (27 signals in July vs 3 in February)
> - November 2025 alone accounts for 47.6% of total PnL
> - The monthly breakdown shows HR ranging from 20% (Dec) to 83% (Jan) — high variance across months
> **Confidence in a stable +30.9pp excess HR is LOW at 98 observations with this PnL concentration.**

### Sports

612 fills is a large sample. HR of 73.0% with SE ±1.8pp. Well-powered. No concern.

---

## Point 5 — Walk-Forward vs In-Sample

### Politics

The synthesis documents walk-forward validation (3 folds) for the composite pool during discovery. The tick validation uses a single 2025-07-01 to ~2026-03 window, which is effectively the same test window as fold 3. This is not a new out-of-sample test — it is the same data used in the fold 3 walk-forward check.

> [!WARNING]
> **The tick validation is not an independent OOS test — it uses the same window as the discovery walk-forward fold 3.** The separation between discovery and tick validation is methodological (vectorized vs tick-by-tick), not temporal. If the pool was selected by sweeping K and N on this same test window, the parameter choices (K=100, N=5) are in-sample relative to the tick test window.

### Crypto

No walk-forward testing performed for Crypto. The synthesis (`synthesis.md` line 22) marks Crypto's walk-forward as "Untested." The tick validation did not perform a walk-forward either — it ran a single 8-month window.

> [!CRITICAL]
> **Crypto has zero walk-forward validation.** The pool (K=50 HR-only) was not tested across multiple folds. The discovery showed HR-only pools collapse in fold 3 for Politics (1 signal). The same collapse risk exists for Crypto HR-only. Without walk-forward, the Crypto result may not be reproducible in a new test window.

### Sports

Walk-forward was performed in discovery (synthesis shows stable fold 3 at 148 signals). Tick validation confirms signal on same window. Similar concern as Politics: not an independent temporal OOS.

---

## Point 6 — Degradation in 20-40pp Band

This is the most diagnostic check. Expected: vectorized excess HR → tick excess HR should drop 20-40pp.

| Strategy | Vectorized UB | Tick Result | Degradation | Expected Band | Assessment |
|----------|--------------|-------------|-------------|---------------|------------|
| Politics (combined) | +62.5pp | +54.5pp | -8.0pp | 20-40pp | BELOW BAND |
| Politics YES-only | ~62.5pp | +51.6pp | ~-10.9pp | 20-40pp | BELOW BAND |
| Crypto (headline) | +72pp | +67.5pp | -4.5pp | 20-40pp | **CRITICALLY BELOW** |
| Crypto (genuine, price<0.70) | +72pp | +30.9pp | **-41.1pp** | 20-40pp | AT UPPER LIMIT |
| Sports | +47pp | +39.8pp | -7.0pp | 20-40pp | BELOW BAND |

> [!CRITICAL]
> **All three strategies show headline degradation well BELOW the expected 20-40pp band.** Under-degradation is a red flag — it suggests either (a) the tick simulation has residual vectorized artifacts, (b) in-play contamination is inflating tick HR, or (c) the vectorized UB was computed on a different (easier) subset than the tick test. For any legitimate tick-by-tick simulation, the vectorized → tick gap should be 20-40pp due to: trade dilution (~15-25pp), timing gaps (~5pp), capital constraints (~5-10pp), direction mismatch (~5-15pp).

> [!CRITICAL]
> **Crypto's "true" degradation for genuine signals is -41pp (at upper limit of expected band), but the headline -4.5pp conceals this.** The validation file correctly identifies this, but the synthesis still cites Crypto's degradation as "-4.5pp." This is misleading. The headline degradation is computed on contaminated signals (price=0.99 inflate tick HR above genuine tick HR). A valid degradation comparison must be like-for-like: compare vectorized signals that would survive the price<0.70 filter against tick signals in the same price range.

> [!WARNING]
> **Sports -7pp degradation is suspiciously mild.** Expected: 20-40pp. Observed: -7pp. The most likely explanations are:
> 1. The vectorized UB was +47pp but the test universe (Sports YES-only, N=3) is a favorable subset where the vectorized UB was already tight.
> 2. The Sports tick simulation may not be enforcing all capital constraints (the PnL of $116,769 from $10k capital + 612 fills at $100 with max 20 open positions requires extensive capital recycling — see Capital Model concern below).
> 3. The sports signal is genuinely robust in tick (N=3 gate is tight enough to prevent signal dilution).
> Explanation (3) is plausible but needs verification.

---

## Additional Concerns

### Capital Model Integrity (Sports)

> [!WARNING]
> **Sports PnL of $116,769 from $10k capital requires capital recycling that may not be realistic.**
> 612 fills at $100 = $61,200 total capital deployed. With max 20 open positions and $10k capital, the strategy can deploy $2,000 at any given time (20 × $100). The PnL is 116% of the notional deployed. This implies the capital was recycled multiple times — plausible if Sports markets resolve daily (weekends). However, the validation notes `max_open_positions=20` and `capital_usd=10000` but does not confirm whether the harness properly enforced the capital ceiling or whether it allowed unlimited parallel positions in practice. **If the runner did not enforce capital limits, the PnL is overstated.**

### Politics NO Signal Design Flaw

> [!CRITICAL]
> **The validator confirmed Politics NO has -5pp excess HR, but the synthesis.md still recommends "YES+NO (both directions)" for Politics Composite.** This creates a dangerous discrepancy: the synthesis (which downstream agents may use for deployment decisions) contradicts the validation finding. The synthesis line 65 states Politics NO has +14.5pp excess — this was vectorized and is now conclusively refuted by tick results. **Synthesis.md must be updated before any deployment decision.**

### Crypto Monthly Variance

> [!WARNING]
> **High monthly PnL variance in Crypto genuine signals undermines the mean PnL estimate.**
> Monthly PnL ranges from -$1,458 (Dec 2025) to +$27,237 (Nov 2025). The coefficient of variation across months is extremely high. With only 8 months of data, a single month (November) dominates the mean. Any forward-looking PnL estimate based on this distribution will have extremely wide confidence intervals.

### Missing Politics Vectorized Direction Breakdown

> [!WARNING]
> **The vectorized UB of +62.5pp for Politics was computed on combined YES+NO signals.** The tick result shows the combined excess is +54.5pp, but this is an average of +51.6pp (YES, genuine) and -5.0pp (NO, base-rate). If the vectorized was also combined, the comparison is:
> - Vectorized combined: +62.5pp
> - Tick combined: +54.5pp (degradation: -8pp)
> - But the -5pp NO component means YES-only vectorized must have been ~+75pp to average to +62.5pp combined
> - YES-only tick: +51.6pp (degradation from implied vectorized YES ~+75pp: **-23pp** — within band)
> This reconciliation is speculative. The validation should explicitly isolate vectorized YES-only excess for a clean apples-to-apples comparison.

---

## Promotion Recommendation by Strategy

### Politics Composite YES-only

**DO NOT PROMOTE until:**
1. NO direction is permanently removed from strategy config
2. Explicit confirmation that `first_trade >= test_start` filter was applied
3. Pool construction look-ahead bug (from discovery phase) confirmed fixed
4. Synthesis.md updated to reflect NO direction has no alpha

**Promote after fixes** — YES-only alpha at +51.6pp over 262 signals is real.

### Crypto HR-only K=50 N=2

**DO NOT PROMOTE in current form.**
1. Requires `max_price=0.65` to exclude price=0.99 fills
2. Requires walk-forward validation (currently zero fold testing)
3. November 2025 outlier must be excluded from forward PnL estimates
4. Genuine signal rate (~12 signals/month) is marginal for deployment

**After adding price filter**: Re-run tick validation on price-filtered version, then promote to paper_dev only.

### Sports Composite K=25 N=3 YES-only

**CONDITIONAL PROMOTE to paper_dev.**
1. Confirm capital model enforcement in runner (max_open_positions binding)
2. Drop `min_hold_hours` parameter entirely (confirmed: filter was never implemented, and the 1-4h bucket has the highest alpha)
3. Monitor: if real-world HR drops below 65% over first 50 fills, reassess

The Sports result is the most credible: largest sample (612 fills), explicit N=3 consensus, low in-play contamination (1.8%), and the validation correctly identified and investigated the hold filter bug.

---

## Files Referenced

- `research/hypotheses/scorecard-v2-strategies/validation/politics_composite.md`
- `research/hypotheses/scorecard-v2-strategies/validation/crypto_elite.md`
- `research/hypotheses/scorecard-v2-strategies/validation/sports_composite.md`
- `research/hypotheses/scorecard-v2-strategies/synthesis.md`
- `research/knowledge/pitfalls/vectorized_vs_tick.md`
- `research/knowledge/pitfalls/phantom_test_signals.md`
- `research/knowledge/pitfalls/in_play_contamination.md`
- `research/knowledge/pitfalls/training_window_lookahead.md`
- `research/knowledge/pitfalls/consensus_dedup.md`
- `research/knowledge/pitfalls/vectorized_tick_gap_anatomy.md`
