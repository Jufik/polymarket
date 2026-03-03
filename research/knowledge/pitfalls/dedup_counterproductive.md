# Position-Level Dedup is Counterproductive in Copy Strategies

> **TL;DR**: Limiting to 1 signal per (trader, market) REDUCES HR by 2-4pp and hurts PnL. Multiple trades from the same trader in the same market carry ongoing conviction signal.

> [!WARNING]
> Do NOT apply position-level dedup to copy strategies. The vectorized gap anatomy
> identifies "16-26 trades per position" as dilution, but in tick-by-tick reality
> subsequent trades from the same trader are informative, not redundant.

## Finding

In the S2 Hit-Rate Copy strategy, position-level dedup (`dedup_per_position=True`)
was expected to address the 23pp signal dilution gap (Step 2 in the gap anatomy).

**Actual results across 3 OOS periods (walk-forward):**

| Variant | Avg HR | Total PnL | Total Fills |
|---------|--------|-----------|-------------|
| With dedup | 51.2% | -$7,185 | 2,684 |
| Without dedup | 53.8% | -$6,231 | 2,939 |
| **Delta** | **-2.6pp** | **-$954** | **-255** |

Dedup hurts EVERY period:
- Apr 25: -2.1pp HR, +$188 PnL (exception)
- Jul 25: -2.0pp HR, -$382 PnL
- Oct 25: -3.6pp HR, -$760 PnL

## Mechanism

The vectorized analysis counts each (trader, market) as one observation, so it
appears that 16-26 trades per position are redundant. But in tick-by-tick:

1. **First trade** in a market is often an exploratory position at a less-informative price
2. **Subsequent trades** represent doubling down or averaging in — these are STRONGER
   signals of ongoing conviction
3. Dedup discards the 2nd-Nth signals which tend to be at BETTER prices (the trader
   is now more confident about their position)
4. The consensus counter still works correctly (set-based, unique traders) — dedup
   only affects the FILL count, not the consensus trigger
5. With dedup ON, a trader who buys 10 times in a market generates 1 fill. Without
   dedup, the same trader generates 1 fill per consensus trigger (which may be
   multiple fills if the market is re-entered via the on_timer exit cycle).

**The vectorized Step 2 gap is real** but its fix is NOT dedup. The real fix would be
position-level TRACKING (enter once, never re-enter the same market regardless of
which trader trades it) — which is what the `_scaled` set already does. Dedup
restricts per-trader rather than per-market, which is too aggressive.

## Evidence

S2 HRC gap fix validation script:
`research/scripts/s2_hitrate_gapfix_validation.py`

Results JSON:
`research/output/s2_tick_hitrate_gapfix/gapfix_validation_results.json`

## Impact

- Do NOT enable `dedup_per_position` in any copy strategy
- Multiple trades from same trader = conviction signal, not noise
- The "signal dilution" gap (23pp) is real but unfixable at the trade level
- Position-level market dedup (enter once per market) is already implemented via `_scaled`

## Related

- `pitfalls/vectorized_tick_gap_anatomy.md` — Step 2 signal dilution analysis
- `pitfalls/no_hr_collapse_tick.md` — NO direction structural collapse
- `pitfalls/consensus_dedup.md` — consensus must count unique traders (different issue)

## Tags

`dedup`, `copy-trading`, `vectorized-vs-tick`, `conviction-signal`, `counterintuitive`
