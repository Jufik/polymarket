# Skeptic Review: crypto-gbm-flip-stop (Round 2)

**Date**: 2026-03-10
**Reviewer**: Skeptic agent
**Files examined**:
- `discovery/01_baseline_analysis.py`
- `discovery/results.json`, `discovery/sweep_results.md`, `discovery/baseline_analysis.md`
- `validation/validation_script.py`
- `validation/results.json`, `validation/comparison.md`, `validation/validation.log`
- `reviews/premortem.md`

---

## Checklist Results

### 1. Look-ahead Bias: CONDITIONAL PASS

The simulation structure is correct in principle: entry uses ASOF-joined PM price at the current
bar, sigma is computed from the 30-minute *pre-window* (lines 210–228 of the discovery script,
lines 283–308 of the validation script). S0 is the first bar *within* the window — that is
available at trade time.

One subtle issue: **sigma tercile thresholds (t33, t67) are computed over the entire dataset
population** (`np.percentile(sigma_arr, [33, 67])` on all 16,176 markets at once). In production
the strategy would need to use a rolling or prior-period sigma distribution to classify regimes.
This affects the `sigma_cond` config specifically, but also the regime breakdown labels used
throughout. For exit-parameter optimization this is a low-severity concern since the regime labels
are only used for post-hoc stratification in reporting, not for entry/exit decisions in the
non-sigma-conditional configs. The `sigma_cond` config does depend on these thresholds for exit
logic.

The ASOF join (`pm.ts <= b.ts`) correctly prevents using future PM prices. The staleness gate
(`PM_STALE_S = 60s`) is applied at entry in the validation script (lines 433–446) but is
**absent from the discovery script** (lines 371–379 of `01_baseline_analysis.py` allow looking
back within 60s, but do not enforce a 60s max age on the ASOF-joined price returned at bar `idx`
directly). This creates a small asymmetry between vectorized and tick validation but does not
constitute look-ahead bias.

No use of `resolved_epoch` or winner information before the entry bar. Resolution information is
only accessed in the exit simulation's `make_result()` call for `hold_resolution` and
`resolution` exit types, where `won` is a pre-computed constant for the market — not derived from
future price data.

### 2. Survivorship Bias: PASS

Universe is all resolved 15-min BTC Up/Down markets where `winner_outcome IN ('Up', 'Down')`
with valid sigma (HAVING count >= 60) and valid S0. No filter on volume, market popularity, or
post-hoc performance criteria. 15,889 out of 16,176 markets (98.2%) generate entries — the
universe is highly inclusive.

The `no_signal` skip rate is 287/16,176 (1.8%), attributable to markets where the GBM-PM lag
never exceeds the effective threshold. This is a legitimate signal-quality filter, not survivorship.

### 3. Edge Above Base Rate: PASS WITH CAVEAT

This is not a new directional signal — it is an exit parameter optimization layered on top of the
existing GBM divergence strategy. The relevant base rate comparison is against the baseline config
(77.1% HR), not the overall market 50/50 base rate.

Against the 50% binary base rate, all configs have large excess:
- Primary (best reported config): tick HR 81.5%, excess = **+31.5pp** — well above 5pp threshold.

Against the baseline config (77.1%):
- Primary improvement: **+4.4pp** (81.5% vs 77.1%)
- Aggressive: +4.0pp
- Delay-only: +5.3pp

These incremental improvements over the baseline are small relative to the absolute edge. However,
the question is whether the *incremental improvement* is durable or noise. With 15,889 trades the
standard error of a hit-rate estimate is approximately `sqrt(0.77 * 0.23 / 15889) = 0.003` (0.3pp).
The 4.4pp improvement is ~14 standard errors above noise — statistically robust at the dataset
level.

The more meaningful concern is whether the improvement persists out-of-sample. See Walk-Forward
section below.

### 4. Sample Size: PASS

- Total entries: 15,889 (tick), 15,934 (vectorized)
- Per-config subsample (high-vol regime): 5,198 observations
- Smallest meaningful sub-segment: high-vol flip exits in primary config = 395 exits

All counts are well above the 100-trade minimum. No sub-segment falls below 50.

### 5. Walk-Forward: FAIL

> [!CRITICAL]
> **All results are in-sample.** The parameter grid (thresholds 0.20/0.25/0.30/0.35/0.40/0.45,
> delays 1/2/3/5) was swept over the full dataset (Sep 2025 – Mar 2026, 171 days), and the
> recommended config (thr=0.25, delay=3) was selected based on this same data. There is no
> walk-forward split, no hold-out period, and no out-of-sample test. The `config.toml` template
> references `train_months = 12 / test_months = 1` walk-forward parameters but they are
> never used — the discovery and validation scripts run over the entire dataset with no train/test
> split whatsoever.
>
> The delta between best and baseline is small (+2.2pp on vectorized avg PnL units, +4.4pp HR).
> With 13 variants evaluated, the probability of finding a spurious 4pp improvement in-sample is
> non-negligible. This is a textbook multiple comparison problem on a single time-series dataset.
> The 171-day window spans only one market regime (BTC bull-to-choppy period). Walk-forward
> validation is required before promotion.

### 6. Degradation Band: CRITICAL CONCERN

> [!CRITICAL]
> **Degradation is 2.6–3.1pp, far below the expected 20–40pp band.** The researcher's explanation
> is that this is a "structural parameter optimization of an already-working signal," not a new
> signal discovery. This argument is not fully credible for the following reasons:
>
> The 20–40pp degradation expectation in `pitfalls/vectorized_vs_tick.md` arises from execution
> timing gaps — specifically, the vectorized simulation assumes an instantaneous fill at the
> exact bar price, while tick-by-tick replay introduces entry delay, orderbook impact, and stale
> price effects. These execution gaps are **independent of whether the parameter being optimized
> is an entry signal or an exit parameter.** The trailing stop (80–91% of exits) is still
> subject to the same execution fidelity difference between the two harnesses.
>
> The actual explanation for the low degradation is structural: both the vectorized and the
> tick validation scripts use the **same underlying data source** (ASOF-joined PM trade prices
> from the Parquet snapshot) and the **same Python loop per market** — they are not independent
> implementations. The tick validation adds slippage and fees explicitly but uses the identical
> bar-level PM price series. The "tick-by-tick" label is therefore misleading: this is not a
> `SyncReplayRunner` tick replay against live orderbook data, it is a second Python-loop simulation
> with arithmetic adjustments for cost. True tick degradation would require replaying through the
> production strategy harness with realistic fill latency.
>
> The premortem (now confirmed) flagged this: "The exit happens because `gbm_ours < 0.35`, but
> the position's P&L at exit depends on the PM orderbook price — not on whether GBM subsequently
> recovers." The PM price used for exit is the ASOF-joined last trade price, not a live bid/ask.
> In production, the exit order hits a real orderbook. Actual exit slippage is likely higher than
> the flat 1% half-spread assumed.

---

## Additional Concerns

> [!CRITICAL]
> **"False stop rate" metric is mis-defined.** The validation script (line 568–570) defines a
> false stop as: `exit_type == "flip_stop" AND pnl_raw >= 0` — i.e., the exit price was above
> the entry price. This is not the correct definition of a false stop. A false stop should be
> defined as an exit where the position *would have been profitable if held*. Instead, the script
> checks whether the exit price was profitable, which merely asks whether the position was in
> profit at the moment of exit — it says nothing about what would have happened if held longer.
> The discovery script uses a different (better) definition: it cross-references the `no_flip_stop`
> variant's `won` flag to check whether the baseline's flip-stop exits would have resolved
> profitably without the stop (lines 553–561 of `01_baseline_analysis.py`). The validation script
> abandons this counterfactual framing in favor of a weaker measure, making the 75% false stop
> rate in the primary config non-comparable to the 58.8% false stop rate in the discovery script.
> The inconsistency is not surfaced anywhere in the comparison document.

> [!CRITICAL]
> **The "best config" selection criterion is internally inconsistent.** The validation script
> (line 694) selects the best config by maximum Sharpe ratio, yet the comparison document and
> implementation recommendation select the "Primary" (thr=0.25, delay=3) config — which has
> Sharpe 48.46 — over the "Aggressive" config with Sharpe 49.08. The script's own
> `best_config` field in `results.json` (line 334) says `"aggressive"`, but the recommendation
> throughout the comparison document pushes "Primary." The researcher resolves this by
> hand-waving about false stop rates, but the selection process has no pre-specified criterion.
> Post-hoc selection of a runner-up config on qualitative grounds is a form of researcher
> degrees of freedom that inflates effective multiple comparisons.

> [!WARNING]
> **The Sharpe ratios (43–49 annualized) are economically meaningless as reported.** The
> researcher acknowledges this in the comparison document, but uses these values anyway as the
> primary selection criterion (`max(viable, key=lambda x: x["sharpe"])`). The Sharpe is computed
> on daily summed PnL from $50 bets with ~93 uncorrelated trades/day. Intra-day positions in the
> same BTC window are correlated (all exposed to the same BTC move). The daily PnL variance is
> therefore understated, and the Sharpe is overstated. The reported Sharpe of 43–49 should not
> be compared against any external benchmark or used to gate promotion.

> [!WARNING]
> **The sigma tercile thresholds are fit on the full dataset population (2026-03-10 run).** At
> `sigma_conditional_threshold()` (validation script lines 102–112), the t33/t67 boundaries
> are percentile cutoffs computed from `meta["sigma"].values` — all 16,176 markets. In production,
> the GBM strategy would have access only to a rolling historical sigma distribution. The
> sigma-conditional config's threshold choices (0.20/0.25/0.35 by tercile) are therefore
> calibrated with look-ahead embedded in the regime classification, even though individual
> market sigma is computed on pre-window bars.

> [!WARNING]
> **The "delay" parameter interacts with the 15-minute window duration in a way that is not
> stress-tested.** The premortem raised this: a delay of 3–5 ticks = 3–5 seconds of additional
> exposure. For positions entering with < 90s remaining (`NO_ENTRY_S = 90.0`), a delay=5 means
> the flip stop cannot fire until 5 consecutive below-threshold bars occur — but there may be
> fewer than 5 bars remaining before the time_stop fires at `rem_s < 30`. The interaction is
> handled by the time_stop check preceding the flip_stop check (line 191 before line 200 in the
> validation script), so the delay does not cause a crash. However, the effective flip-stop
> coverage for late-entering positions is reduced to zero, and this creates an uneven distribution
> of the delay's benefit across hold durations. No analysis of hold-time-stratified results is
> provided.

> [!WARNING]
> **The improvement concentrates in high-volatility regimes (+9.2pp HR improvement), but those
> are also the regimes where the underlying GBM model is least reliable.** GBM assumes constant
> sigma derived from the prior 30-minute window. In high-vol regimes, realized intra-window
> volatility can differ materially from the pre-window estimate, making the GBM P(Up) values
> systematically unreliable. The results show that simply removing the flip stop in high-vol
> regimes produces 87.7% HR (discovery baseline), barely worse than the best variant's 88.1% HR.
> The marginal value of the optimized flip stop in high vol is ~0.4pp over "no flip stop at all."
> This is a fragile improvement.

> [!TIP]
> The `sigma_adaptive` variant (discovery results.json) underperforms severely (73.8% HR vs
> 79.9% baseline). The implementation uses `sigma_ratio = sigma / sigma_median` at discovery
> time, but the initial pass runs with `sigma_ratio=1.0` as a placeholder and is re-run
> post-hoc. The re-simulation loop (lines 451–531 of `01_baseline_analysis.py`) has an index
> alignment issue: it iterates over `meta.iterrows()` with a hard break at `i >= n_entries`,
> but the original simulation skips markets with `no_bars` or `no_signal` — meaning the `i`
> index in the re-simulation does not align with the entry index in the original simulation.
> The `sigma_adaptive` results should be treated as unreliable and excluded from conclusions.

> [!TIP]
> There is a 45-entry discrepancy in signal counts between discovery (15,934) and validation
> (15,889). The difference is attributable to the PM staleness gate applied in the validation
> but not consistently in the discovery script. The comparison table in `comparison.md` mixes
> results from datasets of different sizes (15,934 vectorized vs 15,889 tick), which slightly
> inflates the apparent degradation in absolute terms. This is minor but worth noting for
> reproducibility.

---

## Summary

The study addresses a legitimate operational concern (the flip stop fires 3x more often than the
trailing stop in paper trading) and uses a reasonable simulation architecture. The large sample
size (15,889 trades) makes individual HR estimates statistically precise, and the absolute edge
(77–82% HR against a 50% binary baseline) is real. However, the study has two blocking problems
and several material ones. First, the entire parameter sweep is in-sample with no walk-forward
validation; the selected config (thr=0.25, delay=3) was chosen from 13 variants on 171 days of
data, and the incremental improvement over baseline (+4.4pp) is small enough to be explained by
in-sample overfit. Second, the 2.6–3.1pp degradation from vectorized to "tick-by-tick" is not
credible as a true tick-fidelity test: both harnesses run the same Python loop over the same
ASOF-joined PM price series with only an arithmetic fee/spread adjustment separating them — this
is not `SyncReplayRunner` against live orderbook data. The false stop rate metric is also
inconsistently defined between discovery and validation, making the headline "75% false stops"
figure non-comparable to the discovery-phase finding. The researcher's explanation for the
anomalous degradation ("structural optimization, not signal discovery") is plausible but
untestable without a true tick replay. Promotion should be blocked until walk-forward validation
and a genuine tick replay are conducted.
