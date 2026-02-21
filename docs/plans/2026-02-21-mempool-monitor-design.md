# Polygon Mempool Monitor — Design Document

**Date:** 2026-02-21
**Status:** Approved

## Goal

A Rust-based PyO3 extension module that connects to the Polygon PoS devp2p network as a "parasitic node", receives pending transaction gossip, filters for Polymarket CTF/NegRisk Exchange trades, decodes calldata, and yields structured data to the existing Python pipeline. Provides 2-12s early visibility into trader intent before on-chain confirmation.

## Phased Scope

### Phase 1 — Pending Transaction Gossip (MVP)

Connect to Polygon peers via devp2p. Receive pending txs from mempool gossip (`NewPooledTransactionHashes` → `GetPooledTransactions`). Filter by CTF/NegRisk Exchange `to` address. Decode `fillOrder`/`fillOrders` calldata with alloy `sol!` macro. Yield decoded trade dicts to Python via async iterator.

**Latency gain:** ~2-12s before Alchemy sees confirmed `OrderFilled`.

**Limitations:**
- Not all txs propagate through public mempool (private/direct-to-validator invisible)
- Reverted/replaced txs appear here but never on `trades.raw`
- Peer churn with `NoopProvider` means intermittent coverage gaps

### Phase 2 — Early Block Propagation

Parse `OrderFilled` event logs from `NewBlock` gossip — same data as Alchemy but ~500ms-1.5s earlier. Enables tx lifecycle tracking: pending (Phase 1) → block gossip (Phase 2) → Alchemy confirmed.

**Implementation delta:** Subscribe to `NewBlock`/`NewBlockHashes`, request block bodies, parse receipts for `OrderFilled` logs. Requires block header cache or RPC proxy for longer peer connections.

**New topic:** `blocks.trades` (confirmed but pre-RPC).

## Architecture

### Integration Model: PyO3 Extension Module

Rust handles ONLY devp2p networking (the part Python can't do). Everything else stays in the existing Python pipeline.

```
[reth tokio runtime]                    [Python asyncio loop]
  NetworkManager                           async for trade in monitor.stream():
  → tx received                              ← pyo3-asyncio bridge
  → filter by to_addr                        → MempoolNormalizer
  → decode calldata                          → token_map lookup
  → mpsc::send(dict)  ──────────────→       → broker.publish("mempool.raw")
```

### Topic Map

| Topic | Phase | Content | Confirmed? | Version |
|-------|-------|---------|------------|---------|
| `mempool.raw` | 1 | Decoded pending txs (fillOrder calldata) | No | 0 |
| `blocks.trades` | 2 | OrderFilled from gossiped blocks | Yes | 2 |
| `trades.raw` | existing | Alchemy/RTDS/Subgraph confirmed | Yes | 1 or 2 |
| `pipeline.status` | both | Heartbeats from all ingestors | n/a | n/a |

### Version Priority (ReplacingMergeTree)

`version=0` (mempool) < `version=1` (RTDS off-chain) < `version=2` (on-chain Alchemy/Subgraph)

## Rust Project Structure

```
crates/polymarket-mempool/
├── Cargo.toml
├── pyproject.toml           # maturin build backend
├── src/
│   ├── lib.rs              # #[pymodule] entry point
│   ├── config.rs           # MempoolConfig from Python kwargs
│   ├── network/
│   │   ├── mod.rs
│   │   ├── chain_spec.rs   # Polygon constants, bootnodes, fork blocks
│   │   └── runner.rs       # NetworkManager setup, tx event loop
│   ├── filter.rs           # to_addr ∈ {CTF, NegRisk} + selector check
│   └── decoder.rs          # alloy sol! definitions → Python dicts
```

### Component Responsibilities

**`lib.rs`** — `MempoolMonitor` Python class with `async stream()` method.

**`chain_spec.rs`** — Hardcoded Polygon constants:
- Network ID: 137
- Genesis hash: `0xa9c28ce2141b56c474f1dc504bee9b01eb1bd7d1a507580d5519d4437a97de1b`
- 8 bootnodes (4 reth example + 4 Polygon official)
- Fork blocks: Petersburg(0), Istanbul(3.4M), Berlin(14.7M), London(23.8M), Shanghai(50.5M)

**`runner.rs`** — reth `NetworkManager` with:
- `NoopProvider` (accept peer churn)
- discv4 with 1s lookup interval
- Ephemeral secp256k1 key (generated at startup)
- Listen port 30304 (configurable)

**`filter.rs`** — Two-stage:
1. `tx.to()` ∈ `{0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e, 0xc5d563a36ae78145c45a50134d48a1215220f80a}`
2. First 4 bytes match `fillOrder` or `fillOrders` selector

**`decoder.rs`** — alloy `sol!` macro:
```rust
sol! {
    struct Order {
        uint256 salt;
        address maker;
        address signer;
        address taker;
        uint256 tokenId;
        uint256 makerAmount;
        uint256 takerAmount;
        uint256 expiration;
        uint256 nonce;
        uint256 feeRateBps;
        uint8 side;
        uint8 signatureType;
    }
    function fillOrder(Order order, Sig sig);
    function fillOrders(Order[] orders, Sig[] sigs);
}
```

### Python API

```python
from polymarket_mempool import MempoolMonitor

monitor = MempoolMonitor(listen_port=30304, log_level="info")

async for trade in monitor.stream():
    # trade = {
    #     "tx_hash": "0xabc...",
    #     "maker": "0x123...",
    #     "taker": "0x456...",
    #     "token_id": "12345...",
    #     "maker_amount": 500000,
    #     "taker_amount": 1000000,
    #     "fee_rate_bps": 150,
    #     "side": 0,
    #     "expiration": 1708500000,
    #     "seen_at": 1708453200.123,
    # }
    normalized = normalizer.normalize(trade)
    await broker.publish(normalized.model_dump_json(), topic="mempool.raw")
```

## Error Handling

### Rust side

| Condition | Behavior |
|-----------|----------|
| `peers_active == 0` | Immediate `warn` log, heartbeat reports `status: "degraded"` |
| `peers_active > 0` | Normal, log connects/disconnects at `debug` |
| Tx passes addr filter but decode fails | `debug` log, increment `decode_errors`, skip |
| Tx targets exchange but wrong function | Skip silently (approve, setApprovalForAll, etc.) |
| Python consumer falls behind | Bounded mpsc (10k), drop oldest on overflow, `warn` log |
| Port already in use | Raise Python `OSError` |

### Python side

`MempoolNormalizer` returns `None` (skip) for:
- Unknown `token_id` (not in token_map)
- Taker is an exchange address (taker-duplicate)

## Python Changes (Minimal)

1. **`models.py`** — add `MEMPOOL = "mempool"` to `Source` enum
2. **`app.py`** — add `_run_mempool()` ingestor task in lifespan
3. **`settings.py`** — add `mempool_enabled: bool = False`, `mempool_listen_port: int = 30304`
4. **`quality/checker.py`** — add `"mempool"` to optional source liveness (when enabled)
5. **`live/normalizers/mempool.py`** — new `MempoolNormalizer` class

No changes to ClickHouse schema, Redpanda config, or existing normalizers.

## Build Integration

### Monorepo layout

```
polymarket/
├── crates/
│   └── polymarket-mempool/
│       ├── Cargo.toml          # PyO3, reth, alloy, secp256k1
│       ├── pyproject.toml      # maturin build backend
│       └── src/
├── src/polymarket_pipeline/    # existing Python
├── pyproject.toml              # root, depends on polymarket-mempool
└── uv.lock
```

### Dependencies

**Rust (Cargo.toml):**
- `pyo3 = "0.23"` + `pyo3-asyncio-0.21` (tokio bridge)
- `reth-ethereum = "1.11"` (features: network)
- `reth-discv4 = "1.11"`
- `alloy = "1.0"` (features: sol-types)
- `secp256k1 = "0.29"`
- `tokio = "1"` (features: full)

**Python (root pyproject.toml):**
```toml
[tool.uv.sources]
polymarket-mempool = { path = "crates/polymarket-mempool", editable = true }
```

### Build workflow

```bash
uv sync --all-extras          # builds Rust extension via maturin
maturin develop --release     # rebuild after Rust changes
```

System requirement: Rust toolchain (rustup). First compile ~3-5 min, incremental ~10-30s.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `PM_MEMPOOL_ENABLED` | `false` | Feature flag |
| `PM_MEMPOOL_LISTEN_PORT` | `30304` | devp2p listen port |
| `PM_MEMPOOL_LOG_LEVEL` | `info` | Rust tracing filter |
