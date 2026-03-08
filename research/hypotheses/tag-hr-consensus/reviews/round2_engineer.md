# Engineer Review: tag-hr-consensus (Round 2)

**Date**: 2026-03-06
**Reviewer role**: Engineer — methodology audit and production viability estimate
**Source files audited**:
- `validation/results_r2.json`
- `validation/notes_r2.md`
- `validation/strategy.py`
- `validation/run_validation.py`
- `scripts/sweep_duckdb.py`
- `research/sync_replay.py`
- `research/harness.py`
- `research/fast_replay.py`
- `src/polymarket_pipeline/strategies/promotion.py`
- `src/polymarket_pipeline/strategies/execution/simulated.py`

---

## 1. Entry Price Audit

**How max_price is computed in strategy.py (line 145):**

```python
max_price=min(signal_price + 0.02, self._price_ceil),
```

`signal_price` is `trade.price` — the price of the Nth qualifying trader's BUY tick that triggered the signal. The strategy adds a 2-cent urgency buffer and caps at `price_ceil=0.75`.

**Observed fill prices vs. signal prices:**

| Combo | Avg Fill | Notes |
|-------|----------|-------|
| Esports primary | 0.499 | Near mid, reasonable |
| Esports sensitive | 0.525 | Elevated — 2025-10 fold drove fill to 0.670 |
| Tennis primary | 0.497 | Near mid overall; 2025-07 fold = 0.517 (eroded edge) |
| Tennis sensitive | 0.478 | Best fill distribution across combos |

**Critical observation — fill price equals signal price + 0.02 (or price_ceil cap).**

`SimulatedExecutor` fills at exactly `intent.max_price` with no rejection. This means the recorded fill price is `min(Nth_trader_price + 0.02, 0.75)`, not the actual orderbook ask at signal time. In live paper trading with `PaperExecutor`, the fill would be the best ask on the CLOB WS orderbook at the moment the order lands — which could be:

- Equal (if market has not moved since the trigger trade)
- Higher (if the signal trade itself moved the market or if there is queue ahead)
- Unavailable (if the YES side has no liquidity at price ceiling)

The +0.02 buffer is the only slippage allowance in the model. For binary markets trading near 0.50, a 2-cent buffer represents a 4% cost-of-entry, which is substantial relative to the edge (+3.4pp Esports, +11.6pp Tennis). There is no modeling of the delay between signal detection and order submission, which in a live system would be at least 100-500ms due to orderbook polling intervals on the CLOB WS.

**Assessment: optimistic.** The fill model assumes the market waits for the strategy to arrive. In reality, the signal event (the Nth qualified trade) is observable to all participants simultaneously. The window between signal and fill is a race, not a guarantee.

---

## 2. Fill Model Assessment

**Executor used:** `SimulatedExecutor(fee_pct=0.0)`

This is the most permissive executor in the framework. Key properties:

- Fills every intent at exactly `max_price` — no rejection, no partial fill
- Fee is zero (`fee_pct=0.0`)
- No slippage cost
- No impact model
- No orderbook depth check

The harness's `run_fast_backtest()` also defaults to `SimulatedExecutor`. The `RealisticFillSimulator` exists in the codebase and was not used. Its slippage model adds `(half_spread + impact) * size_usd` to `fee_usd`, where:
- `half_spread` is calibrated from per-market trade-to-trade price changes
- `impact = size_usd / estimated_liquidity * impact_scale`

**Estimated slippage cost if RealisticFillSimulator had been used:**

Polymarket binary markets near 0.50 typically have half-spreads of 0.5–2.0 cents and shallow liquidity at $100–$500 per level. At `size_usd = $100` per signal:

| Market liquidity estimate | Half-spread | Impact (100/500) | Total slippage | PnL impact per signal |
|--------------------------|-------------|-------------------|----------------|----------------------|
| $500 depth | 1.0 cent | 2.0 cents | 3.0 cents | -$3.00 |
| $200 depth | 1.5 cents | 5.0 cents | 6.5 cents | -$6.50 |

Applying $3–$6 per signal to Esports primary (525 signals, +$32 total PnL) yields a net PnL range of **-$1,543 to -$3,393** — deeply negative. This is not a pessimistic scenario; it is a realistic one for thinly-traded Esports markets.

**Assessment: too lenient.** Using `RealisticFillSimulator` with calibrated spreads is required before any promotion decision. At $100/position the results would turn significantly negative for Esports primary and catastrophically negative for Tennis.

---

## 3. Bootstrap Window Assessment

**No bootstrap_hours parameter exists in the validation harness.** The strategy's qualified pool is built from a training window (6 months) in the DuckDB layer and passed in as a frozen `set[str]`. The strategy itself has no warm-up concept — `_traders`, `_timestamps`, and `_fired` are empty dicts at fold start and populate as test-window trades arrive.

**Phantom filter behavior:** `strategy.py` line 86 applies `if ts < self._test_start: return None`. This correctly prevents pre-test-window trades from contributing to signal generation. It does not prevent the strategy from firing on the very first day of paper trading — there is no warm-up gap.

**In a paper trading deployment**, the pool would be built from ClickHouse historical data (the PolarsBackend/ClickHouseBackend query path), which is equivalent to the DuckDB training window. Pool construction is offline, so no bootstrap delay is needed for the qualification step.

**However**: the `yes_asset_ids` mapping must be populated from `token_market_map` before the first trade arrives. In the live `LiveRunner` + `ClickHouseBackend` path, this would happen during `compute()` at startup, which requires the ClickHouse backend to be reachable. This dependency is not modeled in the validation harness.

**Assessment: sufficient for signal generation, but the pool-to-live-system bridging is not tested.** The strategy would be cold-started with a frozen pool from the most recent 6-month training window, which carries the pool-explosion risk documented in notes_r2 (Esports 2026-01: 774 traders).

---

## 4. Position Sizing Viability

**Config used in validation (run_validation.py lines 231–235):**

```python
config = StrategyConfig(
    name="consensus_copy", enabled=True, mode=ExecutionMode.REPLAY,
    capital_usd=50_000, max_position_usd=POSITION_SIZE_USD,   # $100
    max_open_positions=500, cooldown_s=0,
)
```

**Per-signal size: $100 USD.** Capital ceiling: $50,000. Max 500 concurrent open positions.

At $100/signal, signal rates are:

| Combo | Signals/month | Concurrent exposure |
|-------|--------------|---------------------|
| Esports primary | 175 avg | 175 × $100 = $17,500 deployed |
| Tennis primary | 147 avg | 147 × $100 = $14,700 deployed |
| Both combined | ~322 | ~$32,200 deployed |

Capital utilization is therefore ~35-65% of the $50,000 ceiling, which is within bounds. However:

**Avg hold is 4–7 hours (Esports) or 4–13 hours (Tennis).** With ~175 Esports signals per month and 7h avg hold, roughly 175 × 7 / 720 = 1.7 positions open concurrently on average. Peak concurrency could be 10-20× that during active competition windows. The `max_open_positions=500` cap is never binding.

**Orderbook depth at $100 per position:** Esports binary markets frequently have total YES-side depth under $500 at the best ask. A $100 market order would consume 20%+ of the available liquidity at the best level, pushing the effective fill price up by 2–5 cents beyond the displayed ask. This impact is not modeled.

**Assessment: $100/position is borderline viable for orderbook depth in liquid Esports/Tennis markets, but likely fills with significant slippage in the long tail of markets (smaller pools, narrower spreads). Scaling to $500/position — a natural next step — would make slippage the dominant cost.**

---

## 5. Slippage at Scale

Based on calibrated half-spread estimates for thin Polymarket binary markets:

| Position size | Half-spread cost | Market impact (500 avg depth) | Total slippage | % of $100 PnL budget |
|--------------|-----------------|-------------------------------|----------------|----------------------|
| $100 (current) | ~$1.00 | ~$2.00 | ~$3.00 | 3% |
| $500 | ~$5.00 | ~$50.00 | ~$55.00 | 55% — unviable |
| $5,000 | ~$50.00 | exceeds depth | reject / 5+ levels | unviable |

The impact model is nonlinear: doubling size more than doubles impact because depth at each level is consumed and the strategy walks up the book. At $500/position, the strategy would need to split across multiple resting limit orders and accept partial fills, which the current `urgency="patient"` flag does not implement in paper mode (PaperExecutor submits a single CLOB REST order).

**The strategy is only viable at $100–$200 per position.** Maximum aggregate throughput is therefore constrained to approximately $150-350 of deployed capital per signal, which caps theoretical monthly PnL at a few hundred dollars even if the signal were consistently positive.

---

## 6. Promotion Gate Likelihood

The promotion gates from `PromotionThresholds` (default values, `promotion.py` lines 62–71):

**Gate: vectorized/replay → paper_dev**

| Gate | Threshold | Esports Primary | Tennis Primary | Status |
|------|-----------|----------------|----------------|--------|
| min_trades | >= 1000 fills | 525 total signals | 442 total signals | FAIL (both) |
| positive_pnl | PnL > $0 | +$32.05 | -$2,455.25 | PASS / FAIL |
| min_sharpe | Sharpe >= 0.5 | aggregate not computable from fold data | aggregate not computable | UNCLEAR |

**min_trades gate: both combos fail.** The gate requires 1,000 filled positions from the replay run. Esports primary produced 525 over three months of test data; Tennis primary produced 442. To reach 1,000 fills with current signal rates (175 Esports/month, 147 Tennis/month), the validation window would need to cover approximately 6 months of test data. No single month delivers 1,000 signals at these parameter settings.

**Sharpe gate:** The per-fold Sharpe values are high in profitable folds (3.27 for Esports 2025-07, 6.69 for Esports sensitive 2025-07) but deeply negative in bad folds (-2.91 for Esports 2025-10, -9.54 for Tennis 2025-10). The aggregate Sharpe computed across all folds would depend on the ledger's sequential PnL trace, not the average of fold-level Sharpes. Given that one fold (2025-10) produces catastrophic losses for Tennis, the aggregate Sharpe is almost certainly below 0.5 for Tennis primary. For Esports primary (+$32 net), the Sharpe is near zero by construction — the PnL is barely positive and the variance is large.

**Estimated aggregate Sharpe for Esports primary:** Given $32 total PnL over 525 trades at $100 each, avg_edge ≈ $0.06/trade. With the per-trade variance implied by a 52.6% win rate on binary outcomes at ~$50 avg P&L per win/loss, sigma per trade ≈ $49.93. Annualized Sharpe ≈ (avg_edge / sigma) * sqrt(n_trades) = (0.06 / 49.93) * sqrt(525) ≈ **0.028**. This is 18x below the 0.5 threshold.

**Summary of promotion gate results:**

| Gate | Threshold | Esports Primary | Tennis Primary |
|------|-----------|----------------|----------------|
| min_trades | >= 1000 | 525 — FAIL | 442 — FAIL |
| positive_pnl | > $0 | +$32 — PASS (marginal) | -$2,455 — FAIL |
| min_sharpe | >= 0.5 | ~0.03 — FAIL | negative — FAIL |

**Neither combo would pass the promotion gate to paper_dev.**

---

## 7. Settlement Correctness Assessment

`SyncReplayRunner._settle_market()` and `_enrich_ledger()` use `winning_asset_ids` (a frozenset of asset IDs from `MarketResolution`). The ledger enrichment at lines 238-241 of `sync_replay.py` checks:

```python
won = record.asset_id in resolution.winning_asset_ids
```

This uses `asset_id` from the `TradeIntent` (populated from `yes_asset_ids` dict, which maps `condition_id → YES asset_id` from `token_market_map`). The resolution source is `data/research/metadata/markets_resolved.parquet`, built from CLOB API `tokens[].winner` — the ground truth source per CLAUDE.md. The resolution path is correct.

**One timing subtlety:** `_settle_market` is called when `res_time <= now` (i.e., when a trade tick arrives with timestamp past the resolution time). If the last trade in a market arrives before resolution (i.e., the market resolves but no more ticks come after), that market is settled in the final sweep at lines 171-174 of `sync_replay.py`. This is correct — all open positions are settled at the end of the run regardless of tick availability.

**Settlement is methodologically correct.** No errors found.

---

## 8. Consensus Timing: Signal vs. Fill Race Condition

A structural concern not addressed in the validation notes: the signal fires on the Nth qualifying trader's BUY tick. That tick has `trade.price` as the execution price the Nth trader received. But the strategy's intent is created with `max_price = signal_price + 0.02` — implying the strategy will fill at or below 2 cents above the triggering price.

In the Parquet snapshot, `trade.price` is the maker's execution price from historical RTDS/Goldsky data. The strategy would observe this price when the trade event arrives in the live Kafka stream. The question is: by the time the CLOB order is submitted, has the YES-side orderbook moved?

For consensus signals that require N=3-4 traders to all enter before firing, the triggering event is by definition the last of N coordinated entries. Those entries have already consumed liquidity. The remaining orderbook depth on the YES side is reduced by at least N × avg_size of the qualifying traders. The strategy then arrives to buy at a depleted book.

**This is not modeled anywhere in the validation harness.** The harness uses the triggering trade's price as if that price is still available. In practice, the ask has moved up by the time the strategy order lands. This is a systematic optimism bias in the entry price that would manifest as wider-than-expected slippage in paper trading.

---

## Summary

The validation uses `SimulatedExecutor(fee_pct=0.0)` — the zero-friction executor. This means all reported PnL figures are pre-slippage, pre-spread, and pre-impact upper bounds within the tick-by-tick simulation. The signal itself is real: Esports primary shows +3.4pp excess HR across three folds, and Tennis shows +11.6pp excess HR. However, at $100/position with realistic slippage (~$3/trade), the Esports primary PnL of +$32 across 525 signals becomes approximately -$1,543. Even with the most optimistic slippage estimate ($1/trade), net PnL is -$493.

Neither the Esports primary nor any Tennis combo is viable for paper trading in current form. The three specific blocking issues from an engineering standpoint:

1. **Zero-friction executor**: `SimulatedExecutor(fee_pct=0.0)` was used instead of `RealisticFillSimulator`. Slippage at $100/position is estimated at $3-7/signal. On 525 Esports signals this erases all PnL and goes deeply negative. This is the most critical gap.

2. **min_trades gate**: 525 fills (Esports) vs. 1,000 required. Would need approximately 6 months of test data at current signal rates to qualify. Signal rate is not the bottleneck — the gate threshold is calibrated for higher-frequency strategies.

3. **Pool explosion unresolved**: The 2026-01 fold has 774 qualified Esports traders. N=4 of 774 fires on 433 of the available markets, which is not a consensus signal — it is near-random. The mpe filter (0.80) reduced but did not prevent this. Until a hard pool size cap (e.g., max_pool=50) is implemented and validated, the strategy's behavior in large-pool regimes is unreliable.

The Tennis signal (+11.6pp excess HR) is statistically stronger than Esports, but the fill price mechanics are worse: the 2025-10 fold is a structural regime where HR collapses to base rate despite a large qualified pool (131 traders), producing -$4,296 in losses. The signal does not generalize across market regimes.

**Recommendation: not promotable. Requires (a) RealisticFillSimulator validation run and (b) pool size cap implementation before the next review cycle. If slippage-adjusted PnL turns positive on a new validation run with those changes, re-evaluate min_trades gate with a custom threshold for low-frequency consensus strategies.**
