# Research Acceleration — Single-Server Architecture

**Date**: 2026-03-06
**Status**: Draft
**Goal**: Reduce idea-to-validated-strategy cycle from days to 1-2 hours while keeping tick-by-tick simulation as close to live trading as possible.

## Hardware

**Server (192.168.0.148)**: Ryzen 5950X (16C/32T @ 4.5GHz), 126 GB RAM, 1.8 TB disk (903 GB free)
- ClickHouse Docker: CPUs 0-23, 96 GB cgroup, 207 GB data on disk
- PostgreSQL, Redpanda, MLflow also on this box
- CPUs 24-31 idle (~30 GB RAM available for research workloads)

**Mac (local)**: Development machine, Claude Code sessions, live trade execution (wallet/API keys)

---

## Current Bottlenecks (measured)

| Operation | What happens | Time |
|-----------|-------------|------|
| `trader_positions_resolved` simple count | FINAL on 105M SummingMergeTree + JOIN | **36s** |
| `maker_positions_resolved_corrected` count | FINAL on 38M RMT + LEFT JOIN + JOIN | **12s** |
| Grouped query (per-market HR) | Same + GROUP BY | **21s** |
| One sweep combo (1 param x 1 fold) | 3-5 sequential CH queries | **40-60s** |
| Full sweep (5 tags x 60 combos x 5 folds) | ~1500 CH queries | **15-25 hours** |
| Tick-by-tick replay | `data/compact/` is empty; `order_filled/` is 152 GB uncompacted | **broken** |

Root causes:
1. Every CH query re-does `FINAL` dedup on SummingMergeTree (100M+ rows re-merged per read)
2. Sweep scripts fire sequential queries over the network, each paying the FINAL penalty
3. Tick-by-tick has no working data source (compact dir empty, raw parquet not usable)
4. Two-machine setup adds network latency to every query

---

## 1. Parquet Snapshot Layer

One-time export from CH with FINAL applied, stored as Parquet on the server's local disk.
Two datasets: **positions** (for vectorized discovery) and **trades** (for tick-by-tick replay).

### 1a. Positions Snapshot (vectorized discovery)

Materialized export of the CH views that currently re-compute FINAL on every read.

| File | Source | Rows | Est. size |
|------|--------|------|-----------|
| `maker_positions_resolved.parquet` | `maker_positions_resolved_corrected` VIEW | 30M | ~2 GB |
| `trader_positions_resolved.parquet` | `trader_positions_resolved` VIEW | 28M | ~2 GB |
| `markets_metadata.parquet` | `markets` + `events` + `tags` JOINed | 500K | ~50 MB |
| `trader_volumes.parquet` | `trader_volumes FINAL` | 6M | ~300 MB |
| `markets_resolved.parquet` | `markets_resolved` VIEW | 450K | ~30 MB |
| **Total** | | | **~4.5 GB** |

### 1b. Trades Snapshot (tick-by-tick replay)

Full deduped trade history, partitioned monthly, sorted by `(condition_id, timestamp)`.

| Metric | Value |
|--------|-------|
| Total rows | 440M (after FINAL dedup) |
| Total size | **~3.7 GB** (Parquet with dictionary encoding) |
| Export time | ~8 min (one-time, re-run on demand) |
| Columns | `condition_id, asset_id, side, price, amount_usd, fee_usd, maker, timestamp` |
| Partitioning | Monthly files: `trades_YYYYMM.parquet` (~41 files) |
| Sort order | `(condition_id, timestamp)` within each file |

**Why this sort order**: Parquet stores column min/max statistics per row group. When sorted by `condition_id`, a filtered scan (`WHERE condition_id IN (strategy_universe)`) skips 95%+ of row groups without reading them.

**Measured**: Polars scan of 16.7M-row monthly file with 100-market filter: **0.04s** (vs 0.15s full scan). Predicate pushdown works.

### Export script: `research/export_snapshot.py`

```
CH (localhost:9000, no network)
  ├─ SELECT ... FROM maker_positions_resolved_corrected → positions/maker_positions_resolved.parquet
  ├─ SELECT ... FROM trader_positions_resolved           → positions/trader_positions_resolved.parquet
  ├─ SELECT ... FROM markets + events + tags             → positions/markets_metadata.parquet
  ├─ SELECT ... FROM trader_volumes FINAL                → positions/trader_volumes.parquet
  ├─ SELECT ... FROM markets_resolved                    → resolutions/markets_resolved.parquet
  └─ for each month:
       SELECT condition_id, asset_id, side, price, amount_usd, fee_usd, maker, timestamp
       FROM trades_raw FINAL
       WHERE toYYYYMM(timestamp) = {month}
       ORDER BY condition_id, timestamp
       FORMAT Parquet → trades/trades_{month}.parquet
```

**Refresh cadence**: On demand. Research operates on a stable snapshot. Re-export weekly or when new market categories arrive. The live pipeline continues ingesting into CH independently.

### File layout

```
data/research/                          (~8 GB total)
├── positions/
│   ├── maker_positions_resolved.parquet
│   ├── trader_positions_resolved.parquet
│   ├── markets_metadata.parquet
│   └── trader_volumes.parquet
├── resolutions/
│   └── markets_resolved.parquet
└── trades/
    ├── trades_202211.parquet
    ├── trades_202212.parquet
    ├── ...
    └── trades_202603.parquet
```

---

## 2. DuckDB for Vectorized Discovery

DuckDB runs in-process within the research server. Loads the positions snapshot at startup (~3s for 8 GB). All sweep queries run against DuckDB in-memory — no CH, no FINAL, no network.

### Performance comparison

| Operation | Current (CH over network) | Proposed (DuckDB in-memory) | Speedup |
|-----------|--------------------------|----------------------------|---------|
| Single aggregate query | 12-36s | 50-200ms | **100-500x** |
| Full sweep (1500 combos) | 15-25 hours | 5-10 min | **150-300x** |
| Interactive notebook query | 20-40s | < 1s | **20-40x** |

### How sweeps change

**Current**: Python loop → `clickhouse_connect` → CH query (FINAL on 100M rows) → result. Repeat 1500x.

**Proposed**: Load positions into DuckDB once. Python loop → DuckDB SQL (in-memory scan) → result. Repeat 1500x. Each iteration is a simple columnar scan, no merge, no network.

```python
# Pseudocode
con = duckdb.connect()
con.execute("CREATE TABLE positions AS SELECT * FROM 'data/research/positions/maker_positions_resolved.parquet'")
con.execute("CREATE TABLE metadata AS SELECT * FROM 'data/research/positions/markets_metadata.parquet'")

for tag, min_trades, excess_hr, lookback in param_grid:
    # 50-200ms per query instead of 12-36s
    result = con.execute("""
        SELECT count() as n, countIf(correct=1)/count() as hr, ...
        FROM positions p JOIN metadata m ON p.condition_id = m.condition_id
        WHERE m.tag = ? AND p.month BETWEEN ? AND ?
        GROUP BY p.trader
        HAVING n >= ? AND hr >= ?
    """, [tag, train_start, train_end, min_trades, base_rate + excess_hr]).fetchdf()
```

The `_tmp_*` Memory tables pattern in CH is eliminated entirely — DuckDB handles arbitrary ad-hoc queries natively without materializing intermediate tables.

---

## 3. Tick-by-Tick Replay Acceleration

The ReplayRunner processes every trade through the Strategy protocol: `on_trade()` → risk gate → fill → settlement → ledger. This is intentionally close to the live pipeline — same protocol, same code paths.

### 3a. Pre-filter trades (biggest win, no architecture change)

Most strategies only care about a small universe of markets. The tag-HR-copy strategy watching Esports/Tennis covers ~50K out of 450K markets, ~5% of trades.

**Current**: Feed all 434M trades → strategy says "not my market" 99.99% of the time.

**Proposed**: Read only the relevant trades from Parquet using predicate pushdown.

```python
universe = set(strategy.get_market_universe())  # e.g., Esports condition_ids
trades_df = pl.scan_parquet("data/research/trades/*.parquet").filter(
    pl.col("condition_id").is_in(universe)
).collect()
# 434M → 5-20M trades in < 1s
```

The `(condition_id, timestamp)` sort order within each Parquet file means Polars/DuckDB skip row groups that don't contain the target condition_ids. Measured selectivity: 0.4% for a 100-market filter (16.7M → 70K rows in 0.04s).

**Estimated speedup**: 20-100x reduction in ticks through the Python loop.

### 3b. Lightweight replay trade struct (drop Pydantic overhead)

The `NormalizedTrade` model is a frozen Pydantic BaseModel with Decimal fields, validators, and serializers. Construction cost: ~10 μs/object. For replay, we need 8 fields with no validation.

**Proposed**: A `ReplayTick` namedtuple or slotted dataclass for the replay path only:

```python
@dataclass(slots=True)
class ReplayTick:
    condition_id: str
    asset_id: str
    side: str          # "BUY" or "SELL"
    price: float
    amount_usd: float
    fee_usd: float
    maker: str
    timestamp: float   # epoch seconds (published_at equivalent)
```

Construction: ~0.5 μs vs ~10 μs for NormalizedTrade. The Strategy protocol's `on_trade()` signature uses `NormalizedTrade`, but for replay we can type-pun — the strategy only accesses `.condition_id`, `.asset_id`, `.side`, `.price`, `.maker`, `.published_at`, `.amount_usd`. The ReplayTick provides all of these.

### 3c. Sync replay path (drop async overhead)

The Strategy protocol is async because the live pipeline requires it (Kafka consumers, CLOB REST calls). But in replay, every `await` is a coroutine creation + scheduling overhead on a pure dict lookup.

`InMemoryContext.get_position()` is literally `return self._positions.get(condition_id)` wrapped in `async`. That's ~1-3 μs of coroutine overhead per call, for nothing.

**Proposed**: A `SyncReplayRunner` that calls sync versions of the strategy callbacks. Strategies implement an optional `def on_trade_sync()` method; the runner calls it if available, falls back to `asyncio.run(on_trade())` otherwise.

This does NOT change the live pipeline or the Strategy protocol. It's an optimization for the replay path only.

### 3d. Combined tick-by-tick performance estimate

| Optimization | Per-tick cost | 20M ticks | Cumulative |
|-------------|-------------|-----------|------------|
| Current (if it worked) | ~30 μs | ~10 min | baseline |
| Pre-filter (20-100x fewer ticks) | ~30 μs | 0.5-2 min | **5-20x** |
| + ReplayTick (drop Pydantic) | ~10 μs | 10-40s | **15-60x** |
| + Sync replay (drop async) | ~5 μs | 5-20s | **30-120x** |

Target: **full 12-month replay in 2-5 minutes** for a typical strategy.

### 3e. Rust replay engine (future, optional)

If 2-5 minutes isn't fast enough, a Rust replay engine via PyO3 would bring per-tick cost to ~200 ns. For 20M filtered ticks: **4 seconds**.

Design:
- Parameterized `CopyStrategy` trait in Rust covers all threshold-based copy-strategy variants
- Python strategies supported via PyO3 callback fallback (~5 μs/tick boundary crossing)
- Same settlement logic, risk gate, ledger output — just faster
- Extends the existing `polymarket-mempool` PyO3 crate workspace

Not needed for Phase 1. Revisit when there are 3+ validated strategies worth optimizing.

---

## 4. Research Server (FastAPI)

A persistent process on the server (CPUs 24-31, ~10 GB RAM). Eliminates session reload and provides an API for Claude Code and notebooks.

### Endpoints

| Endpoint | Purpose | Backend |
|----------|---------|---------|
| `POST /query` | Ad-hoc DuckDB SQL → JSON | DuckDB in-process |
| `POST /sweep` | Parameterized sweep → results | DuckDB |
| `POST /replay` | Strategy config + universe → ledger | Parquet + ReplayRunner |
| `POST /refresh` | Re-export from CH → update Parquet + DuckDB | CH native protocol |
| `GET /status` | Loaded tables, last refresh, row counts | DuckDB metadata |

### Session persistence

**Problem**: Every Claude Code session starts cold. Previous `_tmp_*` Memory tables in CH are gone. Research context is lost.

**Solution**: The research server persists across sessions. DuckDB holds the snapshot in memory. Claude scripts query `localhost:9999` — zero reload.

```
Claude session 1:  POST /sweep {...}  →  results in 5 min
Claude session 2:  POST /query "SELECT ..."  →  same data, instant
Claude session 3:  POST /replay {...}  →  ledger in 3 min
                   POST /refresh       →  re-export from CH (8 min), then new data available
```

### Deployment

```bash
# On the server, run once
cd /home/user/polymarket
uv run python research/server.py &

# From Claude sessions (Mac)
curl -X POST http://192.168.0.148:9999/sweep -d '{"tag": "Esports", ...}'
curl -X POST http://192.168.0.148:9999/replay -d '{"strategy": "tag_hr_copy", ...}'
```

Alternatively, Claude Code can SSH to the server and run research scripts directly — they'd use DuckDB/Polars locally on the server with no network overhead.

---

## 5. What Stays Where

### Server (everything compute-heavy)

- ClickHouse: ingestion, MVs, durable storage, source of truth
- PostgreSQL: metadata (events, markets, tags)
- Redpanda: Kafka streaming for live pipeline
- Parquet snapshot: `data/research/` (~8 GB)
- DuckDB: in-process analytical engine
- Research server: FastAPI on port 9999
- Live pipeline: ingestors → Redpanda → CH → MVs
- Paper trading: `PaperExecutor` runs on server

### Mac (thin client)

- Claude Code sessions (SSH to server for compute)
- `LiveExecutor`: real CLOB API trades (needs wallet/API keys)
- `pm-panic`: emergency close (must work even if server is down)
- Code editing and git

---

## 6. What We Acknowledge We Can't Fix

### Orderbook simulation gap

The `RealisticFillSimulator` calibrates spreads from trade-to-trade price changes (median absolute change or Roll estimator). It adds slippage as `(half_spread + impact) * size_usd` to the fee.

We only have ~1 week of `orderbook_l2` data (133M rows). Not enough for historical fill modeling across the full backtest period.

**What we do**: Calibrate from trade data. Honest about the gap.
**What we don't pretend**: We don't claim fills are realistic for large orders or thin markets.

The gap between tick-by-tick simulation and reality is:
- Fill price: simulated (calibrated slippage) vs real (orderbook depth)
- Fill probability: always fills vs sometimes rejected
- Market impact: estimated vs real

This gap is irreducible without a market-making simulation or live paper trading confirmation. The promotion gate (`vectorized → paper_dev → paper_prod → live`) exists precisely to catch strategies that look good in simulation but fail in reality.

### FINAL overhead in CH

SummingMergeTree/ReplacingMergeTree require FINAL to get correct results. This is by design — the merge is deferred for write performance. The solution is not to "fix" CH but to export the merged result once and query the export.

---

## 7. Resource Budget

```
Server RAM (126 GB):
  ClickHouse cgroup:     90 GB (reduce from 96)
  DuckDB + Research:     10 GB
  PG + Redpanda + OS:    26 GB

Server Disk (903 GB free):
  CH data:               207 GB (existing)
  Parquet snapshot:        8 GB (new)
  Replay exports/cache:   20 GB (filtered subsets)
  Plenty of room:        668 GB remaining
```

---

## 8. End-to-End Research Timing (target)

| Step | Current | Target |
|------|---------|--------|
| Load knowledge | ~5 min | ~5 min (unchanged) |
| Vectorized sweep | 15-25 hours | **5-10 min** |
| Manual gate | human review | human review |
| Tick-by-tick (1 month) | broken | **2-5 min** |
| Full validation (12 months) | N/A | **20-60 min** |
| Capture & score | ~2 min | ~2 min |
| **Total idea → validated** | **days** | **1-2 hours** |

---

## 9. Implementation Phases

### Phase 1: Parquet Snapshot + DuckDB (2-3 days)

- `research/export_snapshot.py`: CH → Parquet export script (positions + trades)
- `research/db.py`: DuckDB session manager (load parquet, expose query interface)
- Port one sweep script to DuckDB, verify results match CH
- Run export on server, validate completeness

### Phase 2: Replay Engine (2-3 days)

- `research/replay.py`: Load filtered trades from Parquet via Polars
- `ReplayTick` lightweight struct (drop Pydantic for replay path)
- Pre-filter by `condition_id IN (strategy_universe)`
- Wire into existing `ReplayRunner` with resolution + settlement
- Run on server, verify ledger output

### Phase 3: Research Server (1-2 days)

- FastAPI skeleton: `/query`, `/sweep`, `/replay`, `/refresh`, `/status`
- DuckDB loaded at startup from Parquet snapshot
- Replay endpoint calls Phase 2 engine
- Systemd unit or Docker service for persistence

### Phase 4: Sync Replay Optimization (1-2 days)

- `SyncReplayRunner`: sync callbacks, no coroutine overhead
- Optional `on_trade_sync()` method on Strategy protocol
- Benchmark: measure actual per-tick cost reduction

### Phase 5: Rust Replay Engine (optional, 1-2 weeks)

- PyO3 crate in `polymarket-mempool` workspace (or new crate)
- Parameterized `CopyStrategy` in Rust
- Python callback fallback for prototyping strategies
- Target: full 12-month replay in < 1 minute
