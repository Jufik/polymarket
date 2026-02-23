# Erigon Peer Targeting + Transaction Hash Fetching

**Date**: 2026-02-23
**Status**: Design approved

## Problem

Our Rust devp2p mempool monitor connects to 32+ Polygon peers with correct fork hash (`0x22d523b2`), but receives near-zero pending transactions because Bor nodes (31/32 peers) don't gossip txs via devp2p. Only the 1 Erigon peer sent any transactions — 3 full broadcasts + 7 hash announcements in 120s.

Two issues compound:
1. We connect to mostly Bor peers (no tx gossip) instead of targeting Erigon peers (~17 exist on Polygon per PolygonScan)
2. We receive `NewPooledTransactionHashes` from Erigon peers but never fetch the full txs — our `with_transactions(tx_event_tx)` does NOT spawn a TransactionsManager, and we have no pool anyway (`NoopProvider`)

## Solution

Three changes to `runner.rs`:

### 1. Erigon Auto-Promotion

On `ActivePeerSession`, check `client_version.starts_with("erigon/")`. If Erigon, call `handle.add_trusted_peer()` so reth never evicts it. Track in a `HashSet<PeerId>` for diagnostics. Remove on `SessionClosed`.

### 2. Direct GetPooledTransactions

When `IncomingPooledTransactionHashes` arrives:
- Extract tx hashes from the message
- Call `handle.send_request(peer_id, PeerRequest::GetPooledTransactions { ... })`
- Spawn a task to await the oneshot response
- Run each tx through `is_exchange_tx -> is_fill_order -> decode_calldata_to_json`
- No pool validation — decode and forward immediately

This bypasses the TransactionsManager entirely (which we don't have).

### 3. Diagnostic Stats

- Track `erigon_count` vs `bor_count` separately
- Log tx hash announcement counts and fetch success/failure rates
- Report in the diagnostic timer

## Architecture

```
DHT / DNS Discovery
       |
  All Peers (~300+)
       | RLPx HELLO
       | check client_version
       |
  ┌────┴─────────────┐
  │ Bor peers         │  keep for DHT (don't expect tx gossip)
  │ Erigon peers      │  add_trusted_peer (never evict)
  └────┬─────────────┘
       |
  ┌────┴────────────────────────┐
  │ Transactions (full body)     │ → filter + decode
  │ NewPooledTxHashes (hashes)   │ → GetPooledTransactions → filter + decode
  └────┬────────────────────────┘
       |
  Python channel (existing)
```

## Files Changed

- `crates/polymarket-mempool/src/network/runner.rs` — all 3 changes
- `crates/polymarket-mempool/Cargo.toml` — may need `reth-eth-wire` for hash types

## Not In Scope

- DHT crawler mode (separate tool for later)
- Bor peer eviction (need them for DHT propagation)
- Geographic filtering
- Sentry gRPC (port 9091 is internal-only, same data pipe)

## Expected Outcome

With ~17 Erigon nodes on Polygon, we may connect to 3-5. Volume will be low but non-zero and free. This run tells us actual coverage before deciding on paid alternatives (bloXroute $300/mo).
