# Skeptic Review: tag-hr-consensus (Round 2)

Reviewer: Skeptic agent
Artifacts reviewed:
- `validation/results_r2.json`
- `validation/notes_r2.md`
- `validation/run_validation.py`
- `validation/strategy.py`
- `discovery/results.json` (Round 3 vectorized, for degradation comparison)
- `reviews/round1_skeptic.md` (R1 issues)

---

## R1 Critical Issue Resolution

### Volume filter look-ahead (R1 CRITICAL)
**Addressed.** The R2 validation drops the volume filter entirely (`no volume filter` in the results note).
This is a blunt but valid fix — the filter is not tested rather than fixed. The signal now runs
without the confounding variable. Consequence: lower HR vs the original sweeps, but no look-ahead.

### Per-fold HR reporting (R1 CRITICAL)
**Addressed.** Per-fold HR tables are now present in `results_r2.json` and `notes_r2.md`.

### Tennis vol>=2k unsupported signal count (R1 CRITICAL)
**Addressed by abandonment.** Tennis validation uses different params (N=3, ep=15pp, mpe=0.70)
rather than the unsupported vol>=2k combo. However, the params actually run differ from
what notes_r2 describes — see Critical finding below.

---

## Checklist Results

### 1. Look-ahead Bias: CONDITIONAL PASS

The tick-by-tick harness (`run_validation.py`) correctly implements the phantom filter:
`test_start_epoch` is passed to `ConsensusStrategy`, which ignores trades with
`ts < self._test_start` (strategy.py line 86). Pool qualification uses the training window
date bounds. No resolved epoch appears in the feature computation path.

**One residual concern** (WARNING-level): pool qualification in `build_qualified_pool`
filters by `CAST(p.resolved_at AS DATE) >= train_start` (line 145). The `resolved_at` date
is the resolution timestamp, not the entry date. A position entered in the training window
but resolving after `train_end` would be excluded. Conversely, positions entered before
`train_start` but resolving within the training window would be included. This is a
date-boundary ambiguity that slightly contaminates the training-window pool, but is
unlikely to be material.

**Another residual concern** (WARNING-level): the base rate used for pool qualification
(`ep_thresh = ep_pp / 100.0`, line 197) is computed from the TEST window
(`get_base_rate(d, test_start, test_end)` line 191), not the training window.
This means a trader qualifies for the pool based on excess HR over the test-window base rate,
which is not known at train time. If test-window base rates diverge from training-window
base rates (which they do — Esports flips from 37% to 65%), the qualification threshold
shifts retroactively. This is a mild look-ahead in pool construction.

> [!WARNING]
> `run_validation.py` line 197: `build_qualified_pool` is called with `base_rate` drawn
> from `get_base_rate(d, test_start, test_end)` (line 191). The test-window base rate is
> used as the excess-HR qualification threshold for the TRAINING-window pool. Traders are
> qualified based on a base rate they could not have known at training time. In the 2026-01
> Esports fold, the test base is 45.6% vs the training base (2025-07 to 2026-01) which
> spans a period where base ranged from 37% to 65%. This retroactive threshold shifts which
> traders enter the pool. For the 2025-10 fold with base=65.4%, any trader with HR < 75.4%
> (ep=10pp) is excluded — a much stricter cut than training-time information would justify.

---

### 2. Survivorship Bias: PASS (with inherited concern)

The universe is defined as markets with `resolved_at` in the test window — correct and
necessary. The pool is built only from resolved training-window positions — also correct.
No post-hoc volume or liquidity filters are applied to the universe.

The `correct` field contamination from split-route PnL errors (flagged in R1) remains
unaddressed. The R1 WARNING stands: pool membership is partially corrupted for ~12% of
trader-market pairs. No mitigation was attempted. This is acceptable for a MARGINAL-verdict
round but must be addressed before any paper promotion.

---

### 3. Edge Above Base Rate: CONDITIONAL PASS

Calculated per-fold excess HR for each combo:

**Esports Primary (N=4, ep=10pp, mpe=0.80):**
- 2025-07: base=36.8%, HR=51.9%, excess=+15.2pp — PASS
- 2025-10: base=65.4%, HR=60.0%, excess=**-5.4pp** — FAIL
- 2026-01: base=45.6%, HR=46.0%, excess=**+0.4pp** — effectively zero
- Aggregate: +3.4pp excess — marginal

**Esports Sensitive (N=3, ep=15pp, mpe=0.70):**
- 2025-07: base=36.8%, HR=54.4%, excess=+17.6pp — PASS
- 2025-10: base=65.4%, HR=66.7%, n=**3 signals** — meaningless
- 2026-01: base=45.6%, HR=43.7%, excess=**-1.9pp** — FAIL
- Aggregate: +5.7pp but driven almost entirely by one fold

**Tennis Primary (N=3, ep=15pp, mpe=0.70):**
- 2025-07: base=24.8%, HR=52.8%, excess=+28.1pp — but PnL=-$655
- 2025-10: base=39.6%, HR=38.3%, excess=**-1.2pp** — FAIL
- 2026-01: base=45.3%, HR=53.2%, excess=+7.9pp — PASS
- Aggregate: +11.6pp but PnL=-$2,455

The aggregate excess HR figures are inflated by simple averaging across folds with radically
different base rates and signal counts. The 2025-07 Esports fold (52 signals) gets equal
weight to the 2026-01 fold (433 signals) in the simple average. Signal-count-weighted
excess HR for Esports Primary is approximately:
(52×15.2 + 40×(-5.4) + 433×0.4) / 525 = (790 - 216 + 173) / 525 = **+1.4pp**
— not +3.4pp as reported.

> [!CRITICAL]
> The aggregate `avg_excess_hr_pp` in `results_r2.json` is a simple fold-average, not
> signal-count-weighted. For Esports Primary, the signal-count-weighted excess is
> approximately +1.4pp (vs reported +3.4pp). For Esports Sensitive, the 2025-10 fold
> has n=3 signals and is included in the fold average with equal weight — 3 signals
> cannot be considered evidence of any HR. Removing the n=3 fold, Esports Sensitive
> aggregate is (17.6pp + (-1.9pp)) / 2 = +7.9pp, but across only 275 effective signals.
> All reported aggregate excess HR figures overstate the true signal strength. The
> run_validation.py aggregation at lines 333-335 confirms simple averaging:
> `agg_excess = sum(f["excess_hr_pp"] for f in valid_folds) / n_folds`.

---

### 4. Sample Size: PARTIAL FAIL

**Esports Primary**: 525 total signals across 3 folds (52, 40, 433). The 2025-10 fold
has only 40 signals — borderline. PASS at aggregate level but the folds are radically
unbalanced.

**Esports Sensitive**: 278 total signals, but the 2025-10 fold has **n=3 signals**.
Three signals is not a fold — it is noise. Including it in a fold average corrupts the
aggregate statistic.

> [!CRITICAL]
> Esports Sensitive 2025-10 fold: n_signals=3, n_settled=3, n_wins=2, HR=66.7%.
> This fold is included in the aggregate average with equal weight to folds with 46
> and 229 signals. A fold with n=3 has a HR standard error of approximately
> sqrt(0.667 * 0.333 / 3) = 27pp — the entire reported HR is within one standard error
> of the base rate. This fold must be excluded from any statistical claim.
> Per checklist: flag n < 50 as CRITICAL. The run_validation.py `min_n_markets` guard
> (line 193) only excludes folds with fewer than 5 markets, not fewer than 50 signals.
> A minimum signal count guard is absent.

**Tennis Primary**: 442 signals across 3 folds (106, 180, 156). Adequate sample size.

**Tennis Sensitive**: 876 signals across 3 folds (151, 353, 372). PASS.

---

### 5. Walk-Forward: PASS (with parameter mismatch concern)

The fold design is correctly implemented: training windows are disjoint from test windows,
pool is built on training data only, evaluation is on the held-out test month.

**However, there is a material discrepancy between the run_validation.py COMBOS
definition and the results_r2.json output.**

`run_validation.py` COMBOS (lines 63-68) specifies:
```python
COMBOS = [
    ("Esports", "primary",   2, 0.75, 15, 0.80, None),   # N=2, ep=15pp
    ("Esports", "sensitive", 4, 0.75, 10, 0.80, None),   # N=4, ep=10pp
    ("Tennis",  "primary",   3, 0.75, 20, 0.90, None),   # N=3, ep=20pp, mpe=0.90
    ("Tennis",  "sensitive", 2, 0.75, 20, 0.70, None),   # N=2, ep=20pp
]
```

`results_r2.json` reports:
```
Esports primary:   N=4, ep=10pp, mpe=0.80
Esports sensitive: N=3, ep=15pp, mpe=0.70
Tennis primary:    N=3, ep=15pp, mpe=0.70
Tennis sensitive:  N=2, ep=15pp, mpe=0.70
```

The COMBOS in the script are labelled with a comment "R3 params" and output to
`results_r3.json` (line 410). The file `results_r2.json` was produced by a different
run — either a prior version of the script or a separate execution. The current
`run_validation.py` on disk does NOT reproduce `results_r2.json`. Furthermore,
`notes_r2.md` documents params matching `results_r2.json`, not the current script.

> [!CRITICAL]
> The `run_validation.py` script on disk is not the code that produced `results_r2.json`.
> The COMBOS in the script (N=2/4 for Esports, ep=15/10pp) differ from the reported
> results (N=4/3, ep=10/15pp). The script also writes output to `results_r3.json`, not
> `results_r2.json`. This means the validation results cannot be reproduced from the
> current codebase without identifying which version of the script was used. Reproducibility
> is broken. Before any promotion decision, the exact script used to generate
> `results_r2.json` must be recovered or the run must be re-executed with a frozen script.

---

### 6. Degradation Band: PASS (lower end, but within band)

Vectorized vs tick degradation:
- Esports: vectorized UB for the closest matching combo (N=4, ep=10pp, mpe=0.80) shows
  avg_hr=73.3% (excess=+24.1pp). Tick shows HR=52.6% (excess=+3.4pp unweighted).
  Degradation: 73.3% - 52.6% = **-20.7pp** on HR, excess drops from +24.1pp to +1.4pp
  (signal-count-weighted). On the lower edge of the 20-40pp band.
- Tennis: vectorized UB for (N=3, ep=15pp, mpe=0.70) from the R3 sweep top-15 shows
  avg_hr=72.6%, excess=+36.1pp. Tick shows HR=48.1% (excess=+11.6pp unweighted).
  Degradation: 72.6% - 48.1% = **-24.5pp**. Within the 20-40pp band.

The 26-28pp degradation reported in `notes_r2.md` is consistent with these calculations.
Degradation band does not trigger any flags.

One concern: the vectorized comparator in `notes_r2.md` uses `Vect UB (Esports): 80.7%`
which does not match any combo in `discovery/results.json` with the exact params tested.
The closest match is N=4/ep=10pp/mpe=0.80 at avg_hr=73.3%. The 80.7% figure appears to
be from the R1 sweep (which included the look-ahead volume filter). If the vectorized UB
includes the removed volume filter, the degradation baseline is biased and the 26-28pp
figure is incorrect.

> [!WARNING]
> `notes_r2.md` vectorized UB column shows `80.7%` for Esports and `74.5%` for Tennis.
> These figures are not reproducible from `discovery/results.json` for the exact params
> tested in R2 (N=4, ep=10pp, mpe=0.80). The R3 sweep closest match is 73.3% for Esports.
> If the 80.7% figure derives from the R1 sweep (which had a look-ahead volume filter),
> the degradation calculation is comparing tick results against a biased vectorized UB.
> The true degradation may be closer to 20pp than 28pp, which is still within the band
> but the comparison should use the clean R3 sweep numbers as the UB.

---

## Additional Concerns

> [!CRITICAL]
> **Pool explosion is not a tertiary issue — it is the primary mechanism of signal death.**
> The 2026-01 Esports fold has 774 qualified traders. With N=4, the signal fires on 433
> markets in a single month — 14 signals per day. At that frequency, the strategy is no
> longer copying insiders: it is firing on every market that any 4 of 774 mediocre traders
> entered. The excess HR in this fold is +0.4pp (or zero after weighting). The researcher's
> proposed fix (pool size cap at 50) is directionally correct but untested. Until validated,
> pool explosion renders the 2026-01 fold uninvestable. The 2026-01 fold contains 433/525
> (82%) of Esports Primary's total signals. This means 82% of reported signals are
> operating at near-zero edge. The +$32 aggregate PnL is a statistical artifact of
> offsetting folds, not evidence of a deployable signal.

> [!CRITICAL]
> **Tennis has positive excess HR (+11.6pp aggregate) but deeply negative PnL (-$2,455).
> This is internally inconsistent and must be explained before any promotion.**
> The researcher's explanation (high fill prices at 0.49-0.52 eroding edge at prices near
> 0.50) is qualitatively correct but the numbers do not fully reconcile. For Tennis Primary
> 2025-10: HR=38.3%, base=39.6% (negative excess), 180 signals at avg fill 0.489.
> Expected PnL per signal at HR=38.3%, fill=0.489:
> E[PnL] = (0.383*(1-0.489) - 0.617*0.489) * 100 = (0.196 - 0.302) * 100 = -$10.6
> Total expected: -$10.6 * 180 = **-$1,908**.
> Actual reported PnL: -$4,296. The discrepancy of ~$2,400 implies either
> (a) fills are systematically worse than the reported average (right-tail fill prices
> near 0.75 with zero-edge HR), or (b) the SimulatedExecutor is not filling at the
> reported avg_fill_price. The ledger parquet files at
> `validation/ledger_Tennis_primary_N3_ep15_mpe0.7_2025-10.parquet` should be inspected
> directly to diagnose this. Without reconciliation, the PnL figures cannot be trusted.

> [!CRITICAL]
> **The meh=10-15pp parameters tested in R2 are NOT the optimal parameters identified
> in Round 3 vectorized sweep.** The R3 sweep found meh=20pp to be optimal for both tags
> (the R1 validation notes explicitly state "Key change: meh=10pp → meh=15-20pp. This is
> the critical lever"). The R2 validation uses meh=10pp (Esports primary) and meh=15pp
> (Esports sensitive, Tennis). The meh=20pp regime — which was identified as the
> hypothesis-level sweet spot — was never validated tick-by-tick. The researcher's verdict
> of MARGINAL is based on suboptimal parameters. Either the optimal parameters should be
> validated (meh=20pp combos), or the sweep finding that meh=20pp is optimal should be
> retracted. Running validation on deliberately non-optimal parameters and calling the
> signal MARGINAL is an incomplete test.

> [!WARNING]
> **Strategy price ceiling is applied to the triggering trade price, not the signal entry
> price.** In `strategy.py` line 128: `signal_price = trade.price` and the check is
> `if signal_price > self._price_ceil`. The `max_price` in the TradeIntent is set to
> `min(signal_price + 0.02, self._price_ceil)` (line 145). This means the strategy
> evaluates the price ceiling against the Nth qualified trader's price, not the price
> the strategy would actually fill at. If the Nth trader buys at 0.73 (just under the
> 0.75 ceiling), the TradeIntent is issued with max_price=0.75. The SimulatedExecutor
> fills at max_price (0.75), not at 0.73. The strategy is therefore sometimes entering
> at a price 0-2pp above the price ceiling check. For the Tennis combos with high
> average fills (0.49-0.52), this slippage of up to 2pp on each signal materially
> degrades expected PnL.

> [!WARNING]
> **The 2025-07 Esports fold result drives the optimistic narrative but is structurally
> different from the 2026-01 fold.** In 2025-07: 223 markets, 47 qualified traders,
> avg_fill=0.443 (cheap entry). In 2026-01: 13,538 markets, 774 traders, avg_fill=0.477.
> The 2025-07 fold represents the Esports market in an early, inefficient phase. The
> signal worked because the market was new and prices were cheap. The 2026-01 fold
> represents the current market — efficient, large, and the primary operating regime for
> any deployed strategy. The hypothesis should be evaluated primarily on the 2026-01 fold
> (where 82% of signals live), not on the 2025-07 fold which is a historical artifact.
> The signal does not work in the current regime.

> [!WARNING]
> **Esports 2025-10 fold base rate of 65.4% makes positive excess HR structurally
> impossible for a BUY-YES strategy in that regime.** At base=65.4%, even a highly
> skilled YES-buyer has an unconditional 65% chance of being right. The strategy would
> need HR > 75% (with ep=10pp threshold) to demonstrate real edge. The fold is an
> adversarial environment for a YES-momentum strategy. Any evaluation that includes
> this fold in a simple average will dilute the apparent signal quality. The correct
> interpretation is: the signal does not exist in high-base-rate regimes. If the
> deployment would include such regimes, the strategy must include a regime filter
> (e.g., skip markets when tag base rate > 55%). No such filter exists.

> [!TIP]
> The researcher proposes three fixes: pool size cap at 50, per-fold HR re-evaluation,
> and price filter at 0.40. Before validating any of these, produce a signal-count-weighted
> aggregate HR table. The current fold-averaged figures (3.4pp Esports, 11.6pp Tennis)
> are misleading. The weighted figures are approximately 1.4pp and 6-7pp respectively.
> At 1.4pp weighted excess, the Esports signal is below the 5pp WARNING threshold and
> has no deployable edge at $100 position size.

> [!TIP]
> The 2025-07 Esports fold (47 traders, avg_fill=0.443, Sharpe=3.27, PnL=+$616) is
> genuinely interesting as a historical data point, but cannot be generalized without
> answering: what causes small-pool / cheap-entry markets? If this regime reappears
> (new tags, newly launched markets), there may be an early-mover strategy. This is
> a different hypothesis from the current consensus-copy framing and should be
> branched into a separate idea.

---

## Summary

Three of the four R1 CRITICAL issues were addressed (volume look-ahead removed, per-fold HR reported, Tennis thin sample handled). However, this round introduces four new CRITICAL issues that block any promotion decision.

The most serious finding is the script-result mismatch: the `run_validation.py` on disk does not reproduce `results_r2.json`, breaking reproducibility entirely. Before the results can be acted upon, the exact script version must be identified.

The second critical finding is statistical: the aggregate excess HR figures (3.4pp Esports, 11.6pp Tennis) are simple fold averages that overweight small folds. Signal-count-weighted, the Esports excess is approximately 1.4pp — below the 5pp minimum threshold for a deployable signal. The n=3 Esports Sensitive 2025-10 fold is included in the average without any minimum-signal guard, further corrupting the statistics.

The third critical finding is parameter under-testing: the R2 validation deliberately uses meh=10-15pp, not the meh=20pp identified as optimal in Round 3 sweep. The signal at optimal parameters was never tick-by-tick validated.

The fourth critical finding is that Tennis shows positive excess HR (+11.6pp) but negative PnL (-$2,455) with an unexplained $2,400 gap in the 2025-10 fold between expected and actual PnL. This gap implies either fill simulation errors or fill price distribution pathology that must be diagnosed before trusting any PnL-based promotion criterion.

The researcher's MARGINAL verdict is not wrong, but it understates the problem: the signal as currently configured does not have deployable edge in the current market regime (2026-01, which represents 82% of Esports signals and the future operating environment). The 2025-07 fold that drives optimism is a historical artifact of an immature market. **Recommend: do not advance to paper trading. Re-run validation with meh=20pp and a pool size cap of 50. Report signal-count-weighted excess HR. Diagnose the Tennis PnL gap before any further interpretation.**
