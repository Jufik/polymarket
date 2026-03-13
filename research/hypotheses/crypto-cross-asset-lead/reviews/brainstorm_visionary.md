# Visionary Brainstorm: Crypto GBM Strategy Improvements

**Date**: 2026-03-10
**Author**: Visionary agent
**Context**: Cross-referencing crypto-gbm-exit, crypto-gbm-flip-stop, crypto-gbm-improvements,
and the full knowledge base to find improvements beyond what has been tested.

---

## Background: What the Data Says

The strategy has a demonstrated edge:
- +$2.10 median EV per $50 trade (tick-by-tick validated)
- 96.2% convergence exit rate
- 3.8% time-stop rate at -28.75% median PnL
- GBM flip stop fires on ~14% of trades at baseline thr=0.35; tighten to thr=0.25+delay=3 for best results
- PM is well-calibrated: entry price tracks actual resolution probability within ±4pp

The fundamental insight is that GBM is a SPEED ADVANTAGE, not a foresight advantage. PM lags
BTC by 10-30 seconds on large moves. The strategy captures that lag window. Once PM catches up,
the edge is gone — scalp and recycle capital.

---

## Idea 1: Volatility Spike Entry Suppression

**Signal/Mechanism**: Compute a short-lookback (60-120s) realized vol and compare it to the
30-min baseline sigma. If the ratio exceeds a threshold (e.g., 2.5x), suppress new entries for
that window until vol normalizes. An `intrawindow_vol_spike_ratio` parameter gates entry.

**Thesis**: The flip stop sweep shows that the high-vol regime is where the baseline is most
broken (HR drops to 78% vs 88% for tighter stops). Vol spikes are the causal mechanism: during
sharp BTC moves, PM lags, GBM fires, but then BTC reverses just as fast (mean reversion after
spike). The entry fires into the spike rather than into a sustained directional move. Suppressing
entry during active spikes (ratio >> 1) preserves capital for cleaner setups.

**Testability**: The `estimate_realtime_sigma` function already runs on 30-min lookback 1s bars.
Add a second call with `lookback_s=60` and compute `ratio = sigma_60s / sigma_1800s`. Use
`exchange_bars` data directly. This is a pure parameter addition to `CryptoGBMConfig` and one
extra sigma estimate in `ExchangePriceProvider`. Can be tested in tick-by-tick with
`SyncReplayRunner` on the existing 6-month BTC dataset.

**Expected Impact**: Targets the high-vol regime where flip stop fires 20% of the time. Suppressing
~15-20% of entries (the vol-spike subset) should improve HR by 2-5pp on remaining entries. The cost
is fewer trades per day, but each trade has higher EV. Best tested by stratifying the 15,845
simulated windows in the flip stop dataset by `sigma_60s / sigma_1800s` ratio at signal time.

**Priority**: High. The data infrastructure is entirely in place. This is a 2-hour implementation.

---

## Idea 2: PM Orderbook Momentum as Confirmation Filter

**Signal/Mechanism**: Before entering, observe the best_ask on the target token across 2-3
consecutive timer ticks (10-15 seconds). If the PM price is ALREADY MOVING toward GBM fair value
(ask declining on a YES buy, for example), entry is confirmed. If PM is static or moving away,
skip or wait. Parameter: `ob_momentum_ticks = 2` — require 2 consecutive ticks showing PM moving
toward fair value.

**Thesis**: The GBM edge is that PM hasn't yet repriced. But the orderbook is the mechanism by which
it does reprice. If we can observe the PM price beginning to converge before we enter, we know: (a)
the signal is real (PM agrees it was mispriced), and (b) convergence is already happening (faster
scalp exit). If PM shows zero movement over 10-15s after a large BTC move, it could mean the market
makers have already baked in the move and the apparent lag is stale orderbook data.

The `_get_pm_p_up` method already tracks YES and NO orderbook snapshots with timestamps. Adding
a small circular buffer per `condition_id` (3 entries, 5s each) gives the momentum signal without
additional data infrastructure.

**Testability**: Requires the live `orderbooks.raw` Kafka topic, not the Parquet snapshot. Can be
backtested in paper_dev by logging the orderbook sequence on entry and tagging each fill with the
pre-entry PM momentum direction. After 200+ fills, correlate momentum direction with convergence
outcome.

**Expected Impact**: Likely to increase the 96.2% convergence rate closer to 98-99% by filtering
out entries where PM is actively diverging from fair value. Lower trade frequency (maybe -15%)
but higher per-trade EV. Particularly valuable for the 3.8% time-stop losers, which are
disproportionately cases where PM never converged.

**Priority**: High. Low implementation cost. Can be A/B tested by running one instance with
momentum filter enabled and one without, and comparing convergence rates.

---

## Idea 3: Differentiated Exit Thresholds by Entry Price

**Signal/Mechanism**: The current convergence exit threshold is a fixed `exit_threshold = 0.02`
regardless of where we entered. For entries near 0.50 (symmetric), a 2pp gap closure may be genuine
convergence. For entries at 0.20-0.30 (asymmetric), a 2pp gap closure is noise. Use
`exit_threshold = max(0.02, entry_gap * exit_fraction)` where `entry_gap` is the GBM-PM lag at
entry and `exit_fraction` is a configurable parameter (e.g., 0.25). So for an entry with 12pp lag,
exit when gap closes to 3pp. For an entry with 8pp lag, exit at 2pp.

**Thesis**: The fixed 2pp convergence threshold was calibrated for "average" entries. But the
strategy fires on entries with 10-20pp lags. For a 10pp entry, waiting for 2pp residual gap means
we've seen 80% of expected convergence — reasonable. For a 15pp entry, 2pp means only 87%
convergence — still good. But for a 5pp entry (minimum threshold), 2pp means only 60% convergence
before arming the trailing stop. Proportional exit thresholds prevent premature trailing stop arming
on small-lag entries.

**Testability**: Fully testable in the existing tick-by-tick harness. Add `exit_fraction`
to `CryptoGBMConfig`, compute `scaled_exit_threshold` in `_check_exits` using `entry_gbm - entry_price`
from `_OpenPosition.entry_gbm` and `entry_price`. The `_OpenPosition` dataclass already stores both.

**Expected Impact**: Primarily affects entries with smaller lags (near the threshold). For entries
near 0.10 (minimum), the proportional threshold raises it to ~0.025-0.03, matching the threshold
floor. This should reduce the number of "trailing stop armed too early then triggered by noise"
cases, improving HR by 1-2pp. Secondary benefit: reduces the oscillation problem where PM crosses
the 0.02 threshold, arms the stop, then reverses — the proportional threshold is harder to
accidentally trigger.

**Priority**: Medium. Simple to implement. The improvement is modest but the logic is cleaner.

---

## Idea 4: Hysteresis on GBM Flip Stop (Require Re-Cross to Re-Enter)

**Signal/Mechanism**: After a GBM flip stop fires and the position is exited, the current code
re-admits re-entry via `allow_reentry = True` if the signal re-appears. But if GBM just crossed
below 0.35 and bounced back to 0.38, then dipped to 0.33 again — this re-entry after a flip exit
is almost certainly a continuation of the reversal, not a new opportunity. Add a cooldown state:
after a flip exit on window X, require GBM to recover above `gbm_flip_recovery` (e.g., 0.50) before
allowing re-entry on the same window.

**Thesis**: The flip stop sweep found that thr=0.25+delay=3 is optimal because it waits for
sustained pessimism. The re-entry problem is the inverse: after a flip exit, BTC often continues
reversing. The window saw one flip; the PM price already moved against us. Re-entering requires
the GBM to genuinely recover past neutrality (0.50), not just bounce off 0.30 back to 0.38.
This is a microstructure insight: after a sharp reversal, the probability of further reversal is
elevated (momentum), and re-entry before recovery is likely to hit another flip stop.

**Testability**: Straightforward extension to `_OpenPosition` — add `flip_exited: bool` flag.
In `_check_exits`, when a flip stop fires, mark the condition_id. In the entry section, skip
re-entry unless GBM has since exceeded `gbm_flip_recovery_threshold`. Can be tested by logging
re-entry attempts on windows that previously had flip exits, and tracking their PnL outcomes.

**Expected Impact**: Affects only the ~14% of trades that hit flip stops. Among those, some
fraction attempt re-entry (how many depends on `allow_reentry` frequency). Conservative estimate:
prevents 30-50% of post-flip re-entries, each of which currently has below-average HR. Estimated
HR improvement on re-entry trades: +5-10pp. Overall strategy impact: small (1-3pp on the 14%
flip-exit subset), but high-precision improvement.

**Priority**: Medium. Requires careful logging to validate, but the implementation is 10 lines of code.

---

## Idea 5: Multi-Timeframe Sigma — Regime-Aware Sigma Selection

**Signal/Mechanism**: Compute sigma over three lookback windows simultaneously: short (5 min, 300s
1s-bars), medium (30 min, 1800s), and long (24 hours via minute bars). At signal time, select the
sigma that best represents the CURRENT regime: if short/medium ratio exceeds 1.5, use short sigma
(we're in a high-vol burst); if short/medium is stable, use medium (current behavior); if
medium/long ratio < 0.7, use long (vol has been anomalously low, market is quiet). Expose as
`sigma_selection_policy: "adaptive" | "short" | "medium" | "long"`.

**Thesis**: The current realtime sigma (1800s lookback) is a reasonable default. But sigma is
highly non-stationary. A 30-minute lookback includes the last burst if one happened 25 minutes ago,
making current sigma appear elevated even though BTC is now quiet. Conversely, a 30-min lookback
during a quiet spell before a spike underestimates current risk. Multi-timeframe sigma selection
adapts to regime faster without the flip-flop behavior of pure short-window sigma.

The flip stop sweep's sigma-adaptive variant was rejected because it adjusted the THRESHOLD by
sigma. This idea is different: it selects the best sigma ESTIMATE for the GBM model itself, not
for the threshold. Accurate sigma makes GBM P(Up) more accurately reflect true probability,
reducing both false entries and false flip stops simultaneously.

**Testability**: Can be added to `ExchangePriceProvider.handle_bar()` by maintaining three
`_second_closes` buffers (300s, 1800s, 7200s). The `get_features()` method already exports
multiple sigma values. The strategy's `on_timer()` section that selects sigma can implement the
policy. Test by comparing the 3-policy variants on the 6-month BTC tick dataset.

**Expected Impact**: The key regime error is entering in high-vol bursts with a stale low-vol sigma
(sigma underestimated → d₂ overestimated → GBM is overconfident → fires into noise). Multi-timeframe
selection should reduce this error by 30-50%. Expected HR improvement: 1-3pp overall, concentrated
in the high-vol regime where the strategy is currently weakest. Compounding benefit: more accurate
GBM also improves flip stop calibration (fewer false signals).

**Priority**: Medium. Moderate implementation effort (3 sigma buffers + selection logic). The
expected gain is real but requires tick validation to quantify precisely.

---

## Idea 6: Flat-Window Entry Suppression (No-Gap Markets)

**Signal/Mechanism**: Compute the absolute log-return from window open to now: `abs(log(spot/s0))`.
If this is below a minimum level (e.g., `min_window_move = 0.001` = 10 basis points), skip entry
regardless of apparent GBM-PM lag. This filters markets where BTC has barely moved since window
open — the apparent PM "lag" is likely just the PM spread, not genuine mispricing.

**Thesis**: GBM generates a useful signal when `log(S_t/S₀)` is meaningful relative to `sigma * sqrt(T)`.
When BTC has barely moved (say 5bp in 10 of 15 minutes), the GBM P(Up) will be close to 0.50 by
construction, and any signal near the threshold comes from rounding/noise rather than genuine price
discovery lag. The `min_gbm_deviation` parameter (0.05) is supposed to handle this, but a 5bp move
can still produce GBM deviations above 0.05 when sigma is very low.

Critically: these flat-window markets have high PM market maker density (prices are stable so MMs
are active). The spread is tight, entry quality is good. But the SIGNAL is weak: PM will not
converge because it was never wrong. The 3.8% time-stop rate is disproportionately these markets.

**Testability**: Pure addition to the entry section of `on_timer()`. Add `min_window_move` to
config, compute `abs_log_return = abs(math.log(spot_price / s0))`, gate entry. Test on tick data
by comparing convergence rates between flat-window entries (low abs_log_return) and active-window
entries. The `s0_prices` dict already stores S₀ per window.

**Expected Impact**: Based on the exchange_bars distribution: BTC has sigma_1m ~0.000670, meaning
in 10 minutes the expected abs move is `0.000670 * sqrt(10) = 0.0021 = 21bp`. Markets with < 10bp
(0.001) log-return are roughly the bottom quartile. These likely have the lowest convergence rates.
Filtering them should raise average convergence rate from 96.2% toward 97.5-98%, and reduce time-stop
rate from 3.8% to ~2.5%. Small improvement but at zero trade-off for the removed trades.

**Priority**: High. 5-line implementation. The false positive rate is directly measurable in logs
by correlating `abs_log_return` with convergence outcome on historical entries.

---

## Idea 7: 4-Hour Window Sub-Strategy

**Signal/Mechanism**: The existing window parser (`parse_window`) accepts only 5-min and 15-min
windows (`if duration_min not in (5, 15): return None`). The data shows 6,117 resolved BTC
4-hour windows with non-trivial volume ($5,715 median). Deploy a parallel sub-strategy targeting
4-hour windows with adapted parameters: higher threshold (GBM signal strength decays as T grows,
requiring larger moves to maintain the same d₂), longer hold tolerance, and a different sigma
lookback (7-day rather than 24h, since 4h windows have more time for regime drift).

**Thesis**: 4-hour windows have a different risk/reward profile. The PM convergence may be slower
(fewer trades per window, less market maker activity), but each trade can have higher EV because the
GBM signal is computed over 4 hours of remaining time (larger sigma*sqrt(T) = wider uncertainty
band = larger potential gap). The "flat entry after big early move" problem is reduced because the
window is long enough for BTC to have meaningful directional drift.

The critical check: do 4h windows actually have PM price lag? The flip-stop analysis was done on
15-min windows. If 4h market MMs are slower to reprice, the GBM lag can persist for minutes
rather than seconds — enabling limit orders rather than market orders at the ask.

**Testability**: Requires modifying `parse_window` to accept `duration_min in (5, 15, 240)` and
adjusting the `CryptoWindowProvider` to filter 4h windows. Then running a dedicated tick backtest
on the 778 BTC 4h windows in the dataset. This is a medium-effort feature extension.

**Expected Impact**: 6,117 resolved 4h BTC markets is a meaningful universe. If even 10-15% show
PM lag > threshold, that's 600-900 additional signal opportunities over the 6-month dataset. At
BTC baseline EV, this is $1,260-$1,890 additional monthly PnL if the conversion rate matches
15-min windows. The main risk is that 4h markets are so efficiently priced that the lag rarely
exceeds threshold — but this is testable directly in the tick data.

**Priority**: Medium. Requires parser change + config change + new TOML. The infrastructure
supports it. The risk is low since it's an opt-in sub-strategy.

---

## Adjacent Signals

**Convergence speed as entry quality signal**: Log how fast the GBM-PM gap closes in the first
60s after entry. Fast convergence (gap halved in <30s) predicts high probability of clean scalp
exit. Slow convergence (gap unchanged at 60s) predicts higher time-stop risk. This is purely
observational from live fills but becomes a quality score for parameter tuning over time.

**Order book depth imbalance direction**: The existing `min_book_ratio` filter rejects entries when
bid_depth is too low. Extend this: track whether the imbalance is CHANGING toward the entry
direction (bid_depth growing for a YES buy). A growing bid implies demand is materializing, which
correlates with PM moving toward GBM fair value. A shrinking bid implies someone is lifting offers
against us — exit risk.

**Multiple simultaneous window entries**: When two BTC windows are active (e.g., a 15-min window
and a 5-min window for the same hour), both may fire signals in the same direction simultaneously.
This double-fire is not meaningful new information — it's the same BTC move triggering two correlated
bets. Add a cross-window correlation gate: if both windows fire in the same direction within 30s,
only enter the one with better entry quality (lower entry price = more gap to converge).

---

## Parameter Variations

1. **`gbm_flip_threshold = 0.25`, `gbm_flip_confirmation_ticks = 3`**: The flip stop sweep
   recommendation. Best overall improvement (+0.0022 per trade). Direct production candidate.
   Needs tick-by-tick validation before deploy. See `crypto-gbm-flip-stop/discovery/sweep_results.md`.

2. **`sigma_rt_lookback_s = 900`**: Halve the realtime sigma lookback from 30 min to 15 min.
   Faster regime adaptation in the volatile windows where the strategy is weakest. Risk: noisier sigma
   in quiet periods. Test alongside the 1800s default in the flip stop validation sweep.

3. **`threshold_floor = 0.02`**: Lower the floor from 0.03 to 0.02 late in the window (when GBM
   is most accurate). Currently the threshold never goes below 3pp regardless of how precise GBM is
   near window end. Late-window entries with 2-3pp gap have strong GBM confidence (small remaining
   uncertainty) — these are currently filtered out unnecessarily.

4. **`no_entry_within_s = 60` (from 90)**: Reduce the no-entry zone by 30s. With the flip stop
   at thr=0.25 (fewer false exits), entries with 60-90s remaining have more room to converge.
   Test whether entries at 60-90s remaining have comparable convergence rates to earlier entries.

5. **`exit_threshold = 0.015`**: Tighter convergence criterion (from 0.02). Arms the trailing stop
   earlier (smaller residual gap). Could improve the trailing stop's ability to capture
   late-converging moves. Trade-off: more positions where PM oscillates around 0.015 gap without
   truly converging, leading to premature trailing stop arming.

---

## Cross-Hypothesis Connections

**crypto-gbm-exit FINDINGS.md (Section 4)**: The Hold vs Scalp analysis shows that hold is only
better than scalp when entry price > 0.65 (GBM confidence > 0.75). The `hold_to_resolution_threshold`
is currently 0.65. The data supports this parameter as-is. The connection: Idea 3 (proportional
exit threshold) is consistent with this finding — near-convergence trades at high GBM confidence
should have tighter exit thresholds (less residual gap needed before arming stop).

**crypto-gbm-flip-stop sweep**: The sigma regime stratification shows the high-vol regime as the
primary problem. Idea 1 (vol spike suppression) and Idea 5 (multi-timeframe sigma) both target
this regime. They are additive: vol spike suppression prevents entry in the worst moments; adaptive
sigma improves GBM accuracy during sustained high-vol.

**execution/trailing_stop_tuning.md**: The trailing stop is effective in the 0.30-0.70 entry band.
The GBM strategy's typical entry range is 0.35-0.55 (buying underpriced side), which sits squarely
in the effective zone. This validates the current trailing stop design. However, the trailing stop
gap of 0.05 was calibrated for general consensus strategies. For the GBM scalp (shorter hold, faster
convergence), the gap may be too wide — a 4pp trailing gap might be sufficient and would capture
more of the convergence move before exit.

**execution/spread_microstructure.md**: Crypto half-spread is 0.01 (1 cent MAC). At $50 notional,
this is $0.50 effective slippage. The +$2.10 median EV already accounts for this. The implication
for Idea 7 (4h windows): if 4h windows have lower trade density, spreads will be wider (the
microstructure data shows higher spreads in low-volume markets). Need to model this before sizing.

**crypto-gbm-improvements (eth_viability.md)**: ETH is viable but requires threshold=0.14 and
$35 max bet. Key point: "Market maker density — fewer MMs on ETH → PM prices may lag MORE than BTC
(good for signal) but may also recover MORE SLOWLY (bad for scalp exit)." This slower recovery
means ETH convergence exits may take 20-40s longer than BTC, affecting the time-stop rate. The
ETH strategy should have `exit_min_time_remaining_s = 45` (from 30) to compensate.

---

## Compounding Improvements

**Capital recycling via faster exit confirmation**: Currently, the strategy checks exits on every
timer tick (~5s). On a 15-min window, there are ~180 timer ticks. If convergence happens at tick 60
(5 minutes in), the position is exited and capital is freed within 5s. No change needed here — the
timer-based exit loop already provides sub-5s exit latency. The bottleneck is PM settlement time,
not the strategy's exit detection.

**Parallel BTC + ETH deployment**: At current BTC rate (~20 trades/day at full load), $500 capital
utilization is ~20 × $50 = $1,000 gross notional, but positions typically hold for 5-15 minutes,
so actual peak utilization is 2-4 positions simultaneously. Adding ETH ($35 bets) at ~15 trades/day
adds $35-140 in concurrent utilization — comfortably within the $500 capital envelope if BTC and
ETH don't fire simultaneously on the same BTC move. The correlation risk is real (BTC and ETH
co-move) but manageable with a shared `max_open_positions` cap.

**Re-entry rate measurement**: `allow_reentry = True` is deployed but we don't know what fraction
of scalp exits result in successful re-entries. Log the re-entry rate and the PnL of re-entry
trades specifically. If re-entries have below-average convergence, the re-entry logic is adding noise
rather than signal. A data-driven re-entry threshold (require larger gap for re-entry, e.g., 1.5x
normal threshold) could improve re-entry selectivity.

---

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **Vol Spike Entry Suppression**: Suppress GBM scalp entries when 60s sigma > 2.5x 1800s baseline.
   Targets the high-vol regime where flip stop fires most. Priority: high.

2. **PM Orderbook Momentum Confirmation**: Require 2-3 consecutive orderbook ticks showing PM moving
   toward GBM fair value before entry. Reduces false entries on stale orderbook data. Priority: high.

3. **GBM Flip Stop Deployment**: Implement thr=0.25 + confirmation=3 ticks from flip stop sweep.
   Best vectorized improvement (+0.0022/trade). Needs tick validation. Priority: high.

4. **4-Hour BTC Window Sub-Strategy**: Extend `parse_window` to accept 240-min windows. ~6,100
   additional resolved markets to test against. Priority: medium.

5. **Proportional Exit Threshold**: Scale `exit_threshold` proportionally to entry lag size.
   Prevents premature trailing stop arming on small-lag entries. Priority: medium.

6. **Multi-Timeframe Sigma Selection**: Add 5-min and 4-hour sigma estimates to `ExchangePriceProvider`.
   Select sigma based on current regime. Improves GBM accuracy during vol transitions. Priority: medium.

7. **ETH Tick Validation**: Run SyncReplayRunner on 26,616 ETH Up/Down markets with threshold=0.14,
   base_bet=$35. Confirm positive EV before ETH paper deployment. Priority: high (deployment gate).

8. **Flat-Window Entry Suppression**: Gate entry on minimum abs log-return since window open
   (`abs(log(spot/s0)) > min_window_move = 0.001`). Removes the bottom quartile of no-signal entries.
   Priority: high.

---

## Summary

The crypto GBM strategy is already well-optimized for the core scalp loop. The remaining improvements
fall into two categories.

The first is precision improvements to entry and exit quality — these are incremental but concrete:
vol spike suppression (Idea 1), flat-window filtering (Idea 6), orderbook momentum confirmation
(Idea 2), and the flip stop deployment from the sweep results (thr=0.25, confirmation=3). Each of
these targets a specific failure mode visible in the existing data. Together they could reduce the
3.8% time-stop rate to ~2% and improve HR by 3-5pp, adding roughly +$0.30-$0.50 per trade to the
baseline +$2.10.

The second category is scope expansion: 4-hour windows (Idea 7) and ETH deployment (per
eth_viability.md). Both require tick validation before production. ETH is the higher priority because
the infrastructure is ready — just needs a TOML config and 1-2 weeks of paper trading to confirm
fill quality. 4-hour windows are a lower-probability but potentially higher-impact expansion that
could meaningfully increase trade frequency without increasing capital requirements.

The most actionable next step is the flip stop deployment (thr=0.25, delay=3) combined with flat-window
suppression — both are implementable in a single session and together represent the clearest data-driven
path to improving per-trade EV without any structural changes to the strategy.
