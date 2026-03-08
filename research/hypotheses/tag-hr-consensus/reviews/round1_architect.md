# Architect Review — tag-hr-consensus Round 1

**Date**: 2026-03-06
**Reviewer**: Architect
**Artifacts reviewed**:
- `scripts/sweep_duckdb.py`
- `discovery/results.json`
- `discovery/notes.md`
- `research/harness.py`, `research/sync_replay.py`, `research/fast_replay.py`, `research/db.py`

**Verdict**: APPROVED FOR VALIDATION — with 4 required implementation notes

---

## 1. Consensus Counting Correctness

**Finding: CORRECT — unique traders, not events.**

The sweep uses `count(DISTINCT c.trader)` in `_build_mkt_stats()` (line ~232) and the tick-by-tick
strategy is expected to use `set.add(maker)` to track unique entrants. Both counting units match.

The market-level aggregate table (`mkt_stats_*`) has exactly one row per `condition_id`, with
`n_traders` = count of distinct qualified traders in that market. The batch combo query then filters
`n_traders >= consensus_n`. This is the correct vectorized representation of "Nth unique qualified
BUY triggers signal."

**No dedup bug here.** `pitfalls/consensus_dedup.md` is satisfied.

---

## 2. Phantom Signal Filter

**Finding: CORRECTLY APPLIED in `_build_mkt_stats()` — but NOT in `_build_qual_table()`.**

In `_build_mkt_stats()` (the test-window position table `tp_*`), line 208 filters:
```sql
AND CAST(p.first_trade AS DATE) >= '{xs}'
```
This correctly removes phantom signals — any qualified trader whose first trade in the market
predates the test window start is excluded. The consensus trigger can only fire on NEW entries
within the test window.

However, `_build_qual_table()` computes excess_hr from training-window positions using:
```sql
AND CAST(p.resolved_at AS DATE) >= '{ts}'
AND CAST(p.resolved_at AS DATE) < '{te}'
```
This is correct for the TRAINING window (pool qualification). The phantom filter is only needed
in the test step, where it is applied.

**No phantom signal leak.** `pitfalls/phantom_signals.md` requirement is met.

---

## 3. Resolution Handling — asset_id / yes_won Boolean

**Finding: CORRECT. No string matching anywhere.**

The sweep uses `p.yes_won` (a boolean pre-computed in `maker_positions`) and
`first(c.yes_won)::BOOLEAN AS yes_won` in the market-level aggregate. No string comparison
against outcome names.

The `SyncReplayRunner._settle_market()` uses:
```python
yes_asset = cid_tokens.get("YES", "")
yes_won = yes_asset in resolution.winning_asset_ids
```
And `load_replay_resolutions()` populates `winning_asset_ids` from `token_won` boolean column in
`markets_resolved.parquet` — asset_id-based, never string matching.

**`data/resolution_mechanics.md` requirement is satisfied end-to-end.**

---

## 4. Volume Filter Application

**Finding: APPLIED AT THE WRONG LEVEL — this is the primary fidelity gap.**

The sweep's volume filter (`vol>=1k`, `vol>=2k`) is based on `sum(abs(net_usd))` of QUALIFIED
TRADERS' positions in `tp_{variant}`, which is the sum of position size across the N qualifying
traders in that market.

This is INTERNAL volume (capital deployed by the qualified pool), NOT total market volume.

This creates two problems for tick-by-tick:

1. **Look-ahead in vectorized sweep**: at the signal trigger time (when the Nth trader enters),
   the full `net_usd` of all N traders is not yet known — later traders haven't entered yet. The
   sweep measures total qualified-trader volume at resolution time, not at signal time.

2. **Tick-by-tick strategy cannot replicate the filter cleanly**: to apply vol>=1k at signal
   time, the strategy would need to track running position size of qualified traders and only
   fire when cumulative qualified volume crosses the threshold. This is different from what the
   vectorized sweep computed.

**Implication**: If the strategy fires on Nth-trader consensus without a vol filter, results will
be lower than the vectorized upper bound by more than the 20-40pp expected band (since high-volume
markets may constitute only a subset of N>=consensus markets). Conversely, if a vol filter is
applied at signal time using same-direction volume accumulated so far, there is partial look-ahead
because some traders may not have entered yet.

**Recommendation**: For the initial tick-by-tick run, omit the vol filter (use N=5, W=inf,
ep>=10pp with no vol minimum) and benchmark against the `vol_filter_usd=inf` vectorized row.
The `results.json` has this: Esports N=5, W=inf, vol>=1k at 82.3% HR; the "no vol filter" row
is not explicitly reported but can be reconstructed from the sweep's `price_ceil=1.00` results.

---

## 5. Tick-by-Tick Strategy Requirements

For `SyncReplayRunner` validation the strategy must implement `on_trade_sync()` and maintain:

### A. Qualified pool — per fold, not at-startup
The strategy must know which traders are in the qualified pool. For the tick test, pre-compute the
qualified pool from the training window and inject it as a frozen set before replay starts. Do NOT
re-qualify during replay — that would contaminate with test-window data.

The harness should call `run_fast_backtest()` with `universe` set to the condition_ids of the
test fold's markets, and the strategy initialized with the per-fold qualified pool.

### B. Per-market consensus state
The strategy needs a `dict[str, set[str]]` tracking qualified makers seen per market. On each
tick: if `trade.maker` is in the qualified pool and `trade.side == "BUY"`, add to the market set.
Fire signal when `len(market_set[cid]) == consensus_n`.

### C. Entry price — use tick price, not avg_ep
At signal time, the strategy submits a `TradeIntent` with `max_price = trade.price`. This is the
price of the Nth qualified trader's entry tick, which is consistent with how the vectorized sweep
computes `avg_ep` (volume-weighted average across the N entries, not a future price).

### D. Walk-forward execution
Each fold must be run as a separate `run_fast_backtest()` call with the fold's qualified pool.
Using a single run over all dates would contaminate the qualified pool across fold boundaries.
The harness already supports `start_month`/`end_month` for trade loading — use these per fold.

---

## 6. Simulation Gaps: Vectorized vs SyncReplayRunner

| Gap | Direction | Magnitude estimate |
|-----|-----------|-------------------|
| Vol filter look-ahead (see section 4) | optimistic bias in sweep | 5-15pp HR inflation |
| `hold_hours` computed from `max(first_trade)` to `resolved_at` — tick path holds to resolution | neutral if settlement fires correctly | 0pp |
| `avg_ep` computed over ALL N qualified traders — tick path enters at Nth tick price | optimistic bias in sweep (lower entry price) | 2-5pp |
| Capital constraint: $1k budget may force sub-position sizes in high-signal periods | pessimistic bias in tick | 0-5pp |
| Per-fold train window contaminates 2025-10 fold (base=65.4% vs 45.6% in 2026-01): sweep separates | neutral (per-fold correctly scoped) | 0pp |
| Settlement timing: `_res_timeline` fires when `res_time <= now` (trade timestamp) — accurate | neutral | 0pp |

**Expected total degradation**: 25-45pp from the combination of vol filter look-ahead plus standard
20-40pp band. The Esports vectorized upper bound is 82.3% (+33pp). Expected tick range with no vol
filter: 42-62%. With vol filter applied at consensus time: may be slightly higher (8-15pp boost
from quality selection) but with inflated look-ahead bias not directly attributable to fidelity.

**Anomalous degradation thresholds**:
- If tick HR > 65%: investigate look-ahead in strategy (consensus set not properly cleared, or
  using future qualified-pool membership)
- If tick HR < 40% (below base rate): investigate settlement rate via `runner.n_settled` —
  may indicate the universe set passed to `run_fast_backtest()` doesn't match the strategy's
  fired markets, causing 0% settlement rate on positions

---

## 7. Harness Config for Validation Run

No `config.toml` exists yet for this hypothesis. When created, required settings:

```toml
[strategy]
name = "tag_hr_consensus"
enabled = true
mode = "replay"           # required — not "vectorized"
capital_usd = 5000        # sized for ~50 concurrent positions at $100 each
max_position_usd = 100    # consistent with $12-17 median PnL at ~50¢ avg entry
max_open_positions = 50   # Esports fires ~210 signals/month; hold ~2h each
cooldown_s = 0            # no cooldown — multiple concurrent markets OK

[strategy.params]
consensus_n = 5
max_entry_price = 1.00    # no price filter initially — benchmark clean
min_excess_hr_pp = 10
window_hours = 0          # 0 = no window (inf)
tags = ["Esports", "Tennis"]
```

Capital sizing note: 210 signals/month = ~7/day, hold 2h each → max ~6 concurrent. $5k capital
with $100 positions = 50 slots. This is adequate; budget gate should rarely reject.

---

## 8. Harness Fidelity Assessment

The `SyncReplayRunner` correctly handles all requirements for this strategy:

- Settlement fires mid-replay when `res_time <= trade.published_at` (clock-ordered)
- `_settle_market()` uses `yes_asset in resolution.winning_asset_ids` (asset_id, not string)
- `n_settled` counter is populated; check this in post-run diagnostic
- `load_replay_resolutions()` reads `token_won` boolean from `markets_resolved.parquet` — no
  string matching anywhere in the resolution pipeline
- `load_replay_trades()` applies predicate pushdown on `condition_id` — universe filtering is
  efficient

One note: `load_replay_trades()` computes `published_at` from `timestamp` column
(line: `pl.col("timestamp").dt.epoch("s")`), not from the raw `published_at` field. This is
intentional (see harness bug #2 in MEMORY.md: published_at=0 for backfill trades). The clock
ordering is correct.

No harness changes required for this validation run.

---

## Summary

| Check | Status | Notes |
|-------|--------|-------|
| Consensus = unique traders | PASS | `count(DISTINCT c.trader)` + expected `set.add(maker)` |
| Phantom signal filter | PASS | `first_trade >= xs` applied in test positions |
| Resolution via asset_id | PASS | `yes_won` boolean throughout, no string matching |
| Volume filter timing | FAIL — fidelity gap | Vol computed at resolution, not signal time |
| Walk-forward fold isolation | PASS | Per-(ep,mpe) qual tables per fold |
| Harness settlement | PASS | SyncReplayRunner correct |
| Config exists | MISSING | Create before validation run |

**Blocking issue before validation**: None that prevent running. Vol filter gap is a known
upper-bound inflation source, not a bug — benchmark without vol filter first, then add as a
strategy feature with cumulative-volume-at-signal-time semantics.

**One item to verify**: The `yes_entry_data` external Parquet view in DuckDB (used for `avg_ep`
computation in the sweep) joins `trader + condition_id` from `tp_{variant}`. Verify that
`yes_entry_data.first_trade` is the first trade timestamp in the market (not overall), otherwise
the phantom filter in the ep subquery (`CAST(y.first_trade AS DATE) >= '{xs}'`) may silently
exclude valid entries where the trader's first trade in the market was before the test window
but the position resolved in the test window. Cross-check: the `tp_{variant}` already filters
`first_trade >= xs`, so the ep join on those rows is consistent. This is not a bug, just a
confirmation that the ep subquery and the tp filter are aligned.
