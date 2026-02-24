# Industrialization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the polymarket pipeline for production: fix critical bugs, containerize, add real execution with position tracking, self-protecting shutdown, observability, and long-term resilience.

**Architecture:** 5 vertical slices, each delivering end-to-end value. Pipeline (FastStream) and API (FastAPI) are separate processes. `pm-panic` CLI works independently. Next.js UI in `ui/` monorepo directory.

**Tech Stack:** Python 3.11+, FastStream, FastAPI, Next.js, Docker Compose, Prometheus, Grafana, Alembic, asyncpg, clickhouse-connect, pydantic-settings.

---

## Slice 1: "Safe to Run"

### Task 1.1: Replace hardcoded `192.168.0.148` with Settings-based defaults

**Files:**
- Modify: `src/polymarket_pipeline/sinks/clickhouse.py:16-22`
- Modify: `src/polymarket_pipeline/cli/backfill.py:30`
- Modify: `src/polymarket_pipeline/exploration/data.py:30-34`
- Modify: `src/polymarket_pipeline/exploration/tracking.py:24`
- Modify: `research/scripts/recompress_parquet.py:40`
- Modify: `tests/test_sink_clickhouse.py:44`
- Modify: `tests/test_e2e_backfill.py:17`
- Modify: `tests/test_sink_postgres.py:13`

**Step 1: Fix ClickHouseSink default host**

In `src/polymarket_pipeline/sinks/clickhouse.py`, change:
```python
    def __init__(
        self,
        host: str = "192.168.0.148",
```
to:
```python
    def __init__(
        self,
        host: str = "localhost",
```

**Step 2: Fix backfill CLI default DSN**

In `src/polymarket_pipeline/cli/backfill.py:30`, change:
```python
PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@192.168.0.148:15432/polymarket"
```
to:
```python
PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@localhost:15432/polymarket"
```

**Step 3: Fix exploration modules**

In `src/polymarket_pipeline/exploration/data.py:31`, change `"192.168.0.148"` to `"localhost"`.
In `src/polymarket_pipeline/exploration/data.py:77` (the `create_data_source` function), same change.
In `src/polymarket_pipeline/exploration/tracking.py:24`, change `"http://192.168.0.148:5050"` to `"http://localhost:5050"`.

**Step 4: Fix research script**

In `research/scripts/recompress_parquet.py:40`, change `"192.168.0.148"` to `"localhost"`.

**Step 5: Fix test files to use `localhost`**

In `tests/test_sink_clickhouse.py:44`, `tests/test_e2e_backfill.py:17`, `tests/test_sink_postgres.py:13` — replace `192.168.0.148` with `localhost`.

**Step 6: Run tests**

Run: `uv run pytest tests/test_models.py tests/test_trade_id.py tests/test_normalizer_rtds.py -x -q`
Expected: PASS (these don't touch ClickHouse/Postgres)

**Step 7: Commit**

```bash
git add -A && git commit -m "fix: replace hardcoded 192.168.0.148 with localhost defaults"
```

---

### Task 1.2: Reconcile ClickHouse schema — add `published_at` column

**Files:**
- Modify: `docker/clickhouse/init.sql`

**Step 1: Add `published_at` column to trades_raw**

In `docker/clickhouse/init.sql`, after line 33 (`ingested_at DateTime64(3) DEFAULT now64()`), add:
```sql
    published_at Float64 DEFAULT 0
```

So the ingestion section becomes:
```sql
    -- Ingestion
    ingested_at DateTime64(3) DEFAULT now64(),
    published_at Float64 DEFAULT 0
```

**Step 2: Commit**

```bash
git add docker/clickhouse/init.sql && git commit -m "fix: add published_at column to ClickHouse init schema"
```

**Note:** For existing deployments, run manually:
```sql
ALTER TABLE polymarket.trades_raw ADD COLUMN IF NOT EXISTS published_at Float64 DEFAULT 0;
```

---

### Task 1.3: Fix `ch.execute()` → `ch.query()` bug in app.py

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py:76-78`

**Step 1: Write a test**

Create or add to `tests/test_live_app.py`:
```python
def test_check_and_recover_uses_query_not_execute():
    """Verify _check_and_recover calls ch.query(), not ch.execute()."""
    import inspect
    from polymarket_pipeline.live import app
    source = inspect.getsource(app._check_and_recover)
    assert "ch.query(" in source or 'ch.query("' in source
    assert "ch.execute(" not in source
```

**Step 2: Run test to verify it fails**

Run: `PM_ALCHEMY_WS_URL=wss://dummy uv run pytest tests/test_live_app.py::test_check_and_recover_uses_query_not_execute -x -q`
Expected: FAIL

**Step 3: Fix the bug**

In `src/polymarket_pipeline/live/app.py`, change lines 76-78:
```python
    try:
        result = ch.execute("SELECT max(timestamp) FROM trades_raw")
        max_ts = result[0][0] if result and result[0][0] else None
```
to:
```python
    try:
        rows = ch.query("SELECT max(timestamp) AS max_ts FROM trades_raw")
        max_ts = rows[0]["max_ts"] if rows else None
```

**Step 4: Run test to verify it passes**

Run: `PM_ALCHEMY_WS_URL=wss://dummy uv run pytest tests/test_live_app.py::test_check_and_recover_uses_query_not_execute -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/app.py tests/test_live_app.py && git commit -m "fix: use ch.query() instead of ch.execute() for SELECT in recovery"
```

---

### Task 1.4: Add ClickHouseSink lifecycle management

**Files:**
- Modify: `src/polymarket_pipeline/sinks/clickhouse.py`
- Create: `tests/test_clickhouse_sink_lifecycle.py`

**Step 1: Write failing tests**

```python
# tests/test_clickhouse_sink_lifecycle.py
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink


def test_sink_has_close_method():
    assert hasattr(ClickHouseSink, "close")


def test_sink_supports_context_manager():
    assert hasattr(ClickHouseSink, "__enter__")
    assert hasattr(ClickHouseSink, "__exit__")


def test_sink_has_ping_method():
    assert hasattr(ClickHouseSink, "ping")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clickhouse_sink_lifecycle.py -x -q`
Expected: FAIL

**Step 3: Add lifecycle methods**

In `src/polymarket_pipeline/sinks/clickhouse.py`, add after `__init__`:

```python
    def close(self) -> None:
        """Close the underlying connection."""
        if self._client:
            self._client.close()

    def ping(self) -> bool:
        """Return True if ClickHouse is reachable."""
        try:
            self._client.command("SELECT 1")
            return True
        except Exception:
            return False

    def __enter__(self) -> ClickHouseSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

Also add the `from __future__ import annotations` import and `ClickHouseSink` type annotation.

**Step 4: Run tests**

Run: `uv run pytest tests/test_clickhouse_sink_lifecycle.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/sinks/clickhouse.py tests/test_clickhouse_sink_lifecycle.py && git commit -m "feat: add close/ping/context-manager to ClickHouseSink"
```

---

### Task 1.5: Add publish timeouts to all ingestors

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/rtds.py:112-116`
- Modify: `src/polymarket_pipeline/live/ingestors/alchemy.py:78-82`
- Modify: `src/polymarket_pipeline/live/ingestors/pending_block.py:186-190`
- Modify: `src/polymarket_pipeline/live/ingestors/mempool.py`
- Modify: `src/polymarket_pipeline/live/ingestors/clob_orderbook.py`

**Step 1: Create a shared publish helper**

Create `src/polymarket_pipeline/live/ingestors/_publish.py`:

```python
"""Shared publish helper with timeout protection."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger()

PUBLISH_TIMEOUT_S = 5.0


async def safe_publish(
    broker: Any,
    *,
    message: str,
    topic: str,
    key: bytes,
    source: str,
) -> bool:
    """Publish with timeout. Returns True on success, False on timeout."""
    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_S):
            await broker.publish(message=message, topic=topic, key=key)
        return True
    except TimeoutError:
        log.warning("publish.timeout", source=source, topic=topic, timeout=PUBLISH_TIMEOUT_S)
        return False
```

**Step 2: Replace raw `broker.publish` in RTDS ingestor**

In `src/polymarket_pipeline/live/ingestors/rtds.py`, add import:
```python
from polymarket_pipeline.live.ingestors._publish import safe_publish
```

Replace lines 112-116 (`await self._broker.publish(...)`) with:
```python
        await safe_publish(
            self._broker,
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
            source="rtds",
        )
```

Same for the heartbeat publish at lines 133-137:
```python
        await safe_publish(
            self._broker,
            message=heartbeat,
            topic=self._status_topic,
            key=b"rtds",
            source="rtds",
        )
```

**Step 3: Repeat for all other ingestors**

Apply the same pattern to:
- `alchemy.py` — trade publish (line ~78) and heartbeat (line ~96)
- `pending_block.py` — trade publish (line ~186) and heartbeat (line ~213)
- `mempool.py` — trade publish and heartbeat
- `clob_orderbook.py` — orderbook publish and heartbeat

**Step 4: Write a test for safe_publish**

```python
# tests/test_publish_helper.py
import asyncio
import pytest
from polymarket_pipeline.live.ingestors._publish import safe_publish


class _SlowBroker:
    async def publish(self, **kwargs: object) -> None:
        await asyncio.sleep(10)


class _FastBroker:
    published: list = []
    async def publish(self, **kwargs: object) -> None:
        self.published.append(kwargs)


@pytest.mark.asyncio
async def test_safe_publish_timeout():
    result = await safe_publish(
        _SlowBroker(), message="x", topic="t", key=b"k", source="test",
    )
    assert result is False


@pytest.mark.asyncio
async def test_safe_publish_success():
    broker = _FastBroker()
    result = await safe_publish(
        broker, message="x", topic="t", key=b"k", source="test",
    )
    assert result is True
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_publish_helper.py -x -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/_publish.py src/polymarket_pipeline/live/ingestors/*.py tests/test_publish_helper.py && git commit -m "feat: add publish timeout protection to all ingestors"
```

---

### Task 1.6: Bound the pending block queue + wrap decode in try/except

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/pending_block.py:91,177-201`

**Step 1: Add maxsize to the queue**

In `src/polymarket_pipeline/live/ingestors/pending_block.py:91`, change:
```python
        self._tx_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
```
to:
```python
        self._tx_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
```

**Step 2: Handle full queue in poll loop**

In the `_poll_loop` method, around line 158-159, change:
```python
                            for tx in new_txs:
                                await self._tx_queue.put(tx)
```
to:
```python
                            for tx in new_txs:
                                try:
                                    self._tx_queue.put_nowait(tx)
                                except asyncio.QueueFull:
                                    log.warning("pending_block.queue_full", dropped_tx=tx.get("hash", "?"))
```

**Step 3: Wrap _process_loop in try/except**

In the `_process_loop` method (line 177), change:
```python
    async def _process_loop(self) -> None:
        """Consume txs from the shared queue, decode, and publish."""
        while True:
            tx = await self._tx_queue.get()
            self._tx_count += 1

            trades = self._normalizer.decode_tx(tx)
            for trade in trades:
```
to:
```python
    async def _process_loop(self) -> None:
        """Consume txs from the shared queue, decode, and publish."""
        while True:
            tx = await self._tx_queue.get()
            self._tx_count += 1

            try:
                trades = self._normalizer.decode_tx(tx)
            except Exception:
                log.exception("pending_block.decode_error", tx_hash=tx.get("hash", "?"))
                continue

            for trade in trades:
```

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/pending_block.py && git commit -m "fix: bound pending block queue + catch decode errors"
```

---

### Task 1.7: Add timeout to Subgraph GQL queries

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/subgraph.py`

**Step 1: Find the `_execute_with_retry` method and wrap with timeout**

Around line 162, the method calls `client.execute(query, variable_values=variables)`. Wrap it:

```python
            return await asyncio.wait_for(
                client.execute(query, variable_values=variables),
                timeout=30.0,
            )
```

Make sure `asyncio` is already imported (it is, at line 5).

**Step 2: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/subgraph.py && git commit -m "fix: add 30s timeout to Subgraph GQL queries"
```

---

### Task 1.8: Make startup recovery non-blocking

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py:109,125`

**Step 1: Add timeout to the recovery call**

In `src/polymarket_pipeline/live/app.py`, change line 109:
```python
    total = await poller.recover(from_timestamp=last_ts)
```
to:
```python
    try:
        async with asyncio.timeout(300):
            total = await poller.recover(from_timestamp=last_ts)
    except TimeoutError:
        log.warning("recovery.timeout", timeout_s=300, from_ts=last_ts)
        return
```

**Step 2: Launch recovery as a background task in on_startup**

In `on_startup()`, change line 125:
```python
    await _check_and_recover(token_map)
```
to:
```python
    _ingestor_tasks.append(asyncio.create_task(_check_and_recover(token_map)))
```

This way recovery runs in parallel with ingestors rather than blocking them.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/app.py && git commit -m "fix: make startup recovery non-blocking with 300s timeout"
```

---

### Task 1.9: Create Dockerfile

**Files:**
- Create: `Dockerfile`

**Step 1: Write multi-stage Dockerfile**

```dockerfile
# ── Build stage ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --all-extras --no-dev --frozen

COPY src/ src/

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"

# Default: run the live pipeline
CMD ["pm-live"]
```

**Step 2: Add `.dockerignore`**

```
docker-drives/
order_filled/
data/
.git/
__pycache__/
*.pyc
.env
.venv/
crates/
research/
```

**Step 3: Test build**

Run: `docker build -t polymarket-app .`

**Step 4: Commit**

```bash
git add Dockerfile .dockerignore && git commit -m "feat: add multi-stage Dockerfile for pipeline app"
```

---

### Task 1.10: Add pipeline app to docker-compose + health check

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add polymarket-app service**

Add before the final line of `docker-compose.yml`:

```yaml
  polymarket-app:
    build: .
    env_file: .env
    depends_on:
      - clickhouse
      - postgres
      - redpanda
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8099/health/live"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
    ports:
      - "8099:8099"
```

**Step 2: Add Docker health checks to existing services**

Add to the `clickhouse` service:
```yaml
    healthcheck:
      test: ["CMD", "clickhouse-client", "--query", "SELECT 1"]
      interval: 10s
      timeout: 5s
      retries: 3
```

Add to the `postgres` service:
```yaml
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U polymarket"]
      interval: 10s
      timeout: 5s
      retries: 3
```

**Step 3: Commit**

```bash
git add docker-compose.yml && git commit -m "feat: add polymarket-app service to docker-compose with health checks"
```

---

### Task 1.11: Add `/health/live` and `/health/ready` endpoints

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py`

**Step 1: Add health endpoint handlers**

In `src/polymarket_pipeline/live/app.py`, add after the `_dashboard_placeholder` function:

```python
async def _health_live(scope: Any, receive: Any, send: Any) -> None:
    """Liveness: 200 if process is running."""
    from faststream.asgi import AsgiResponse
    resp = AsgiResponse(body=b'{"status":"alive"}', status_code=200,
                        headers={"content-type": "application/json"})
    await resp(scope, receive, send)


async def _health_ready(scope: Any, receive: Any, send: Any) -> None:
    """Readiness: 200 if pipeline state is READY, 503 otherwise."""
    from faststream.asgi import AsgiResponse
    if _quality_checker and _quality_checker.state.current.value == "ready":
        resp = AsgiResponse(body=b'{"status":"ready"}', status_code=200,
                            headers={"content-type": "application/json"})
    else:
        resp = AsgiResponse(body=b'{"status":"not_ready"}', status_code=503,
                            headers={"content-type": "application/json"})
    await resp(scope, receive, send)
```

**Step 2: Mount routes**

Change the `asgi_app` line:
```python
asgi_app = app.as_asgi(asgi_routes=[("/dashboard", _dashboard_placeholder)])
```
to:
```python
asgi_app = app.as_asgi(asgi_routes=[
    ("/dashboard", _dashboard_placeholder),
    ("/health/live", _health_live),
    ("/health/ready", _health_ready),
])
```

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/app.py && git commit -m "feat: add /health/live and /health/ready endpoints"
```

---

### Task 1.12: Add GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Write CI workflow**

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-type-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
      - run: uv sync --all-extras
      - name: Lint
        run: uv run ruff check src/ tests/
      - name: Format check
        run: uv run ruff format --check src/ tests/
      - name: Type check
        run: uv run mypy --strict src/
      - name: Unit tests
        run: |
          uv run pytest tests/ -x -q \
            --ignore=tests/test_loader_parquet.py \
            --ignore=tests/test_e2e_backfill.py \
            --ignore=tests/test_market_sync.py \
            --ignore=tests/test_sink_clickhouse.py \
            --ignore=tests/test_sink_postgres.py
```

**Step 2: Commit**

```bash
git add .github/workflows/ci.yml && git commit -m "feat: add GitHub Actions CI (lint, typecheck, unit tests)"
```

---

### Task 1.13: Run full unit test suite and fix any regressions

**Step 1: Run all unit tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`

**Step 2: Run type checker**

Run: `uv run mypy --strict src/`

**Step 3: Run linter**

Run: `uv run ruff check src/ tests/`

**Step 4: Fix any failures, then commit**

```bash
git add -A && git commit -m "fix: resolve regressions from Slice 1 changes"
```

---

## Slice 2: "Safe to Trade Small"

### Task 2.1: CLOB API order client

**Files:**
- Create: `src/polymarket_pipeline/execution/__init__.py`
- Create: `src/polymarket_pipeline/execution/clob_client.py`
- Create: `tests/test_clob_client.py`

**Step 1: Write protocol + client**

The client wraps the Polymarket CLOB API (REST). Key methods:
- `async def submit_order(condition_id, side, size_usd, price) -> OrderResult`
- `async def cancel_order(order_id) -> bool`
- `async def get_balances() -> dict[str, float]`
- `async def get_open_orders() -> list[OpenOrder]`

Use `httpx.AsyncClient` internally. Base URL configurable via `PM_CLOB_API_URL`. API key via `PM_CLOB_API_KEY`.

**Step 2: Write unit tests** with mocked HTTP responses (use `httpx`'s mock transport or `respx`).

**Step 3: Commit**

---

### Task 2.2: Position tracker + PostgreSQL persistence

**Files:**
- Create: `src/polymarket_pipeline/execution/position_tracker.py`
- Create: `src/polymarket_pipeline/execution/models.py`
- Create: `tests/test_position_tracker.py`

**Step 1: Design the data model**

```python
@dataclass(frozen=True)
class Position:
    condition_id: str
    side: str          # "BUY" or "SELL"
    size: float        # number of tokens
    avg_entry: float   # avg price paid
    cost_basis: float  # total USD in
    unrealized_pnl: float
    last_price: float


@dataclass(frozen=True)
class FillRecord:
    intent_id: str
    strategy: str
    condition_id: str
    side: str
    outcome: str
    price: float
    size_usd: float
    fee_usd: float
    filled_at: datetime
```

**Step 2: Create PostgreSQL tables** — add to a new migration or init.sql:

```sql
CREATE TABLE IF NOT EXISTS positions (
    condition_id TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_entry DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_basis DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fills (
    id SERIAL PRIMARY KEY,
    intent_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    size_usd DOUBLE PRECISION NOT NULL,
    fee_usd DOUBLE PRECISION NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fills_condition ON fills(condition_id);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy);
```

**Step 3: Implement PositionTracker** — methods: `record_fill()`, `get_position()`, `get_all_positions()`, `get_total_exposure()`.

**Step 4: Write unit tests with in-memory state** (test the logic, not the DB).

**Step 5: Commit**

---

### Task 2.3: Panic close module (shared core)

**Files:**
- Create: `src/polymarket_pipeline/execution/panic.py`
- Create: `tests/test_panic.py`

**Step 1: Implement panic_close_all()**

```python
async def panic_close_all(
    clob: ClobClient,
    tracker: PositionTracker,
) -> list[OrderResult]:
    """Market-sell all open positions. Returns list of fill results."""
    positions = await tracker.get_all_positions()
    results = []
    for pos in positions:
        if pos.size <= 0:
            continue
        # Close by taking opposite side
        close_side = "SELL" if pos.side == "BUY" else "BUY"
        result = await clob.submit_order(
            condition_id=pos.condition_id,
            side=close_side,
            size_usd=pos.size * pos.last_price,
            price=None,  # market order
        )
        results.append(result)
    return results
```

**Step 2: Test with mock client + tracker**

**Step 3: Commit**

---

### Task 2.4: `pm-panic` CLI

**Files:**
- Create: `src/polymarket_pipeline/cli/panic.py`
- Modify: `pyproject.toml` (add entry point)

**Step 1: Implement CLI entry point**

Uses `asyncio.run()`, creates `ClobClient` and `PositionTracker` directly (no FastAPI dependency), calls `panic_close_all()`.

**Step 2: Add entry point to pyproject.toml**

```toml
pm-panic = "polymarket_pipeline.cli.panic:main"
```

**Step 3: Commit**

---

### Task 2.5: Wire strategy signals → live executor

**Files:**
- Create: `src/polymarket_pipeline/strategies/execution/live.py`
- Modify: `src/polymarket_pipeline/strategies/execution/gateway.py` (add position limit check)

**Step 1: Create LiveExecutor**

Similar to `PaperExecutor` but calls `ClobClient.submit_order()` and records fills via `PositionTracker.record_fill()`.

**Step 2: Add position size limit check in ExecutionGateway**

Before delegating to executor, check:
- Position in this market < `PM_MAX_POSITION_USD`
- Total exposure < `PM_MAX_TOTAL_EXPOSURE_USD`

If exceeded, reject the intent with a `FillStatus.REJECTED` fill.

**Step 3: Add settings**

In `src/polymarket_pipeline/live/settings.py`, add:
```python
    max_position_usd: float = 100.0
    max_total_exposure_usd: float = 500.0
    clob_api_url: str = "https://clob.polymarket.com"
    clob_api_key: str = ""
```

**Step 4: Tests + commit**

---

### Task 2.6: FastAPI service

**Files:**
- Create: `src/polymarket_pipeline/api/__init__.py`
- Create: `src/polymarket_pipeline/api/app.py`
- Create: `src/polymarket_pipeline/api/routes/positions.py`
- Create: `src/polymarket_pipeline/api/routes/panic.py`
- Create: `src/polymarket_pipeline/api/routes/strategies.py`
- Create: `src/polymarket_pipeline/api/routes/health.py`
- Modify: `pyproject.toml` (add `api` extras, entry point)

**Step 1: Design API routes**

```
GET  /api/positions          → list all open positions
GET  /api/positions/:cid     → single position detail
GET  /api/fills              → recent fills (paginated)
POST /api/panic              → trigger panic close all
GET  /api/strategies         → list active strategies + state
GET  /api/health             → deep health check (CH, PG, Redpanda)
```

**Step 2: Implement FastAPI app**

Standard FastAPI with `lifespan` context manager for startup/shutdown (connect PG pool, init ClobClient).

**Step 3: Add entry point**

```toml
pm-api = "polymarket_pipeline.api.app:main"
```

**Step 4: Add `api` extras to pyproject.toml**

```toml
api = ["fastapi>=0.115", "uvicorn"]
```

**Step 5: Write tests (TestClient)**

**Step 6: Commit**

---

### Task 2.7: Next.js UI scaffold

**Files:**
- Create: `ui/` directory (Next.js app)

**Step 1: Initialize Next.js project**

```bash
cd ui && npx create-next-app@latest . --typescript --tailwind --app --eslint
```

**Step 2: Create pages**

- `/` — Dashboard: positions table, total exposure, PnL
- `/positions` — Detailed positions view
- `/fills` — Fill history
- `/panic` — Panic button with confirmation dialog

**Step 3: Add API client**

Create `ui/lib/api.ts` that fetches from `http://localhost:8000/api/*`.

**Step 4: Add Dockerfile for UI**

```dockerfile
FROM node:20-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY --from=builder /app/.next .next
COPY --from=builder /app/node_modules node_modules
COPY --from=builder /app/package.json .
CMD ["npm", "start"]
```

**Step 5: Add to docker-compose**

```yaml
  polymarket-ui:
    build: ./ui
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://polymarket-api:8000
    depends_on:
      - polymarket-api

  polymarket-api:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["pm-api"]
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      - clickhouse
      - postgres
      - redpanda
```

**Step 6: Commit**

---

### Task 2.8: Drop old dashboard

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py` — remove dashboard mount, keep health endpoints
- Keep: `src/polymarket_pipeline/live/dashboard.py` for now (quality checker still uses SQL queries for data)

**Step 1: Remove dashboard route from asgi_app**

Remove the `/dashboard` route and the dashboard mount in `on_startup`. Keep `/health/*` routes.

**Step 2: Commit**

---

## Slice 3: "Self-Protecting"

### Task 3.1: Extend ReadinessState with RED and grace periods

**Files:**
- Modify: `src/polymarket_pipeline/live/quality/state.py`
- Modify: `src/polymarket_pipeline/live/settings.py`
- Create: `tests/test_quality_state_extended.py`

Add `RED`, `CLOSING`, `SAFE_STOP` to `PipelineState`. Add `degraded_since` timestamp and grace period logic.

---

### Task 3.2: Stop opening positions on DEGRADED

**Files:**
- Modify: `src/polymarket_pipeline/strategies/execution/gateway.py`

Check pipeline state before executing. If DEGRADED, reject new intents.

---

### Task 3.3: Auto-panic on RED

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py` (status consumer)

When state transitions to RED after grace period, call `panic_close_all()`.

---

### Task 3.4: Fix state machine — emit `caught_up`

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py`

After recovery completes (or on first heartbeat from each required source), publish `{"event": "caught_up"}` to `pipeline.status`.

---

### Task 3.5: Pipeline → API quality bridge

**Files:**
- Create: `pipeline.quality` Redpanda topic
- Modify: API to consume quality state

---

### Task 3.6: Position-aware shutdown

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py:199-205`

In `on_shutdown`, close all positions BEFORE cancelling ingestors.

---

## Slice 4: "Observable"

### Task 4.1: Prometheus metrics on FastAPI

Add `prometheus_client` to API extras. Expose `/metrics` endpoint. Key gauges: `positions_open`, `total_exposure_usd`, `unrealized_pnl`. Key counters: `trades_published_total` (by source), `publish_timeouts_total`, `normalize_drops_total`.

---

### Task 4.2: Pipeline metrics via Redpanda

Ingestors publish to `pipeline.metrics` topic. API aggregates and exposes.

---

### Task 4.3: Grafana + provisioned dashboard

Add Grafana service to docker-compose with auto-provisioned Prometheus datasource and dashboard JSON.

---

### Task 4.4: Normalization drop counters

Add counters to each normalizer for dropped trades. Publish in heartbeat messages.

---

### Task 4.5: Structured log sink to file

Configure structlog to write to a rotated file in addition to console.

---

## Slice 5: "Resilient"

### Task 5.1: Circuit breaker on broker publish

Shared circuit breaker state. After N consecutive publish timeouts, trip the breaker. Auto-reset after cooldown.

---

### Task 5.2: Graceful shutdown with drain

```python
@app.on_shutdown
async def on_shutdown() -> None:
    # 1. Close positions (Slice 3)
    # 2. Signal ingestors to stop accepting new messages
    # 3. Wait up to 10s for in-flight publishes to complete
    for task in _ingestor_tasks:
        task.cancel()
    await asyncio.gather(*_ingestor_tasks, return_exceptions=True)
    _ingestor_tasks.clear()
```

---

### Task 5.3: Alembic for PostgreSQL migrations

Initialize Alembic, create initial migration from current schema, add `pm-migrate` CLI entry point.

---

### Task 5.4: ClickHouse migration table

Create `_migrations` table in ClickHouse, sequential `.sql` files in `docker/clickhouse/migrations/`, apply on app startup.

---

### Task 5.5: Backpressure queues in all ingestors

Generalize the PendingBlock pattern: bounded async queue between WS read and publish for RTDS and Alchemy ingestors.

---

### Task 5.6: Time-based dedup eviction

Replace size-based eviction in `_TradeDedup` with TTL-based eviction using the `time.monotonic()` values already stored.

---

### Task 5.7: Fix RedpandaSink.write() async issue

Make `TradeSink.write()` an async method, or use `asyncio.run_coroutine_threadsafe()`.

---

### Task 5.8: Fix trade ID divergence

In `loaders/parquet.py`, replace inline SHA-256 with call to `make_trade_id_chain()`.

---

### Task 5.9: Fix load_strategy_configs mutation

In `strategies/config.py:66-67`, replace `.pop()` with `.get()`.

---

### Task 5.10: Implement or remove placeholder quality checks

Either implement `check_metadata_freshness()` and `check_resolved_completeness()`, or remove them from `run_all_checks()`.

---
