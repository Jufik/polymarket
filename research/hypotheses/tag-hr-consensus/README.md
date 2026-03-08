# tag-hr-consensus

**Date**: 2026-03-06
**Status**: marginal (iterating — pool control + price ceiling untested)
**Category**: copy-trading / consensus signal

## Hypothesis

**Signal**: Fire entry when N distinct qualified traders (high-HR within a tag) have all entered YES positions in the same market. Entry triggers at the Nth unique qualified BUY.

**Thesis**: tag-hr-copy proved the qualified pool has genuine predictive power (67% vectorized HR for Esports). The failure was structural: executing on individual trades instead of waiting for consensus. By waiting for N traders to converge, we replicate what the vectorized sweep actually measured.

**Test**: DuckDB vectorized sweep over Parquet snapshot. Walk-forward with per-fold pool qualification. Sweep N={2,3,4,5}, time window={2h,4h,8h,inf}, price ceiling={0.60-1.00}.

**Success criteria**:
- Excess HR > 10pp above tag-specific base rate (after 20-40pp discount)
- Positive PnL in tick-by-tick
- Compounding score > 1.0

**Tags**: Esports, Tennis (1H excluded — confirmed gambling)

## Key difference from tag-hr-copy

The vectorized sweep and the tick strategy now use the **same counting unit** — consensus formation (Nth unique qualified trader) — not individual trades.

## Prior art

- tag-hr-copy REJECTED: vectorized 67% HR, tick 46% HR. Root cause: individual != consensus signal.
- See `research/knowledge/pitfalls/individual_vs_consensus_signal.md`

## Results

**Status**: MARGINAL — signal is real but not profitable after execution costs.

### Discovery (3 vectorized sweep rounds)
- Esports best: N=2, meh=15pp, mpe=0.80 → 69.2% HR, +20pp excess (UPPER BOUND)
- Tennis best: N=3, meh=20pp, mpe=0.90 → 82.0% HR, +45.5pp excess (UPPER BOUND)
- Directional variant (SELL NO): DEAD — mpe filter exposed it as noise
- Volume filter: +45pp uplift but LOOK-AHEAD BIASED (computed at resolution time)
- Pool entry price guardrail (mpe): confirmed effective — excludes sure-thing buyers

### Validation (3 tick-by-tick rounds)

| Round | Params | Esports HR | Excess | PnL | Tennis HR | Excess | PnL |
|-------|--------|-----------|--------|-----|----------|--------|-----|
| R1 | N=5, meh=10pp | 47.1% | -2.1pp | -$2,982 | 49.4% | +12.9pp | -$1,365 |
| R2 | N=4, meh=10pp, mpe=0.80 | 52.6% | +3.4pp | +$32* | 48.1% | +11.6pp | -$2,455 |
| R3 | N=2, meh=15pp, mpe=0.80 | 50.0% | +0.7pp | -$3,276 | 51.5% | +15.0pp | -$1,085 |

*SimulatedExecutor with zero fees. Realistic estimate: -$1.5K to -$3.4K.

**Degradation**: 19-31pp across all rounds (within expected 20-40pp band — signal is real).

### Prototype that works
2025-07 Esports fold, N=4: pool=47, 52 signals, HR=51.9%, Sharpe=3.27, PnL=+$616, avg_fill=0.443.

### Structural failure mode
Pool explosion in high-volume eras: 536-774 qualified traders in 2026-01. N=2 of 536 fires on virtually every market. The edge disappears when the pool is large.

### Untested fixes (reviewer-recommended)
1. price_ceil=0.40 (never implemented despite being the #1 fix)
2. RealisticFillSimulator (never used — all PnL numbers use zero-fee executor)
3. Dynamic pool floor: `threshold = base + k * max(0, pool_size - target)`
4. Hard pool cap at 50-60 (top traders by excess_hr)
5. Signal-time volume as hard gate

## Anti-Knowledge

### What we learned
1. **Consensus signal exists but has inverse scalability** — works when pool is small (early-growth regime), collapses when pool explodes (high-volume regime). The signal quality degrades as Polymarket grows.
2. **Pool quality > consensus count** — meh=15-20pp with tight mpe filter beats any N value with loose pool filters.
3. **Fill price is the economics bottleneck** — signals fire at 0.45-0.55 avg fill, which is break-even territory. Need price_ceil=0.40 to create margin.
4. **Directional variant is dead** — SELL NO adds no information after mpe filtering.
5. **Base rate non-stationarity in Esports** — 10% to 65% across folds. Walk-forward per-fold estimation is necessary but not sufficient.
6. **Volume filter is look-ahead biased** when computed at resolution time. Signal-time volume (from first N traders only) is untested but potentially causal.
7. **2025-10 fold is structurally hostile** — destroys PnL in every configuration for both tags.

### Conditions for revisiting
- Pool size control mechanism validated (dynamic floor or hard cap)
- price_ceil=0.40 tested with RealisticFillSimulator
- Signal-time volume tested as causal filter
- Category expansion to Sports/Politics where pool dynamics may differ

### Spawned ideas
- volume-as-primary-signal: volume alone may predict HR without trader pools
- consensus-regime-gate: suspend trading when base rate spikes above threshold
- first-mover-consensus: only count traders who enter early in market lifecycle
- esports-game-decomposition: per-game (CS2, Dota, LoL) pools to handle base rate non-stationarity
- dissent-filtered-consensus: filter by YES/NO disagreement ratio among qualified traders
