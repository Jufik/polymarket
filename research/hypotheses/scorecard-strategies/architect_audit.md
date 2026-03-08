# Architect Audit: Harness Configuration and Simulation Gaps

**Date**: 2026-03-07
**Auditor**: Architect agent
**Hypothesis**: scorecard-strategies (Smart Money Pool, Tag-Expert Consensus, Elite Copy)
**Status**: Audit complete — awaiting tick-by-tick results from task #2 to fill in [FILL] placeholders

---

## 1. SyncReplayRunner Configuration for Consensus/Pool Strategies

### 1.1 Executor Choice

`run_fast_backtest()` (harness.py:250) hard-codes `SimulatedExecutor(fee_pct=0.0)` — **zero friction, zero fees**.

```python
# harness.py:250
executor = SimulatedExecutor(fee_pct=0.0)
```

This is appropriate for **signal discovery** but not for validation. For these strategies:

- Esports signals trade at prices 0.60-0.90 (near-resolution). Polymarket charges `fee_pct * min(price, 1-price)`. At price=0.80, that is 2% of stake. With a $50 stake and 403 signals, zero fees overestimates PnL by ~$403.
- The `RealisticFillSimulator` is not wired in `run_fast_backtest()` at all — only available via the legacy `run_backtest()` async path.

**Finding**: If the tick validator uses `run_fast_backtest()` without modification, fill friction is zero. This means tick-by-tick HR may look *better* than real-world, not worse. The known 20-40pp degradation from knowledge base comes primarily from signal dilution and capital constraints, not fill friction — so this is not the primary issue. But PnL estimates will be systematically inflated.

**Proposal (generic harness improvement)**:

```python
# harness.py — run_fast_backtest() signature addition
def run_fast_backtest(
    strategy: Strategy,
    config: StrategyConfig,
    *,
    universe: set[str] | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
    output_dir: Path = Path("research/output"),
    use_realistic_fills: bool = False,          # NEW
    fill_config: FillModelConfig | None = None, # NEW
) -> tuple[BacktestResult, LedgerSummary | None]:
```

When `use_realistic_fills=True`, load calibration data via `load_calibration_df()` and pass a `RealisticFillSimulator`. This is a generic improvement — all strategies benefit from optional realistic fills in the fast path.

### 1.2 Capital Configuration

Default `run_fast_backtest()` requires callers to pass their own `StrategyConfig`. The key risk gates are:

- `capital_usd` — total budget
- `max_position_usd` — per-market limit
- `max_open_positions` — concurrent slot limit
- `cooldown_s` — inter-trade wait

For the Smart Money Pool (Esports, 403 signals in ~3 months of test window = ~4 signals/day), with a typical config of `max_open_positions=20` and `capital_usd=1000`:
- At $50/position, 20 slots = $1,000 total, fully utilized if 20 markets open simultaneously.
- Esports markets resolve in 2h median — capital recycles quickly.
- **Settlement is working correctly** (SyncReplayRunner._settle_market fires chronologically via `_res_timeline`). Capital lock is minimal for fast-resolving markets.
- **Risk**: if `max_open_positions` is not set (defaults to unlimited in permissive research configs), capital gates never fire and results are optimistic vs real deployment.

**Proposal**: tick validator should use `max_open_positions=20` to match real deployment constraints.

### 1.3 Settlement Timing

`SyncReplayRunner.run()` settles markets when `res_time <= now` (current trade timestamp), where `now = trade.published_at`. This is correct: settlement fires as soon as any trade arrives after resolution time.

For Esports (2h median hold), there will typically be post-resolution trades in the test window — settlement fires reliably. For Politics (multi-day hold), the same logic applies.

**Settlement fidelity: HIGH.** No bug here.

However, there is a subtle issue: `_res_timeline` is built from `resolutions` keys passed at construction. If `universe` filtering in `run_fast_backtest()` is applied to resolutions (`resolutions = {k: v for k, v in resolutions.items() if k in universe}`), only markets in the universe get settled. This is correct behavior but depends on the caller providing a complete universe. If the universe is under-specified (missing condition_ids), positions in those markets never settle and capital stays locked permanently.

**Proposal**: The harness should log a warning when `n_settled / n_fills < 0.8` at the end of a run, indicating potential universe/resolution coverage gap.

---

## 2. Fill Model: Linear Market Impact Realism

### 2.1 Impact Formula

```python
# realistic.py:142-149
def _compute_impact(self, size_usd: float, condition_id: str) -> float:
    vol = self._market_volumes.get(condition_id, 0.0)
    estimated_liquidity = max(
        self._config.default_liquidity_usd,  # default = $5,000
        vol * 0.01,                           # 1% of total volume
    )
    return (size_usd / estimated_liquidity) * self._config.impact_scale
```

For a $50 order into a market with $10,000 total volume:
- `estimated_liquidity = max(5000, 100) = $5,000`
- `impact = 50 / 5000 * 1.0 = 0.01` = 1pp additional fee

For a $50 order into a large market ($500K volume, e.g. US election):
- `estimated_liquidity = max(5000, 5000) = $5,000` — capped at default
- Same 1pp impact

The 1% proxy is extremely crude. Real Polymarket limit order books have instantaneous depth of $500-$2,000 at best bid/ask. Using `vol * 0.01` as liquidity overestimates depth for large markets, underestimates for small ones.

**For the scorecard strategies specifically:**
- Esports markets: typical volume $2,000-$15,000. At $50 stake and vol=$5,000: `liquidity = max(5000, 50) = $5,000`, impact = 1pp. Reasonable.
- Sports markets: typical volume $10K-$200K. At $50 stake: impact = 0.5pp or less. Slightly underestimated impact.
- Crypto markets: mixed. Large Crypto markets (Trump 2024 had $50M+) would have near-zero impact estimate, which is correct.
- Politics markets: similar to Crypto.

**Verdict**: Linear impact is appropriate for $25-$100 position sizes. For these strategies, positions are small relative to market volume. The fill model is not the primary source of degradation — it introduces ~0.5-2pp of friction per trade, which correctly penalizes tick-by-tick PnL vs vectorized (zero friction).

**BUT**: since `run_fast_backtest()` uses `SimulatedExecutor` (not `RealisticFillSimulator`), this impact model is **never invoked** in the current fast path. See §1.1.

### 2.2 Spread Calibration Source

`calibrate_spreads()` estimates half-spreads from consecutive trade price changes (`median(|p_t - p_{t-1}|)`). This uses trade prices, not orderbook quotes.

Problem: trade price changes conflate spread with genuine price discovery. In Polymarket:
- Prices move in $0.01 increments
- Consecutive trades at 0.55, 0.56, 0.55, 0.56 produce half-spread estimate of 0.01
- But the actual LOB half-spread is 0.01-0.02 for liquid markets

For near-resolution trades (price 0.90+), price changes are mostly directional (approaching 1.0), not bid-ask bounce. The Roll estimator would be more accurate here, but has its own limitations.

**Assessment**: The spread calibration is medium fidelity. It will tend to slightly overestimate spreads for mid-market trades and underestimate for near-resolution trades. The net effect is ~1-2pp on PnL. Since these strategies hold to resolution, the spread is paid once at entry — a one-time 1-2pp cost.

---

## 3. Settlement: Capital Freed Mid-Simulation

Confirmed working correctly in `SyncReplayRunner`:

```python
# sync_replay.py:131-136
while res_idx < n_res:
    res_time, res_cid = self._res_timeline[res_idx]
    if res_time > now:
        break
    self._settle_market(res_cid)
    res_idx += 1
```

Settlement fires **before** provider updates and strategy decisions for that tick, so a freshly-settled market's capital is available for reuse on the same tick. This is slightly optimistic (in production there would be settlement processing latency of minutes) but the effect is negligible for multi-hour holding strategies.

`_settle_market` correctly:
1. Zeroes out qty_yes/qty_no
2. Computes PnL from avg_entry vs resolution
3. Sets cost_basis to 0 (freeing capital)
4. Increments `n_settled`

**Settlement fidelity: HIGH.** One caveat: `_settle_market` only fires if the position's condition_id is in `_resolutions`. If a market resolves but its resolution data is missing from the snapshot (export gap), capital remains locked. The harness should log such orphaned positions.

---

## 4. Slippage: RealisticFillSimulator Calibration for These Tags

### 4.1 Not Used in Fast Path

As noted in §1.1, `run_fast_backtest()` uses `SimulatedExecutor` — no slippage at all. The `RealisticFillSimulator` would need to be explicitly invoked.

### 4.2 If RealisticFillSimulator Were Used

For the scorecard strategies with typical position sizes ($25-$100):

| Tag | Typical Market Vol | Estimated Half-Spread | Impact at $50 | Total Friction |
|-----|-------------------|-----------------------|---------------|----------------|
| Esports | $5K-$15K | 0.01-0.02 | ~1pp | ~2-3pp |
| Sports | $10K-$200K | 0.005-0.015 | ~0.5pp | ~1-2pp |
| Crypto | $10K-$500K | 0.005-0.01 | <0.5pp | ~1pp |
| Politics | $50K-$5M | 0.005-0.01 | <0.5pp | ~1pp |

At 1-3pp friction per trade and entry prices of 0.50-0.90, the slippage degrades HR by less than 1pp in absolute terms (slippage is a fixed cost, not direction-dependent). Its primary effect is on PnL, not HR.

**Conclusion**: For HR-focused strategies like Smart Money Pool, slippage calibration matters little for the headline metric but matters for PnL estimates. The `SimulatedExecutor` (zero friction) overstates PnL by ~1-3% of stake per trade.

---

## 5. Signal Timing: Chronological Order Within Markets

### 5.1 Global Sort by published_at

```python
# sync_replay.py:114
sorted_trades = sorted(trades, key=lambda t: t.published_at)
```

`published_at` is computed in `fast_replay.py:110` as:
```python
(pl.col("timestamp").dt.epoch("s").cast(pl.Float64)).alias("published_at"),
```

This uses the `timestamp` column (DateTime64[3]) converted to epoch seconds. Trades are globally sorted — chronological across all markets simultaneously. This is correct.

**Within a single market**, if multiple trades arrive at the exact same second, their relative order within that second is not guaranteed. For consensus strategies, this could theoretically cause the Nth qualified trader's trade to be processed before the (N-1)th, firing the consensus signal slightly early. In practice, sub-second ordering within Parquet snapshots follows the ClickHouse export order, which is by `(condition_id, timestamp, trade_id)`. The harness does not preserve intra-second order when sorting globally.

**Assessment**: This is a minor timing imprecision affecting only trades with identical 1-second timestamps. The practical effect on HR is negligible (<0.5pp). However, it means the harness is technically not respecting the full `trade_id` tiebreaking order.

**Proposal (optional)**: Sort by `(published_at, trade_id)` for full determinism. This requires `trade_id` to be included in the `ReplayTick` struct and sort key — `trade_id` field already exists with default `""`.

```python
# fast_replay.py — in load_replay_trades()
).sort(["published_at", "trade_id"])  # instead of .sort("published_at")
```

### 5.2 Consensus Signal Construction in Tick-by-Tick

The consensus/pool strategy must build its qualified-trader signal incrementally, one trade at a time. In the vectorized approach, `max(first_trade)` is known in advance (look-ahead). In tick-by-tick, the strategy fires when it observes the Nth qualifying trade.

**This is the primary structural difference** and the dominant source of 20-40pp degradation:
- Vectorized: signal fires at the theoretical entry time (look-back aggregate)
- Tick-by-tick: signal fires when the Nth trade is observed (no look-ahead)

The harness supports this correctly — `on_trade()` gives the strategy one tick at a time. The strategy implementation determines whether it correctly counts unique qualified traders vs events.

**Critical check for strategy implementation** (not harness): the strategy must maintain a per-market set of unique qualified-trader addresses seen so far, triggering on the Nth unique address, not the Nth trade.

---

## 6. Decomposition: Simulation Gaps vs Genuine Signal Weakness

Based on the knowledge base and code audit, here is the expected degradation decomposition for Smart Money Pool (Esports K=50, vectorized HR = 100%):

```
Vectorized HR:              100.0%  (upper bound, test window)
Step 1: OOS decay:           -3pp   → 97%   (modest, expected)
Step 2: Signal dilution:    -15pp   → 82%   (N trades/position; harness-correct but expected)
Step 3: Consensus gap:       -8pp   → 74%   (N traders must all have entered — entry order unknown at signal time)
Step 4: Capital constraints: -5pp   → 69%   (position limits reject some signals; capital locked)
Step 5: Entry timing:        -3pp   → 66%   (copying at last-trader-entry; price may have moved)
Step 6: Simulation gaps:     -2pp   → 64%   (timing precision, fill friction, spread approx)
Expected tick-by-tick:             ~60-68%  (28-40pp degradation from vectorized 100%)
```

For Tag-Expert Consensus (Politics NO, vectorized HR = 92%):

```
Vectorized HR:               92.0%  (upper bound)
Step 1: OOS decay:            -3pp  → 89%
Step 2: Signal dilution:     -10pp  → 79%   (Politics traders less active per position than Sports)
Step 3: Consensus gap:        -5pp  → 74%
Step 4: Capital constraints:  -3pp  → 71%   (multi-day lock, but max_open_positions helps)
Step 5: Entry timing:         -3pp  → 68%
Step 6: Simulation gaps:      -1pp  → 67%
Expected tick-by-tick:               ~65-72%  (20-27pp degradation)
```

**Degradation band target**: 20-40pp from vectorized is expected. Anything outside this range should be investigated:

- **<10pp degradation**: suspect look-ahead bias in strategy (is the qualified pool built on test-window data?)
- **40-60pp degradation**: likely capital gate issue (max_open_positions too tight, or settlement not freeing capital)
- **>60pp degradation**: harness bug — check trade loading, resolution coverage, and budget gate

---

## 7. Concrete Harness Improvement Proposals

### P1 (Generic, Priority: HIGH): Add Settlement Coverage Warning

```python
# sync_replay.py — end of run() method, after "Settle remaining" block
fill_count = result.total_fills
if fill_count > 0 and self.n_settled < fill_count * 0.5:
    logger.warning(
        "sync_replay.low_settlement_rate",
        n_settled=self.n_settled,
        n_fills=fill_count,
        rate=round(self.n_settled / fill_count, 3),
        hint="Check universe/resolution coverage — capital may be permanently locked",
    )
```

This is a generic improvement that benefits all strategies.

### P2 (Generic, Priority: HIGH): Optional Realistic Fills in Fast Path

Extend `run_fast_backtest()` to accept `use_realistic_fills: bool = False`. When True, load calibration data and use `RealisticFillSimulator`. This allows PnL validation without changing the default (zero-friction) behavior.

Implementation detail: `load_calibration_df()` already exists in `fast_replay.py`. The wiring is straightforward.

### P3 (Generic, Priority: MEDIUM): Sort Stability for Tick Determinism

In `fast_replay.py:113`, change `.sort("published_at")` to `.sort(["published_at", "trade_id"])`. This makes replay results fully deterministic regardless of Parquet row group ordering. `trade_id` is already in the snapshot schema.

### P4 (Generic, Priority: MEDIUM): Capital Recycle Rate Metric in BacktestResult

Add `capital_utilization` metric to `BacktestResult`: ratio of (total USD deployed) / (capital_usd * simulation_duration_days). This helps distinguish "rejected due to capital" from "no signal." Currently only `rejected_intents` is tracked, which requires manual inspection.

### P5 (Generic, Priority: LOW): Budget Gate in run_fast_backtest()

Currently `run_fast_backtest()` passes `strategy_budgets={strategy.name: config.capital_usd}` to `ExecutionGateway`. The gateway tracks **cumulative** spend, not concurrent capital. For replay mode, this means once the strategy has spent `capital_usd` in total fills, all further intents are rejected — even if earlier positions have settled and capital has been freed.

This is a known issue from the tag-hr-copy validation (MEMORY.md item 4). Fix: pass `strategy_budgets=None` for replay mode and rely solely on the per-strategy risk gate in `check_risk_gate()`.

```python
# harness.py — run_fast_backtest()
# BEFORE (broken for long replays):
gateway = ExecutionGateway(executor, strategy_budgets={strategy.name: config.capital_usd})

# AFTER (correct for replay):
gateway = ExecutionGateway(executor, strategy_budgets=None)
```

This is critical: without this fix, any strategy that generates more than `capital_usd` in *cumulative* fills (even with settlement recycling capital) will see all intents rejected after the first cycle. For Esports with 403 signals at $50 each = $20,150 cumulative volume against a $1,000 budget, approximately 19 signals would fill and then all remaining signals would be blocked by the cumulative budget gate, causing artificially low fill counts.

**This is likely the most impactful harness fix for these strategies.**

---

## 8. Summary: Gap Attribution

| Source | Estimated HR Impact | Type | Fixed in Harness? |
|--------|--------------------|----|---|
| Budget gate cumulative (P5) | -20 to -40pp | Bug | Proposed fix |
| Signal dilution (N trades/position) | -10 to -15pp | Structural | Correct behavior |
| Consensus timing gap | -5 to -10pp | Structural | Correct behavior |
| Capital slot limits | -2 to -5pp | Structural | Correct behavior |
| Entry timing (copy lag) | -2 to -5pp | Structural | Correct behavior |
| OOS decay | -3pp | Expected | N/A |
| Fill friction (zero fees) | -1 to -2pp PnL only | Harness gap | Proposed P2 |
| Spread calibration | -1 to -2pp PnL only | Approximation | P2 improves it |
| Sub-second ordering | <0.5pp | Minor | Proposed P3 |

**The P5 budget gate bug is the most critical finding.** If the tick validator used `ExecutionGateway` with cumulative budgets, the reported tick-by-tick HR would be artificially depressed (fewer fills, survivorship toward early positions only). This would make degradation appear >40pp and falsely suggest harness fidelity issues.

---

## 9. Action Items for Tick Validator

When task #2 completes, check the following in `tick_validation_results.md`:

1. **Fill count**: should be proportional to `n_signals` from vectorized sweep. If `total_fills << expected`, check budget gate rejection rate (`rejected_intents`).
2. **Settlement rate**: `n_settled / total_fills` should be >0.80. Below 0.50 indicates resolution coverage gap.
3. **Degradation magnitude**: if >40pp, first check whether P5 (budget gate) is applied. If budget gate is correct and degradation is still >40pp, investigate capital constraints (P4 metric needed).
4. **HR by tag**: Esports should degrade more than Politics (shorter hold, more in-play contamination risk). Crypto/Elections should show modest degradation (<30pp from their vectorized HR).

---

## 10. Files Reviewed

- `research/sync_replay.py` — SyncReplayRunner (settlement, fill execution, risk gates)
- `research/harness.py` — run_fast_backtest() (executor choice, gateway config)
- `research/fast_replay.py` — ReplayTick loader (sort order, published_at derivation)
- `src/polymarket_pipeline/strategies/execution/realistic.py` — RealisticFillSimulator (impact formula)
- `src/polymarket_pipeline/strategies/execution/calibrate.py` — calibrate_spreads/volumes
- `src/polymarket_pipeline/strategies/runners/helpers.py` — check_risk_gate, apply_fill_to_position
- `research/hypotheses/scorecard-strategies/strategy2_smart_pool.md` — strategy design
- `research/hypotheses/scorecard-strategies/synthesis.md` — vectorized results
- `research/knowledge/pitfalls/simulation_fidelity.md`
- `research/knowledge/pitfalls/vectorized_vs_tick.md`
- `research/knowledge/pitfalls/vectorized_tick_gap_anatomy.md`
