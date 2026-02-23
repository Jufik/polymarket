# Polymarket Pipeline

Unified trade data pipeline for Polymarket: ingests from on-chain Parquet archives, RTDS WebSocket, and Alchemy RPC, normalizes into a canonical model, deduplicates across sources, and stores in ClickHouse. Market metadata syncs from the Gamma API into PostgreSQL.

## Greenfield Deployment Guide

Step-by-step instructions to set up the full pipeline from scratch on a new machine.

### Prerequisites

| Requirement | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker | 20.10+ | [docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2+ | Bundled with Docker Desktop |
| Goldsky Parquet | ~200 GB | `order_filled/` directory with `*.parquet` files |

> **Optional:** For the Rust mempool sidecar, you also need `cargo` and `maturin`.

---

### Step 1: Clone and install

```bash
git clone <repo-url> polymarket && cd polymarket

# Install all Python dependencies (uses uv lockfile for reproducibility)
uv sync --all-extras
```

This installs the package in editable mode with all optional dependency groups (clickhouse, postgres, polars, live pipeline, strategy, dev tools).

---

### Step 2: Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
# Required for the live pipeline (get a key at https://dashboard.alchemy.com)
PM_ALCHEMY_WS_URL=wss://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
```

All settings use the `PM_` prefix and have sensible defaults for local development:

| Variable | Default | Used by |
|---|---|---|
| `PM_DATA_DIR` | `data` | pm-sync, pm-compact, pm-build |
| `PM_RAW_PARQUET_DIR` | `order_filled` | pm-compact |
| `PM_PG_DSN` | `postgresql://polymarket:polymarket@localhost:15432/polymarket` | pm-sync, pm-live |
| `PM_CH_HOST` | `localhost` | pm-load, pm-live |
| `PM_CH_PORT` | `18123` | pm-load, pm-live |
| `PM_CH_DATABASE` | `polymarket` | pm-load, pm-live |
| `PM_ALCHEMY_WS_URL` | *(required)* | pm-live |
| `PM_REDPANDA_URL` | `localhost:19092` | pm-live |

---

### Step 3: Start infrastructure

```bash
docker compose up -d
```

Wait ~15 seconds for all services to initialize. The init scripts (`docker/postgres/init.sql`, `docker/clickhouse/init.sql`) create all tables automatically on first start.

| Service | Host Port | UI | Purpose |
|---|---:|---|---|
| ClickHouse 24.8 | 18123 (HTTP), 19000 (native) | — | Trade storage (OLAP) |
| CH-UI | 15521 | http://localhost:15521 | ClickHouse web interface |
| PostgreSQL 16 | 15432 | — | Metadata (events, markets, tags, token_map) |
| MLflow 2.19 | 5050 | http://localhost:5050 | Experiment tracking |
| Redpanda | 19092 (Kafka), 18082 (proxy) | — | Message broker (live pipeline) |
| Redpanda Console | 18080 | http://localhost:18080 | Redpanda web interface |

Verify services are healthy:

```bash
docker compose ps
docker compose exec clickhouse clickhouse-client --query "SELECT 'CH OK'"
docker compose exec postgres psql -U polymarket -c "SELECT 'PG OK'"
```

Data persists across restarts in `docker-drives/` (gitignored).

---

### Step 4: Create Redpanda topics

Required for the live pipeline:

```bash
docker compose exec redpanda rpk topic create trades.raw -p 8 -r 1
docker compose exec redpanda rpk topic create pipeline.status -p 1 -r 1
```

---

### Step 5: Place raw Parquet data

Copy or symlink the Goldsky Sink Parquet archive into `order_filled/` at the project root:

```bash
# Example: symlink from an external drive
ln -s /Volumes/data/order_filled ./order_filled
```

This directory should contain ~2,033 files (`*.parquet`), ~200 GB total.

---

### Step 6: Run the offline data pipeline

The full pipeline is orchestrated by `pm-build`. Run all steps in order:

```bash
pm-build
```

Or run steps individually:

```bash
# Step 6a: Sync metadata (CLOB + Gamma API → PostgreSQL + Parquet)
pm-sync --force

# Step 6b: Recompress raw parquet → compact sorted batches
pm-compact

# Step 6c: Load compact parquet → ClickHouse
pm-load

# Step 6d: Compute derived tables (PnL, MVF, markets_resolved)
pm-build --step derived

# Step 6e: Compute market price timeseries
pm-build --step prices
```

#### What each step does

| Step | Command | Input | Output | Time estimate |
|---|---|---|---|---|
| **sync** | `pm-sync` | CLOB API, Gamma API | `data/metadata/` (parquet + PG) | ~10 min (455K markets) |
| **compact** | `pm-compact` | `order_filled/*.parquet` | `data/compact/compact_*.parquet` | ~2-4 hours (438M rows) |
| **load** | `pm-load` | `data/compact/` | ClickHouse `trades_raw` | ~30-60 min |
| **derived** | `pm-build --step derived` | `data/compact/` + `data/metadata/` | `data/derived/*.parquet` | ~30-60 min |
| **prices** | `pm-build --step prices` | `data/compact/` + `data/metadata/` | `data/derived/market_prices.parquet` | ~15-30 min |

`pm-sync` has a 24h freshness gate — it skips if metadata was fetched recently. Use `--force` to override. `pm-compact` is resumable via `_manifest.json`.

#### Output directory structure

```
data/
├── metadata/
│   ├── markets.parquet          # CLOB + Gamma merged (condition_id, resolution, event_id, ...)
│   ├── token_map.parquet        # asset_id → condition_id, outcome, winner, token_index
│   └── _fetch_meta.json         # Freshness marker with fetch stats
├── compact/
│   ├── compact_0000.parquet     # Sorted, deduplicated, zstd-compressed
│   ├── compact_0001.parquet
│   ├── ...
│   └── _manifest.json           # Resume tracking
└── derived/
    ├── markets_resolved.parquet       # Resolved markets with yes_won flag
    ├── trader_market_pnl.parquet      # Per-trader per-market PnL
    ├── maker_volume_fractions.parquet  # Maker volume fractions
    └── market_prices.parquet          # YES price timeseries
```

---

### Step 7: Verify the backfill

```sql
-- Connect to ClickHouse
docker compose exec clickhouse clickhouse-client

-- Total trades loaded
SELECT count() FROM polymarket.trades_raw;
-- Expected: ~260M after dedup

-- Trades by month
SELECT toYYYYMM(timestamp) AS month, count() AS trades
FROM polymarket.trades_raw
GROUP BY month ORDER BY month;

-- Market metadata (reads from PG automatically)
SELECT count() FROM polymarket.markets;
-- Expected: ~455K

-- Sample query: top markets by volume
SELECT condition_id, count() AS trades, sum(amount_usd) AS volume
FROM polymarket.trades_raw
GROUP BY condition_id
ORDER BY volume DESC
LIMIT 10;
```

---

### Step 8: Start the live pipeline

```bash
pm-live
```

This starts the FastStream app which:
- Connects to RTDS WebSocket (real-time global trade feed)
- Connects to Alchemy RPC (on-chain Polygon events)
- Optionally polls pending blocks for ~1s early trade detection
- Publishes normalized trades to Redpanda `trades.raw`
- Runs a monitoring dashboard at http://localhost:8099/dashboard

Requires `PM_ALCHEMY_WS_URL` in `.env`.

---

## CLI Reference

All commands are registered as entrypoints (runnable after `uv sync`):

| Command | Purpose |
|---|---|
| `pm-build` | Orchestrate full offline pipeline (`--step {sync,compact,load,derived,prices,all}`) |
| `pm-sync` | Metadata sync: CLOB + Gamma API → PostgreSQL + Parquet (`--force`, `--skip-pg`, `--skip-parquet`) |
| `pm-compact` | Recompress raw parquet → sorted compact batches (`--workers`, `--batch-files`, `--global-sort`) |
| `pm-load` | Stream compact parquet → ClickHouse (`--compact-dir`, `--ch-host`) |
| `pm-live` | Start live pipeline (RTDS + Alchemy → Redpanda → ClickHouse) |
| `pm-backfill` | Legacy: direct raw parquet → ClickHouse (use `pm-build` instead) |
| `pm-explore` | Strategy exploration CLI (Typer) |
| `pm-recover` | Manual gap recovery via Goldsky subgraph |

Use `<command> --help` for full option documentation.

---

## Development

```bash
# Unit tests (fast, no Docker needed)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Integration tests (requires Docker services running)
uv run pytest tests/test_sink_clickhouse.py -x -q
uv run pytest tests/test_sink_postgres.py -x -q

# Type checking (strict, Pydantic plugin)
uv run mypy --strict src/

# Lint + format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

---

## Architecture

See [docs/README.md](docs/README.md) for the full architecture documentation, canonical model, and schema details.

```
Sources                    Pipeline                 Storage
────────                   ────────                 ───────
Goldsky Parquet ──┐                              ┌─ ClickHouse (trades_raw)
  (backfill)      │                              │   ReplacingMergeTree(_version)
                  ├── NormalizedTrade ──Redpanda──┤   ORDER BY (condition_id, timestamp, trade_id)
RTDS WebSocket ───┤   (canonical model)          │
  (live ~50/sec)  │                              │
Alchemy RPC ──────┘                              └─ PostgreSQL (metadata)
                                                     events, markets, tags, token_map
CLOB + Gamma API ──> pm-sync ──> PostgreSQL
  (metadata)                  └─> data/metadata/*.parquet

order_filled/ ──> pm-compact ──> data/compact/ ──> pm-load ──> ClickHouse
  (2033 raw files)                                             trades_raw

data/compact/ + data/metadata/ ──> pm-build --step derived ──> data/derived/
                                                                trader_market_pnl
                                                                maker_volume_fractions
                                                                markets_resolved
                                                                market_prices
```

### Key Design Decisions

- **Dedup**: Deterministic SHA-256 trade IDs. ClickHouse `ReplacingMergeTree` keeps highest `_version` — on-chain (2) > off-chain (1) > mempool (0).
- **Parquet reader**: Only `fastparquet` works. pyarrow fails on `DECIMAL(100,18)` precision > 76.
- **USDC scaling**: 1e6 (6 decimals), NOT 1e18.
- **Taker dedup**: ~40.5% of raw Parquet rows are taker-focused duplicates, filtered by matching taker against exchange contracts.
- **Metadata flow**: PostgreSQL is single source of truth. ClickHouse reads metadata via PostgreSQL engine (no duplicate writes).
- **Token convention**: `token_index=0` = affirmative/"YES" side.
- **Resolution source**: CLOB API `tokens[].winner` (ground truth). Gamma's `resolved` field is unreliable.
