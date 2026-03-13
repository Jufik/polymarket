# Pre-Mortem: GBM Flip Stop-Loss Optimization

**Date**: 2026-03-10
**Scope**: Framing check only — not a full methodology audit.

---

## 1. Contradiction with CRITICAL Knowledge

> [!WARNING]
> The framing imports equity-market intuition ("temporary dip recoveries") into a binary-resolution
> instrument. `trailing_stop_tuning.md` states explicitly: "Binary resolution means dip recoveries
> behave differently from equities." A recovered GBM signal does not guarantee a recovered PM price
> before window close. These are independent processes. The framing conflates "GBM recovers" with
> "position would have been profitable" — they are not equivalent.

No direct CRITICAL contradiction is triggered, but the framing sits in dangerous proximity to the
WARNING in `trailing_stop_tuning.md`.

## 2. Fundamental Flaw in the Test Approach

> [!CRITICAL]
> The proposed test ("simulate GBM trajectories over historical BTC 5-min windows... track whether
> GBM recovers") measures GBM signal recovery, not position PnL recovery. The exit happens because
> `gbm_ours < 0.35`, but the position's P&L at exit depends on the PM orderbook price — not on
> whether GBM subsequently recovers. A counterfactual study must track what the PM price would have
> been at the next N ticks, not just the GBM value. Without PM price data in the counterfactual,
> the "false stop" fraction is unmeasurable from exchange bar data alone.

## 3. Obvious Confounders

> [!WARNING]
> **Sigma feedback loop**: GBM flip exits preferentially cluster in high-sigma regimes (large BTC
> moves). Widening the threshold in high-sigma windows may leave positions open through adverse
> moves that are no longer "temporary." The proposed threshold sweep (0.25–0.40) is not conditioned
> on sigma — the optimal threshold likely varies with realized volatility, making a flat threshold
> search misleading.

> [!WARNING]
> **Confirmation delay asymmetry**: A 5-10 tick delay at the 5s timer cadence = 25-50 seconds of
> additional exposure. For a 5-minute window with ~2 minutes remaining, this delay consumes 20-40%
> of remaining time. The cost of delayed exit is not uniform across hold time — late-window
> positions are disproportionately penalized.

## 4. Verdict

**Conditional greenlight.** The core motivation (3x flip-to-trailing ratio suggests over-triggering)
is sound and worth testing. However, the test must be redesigned: counterfactual analysis requires
PM orderbook price replay, not just GBM trajectory simulation. Exchange bar data alone cannot
answer the question. Recommend restricting the study to the Parquet snapshot
(`data/research/trades/`) with tick-by-tick PM price reconstruction alongside the GBM signal, using
`SyncReplayRunner` or direct trade-level replay.
