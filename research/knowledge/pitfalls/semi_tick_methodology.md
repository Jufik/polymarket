# Semi-Tick Methodology is Not True Tick-by-Tick

> **TL;DR**: Using `maker_positions` (resolved position aggregates) for "tick validation" bypasses capital constraints, fill friction, and temporal ordering. It produces <2pp degradation vs the expected 20-40pp — a methodology artifact, not a real result.

> [!CRITICAL]
> Semi-tick validation (processing resolved positions chronologically) is NOT a substitute for SyncReplayRunner tick-by-tick. It finds MORE signals than vectorized (impossible under capital constraints), has no fill model, and no position limits. Only use SyncReplayRunner with Strategy protocol objects for deployment-quality validation.

## Finding

Semi-tick validation of 3 candidates showed:
- 1-2pp degradation from vectorized (expected: 20-40pp)
- MORE signals in tick than vectorized (impossible with capital constraints)
- No fill friction applied

The near-zero degradation was initially celebrated but flagged by the skeptic as a methodology artifact.

## Why Semi-Tick Overestimates

1. **No capital limits**: Can enter unlimited simultaneous positions
2. **No fill model**: Every signal is filled at the ideal price
3. **No temporal ordering**: Uses `first_trade` timestamps but doesn't process individual trades chronologically
4. **No slippage**: Market impact is zero
5. **Settlement not modeled**: Capital never locks up

## When Semi-Tick IS Useful

- Quick directional validation ("does the signal have any correlation with resolution?")
- Direction decomposition (YES/NO breakdown)
- Parameter ranking (relative ordering of configs)
- NOT for absolute HR estimates or deployment decisions

## Related

- `pitfalls/vectorized_vs_tick.md` — the 20-40pp gap is real
- `pitfalls/vectorized_tick_gap_anatomy.md` — 6 compounding effects
- `execution/position_settlement.md` — settlement matters for capital-constrained replay

## Tags

`methodology`, `semi-tick`, `validation`, `critical`, `simulation`
