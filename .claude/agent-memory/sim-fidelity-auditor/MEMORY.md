# Sim Fidelity Auditor Memory

## Known Gaps (initial audit 2026-03-02)

### Critical
- Consensus dedup: user must implement in provider (sets not counters). -48pp if missed.
- SELL handling: SELL is directional (SELL YES = bearish, SELL NO = bullish) but ambiguous (exit vs split-entry). Include/exclude is a research parameter. Naive "copy SELL as opposite direction" caused -48pp NO HR in early tests — likely due to mixing exit noise, not because SELL has no signal.

### High
- No signal aggregation window: each trade triggers immediate decision. ~-3pp.
- Entry price divergence: signal price != fill price. ~-3pp.
- Linear market impact, no convexity. ~-3pp.

### Medium
- No partial fills (FillStatus.PARTIAL exists but never returned). ~-2pp.
- Spread calibration from trade prices, not orderbook. ~-2pp.
- No latency distribution (uniform delay_s only). ~-2pp.
- No mark-to-market tracking. ~-1pp.
- Sharpe annualization assumes constant frequency. ~-1pp.
- ledger._buffer access via getattr hack in replay.py (encapsulation violation).
- Batch resolution enrichment is O(markets × records), should be O(records).

### Fixed
- Capital settlement mid-replay (ReplayRunner). 0pp gap.
- Risk gates (4 checks, atomic budget). 0pp gap.
- Asset_id-based resolution (no strings). 0pp gap.
- Fee model (most markets = 0). 0pp gap.

## Files to Know
- `strategies/runners/replay.py` — ReplayRunner (tick-by-tick)
- `strategies/runners/backtest.py` — BacktestRunner (no settlement)
- `strategies/execution/realistic.py` — RealisticFillSimulator
- `strategies/execution/gateway.py` — ExecutionGateway
- `strategies/types.py` — Position, Fill, TradeIntent
- `strategies/ledger/` — LedgerRecord, analytics
