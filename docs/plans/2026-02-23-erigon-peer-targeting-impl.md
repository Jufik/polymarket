# Erigon Peer Targeting + Tx Hash Fetching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Maximize pending transaction visibility from Polygon's ~17 Erigon peers by auto-promoting them and fetching full txs from hash announcements.

**Architecture:** On peer connect, check client_version for "erigon/" prefix and promote to trusted. When hash announcements arrive, send direct GetPooledTransactions requests via NetworkHandle::send_request() and process responses through the existing filter+decode pipeline.

**Tech Stack:** Rust, reth v1.11.0 (reth-network, reth-network-api, reth-eth-wire-types, reth-network-p2p), PyO3, tokio

---

### Task 1: Add New Cargo Dependencies

**Files:**
- Modify: `crates/polymarket-mempool/Cargo.toml`

**Step 1: Add reth-eth-wire-types and reth-network-p2p**

Add after the existing `reth-network-types` line:

```toml
reth-eth-wire-types = { git = "https://github.com/paradigmxyz/reth", tag = "v1.11.0" }
reth-network-p2p = { git = "https://github.com/paradigmxyz/reth", tag = "v1.11.0" }
```

**Step 2: Verify compilation**

Run: `cd crates/polymarket-mempool && cargo check`
Expected: compiles with existing warnings only (dead_code on decode_calldata)

**Step 3: Commit**

```bash
git add crates/polymarket-mempool/Cargo.toml crates/polymarket-mempool/Cargo.lock
git commit -m "chore: add reth-eth-wire-types and reth-network-p2p deps"
```

---

### Task 2: Erigon Auto-Promotion

**Files:**
- Modify: `crates/polymarket-mempool/src/network/runner.rs`

**Step 1: Add erigon_peers tracking state**

Add after `let mut diag_count: usize = 0;` (line 190):

```rust
let mut erigon_peers: HashSet<reth_network_peers::PeerId> = HashSet::new();
```

**Step 2: Add Erigon detection in ActivePeerSession handler**

Replace the `NetworkEvent::ActivePeerSession` arm (lines 243-250) with:

```rust
NetworkEvent::ActivePeerSession { info, .. } => {
    peers_active += 1;
    let client = info.client_version.to_string();
    let is_erigon = client.starts_with("erigon/");

    if is_erigon {
        erigon_peers.insert(info.peer_id);
        handle.add_trusted_peer(info.peer_id, info.remote_addr);
        tracing::warn!(
            peers = peers_active,
            erigon_count = erigon_peers.len(),
            client = %client,
            peer = %info.peer_id,
            addr = %info.remote_addr,
            "ERIGON peer connected — promoted to trusted"
        );
    } else {
        tracing::info!(
            peers = peers_active,
            client = %client,
            peer = %info.peer_id,
            "Peer session active"
        );
    }
}
```

**Step 3: Remove Erigon peers on disconnect**

In the `SessionClosed` arm (lines 252-260), add after `sessions_closed += 1;`:

```rust
erigon_peers.remove(&peer_id);
```

**Step 4: Add Erigon count to diagnostic timer**

In the diagnostic timer arm, add `erigon_count = erigon_peers.len(),` to the `tracing::info!` fields (around line 213).

**Step 5: Verify compilation**

Run: `cd crates/polymarket-mempool && cargo check`
Expected: compiles cleanly

**Step 6: Commit**

```bash
git add crates/polymarket-mempool/src/network/runner.rs
git commit -m "feat(mempool): auto-promote Erigon peers to trusted"
```

---

### Task 3: Direct GetPooledTransactions Fetch

This is the core change. When we receive `IncomingPooledTransactionHashes`, we extract the hashes, send a `GetPooledTransactions` request to the announcing peer, and process the response.

**Files:**
- Modify: `crates/polymarket-mempool/src/network/runner.rs`

**Step 1: Add imports**

Add to the imports at the top of runner.rs:

```rust
use reth_network_api::PeerRequest;
use reth_eth_wire_types::GetPooledTransactions;
use alloy_consensus::Transaction;
```

Note: `alloy_consensus::Transaction` is already imported (line 10). `PeerRequest` needs adding. `GetPooledTransactions` needs adding.

**Step 2: Replace the IncomingPooledTransactionHashes handler**

Replace the current handler (lines 342-346):

```rust
NetworkTransactionEvent::IncomingPooledTransactionHashes { peer_id, .. } => {
    tracing::info!(
        peer = %peer_id,
        "Received IncomingPooledTransactionHashes (not yet fetched)"
    );
}
```

With:

```rust
NetworkTransactionEvent::IncomingPooledTransactionHashes { peer_id, msg } => {
    let hashes: Vec<alloy_primitives::B256> = msg.hashes().clone();
    let hash_count = hashes.len();
    tracing::info!(
        peer = %peer_id,
        hash_count,
        "Received tx hash announcements — fetching full txs"
    );

    if hashes.is_empty() {
        continue;
    }

    // Send GetPooledTransactions request directly to the announcing peer.
    // Response comes back on a oneshot channel — spawn a task to handle it.
    let (resp_tx, resp_rx) = tokio::sync::oneshot::channel();
    handle.send_request(
        peer_id,
        PeerRequest::GetPooledTransactions {
            request: GetPooledTransactions(hashes),
            response: resp_tx,
        },
    );

    // Clone what the spawned task needs
    let tx_out = tx.clone();
    tokio::spawn(async move {
        match resp_rx.await {
            Ok(Ok(pooled_txs)) => {
                let fetched = pooled_txs.0.len();
                tracing::info!(
                    peer = %peer_id,
                    fetched,
                    requested = hash_count,
                    "GetPooledTransactions response"
                );
                for pooled_tx in pooled_txs.0.iter() {
                    let to_addr = match pooled_tx.to() {
                        Some(addr) => addr,
                        None => continue,
                    };
                    if !is_exchange_tx(&to_addr) {
                        continue;
                    }
                    let input = pooled_tx.input();
                    if !is_fill_order(input) {
                        continue;
                    }
                    let tx_hash = format!("0x{:x}", pooled_tx.tx_hash());
                    match decode_calldata_to_json(input, &tx_hash) {
                        Ok(trades) => {
                            for trade in trades {
                                let _ = tx_out.try_send(trade);
                            }
                        }
                        Err(e) => {
                            tracing::debug!(
                                %tx_hash,
                                error = %e,
                                "Failed to decode calldata (fetched)"
                            );
                        }
                    }
                }
            }
            Ok(Err(req_err)) => {
                tracing::debug!(
                    peer = %peer_id,
                    error = ?req_err,
                    "GetPooledTransactions request failed"
                );
            }
            Err(_) => {
                tracing::debug!(
                    peer = %peer_id,
                    "GetPooledTransactions channel closed"
                );
            }
        }
    });
}
```

**Step 3: Verify compilation**

Run: `cd crates/polymarket-mempool && cargo check`
Expected: compiles. May need to adjust `Transaction` trait import if there's a conflict.

**Step 4: Commit**

```bash
git add crates/polymarket-mempool/src/network/runner.rs
git commit -m "feat(mempool): fetch full txs from hash announcements via GetPooledTransactions"
```

---

### Task 4: Build, Install, and Test

**Files:**
- No new files

**Step 1: Build release wheel**

Run: `uv tool run maturin build --release`
Expected: wheel built at `target/wheels/polymarket_mempool-0.1.0-cp314-cp314-macosx_11_0_arm64.whl`

**Step 2: Install wheel**

Run: `cd /Users/kiefferjulien/git/polymarket && uv pip install --force-reinstall crates/polymarket-mempool/target/wheels/polymarket_mempool-0.1.0-cp314-cp314-macosx_11_0_arm64.whl`

**Step 3: Run probe for 180s**

Run: `MEMPOOL_LOG_FILE=data/mempool_rust.log uv run python scripts/mempool_latency_probe.py --duration 180 --port 30304 2>data/probe_stderr.log`

**Step 4: Analyze results**

Check probe_stderr.log for:
- `ERIGON peer connected — promoted to trusted` log lines (expect 1-5)
- `Received tx hash announcements — fetching full txs` log lines
- `GetPooledTransactions response` with fetched > 0
- `mempool=N` in stats (expect small but non-zero if Erigon peers are found)

Check data/mempool_rust.log for:
- `grep -c "ERIGON peer" data/mempool_rust.log` — Erigon peer count
- `grep -c "GetPooledTransactions response" data/mempool_rust.log` — fetch success count
- `grep -c "erigon_count" data/mempool_rust.log` — diagnostic entries

**Step 5: Commit any probe fixes if needed**

---

### Task 5: Final Commit

**Step 1: Commit all changes**

```bash
git add crates/polymarket-mempool/
git commit -m "feat(mempool): Erigon peer auto-promotion + GetPooledTransactions fetch

- Detect Erigon peers by client_version prefix, promote to trusted (never evicted)
- Fetch full txs from NewPooledTransactionHashes via direct GetPooledTransactions
- Track erigon_count separately in diagnostics
- Bypass TransactionsManager entirely (no pool needed)"
```
