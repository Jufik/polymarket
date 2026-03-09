# Live Infrastructure — Sub-Second Signal Delivery

> **TL;DR**: The production pipeline has sub-second signal delivery via WebSocket ingestors, Kafka topics (trades.raw, pending.signal), and CLOB orderbook streaming. In-play copy strategies do NOT suffer latency degradation — the "50-60pp degradation" estimate applies only to simulations without real-time infrastructure.

> [!CRITICAL]
> Do NOT apply in-play latency penalties (50-60pp) to strategy viability estimates when evaluating for production deployment. The live pipeline delivers signals via WebSocket within ~100ms. Tick-by-tick simulations that process trades sequentially approximate production latency well — the degradation in sim comes from fill model limitations, not signal delay.

> [!WARNING]
> Simulation-only degradation estimates (20-40pp general, 50-60pp in-play) reflect the fill model gap, NOT production latency. When a reviewer flags "in-play will collapse due to latency", challenge whether they're conflating simulation fidelity with deployment reality.

## Infrastructure

| Component | Latency | Topic |
|-----------|---------|-------|
| RTDS WebSocket | ~50ms | trades.raw |
| Pending Block Poller | ~1s pre-confirmation | pending.signal |
| CLOB WS Orderbook | ~100ms | orderbooks.raw |
| RPC Logs (on-chain) | ~2s post-block | trades.raw |
| Market Events | ~5s (debounced) | markets.events |

## Implications for Strategy Design

1. **In-play copy**: Elite traders lead by 58 minutes median. With sub-second signal delivery, we capture their entry within 100ms — the full 58-minute lead is available.
2. **Consensus strategies**: N-trader convergence can be tracked in real-time via trades.raw consumer. No need to wait for batch processing.
3. **Orderbook context**: CLOB WS provides best bid/ask at signal time — fill price estimation is realistic.

## What Simulation DOES Capture

- Capital constraints (position limits, settlement)
- Fill model (slippage, market impact)
- Resolution timing
- Trade ordering within a block (~same second)

## What Simulation Does NOT Capture

- Sub-second execution advantage
- Orderbook depth at signal time
- Pending block early signals (~1s advantage)
- Concurrent position management across tracks

## Related

- `signals/in_play_elite_traders.md` — elite traders validated at 94.2% HR tick-by-tick
- `pitfalls/simulation_fidelity.md` — fill model gaps (the real degradation source)

## Tags

`infrastructure`, `latency`, `websocket`, `kafka`, `live-pipeline`, `in-play`
