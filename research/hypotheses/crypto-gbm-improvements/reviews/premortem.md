# Pre-Mortem: Crypto GBM Strategy Improvements

**Date**: 2026-03-10
**Scope**: Hypothesis framing check only — not a full round review

---

## Knowledge Contradiction Check

**[gambling_market_taxonomy]**: No contradiction. The hypothesis explicitly acknowledges BTC
up/down markets are gambling markets and trades them intentionally. The framing is internally
consistent on this point.

**[price_level_base_rates]**: No contradiction. The hypothesis correctly states that entry price
approximates resolution probability and frames the edge as convergence speed (GBM lead time),
not foresight. The FINDINGS.md hold-vs-scalp EV table is consistent with the calibration data.

**[vectorized_vs_tick]**: Partial concern — see below.

**[trailing_stop_tuning]**: No contradiction. The GBM strategy entries cluster in the
0.40-0.55 PM price range (FINDINGS.md Section 4), which sits inside the 0.30-0.70 band where
trailing stops are helpful. The strategy does not apply stops to longshot entries.

---

## Approach Flaw Check

> [!WARNING]
> The baseline "+$2.10 median EV" comes from tick-by-tick validation (FINDINGS.md cites "tick
> validation" explicitly), which is appropriate ground truth. However, none of the six improvement
> axes have been validated at tick level — they are all proposed for vectorized or analytical
> evaluation. The vectorized-vs-tick pitfall applies directly to axes 1 (dynamic sizing), 2
> (re-entry logic), and 3 (fee-aware threshold): vectorized sweeps will overstate the gain by
> 20-40pp of whatever improvement is found. Improvements measured as +$0.10-0.40/trade
> (FINDINGS Section 8) are already at the noise floor of vectorized measurement error. Run
> tick-by-tick validation for any axis before treating the result as actionable.

> [!WARNING]
> Axis 2 (re-entry logic) has a structural confound. The strategy marks `cid` in `self._signaled`
> for the full window lifetime (`_cleanup_expired` only removes on window expiry). Re-entry logic
> requires clearing that flag after exit — but back-to-back entries on the same window create a
> position-tracking conflict: `self._positions[cid]` would be overwritten silently on the second
> entry (strategy.py line 215). Any sweep of re-entry must fix this data structure first, or
> results will reflect incorrect position accounting.

> [!TIP]
> Axis 3 (fee-aware threshold) deserves priority over axis 1 (dynamic sizing). The current
> `fee_pct = 0.03` in config is a flat approximation. The actual PM fee formula
> `0.25 * (p*(1-p))^2` is maximized at p=0.5 (~1.5625%) and is much smaller than 3% at
> extreme prices. At the strategy's typical entry range (0.40-0.55), the true fee is
> ~1.5-1.56%, meaning `threshold = 0.10` already has ~6.5pp of headroom after fees. The
> fee-aware threshold is unlikely to change behavior materially but is the cleanest to validate
> analytically without a full tick backtest.

---

## Obvious Confounders

> [!WARNING]
> The hypothesis assumes ~50 windows/day as a stable throughput. This depends on the BTC
> market count on PM, which is event-driven (PM lists new windows periodically). The GBM
> sigma input is a 24h lookback (`sigma_lookback_min = 1440`). During low-volatility regimes,
> `sigma` will be small, GBM will not deviate enough from 0.5 to clear `min_gbm_deviation =
> 0.05`, and trade frequency will drop substantially. Improvement axes tested during a
> high-vol backfill window may not generalize to low-vol periods. Stratify any sweep by vol
> regime.

---

## Verdict

**Greenlight with caveats.** No CRITICAL knowledge contradiction. The framing is
coherent — the edge source (GBM lead time, convergence speed) is consistent with the
calibration evidence. The two WARNINGs above are methodological, not fundamental: validate
tick-by-tick before treating any improvement axis as real, and fix the re-entry data structure
before testing axis 2.
