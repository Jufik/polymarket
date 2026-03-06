# Pre-Mortem: tag-hr-copy

## Assessment

**Verdict: GREENLIGHT WITH CONDITIONS**

The hypothesis is sound and does not contradict any CRITICAL knowledge entries. No fundamental flaw in the test approach. Two structural risks need explicit handling in the sweep design.

---

## Issues Found

> [!WARNING]
> **Tag-specific base rate must be computed per-period, not fixed.** Tag YES rates vary 9-73% across tags AND monthly YES rates swing 20-45% across time (`data/period_base_rate_variance.md`). If the sweep uses a fixed per-tag base rate derived from the full history to compute excess HR in each test window, excess HR can be over- or understated by 10-16pp. The qualifying threshold (`excess_hr > threshold`) must be computed against the trailing-window base rate, not the all-time tag base rate.

> [!WARNING]
> **Multi-outcome events inflate NO base rate by ~10pp.** Tags like politics and culture are dominated by multi-outcome events (N candidates → N-1 NO resolutions). The structural NO base rate for these tags is ~72.4%, not ~62% (`pitfalls/multi_outcome_base_rate.md`). If tag base rates are not disaggregated by event structure, NO-direction qualified traders in these tags will appear to have ~10pp more excess than they actually do. The sweep must compute event-structure-aware base rates per tag.

> [!TIP]
> **48h max-hold resolves the capital lock risk only if applied at entry time, not as a post-hoc filter.** The hold time knowledge (`execution/hold_time_capital.md`) confirms that without a hard `closed_at - now <= 48h` gate at signal time, long-dated markets absorb capital. Confirm the sweep filters by `time_to_resolution <= 48h` at the moment of the simulated entry (not at resolution).

---

## Non-Issues (Confirmed Clean)

- **Counting unit**: No consensus required (each trader fires independently). The counting unit risk (`pitfalls/vectorized_counting_unit.md`) does not apply — there is no N-trader consensus trigger. Each signal is one qualified trader entering one market. Signal count = market count if aggregated correctly.
- **SELL filtering**: Framing correctly implies BUY-only qualification. Must be enforced explicitly in SQL (`side = 'BUY'`), but the framing is correct.
- **Resolution mechanics**: Hypothesis uses direction (YES/NO) which maps correctly to asset_id resolution. No string-matching risk identified.
- **No consensus dedup issue**: Since there is no consensus requirement, the 72.6% single-trader inflation pitfall (`pitfalls/consensus_dedup.md`) does not apply here.
