# Pre-Mortem: Score-Axis Pool Construction

**Date**: 2026-03-11
**Scope**: Framing check only — no sweep SQL to audit yet.

---

## Contradiction Check Against CRITICAL Knowledge Entries

### 1. In-Play Contamination [in_play_contamination] — CRITICAL RISK

The primary universe is Sports YES. The +16pp vectorized finding from cross-pool-consensus is almost certainly inflated by in-play entries (hold < 4h). Sports in-play signals have 97-99.8% HR but are uncopyable. The framing does not mention a hold-time filter. Without `hold_hours >= 4` applied to the pool-construction sweep, the "discovery" figure is unreliable.

> [!CRITICAL]
> The +16pp HR finding cited as "prior art" comes from a cross-pool-consensus sweep. If that sweep did not apply `hold_hours >= 4` for Sports markets, the figure is contaminated by in-play entries and cannot be used as a prior. Confirm the parent sweep applied this filter before treating +16pp as meaningful.

### 2. High Entry Price — Market Structure Artifact [price_level_base_rates] — CRITICAL RISK

The framing notes directional mode fires at **0.86 avg entry price**. At that price level the population HR is already ~93.7% and structural alpha is +6.3pp. Requiring dual-axis agreement may be selecting markets where consensus forms late (i.e., near settlement), not markets with genuine predictive information — exactly the in-play contamination mechanism.

> [!CRITICAL]
> 0.86 avg entry price sits in the structural-alpha band (+6.3pp over break-even for any trader). The claimed +16pp excess needs to be computed over the price-level-adjusted base rate at 0.86, not the tag base rate of ~40.7%. At 0.86 entry, break-even for YES is 86%; population HR at that band is ~93.7%. The real excess would then be `actual_hr - 93.7%`, not `actual_hr - 40.7%`. If the apparent edge vanishes after this adjustment, the signal is market structure, not alpha.

### 3. Sample Size — 3-12 Signals Over 8 Months

> [!CRITICAL]
> BUY-only mode produced only 3-12 signals in 8 months. This is far below the 50-trade minimum for any statistical claim. The dual-axis AND-gate will further reduce signal count. A parameter sweep over K_each x N_a x N_b (18 combos) on a universe this thin is pure in-sample fitting with no statistical power. The hypothesis must demonstrate signal count >= 50 in the chosen universe before sweeping parameters.

### 4. No Contradiction With Other CRITICAL Entries

- [consensus_dedup]: Hypothesis acknowledges unique-trader requirement — no violation if implemented correctly.
- [vectorized_vs_tick]: Framing explicitly budgets 20-40pp degradation — passes.
- [phantom_test_signals]: Not addressed. If the parent sweep used `resolved_at` in test window without `first_trade >= test_start`, phantom signals inflate the +16pp figure.
- [direction_decomposition]: Framing treats YES (Sports) and NO (Politics) separately — correct approach.

---

## Verdict

> [!CRITICAL]
> **Conditional greenlight**: The hypothesis framing is logically sound (orthogonal axes, disjoint pools, dual-axis AND-gate are all valid ideas). However, the foundational +16pp prior art figure is unreliable until three things are confirmed: (1) the parent cross-pool-consensus sweep applied `hold_hours >= 4` for Sports, (2) excess HR was computed over price-level base rate at ~0.86 entry (not tag base rate), and (3) the parent sweep applied `first_trade >= test_start`. Proceed to sweep only after confirming all three. If any fails, re-run the parent sweep with corrections before building on it.
