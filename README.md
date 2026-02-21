# Polymarket Pipeline

Unified trade data pipeline for Polymarket: ingests from on-chain Parquet archives, RTDS WebSocket, and Alchemy RPC, normalizes into a canonical model, deduplicates across sources, and stores in ClickHouse. Market metadata syncs from the Gamma API into PostgreSQL.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker & Docker Compose

### 1. Install dependencies

```bash
uv sync --all-extras
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set PM_ALCHEMY_WS_URL with your Alchemy API key (required for live pipeline)
```

### 3. Start infrastructure

```bash
docker compose up -d
```

This starts:

| Service | Host Port | UI |
|---------|----------:|-----|
| ClickHouse (HTTP) | 18123 | — |
| ClickHouse (native) | 19000 | — |
| CH-UI | 15521 | http://localhost:15521 |
| PostgreSQL | 15432 | — |
| MLflow | 5050 | http://localhost:5050 |
| Redpanda (Kafka) | 19092 | — |
| Redpanda (HTTP Proxy) | 18082 | — |
| Redpanda Console | 18080 | http://localhost:18080 |

### 4. Create Redpanda topics

```bash
docker compose exec redpanda rpk topic create trades.raw -p 8 -r 1
docker compose exec redpanda rpk topic create pipeline.status -p 1 -r 1
```

### 5. Sync market metadata

```bash
uv run python -m polymarket_pipeline.cli.market_sync
```

### 6. Run the backfill (historical data)

```bash
uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
```

### 7. Start the live pipeline

```bash
uv run pm-live
```

Requires `PM_ALCHEMY_WS_URL` in `.env`. Connects to RTDS WebSocket and Alchemy RPC, publishes to Redpanda, and ClickHouse consumes via Kafka engine.

## Development

```bash
# Unit tests (no Docker needed)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Type checking
uv run mypy --strict src/

# Lint + format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Environment Variables

The live pipeline uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) with `PM_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_ALCHEMY_WS_URL` | *(required)* | Alchemy Polygon WebSocket URL |
| `PM_REDPANDA_URL` | `localhost:19092` | Redpanda broker address |
| `PM_CH_HOST` | `localhost` | ClickHouse host |
| `PM_CH_PORT` | `18123` | ClickHouse HTTP port |
| `PM_CH_DATABASE` | `polymarket` | ClickHouse database |
| `PM_PG_DSN` | `postgresql://polymarket:polymarket@localhost:15432/polymarket` | PostgreSQL DSN |

## Architecture

See [docs/README.md](docs/README.md) for the full architecture documentation, data source details, canonical model, and ClickHouse schema.

```
Goldsky Parquet ──┐                              ┌─ ClickHouse (trades_raw)
  (backfill)      │                              │   ReplacingMergeTree
                  ├── NormalizedTrade ──Redpanda──┤
RTDS WebSocket ───┤   (canonical model)          │   Kafka engine → MV
  (live)          │                              │
Alchemy RPC ──────┘                              └─ PostgreSQL (metadata)
                                                     events, markets, tags
Gamma API ─────────────> PostgreSQL
  (metadata sync)
```

## Offline Data Pipeline

```bash
uv run python scripts/build_data.py              # all steps
uv run python scripts/build_data.py --step metadata   # CLOB + Gamma API
uv run python scripts/build_data.py --step compact    # recompress parquet
uv run python scripts/build_data.py --step derived    # Polars PnL + MVF
uv run python scripts/build_data.py --step prices     # market price timeseries
```

## Strategy Backtester

```bash
uv run pm-explore --help                          # exploration CLI
uv run python -m strategies.consistency_copy.backtester   # sweep
```
