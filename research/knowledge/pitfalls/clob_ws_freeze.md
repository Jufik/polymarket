# CLOB WebSocket Pricing: Event Loop Starvation Under Load

> **TL;DR**: During fast market moves, our managed slot pipeline lags behind fresh WS connections by 10-20+ seconds. Root cause (~80% confidence): event loop starvation from Redis pipeline + sequential Kafka publishes competing with 30 WS recv loops in a single asyncio event loop. NOT a server-side CLOB WS freeze.

> [!CRITICAL]
> The CLOB orderbook "freeze" is self-inflicted event loop starvation, not a server
> bug. The fix is process isolation: WS recv in a dedicated process writing to
> SharedMemory, with Redis/Kafka offloaded to separate workers. Optimizing within
> the current single-loop architecture cannot fix this at scale.

## What was observed (2026-03-12)

Two WS connections to the same asset showed divergent prices:

| Source | Bid range | Connection type |
|--------|-----------|-----------------|
| Direct WS (fresh, idle event loop) | 0.74 - 0.81 | Single asset, dedicated |
| Managed slot (busy event loop) | 0.51 - 0.55 | 400+ assets, shared loop |

The managed slot was ~15-20 seconds behind the direct WS.

## What was NOT observed

A 500-second stress test comparing book vs LTP **on the same connection** during a
0.50→0.99 move showed:
- 86K+ events, zero sustained divergence
- 3 transient single-sample blips (<1 second each)
- Book and LTP tracked perfectly throughout

## Root cause hypothesis: event loop backpressure

The managed slot pipeline runs in a single asyncio event loop handling:
- 4+ managed WS slots (each with 100+ assets)
- Kafka publishes per event
- Redis writes per event (tip, ltp, ob keys)
- Reconciler + firehose + ping loops
- JSON parsing of 1000+ events/sec during fast moves

During a burst, the event loop queue grows. BBA/book events from the WS sit in the
recv buffer waiting for their coroutine's turn. A fresh dedicated connection (like the
diagnostic script) has an empty event loop and processes immediately.

This explains:
- Why two connections diverge (load vs no load)
- Why single-connection tests show no freeze (no contention)
- Why no one reports this as a CLOB WS bug (it's client-side)
- Why the gap varies with market volatility (more events = more backpressure)

## Confirmed findings

- **Pipeline latency under normal conditions: ~200ms** (stable + moderate volatility)
- **No server-side CLOB WS freeze detected** (500s stress test, 0.50→0.99)
- **Cross-connection divergence during fast moves: 10-20+ seconds** (client-side)

## Diagnostic tools

- `scripts/ws_freeze_detector.py` — single-connection freeze detector (standalone, `websockets` only)
- `scripts/ws_compare.py` — cross-connection comparison (direct WS vs Redis managed slot)

## Starvation mechanism (traced)

The single asyncio event loop runs:
- 30 WS recv loops (one per managed slot connection)
- 1 flush loop (Redis pipeline + sequential Kafka publish every 10ms)
- 1 reconciler + 1 firehose + heartbeat loops

During a 1000+ msg/sec burst:
1. Per-message CPU: ~1ms (json.loads + sorted() for top-10 + json.dumps for snapshot)
2. 1000 msgs × 1ms = event loop 100% saturated on message handling
3. flush_loop's `await asyncio.sleep(0.01)` can't get a turn
4. When flush_loop DOES run: `await pipe.execute()` (Redis 1-5ms) + sequential Kafka
   publishes (5-50ms each × 100 queued messages = 500-5000ms blocking)
5. During Kafka flush, NO recv loop runs → WS TCP buffers fill with stale messages
6. When recv loops resume, they process messages that are 5-20 seconds old

LTP "survives" because its handler is 10x cheaper (no sorted(), no json.dumps snapshot).

## Fix: process isolation (in REWORK.md)

1. **WS Ingestor Process** (dedicated): recv → orjson.loads → heapq top-10 → SharedMemory write (0.1ms/msg, 10x headroom)
2. **Cold Path Worker** (separate process): mp.Queue → Kafka (can lag freely)
3. **Pipeline Process** (main): reads SharedMemory via BookReader (sub-μs, zero network)
4. **No Redis in hot path** — SharedMemory replaces it entirely

Per-message cost: ~1ms → ~0.1ms. Burst capacity: ~1000/sec → ~10,000/sec.

## Validation plan

After rework, run `scripts/ws_freeze_detector.py` during a fast crypto move:
- If divergence count = 0: event loop starvation confirmed (80% hypothesis)
- If divergence persists: server-side freeze is real (20% case), freeze detector + LTP fallback needed

## Related
- `src/polymarket_pipeline/live/ingestors/clob_orderbook.py` — managed slot event handling
- `src/polymarket_pipeline/live/ingestors/managed_slot.py` — WS recv loop
- `research/knowledge/execution/trailing_stop_tuning.md` — depends on accurate pricing
- `REWORK.md` — full architecture with process isolation + SharedMemory
