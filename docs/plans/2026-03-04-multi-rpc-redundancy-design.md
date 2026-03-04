# Multi-RPC Redundancy for RPCIngestor

**Date**: 2026-03-04
**Status**: Approved

## Problem

The RPCIngestor connects to a single RPC WebSocket endpoint. When that endpoint
degrades (e.g. publicnode silently dropping log subscriptions due to case-sensitive
address matching), the entire on-chain trade feed goes dark with no fallback.

## Design

Race multiple free public RPC endpoints in parallel inside RPCIngestor.
Whichever endpoint delivers an event first wins; duplicates are dropped via
TTL-based dedup. Mirrors the existing `PendingBlockIngestor` multi-endpoint pattern.

### Settings

- Rename `rpc_ws_url: str` to `rpc_ws_urls: str` (comma-separated)
- Default: `"wss://polygon-bor-rpc.publicnode.com,wss://polygon.drpc.org,wss://polygon.gateway.tenderly.co"`
- Keep legacy `PM_RPC_WS_URL` / `PM_ALCHEMY_WS_URL` aliases (single URL still works)

### RPCIngestor changes

- `__init__` takes `ws_urls: list[str]` instead of `ws_url: str`
- `run()` spawns one `_connection_loop(url)` per URL, all feeding the shared `_queue`
- Single `_resolution_loop` on first URL only (resolution events are rare)
- Single `_publish_loop`, `_heartbeat_loop` (unchanged)
- Add `TradeDedup(ttl_s=60.0)` from `live/dedup.py` — check in `_handle_message`
  before queueing. At ~50 trades/sec, ~3K entries max in the set.
- Add `_drops_dedup` counter for metrics/heartbeat

### Orchestrator

Split comma-separated string, pass `ws_urls=list` to RPCIngestor.

### What stays the same

- `_publish_loop`, `_resolution_loop`, `_heartbeat_loop` (single instances)
- All normalization, enrichment, validation logic
- BaseIngestor interface
- Kafka topic structure

## Checksummed addresses (bugfix, same PR)

Exchange addresses in `rpc.py` must be EIP-55 checksummed — publicnode's WS
`eth_subscribe` is case-sensitive on addresses (violates spec but confirmed).
