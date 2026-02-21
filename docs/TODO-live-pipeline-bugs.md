# Live Pipeline Bugs

## BUG-1: Dashboard cross-feed matching is broken (all RACE/WATERFALL queries return 0)

**Impact**: All cross-feed latency metrics (Source Race, Coverage Gaps, Waterfall chart) show
zero matched trades and are completely non-functional.

**Root cause**: RTDS and Alchemy produce structurally incompatible `trade_id` values for the
same underlying trade:
- RTDS: `ws:<sha256(asset_id:ts_ms:price:size)[:16]>`
- Alchemy: `chain:<sha256(tx_hash:order_hash)[:16]>`

The dashboard SQL (`RACE_SQL`, `COVERAGE_GAP_SQL`, `WATERFALL_SQL`) all join on `trade_id`,
which can never match across sources. Every RTDS trade appears as "rtds_only" and every
Alchemy trade as "alchemy_only".

**Fix**: Join on `tx_hash` instead of `trade_id`. Both sources populate `tx_hash`:
- RTDS: `payload.transactionHash`
- Alchemy: `log.transactionHash`

Note: RTDS `transactionHash` is populated post-settlement (not at match time), so the field
may be empty for the most recent trades. The join will only work for trades that have already
settled on-chain, which introduces a ~3-5s lag in the race metrics. This is acceptable for
monitoring but means the dashboard cannot measure sub-second RTDS vs Alchemy delivery races
until both have the tx_hash.

**Files**: `src/polymarket_pipeline/live/dashboard.py` (RACE_SQL, COVERAGE_GAP_SQL, WATERFALL_SQL)

---

## BUG-2: RTDS ingestor has no data-level watchdog (silent freeze vulnerability)

**Impact**: RTDS WebSocket can silently stop delivering messages while the connection stays
healthy (ping/pong continues working). This is a confirmed upstream issue (GitHub
Polymarket/real-time-data-client#26). When frozen, the pipeline loses its primary real-time
feed with no alert.

**Current state**: The ingestor has ping/pong heartbeat (every 5s) and publishes pipeline
heartbeats (every 10s). But the heartbeat only tracks `last_trade_ts` and `trade_count` --
the quality checker's `check_source_liveness` only verifies heartbeat recency, not data flow.
A frozen RTDS connection will keep sending heartbeats with a stale `last_trade_ts`.

**Fix**: Add a data-level watchdog in `RTDSIngestor.run()`:
```python
WATCHDOG_TIMEOUT = 120  # 2 minutes with no trade = force reconnect

async def _watchdog(self, ws):
    while True:
        await asyncio.sleep(30)
        if self._trade_count > 0 and time.time() - self._last_trade_ts > WATCHDOG_TIMEOUT:
            log.warning("rtds.silent_freeze_detected",
                        last_trade_age=time.time() - self._last_trade_ts)
            await ws.close()
            return
```

Also update `check_source_liveness` in the quality checker to flag when RTDS heartbeat
reports a `last_trade_ts` older than the liveness timeout.

**Files**: `src/polymarket_pipeline/live/ingestors/rtds.py`,
`src/polymarket_pipeline/live/quality/checker.py`

---

## BUG-3: COVERAGE_GAP_SQL window function logic is incorrect

**Impact**: Even if trade_id matching were fixed, the window function approach partitions by
`trade_id` and checks for version presence within the partition. With ReplacingMergeTree,
only the highest-version row survives after merges, so the window function will never see
both versions in the same partition after optimization.

**Fix**: Query `trades_raw FINAL` or use a self-join approach (like RACE_SQL does, but on
`tx_hash`). The FINAL modifier forces merge behavior in the query.

**Files**: `src/polymarket_pipeline/live/dashboard.py` (COVERAGE_GAP_SQL)
