# Skeptic Review: tag-hr-copy (Round 1, R3 artifacts)

## Checklist Results

### 1. Look-ahead Bias: CONDITIONAL PASS — one open vector

The R3 fix (`first_trade >= test_start`) was the blocking CRITICAL from the method_audit. According to
`notes.md` and `results_raw_r3.json` (`"fix": "first_trade >= test_start"`), this was applied and
removed 31.9% phantom signals from the 2025-07 fold.

**However**: the fix is NOT present in the on-disk sweep scripts. `sweep2.py` lines 107-120 show
`_tmp_thr_mkt_buy` with no `first_trade` filter. `sweep.py` (v1) likewise has no such filter. The R3
results were produced by an unlisted script that is not in the repository. The fix cannot be
independently verified from the code on disk.

> [!CRITICAL]
> **The R3 sweep script that produced `results_raw_r3.json` does not exist in the repository.**
> `sweep2.py` (the only sweep script on disk) does not contain the `first_trade >= test_start` fix,
> does not have the `max_avg_entry_price` parameter, and uses `avg_pnl` not `median_pnl` in the CS
> formula. The R3 results cannot be reproduced from the committed code. Before tick-by-tick validation
> begins, the researcher must commit the actual R3 sweep script. Without it, the R3 numbers are
> unauditable and unverifiable.

The resolution-conditioned test universe concern from the method_audit (WARNING — markets resolving
after fold boundary are excluded) was acknowledged but not fixed. This remains open.

The tag universe retroactive tagging risk (WARNING from method_audit) was addressed in notes.md:
"Confirmed only `(event_id INT32, tag_id INT32)` — no `created_at`." This means retroactive tagging
cannot be detected or excluded. The risk is asserted negligible but cannot be verified. Condition
unchanged from prior round.

### 2. Survivorship Bias: PASS (conditional)

Only resolved markets are included, appropriate for HR evaluation. The fold boundary exclusion
(markets that resolve just after fold end) is a mild systematic bias, acknowledged in prior rounds
and not newly introduced.

**Esports universe non-stationarity**: The fold_detail shows extreme base rate shift:
- 2025-01: base=0.133 (n=225), 2025-04: base=0.123 (n=227)
- 2025-07: base=0.343 (n=324), 2025-10: base=0.443 (n=1444), 2026-01: base=0.348 (n=2894)

Market count grew 12.9x from 225 to 2,894 in four folds. This is not survivorship bias per se, but
it means the reported "avg_hr=67.2%" is a simple average across folds with wildly different sample
sizes. The 2026-01 fold (n=2,894) dominates the average while the 2025-01 fold (n=225) counts
equally. A signal that only works in the high-volume phase would be invisible in this average.

> [!WARNING]
> **Esports fold sample sizes are asymmetric by 13x (225 vs 2,894).** The simple fold average in
> `results_raw_r3.json` weights each fold equally, but the 2026-01 fold has 13x more markets than
> the 2025-01 fold. A weighted average by n would more accurately represent the deployable signal.
> The early folds (n=225, n=227) have large sampling variance and may be dominating the "avg_hr" in
> misleading ways — either inflating or deflating the mean. Request signal-weighted average or
> per-fold HR report.

### 3. Edge Above Base Rate: PASS for promoted tags; numbers are coherent

**Esports BUY**: HR=67.2%, base=34.3% (weighted avg across folds). Excess=+35.7pp. Well above 5pp
threshold. Directional variant: excess=+24.4pp but med_pnl=$0.20, CS=0.45 — this discrepancy
(+24pp excess HR but near-zero edge dollars) is structurally suspicious. See Additional Concerns.

**1H BUY**: HR=78.0%, base=49.7%. Excess=+27.3pp. Well above threshold. Directional DIR has
excess=+4.43pp and CS=0.06 — essentially noise. The 27pp excess on BUY vs 4.43pp on DIR on the
SAME markets is the central unexplained anomaly. See Additional Concerns.

**Tennis BUY**: HR=72.4%, base=22.4%. Excess=+33.6pp. Strong. The +25.7pp HR jump from R2 (46.7%)
to R3 (72.4%) after removing pre-test entries is structurally plausible but deserves scrutiny (see
Additional Concerns).

### 4. Sample Size: WARNING on Esports early folds

**Esports**: 4 folds, n_folds=4 in top combos. The two earliest folds (Jan, Apr 2025) have n=225
and n=227 markets respectively. With mt=50 (requiring 50+ qualified traders per signal), the actual
signal count in those early folds is likely very small — yet they are included in the HR average.

**1H**: Only 3 folds (2025-07, 2025-10, 2026-01). The 2025-07 fold has n=1,683 markets. Signal
counts appear adequate (5,009 total across 3 folds = ~1,670/fold).

**Tennis**: 5 folds, heavily concentrated in last 2 folds (n=10,525 and n=17,603 vs 108, 120, 545
in first three). The early folds have borderline counts.

> [!WARNING]
> **Esports folds Jan-2025 and Apr-2025 each have n=225/227 training markets and base_rate=13%.**
> With mt=50 requiring traders to have 50+ resolved YES positions in a 6-month training window of
> thin-market Esports, the qualified trader pool in these folds was likely 0-3 traders. The per-fold
> signal counts are not reported in `results_raw_r3.json`. If these folds produce fewer than 50
> signals each, their HR estimates have confidence intervals of ±15pp or more, and including them in
> a simple fold average contaminates the estimate.

### 5. Walk-Forward: PASS

Walk-forward structure is correct: non-overlapping test windows, 6-month lookback, clean
train/test boundary. The `lookback_months` dead variable bug was in `sweep.py` v1 only; confirmed
not in `sweep2.py`. R3 uses 5 folds (Esports effectively 4 due to thin early markets).

No parameter optimization leaks: parameters are swept across all folds independently, not
selected per-fold.

### 6. Degradation Band: N/A (Round 1 — no tick-by-tick yet)

---

## Additional Concerns

> [!CRITICAL]
> **The actual R3 sweep script is missing from the repository.** `sweep2.py` does not implement
> the R3 fixes: no `first_trade >= test_start`, no `max_avg_entry_price` filter, and CS formula
> uses `avg_pnl` not `median_pnl` (sweep2.py line 185: `cs = round(ae*aap/hd, 4)` where `aap` is
> avg_pnl). The `results_raw_r3.json` has `"fix": "first_trade >= test_start"` and params include
> `max_avg_entry_price` — features absent from sweep2.py. The researcher must commit the actual
> script used to generate R3 results before those results can be treated as auditable.

> [!CRITICAL]
> **1H BUY vs DIR anomaly is unresolved and potentially diagnostic of a structural artifact.**
> 1H BUY-only shows HR=78.0% (excess=+27.3pp, CS=19.7). 1H DIR shows excess=+4.43pp, CS=0.06.
> These are computed on the same set of markets. BUY-only filters to YES-position traders; DIR
> includes both YES and NO. The 23pp gap between BUY and DIR excess HR means: (a) YES-position
> qualified traders are systematically more correct than NO-position qualified traders on the same
> 1H crypto markets, OR (b) 1H YES base rate in training was artificially lower than in test (making
> the qualification threshold easier to hit for YES bettors), OR (c) the 1H market is structurally
> biased — e.g., the market maker fills BUY orders at favorable prices and fills are more likely
> to succeed when buying YES near 0.75 (approaching certainty). The Challenger R3 review flags this
> as possible regime-conditioned momentum (bull crypto market). This needs an explicit answer before
> tick-by-tick is authorized. The mechanism must be named — "BUY consensus on 1H crypto" is not
> self-evidently a skill signal.

> [!CRITICAL]
> **Esports directional CS=0.45 with excess_hr=+24.4pp has median_pnl=$0.20.** A 24pp excess HR
> with $0.20 median PnL is internally inconsistent: if traders are right 24pp more often on average,
> they should be realizing substantially more than $0.20 per market. This small PnL with large HR
> excess suggests the directional variant is selecting markets where qualified traders hold positions
> that are nearly worthless (small net_yes or net_no tokens), so the PnL is near zero regardless of
> outcome. The directional HR may be inflated by markets where position size is negligible. This is
> a different form of the whale/size bias seen in R1 Basketball — here it manifests as size-deflated
> PnL masking uncertain HR quality. Request: add volume-weighted HR or minimum position-size filter
> to directional variant.

> [!WARNING]
> **Sensitivity analysis is R2-era and known fragile for the top-ranked combo.**
> `sensitivity.json` was produced before the R3 first_trade fix. Key finding: `1H_mt50_ep15_pc0.75`
> has `max_hr_drop=0.2831` — dropping from 83.7% to 55.4% at pc+5% (pc=0.79 vs 0.75). This 28pp
> cliff at a 0.04 change in price ceiling is the strongest fragility signal in the entire dataset.
> The Challenger R3 review notes: "1H_mt30 (fragile=false, max_hr_drop=0.022) is dramatically more
> stable." If R3 sensitivity were re-run, the cliff would likely persist because the mechanism
> (near-resolution pricing) is structural. **The researcher should switch the promoted 1H combo from
> mt=50 to mt=30 before tick-by-tick: CS drops negligibly (19.7 → 19.4) but sensitivity improves
> dramatically.** If the R3 sensitivity re-run shows the cliff persists at R3 parameters, mt=50
> should not proceed.

> [!WARNING]
> **Tennis HR jumped +25.7pp (46.7% → 72.4%) after first_trade fix — the direction of change is
> counter-intuitive and requires explanation.** The method_audit CRITICAL was that phantom
> training-period entries inflated HR. Removing phantoms should generally reduce HR (fewer signals,
> cleaner set), as it did for Esports (-12.4pp). Tennis reversed: removing pre-test entries
> increased HR by 25.7pp. The notes explain: "pre-test contamination by low-quality historical
> traders." But this means the pre-test entries were systematically WRONG (HR < 50%), pulling the
> test HR down. The post-fix dataset has a very different composition: the traders who entered
> Tennis markets fresh during the test period are apparently much more accurate than the legacy
> holders. This is plausible (fresh entries signal active intent) but is also consistent with a
> narrower, more curated test set that might be overfit to the particular test period composition.
> Verify: what is the per-fold HR for Tennis in R3? A consistent +70% HR across all 5 folds would
> validate; a concentration in 1-2 folds would be concerning.

> [!WARNING]
> **Esports base rate regime shift from 13% to 44% creates a qualification threshold drift that
> is not corrected in the averaged HR.** In fold 2025-01 (base=13.3%), traders needed only 28.3%
> YES HR to qualify (13.3% + 15pp). In fold 2026-01 (base=34.8%), the threshold was 49.8%. The
> averaged reported HR of 67.2% mixes signals from these fundamentally different qualification
> regimes. A trader who qualifies at 28.3% in fold 1 and happens to have 70% YES HR in the test
> period contributes identically to the average as a trader qualifying at 49.8% in fold 5 with 70%
> HR. The excess calculation (HR - base_rate) is reported as avg across folds, but the base rate
> used differs 3.5x between early and late folds. The reported "+35.7pp excess" is an average of
> per-fold excesses computed against fold-specific base rates — this is methodologically correct
> but means the 35.7pp is an average of numbers computed on different scales.

> [!TIP]
> **1H: consider the mt=30 variant (fragile=false, CS=19.4) instead of mt=50 (fragile=true, CS=19.7)
> for tick-by-tick promotion.** CS difference is negligible (0.3 points). Sensitivity profile is
> dramatically better. The 28pp cliff at pc=0.79 for mt=50 is likely a structural property of the
> near-resolution pricing mechanism, not a parameter-fitting artifact. Reducing mt constraint makes
> the signal accessible from more traders and reduces the specific concentration risk.

> [!TIP]
> **Per-fold HR reports are missing from results.json for Tennis and 1H.** The fold_detail in
> results.json reports only (fold, base, n) — not HR per fold. For a signal claiming 72.4% HR
> across 5 folds, fold-by-fold consistency is a basic robustness check. Request this before
> tick-by-tick validation to avoid spending validation compute on a fold-concentrated signal.

> [!TIP]
> **The CS formula in `sweep2.py` (line 185) still uses `avg_pnl` not `median_pnl`.**
> `agg()` function: `cs = round(ae*aap/hd, 4)` where `aap = sum(f["ap"]...)` and `"ap"` maps to
> `avg_pnl`. The R3 results.json states CS uses median_pnl. The discrepancy between script and
> output is another symptom that R3 was produced by an uncommitted script. Whichever formula is
> correct, the script on disk is inconsistent.

---

## Previous CRITICALs Status

| Issue | Status |
|-------|--------|
| Basketball negative excess HR (-3.6pp) | RESOLVED — Basketball excluded |
| CS uses avg_pnl (whale bias) | RESOLVED — results.json uses median_pnl |
| Directional aggregation bug (any(position)) | MOOT — BUY-only only promoted; directional results remain unreliable |
| lookback_months dead variable | RESOLVED — was sweep.py v1 only, sweep2.py clean |
| first_trade >= test_start (method_audit CRITICAL) | CLAIMED FIXED — but unverifiable from committed code |

---

## Summary

R3 resolves the most damaging prior issues: Basketball is gone, CS uses median PnL, and the
first_trade phantom-entry contamination is claimed fixed (removing 31.9% of bogus signals). The
remaining promoted tags (Esports, 1H) show robust excess HR after correction, and Tennis's
unexpected HR improvement after the fix, while counter-intuitive, is plausible.

Two blocking issues prevent clean promotion to tick-by-tick. First, the R3 sweep script is missing
from the repository — the results cannot be reproduced or audited from the committed code, which is
a fundamental research hygiene failure. Second, the 1H BUY vs DIR anomaly (27pp BUY excess vs 4.4pp
DIR excess on identical markets) lacks a named mechanism. Promoting 1H without understanding why
BUY consensus works and DIR does not risks validating a regime artifact (crypto bull market momentum)
rather than a genuine skill signal.

Esports is the cleanest of the three: the fix had the expected directional effect (-12.4pp HR), the
excess remains large (+35.7pp), and the per-fold base rates and signal counts are reported. Its
primary remaining concern is Esports-specific: the 13x sample size asymmetry across folds and the
possibility that the 2025-01/04 folds with ~225 markets produce unreliable HR estimates that distort
the average.

Tennis is the most structurally confusing: the fix improved its HR by 25.7pp in the "wrong"
direction. This is not invalidating — the explanation (pre-test holders were low-quality) is
coherent — but per-fold HR is essential to verify before investing tick-by-tick compute.
