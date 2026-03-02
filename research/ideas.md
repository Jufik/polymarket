# Strategy Research Idea Backlog

## Queued

- [ ] **Maker volume fraction as signal** — traders with high MVF (limit orders) may be more informed
  - Source: data/derived/maker_volume_fractions.parquet exists, unexplored
  - Priority: HIGH
  - Compounding angle: MVF is computable per-trade, no hold-time dependency
  - Related: knowledge base has no MVF entries yet

- [ ] **Consensus velocity** — speed at which qualified traders converge on a side
  - Source: S1 research — consensus threshold is static, timing might carry signal
  - Priority: MEDIUM
  - Compounding angle: fast consensus → short hold time → faster recycling
  - Related: pitfalls/consensus_dedup.md

- [ ] **Category-specialized ensembles** — separate models per category, combine
  - Source: S1c notebook — category breakdown shows very different dynamics
  - Priority: MEDIUM
  - Compounding angle: sports/esports sub-models recycle in <1 day
  - Related: execution/hold_time_capital.md

- [ ] **Exit signal from trader reversals** — qualified traders selling = exit signal
  - Source: pitfalls/sell_is_exit.md — SELL is exit, but IS it an informative exit?
  - Priority: HIGH
  - Compounding angle: early exits free capital faster
  - Related: pitfalls/sell_is_exit.md

- [ ] **Price momentum at consensus** — entry price trajectory when consensus forms
  - Source: S1 research — entry price filter (L2) was dominant, momentum might refine it
  - Priority: LOW
  - Compounding angle: unclear, needs exploration

## In Progress

(none)

## Tested

- [x] **Hit-rate copy trading (S1)** — copy high-HR specialist traders with consensus filter
  - Result: 87.9% HR (tick), $0.94/trade, compounding_score ~2.3
  - Notebook: research/notebooks/S1_hitrate_copy_exploration.py
  - Status: PROMOTED to strategies_impl/s1_hitrate_copy/

## Parked

(none)
