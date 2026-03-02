# Strategy Research Idea Backlog

## Queued

- [ ] **Maker volume fraction as signal** — traders with high MVF (limit orders) may be more informed
  - Source: data/derived/maker_volume_fractions.parquet exists, unexplored
  - Priority: HIGH
  - Compounding angle: MVF is computable per-trade, no hold-time dependency
  - Data: pure takers (MVF<0.1) = 25.8% HR; makers-who-take (MVF 0.5-0.9) ~45% HR

- [ ] **Consensus velocity** — speed at which qualified traders converge on a side
  - Source: consensus threshold is static, timing might carry signal
  - Priority: MEDIUM
  - Compounding angle: fast consensus → short hold time → faster recycling
  - Related: pitfalls/consensus_dedup.md

- [ ] **Category-specialized ensembles** — separate models per category, combine
  - Source: category breakdown shows very different dynamics (hold time, HR, volume)
  - Priority: MEDIUM
  - Compounding angle: sports/esports sub-models recycle in <1 day
  - Related: execution/hold_time_capital.md

- [ ] **Exit signal from trader reversals** — qualified traders selling = informative exit signal
  - Source: pitfalls/sell_is_exit.md — SELL is exit, but IS it predictive?
  - Priority: HIGH
  - Compounding angle: early exits free capital faster (shorter hold)
  - Related: pitfalls/sell_is_exit.md

- [ ] **Price momentum at consensus** — entry price trajectory when consensus forms
  - Source: entry price filter was dominant in prior research, momentum might refine it
  - Priority: LOW
  - Compounding angle: unclear, needs exploration

## In Progress

### S2: Insider Copy (HIGH priority)

**Hypothesis**: Some traders exhibit "insider knowledge" — infrequent, high-conviction,
high-accuracy bets on susceptible markets. Copy their BUY trades.

**Status**: Design approved, implementation in progress.
**Design doc**: `docs/plans/2026-03-02-insider-copy-strategy-design.md`

**Key features**:
- Two-stage market susceptibility filter (HIGH/MEDIUM/LOW)
- 6-feature Bayesian scoring: HR excess, conviction, selectivity, anomaly, timing, susceptibility
- Configurable: single insider vs consensus trigger
- Stop-loss protection (default 50%)
- Hold to resolution

**Open questions**:
- What's the actual insider pool size at various thresholds?
- Single trigger vs consensus: which has better risk-adjusted returns?
- Optimal stop-loss level vs hold-to-resolution?
- Category-specific insider detection (politics vs sports)?

## Tested

(none — clean slate)

## Parked

(none)
