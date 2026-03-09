# Architect Review: Two-Layer Position Management (TP/SL + Eviction)

**Hypothesis**: politics-active-exit
**Reviewer**: Architect agent
**Date**: 2026-03-09
**Status**: Review complete — design is sound, incremental path identified

---

## Codebase Audit Summary

Files read before writing this review:

- `src/polymarket_pipeline/strategies/execution/gateway.py` — budget gate, quality gate, executor delegation
- `src/polymarket_pipeline/strategies/execution/paper.py` — PaperExecutor, orderbook price resolution
- `src/polymarket_pipeline/strategies/execution/realistic.py` — RealisticFillSimulator
- `src/polymarket_pipeline/strategies/execution/simulated.py` — SimulatedExecutor
- `src/polymarket_pipeline/strategies/execution/live.py` — LiveExecutor, position limits, mark-to-market
- `src/polymarket_pipeline/strategies/runners/live.py` — LiveRunner, hot path, risk gate, timer loop
- `src/polymarket_pipeline/strategies/runners/helpers.py` — check_risk_gate, apply_fill_to_position
- `src/polymarket_pipeline/strategies/types.py` — TradeIntent, Position, Fill, OrderbookSnapshot
- `src/polymarket_pipeline/strategies/protocol.py` — Strategy, Executor, FeatureProvider protocols
- `src/polymarket_pipeline/strategies/config.py` — StrategyConfig, HarnessConfig, TOML loaders
- `src/polymarket_pipeline/strategies/context/memory.py` — InMemoryContext
- `src/polymarket_pipeline/strategies/ledger/types.py` — LedgerRecord

---

## 1. Where Should Each Layer Live?

### Layer 1: TP/SL Monitor — Lives in LiveRunner._timer_loop()

**Recommendation: LiveRunner._timer_loop(), NOT a new component and NOT ExecutionGateway.**

Rationale:

The timer loop already fires every 60 seconds with full access to `ctx.get_all_positions()` and
the orderbook store via `ctx._orderbooks` / `ctx._orderbooks_by_asset`. The TP/SL monitor is
fundamentally a position scan — it reads current prices against stored entry prices and decides
to exit. This is identical in shape to what `on_timer` does for strategies.

Adding TP/SL to ExecutionGateway would be wrong for two reasons:
1. ExecutionGateway has no knowledge of positions or prices — it is a pure routing layer.
2. Adding state to the gateway couples it to strategy domain logic, violating its design contract.

A new standalone component (e.g., `PositionMonitor`) is defensible for testability but adds
indirection without benefit at this stage. The runner already owns position + orderbook access.
The simpler path: implement a `_check_tpsl()` method on LiveRunner called from `_timer_loop()`.

**For backtest support**: the SyncReplayRunner/BacktestRunner ticks are trade-driven, not
timer-driven. TP/SL checking should also be wired into `_handle_trade()` — after position update,
check if the fill created a position that should immediately trigger TP/SL on the next orderbook
update. For SyncReplayRunner, the equivalent hook is after each tick. This is the only realistic
way to validate TP/SL exits in replay.

### Layer 2: Eviction — Lives in LiveRunner._handle_trade(), BEFORE gateway.submit()

**Recommendation: inside LiveRunner._handle_trade(), after check_risk_gate() returns
max_open_positions rejection, NOT inside ExecutionGateway.**

Rationale:

ExecutionGateway.submit() currently has no access to positions or strategy config — it only knows
cumulative USD spend. To do eviction, you need:
- The full position dict (to rank by PnL/day and enforce min_hold)
- Strategy config (min_hold, max_open_positions)
- The ability to submit a SELL intent for the evicted position synchronously

If you put this in the gateway you must pass all of the above as constructor args, turning the
gateway into a strategy-aware layer — a design violation. The runner owns all of this naturally.

The correct insert point: in `_handle_trade()`, when `check_risk_gate()` returns
`("max_open_positions", "max_open_positions")`, instead of logging and continuing, optionally
attempt eviction, generate a SELL intent for the worst position, submit it, wait for the fill,
then re-submit the original BUY intent. This preserves the gateway as a dumb routing layer.

---

## 2. Config Schema

Add an `[exit]` subsection under each strategy block. This keeps exit params co-located with
the strategy that owns the positions, is forward-compatible, and avoids polluting StrategyConfig
with fields irrelevant to strategies that don't use this system.

```toml
[strategy.politics_active]
enabled = true
mode = "paper_dev"
capital_usd = 2000
max_position_usd = 200
max_open_positions = 10
cooldown_s = 30

[strategy.politics_active.exit]
# Layer 1: Passive TP/SL (price-based, always active for high-entry positions)
tpsl_enabled = true
tpsl_entry_threshold = 0.80     # Only monitor positions entered above this price
tp_offset = 0.05                # Take profit when best_bid >= entry + tp_offset
sl_offset = 0.10                # Stop loss when best_bid <= entry - sl_offset
tpsl_check_interval_s = 60      # How often to scan (matches timer_interval_s by default)

# Layer 2: Demand-driven eviction (slot pressure)
eviction_enabled = true
min_hold_s = 86400              # 24h — positions younger than this are protected
eviction_require_bleeding = true  # Only evict if unrealized PnL < 0
```

Add an `ExitConfig` frozen dataclass to `config.py` and parse it from
`section.get("exit", {})` inside `load_strategy_configs()`. StrategyConfig gets an optional
`exit_config: ExitConfig | None = None` field. This is additive and backward-compatible —
strategies without an `[exit]` section get `None`.

---

## 3. Intent Types: SELL is Already the Right Type

**No new TradeIntent fields are required for exits.** The existing type handles it:

```python
TradeIntent(
    strategy="politics_active",
    condition_id="0xabc...",
    side="SELL",
    outcome="YES",
    size_usd=pos.cost_basis,      # full exit
    urgency="immediate",          # market order semantics
    max_price=None,               # no limit for SL; use best_bid for TP
    reason="tp_exit",             # or "sl_exit" or "eviction_exit"
    signal_time=time.time(),
    asset_id=asset_id,
)
```

The `reason` field already exists as a free-form string and is captured in LedgerRecord. That
is sufficient for analytics differentiation.

One addition is worth considering as an optional field: `exit_ref_id: str | None = None` to
link the exit TradeIntent back to the original entry fill's `intent_id`. This enables clean
round-trip ledger analysis without string matching on `reason`. However this is not strictly
necessary for an MVP and should be deferred to Phase 2.

---

## 4. Position Enrichment: entry_time is Missing

**Problem**: Position currently has `avg_entry_yes`, `avg_entry_no`, `cost_basis`, `realized_pnl`
but NO timestamp for when the position was opened. Layer 2 eviction needs `hold_days` =
`(now - entry_time) / 86400`. Layer 1 TP/SL does not need it, but eviction's `min_hold_s`
protection requires it.

**Recommended fix**: Add `entry_time: float = 0.0` to Position. This is a non-breaking change
(default zero, zero means unknown/unprotected). Update `apply_fill_to_position()` in helpers.py:
when `old` is None (first fill), set `entry_time = fill.filled_at`. When old position exists and
`entry_time == 0.0`, also set it from `fill.filled_at` (catch existing unlabeled positions).

Do NOT add `entry_time` to the Position protocol — it is an implementation detail of the runner's
eviction logic, not a protocol-level concern. Access it via direct attribute read on the concrete
InMemoryContext position dict.

**TP/SL price field**: `avg_entry_yes` / `avg_entry_no` already exist on Position. Use
`pos.avg_entry_price` (the existing property) as the entry price for TP/SL trigger computation.
No new fields needed.

---

## 5. Backtest Support

### Layer 2 (Eviction) in Backtest

The eviction path in `_handle_trade()` calls `gateway.submit()` for the SELL and then the BUY.
This flows through the same SimulatedExecutor / RealisticFillSimulator path used for entries —
no special handling needed. The SELL fill is recorded via `apply_fill_to_position()` and the
slot opens. The only requirement is that eviction generates a SELL intent with a realistic price.

For SyncReplayRunner / BacktestRunner: since there is no timer loop, eviction should be triggered
inline in the trade dispatch loop when the position count check fails. This means adding a
`_try_evict()` method that both runners can call. This is implementable today.

### Layer 1 (TP/SL) in Backtest

TP/SL requires a price feed. In backtest, there is no orderbook feed — only `trade.price` from
the trade tick stream. The correct approximation: after each BUY fill, check on every subsequent
trade for the same `condition_id` whether `trade.price >= entry + tp_offset` (TP) or
`trade.price <= entry - sl_offset` (SL). Use `trade.price` as the proxy for `best_bid`.

This is a materially worse approximation than live (thinly traded markets may gap through the
trigger without a matching trade), but it is the honest approach for replay validation. Do NOT
use the final resolution price as a TP/SL trigger — that is look-ahead.

**Backtest TP/SL fidelity warning**: expect 5-15pp worse TP/SL execution in backtest vs live
because the price trigger fires on the next trade, not the next orderbook tick. Log this caveat
in validation notes for any hypothesis that uses TP/SL.

---

## 6. Race Conditions

**Scenario**: Layer 1 TP/SL fires for market X (generates SELL intent). Meanwhile, Layer 2 eviction
also wants to evict X to make room for a new signal. Two SELL intents for the same position.

**Current protection**: `apply_fill_to_position()` in helpers.py caps `qty_yes` at zero:
```python
new_qty = max(old_qty - sold_qty, 0.0)
```
It also logs a `position.oversell` warning. So a double-SELL is not catastrophic — it produces
a zero position after the first fill and a zero-size sell on the second. However it will generate
two LedgerRecords for an "exit" which is messy.

**Better fix**: before generating any SELL intent (TP/SL or eviction), check that the position
still has non-zero qty. Use a lightweight `_pending_exits: set[str]` dict in LiveRunner keyed by
`condition_id`. Set it before submitting the first SELL, clear it after the fill completes. If
the condition_id is already in `_pending_exits`, skip the second SELL. This is a single-threaded
asyncio loop so no real lock is needed — the `await gateway.submit()` is the only yield point
and the pending set check is synchronous.

```python
# In LiveRunner
self._pending_exits: set[str] = set()

async def _try_tpsl_exit(self, condition_id: str, reason: str, ...) -> None:
    if condition_id in self._pending_exits:
        return
    self._pending_exits.add(condition_id)
    try:
        await self.gateway.submit(sell_intent)
    finally:
        self._pending_exits.discard(condition_id)
```

---

## 7. Ledger Tracking

**Existing fields that cover exits without change**:
- `reason` on TradeIntent maps to `LedgerRecord.reason` — use `"tp_exit"`, `"sl_exit"`,
  `"eviction_exit"` as the discriminator
- `side = "SELL"` distinguishes exits from entries in the ledger
- `pnl_net` on LedgerRecord is computed from `compute_pnl()` using fill price vs entry price
  — this already handles SELL correctly

**Missing for eviction analysis**: the ledger record for an eviction exit currently has no
reference to the replacement entry that triggered it. Analytics (e.g. "did evicting position X
for position Y produce net positive PnL?") require joining ledger records across a session, which
is possible but requires matching by timestamp proximity and condition_id.

**Recommended additions to LedgerRecord (Phase 2, not MVP)**:
- `exit_trigger: str | None = None` — `"tp"`, `"sl"`, `"eviction"`, `"resolution"`, `None`
- `entry_record_id: str | None = None` — for SELL records: points to the BUY LedgerRecord

Both are backward-compatible (default None). For MVP, `reason` on TradeIntent is sufficient
to filter and group exits in Polars post-processing.

---

## 8. Incremental Implementation: Recommended Sequence

### Phase 1: Eviction (2-3 days, lower risk)

Eviction does not require a price feed — it uses `entry_time` and `cost_basis` already in
Position. It is testable in SyncReplayRunner today.

**Step 1**: Add `entry_time: float = 0.0` to Position in `types.py`. Update
`apply_fill_to_position()` in `helpers.py` to set it on first fill.

**Step 2**: Add `ExitConfig` frozen dataclass to `config.py`. Add `exit_config: ExitConfig | None`
field to `StrategyConfig`. Parse from TOML in `load_strategy_configs()`.

**Step 3**: Add `_try_evict()` coroutine to LiveRunner. Called from `_handle_trade()` when
`check_risk_gate()` returns `"max_open_positions"`. Algorithm:
1. Collect all open positions for this strategy.
2. Filter out positions with `age < exit_config.min_hold_s`.
3. Rank remaining by `unrealized_pnl / hold_days` (lowest = worst, most eligible).
4. If `eviction_require_bleeding=True`, only consider positions with `unrealized_pnl < 0`.
5. If no candidate: return, reject the new BUY intent.
6. If worst position's expected PnL metric < new signal's `max_price` implied edge: evict.
7. Submit SELL for worst position. On fill: decrement open count. Re-check risk gate.

**Step 4**: Run `uv run pytest tests/ -x -q`. Add unit tests for `_try_evict()` with mocked
positions and intents.

### Phase 2: TP/SL Monitor (after eviction is validated in paper)

TP/SL requires the orderbook feed to be wired for the positions you want to monitor. The CLOB
WS only subscribes to a subset of markets. Confirm orderbook coverage before building TP/SL.

**Step 1**: Add `tpsl_enabled`, `tpsl_entry_threshold`, `tp_offset`, `sl_offset` to ExitConfig.

**Step 2**: Add `_check_tpsl()` coroutine to LiveRunner. Called from `_timer_loop()`.

**Step 3**: For replay/backtest support, add a price-trigger check in BacktestRunner's trade
dispatch: after each trade for `condition_id`, check if it crosses the TP or SL threshold for
any open position in that market.

---

## Harness Compatibility

The proposed changes touch:
- `types.py` (Position — add `entry_time`) — requires mypy check after
- `runners/helpers.py` (apply_fill_to_position — set entry_time) — requires mypy + unit test
- `runners/live.py` (eviction path + TP/SL path) — no mypy issues expected, runner owns these
- `config.py` (ExitConfig, StrategyConfig) — requires mypy strict check

None of these changes touch the harness entry points in `cli/harness.py`. The replay harness
uses `apply_fill_to_position()` indirectly via SyncReplayRunner — adding a field with a default
value is non-breaking.

**Run after any change**:
```
uv run pytest tests/ -x -q
uv run mypy --strict src/polymarket_pipeline/strategies/types.py
uv run mypy --strict src/polymarket_pipeline/strategies/runners/helpers.py
uv run mypy --strict src/polymarket_pipeline/strategies/config.py
```

---

## Summary Verdict

The proposed two-layer architecture is coherent and implementable. The key decisions:

| Question | Decision |
|----------|----------|
| Where does TP/SL live? | LiveRunner._timer_loop() + BacktestRunner trade hook |
| Where does eviction live? | LiveRunner._handle_trade() after max_open_positions rejection |
| Should EXIT intents differ? | No — use existing SELL + reason field; defer exit_trigger to Phase 2 |
| Position enrichment needed? | Yes — add entry_time to Position (default 0.0, non-breaking) |
| Backtest support? | Eviction: full support via trade loop. TP/SL: price proxy via trade.price |
| Race condition handling? | _pending_exits set in LiveRunner, checked before any SELL generation |
| Ledger tracking? | reason field sufficient for MVP; exit_trigger + entry_record_id in Phase 2 |
| Start with which layer? | Layer 2 (Eviction) first — no price feed dependency, testable now |
