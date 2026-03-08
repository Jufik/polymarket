# Skeptic Pre-Mortem: tag-hr-consensus

**Scope**: Hypothesis framing only — no artifacts exist yet. Checking for contradiction of CRITICAL knowledge and fundamental test design flaws.

## Assessment

**Greenlight with one WARNING.**

The framing correctly addresses the root cause of tag-hr-copy's failure: individual-trade vs consensus-signal mismatch. The fix is sound — matching the vectorized counting unit (market-level, Nth qualified trader) to the tick-by-tick trigger mechanism is exactly what `pitfalls/individual_vs_consensus_signal.md` prescribes.

All 6 CRITICAL pitfalls are either already built into the test design or explicitly flagged for implementation:

- Consensus dedup (`set.add(maker)`) — specified in test design
- Phantom signals (`first_trade >= test_start`) — explicitly listed in knowledge context
- Per-fold pool qualification — stated in test plan
- Resolution via asset_id — no string matching proposed
- Counting unit = market-level with `max(first_trade)` as signal entry — listed in knowledge context
- Settlement mid-sim — listed in knowledge context

> [!WARNING]
> The vectorized gap anatomy (`pitfalls/vectorized_tick_gap_anatomy.md`, Step 3) warns that
> high-consensus markets may be ANTI-predictive: popular markets are efficiently priced and
> consensus 5+ tends to degrade, not improve, prediction quality. Sweeping N={2,3,4,5} may
> reveal that N=2 dominates and N>=4 degrades. The success criteria must account for this:
> the "excess HR > 10pp after 20-40pp discount" bar may not survive at N>=4. Confirm the
> sweep reports HR by N value, not collapsed across N.

No CRITICAL contradictions found. The hypothesis is well-grounded in prior failure analysis.
