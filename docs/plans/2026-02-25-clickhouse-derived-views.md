# ClickHouse Derived Views Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace stale parquet-based derived tables with live-updating ClickHouse materialized views so strategy features are always fresh.

**Architecture:** Hybrid approach (C) — VIEW for resolution metadata, SummingMergeTree + MVs for trade aggregates (maker + taker perspectives), query-time PnL computation. Backfill existing trades_raw data once via migration.

**Tech Stack:** ClickHouse 24.8, SummingMergeTree, SimpleAggregateFunction, PostgreSQL engine tables, existing `ch_migrate.py` runner.

---

### Task 1: Migration — Create derived tables and MVs

**Files:**
- Create: `docker/clickhouse/migrations/003_derived_views.sql`

**Step 1: Write the migration SQL**

```sql
-- 003_derived_views.sql
-- Derived feature tables: markets_resolved VIEW, trader_volumes MV, trader_trade_agg MV

-- =================================================================
-- 1. markets_resolved — VIEW over PG engine tables (always fresh)
-- =================================================================
CREATE OR REPLACE VIEW polymarket.markets_resolved AS
SELECT
    m.condition_id,
    m.resolution_value,
    m.winner_outcome,
    m.resolved_at,
    tm.asset_id,
    tm.outcome,
    tm.winner AS token_won
FROM polymarket.markets m
INNER JOIN polymarket.token_market_map tm ON m.condition_id = tm.condition_id
WHERE m.resolution_value = 1;

-- =================================================================
-- 2. trader_volumes — SummingMergeTree for MVF computation
-- =================================================================
CREATE TABLE IF NOT EXISTS polymarket.trader_volumes (
    trader          String,
    maker_vol       Float64,
    taker_vol       Float64
) ENGINE = SummingMergeTree((maker_vol, taker_vol))
ORDER BY trader;

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_volumes_maker_mv
TO polymarket.trader_volumes AS
SELECT
    maker AS trader,
    amount_usd AS maker_vol,
    0 AS taker_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != '';

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_volumes_taker_mv
TO polymarket.trader_volumes AS
SELECT
    taker AS trader,
    0 AS maker_vol,
    amount_usd AS taker_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != '';

-- =================================================================
-- 3. trader_trade_agg — SummingMergeTree for per-market aggregates
--    Both maker and taker perspectives, matching derived.py logic.
-- =================================================================
CREATE TABLE IF NOT EXISTS polymarket.trader_trade_agg (
    trader          String,
    condition_id    LowCardinality(String),
    asset_id        String,
    -- Signed: maker BUY = +tokens/-usd, taker BUY = -tokens/+usd
    net_tokens      Float64,
    net_usd         Float64,
    total_fees      Float64,
    volume          Float64,
    trade_count     UInt64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3)),
    price_x_vol     Float64
) ENGINE = SummingMergeTree(
    (net_tokens, net_usd, total_fees, volume, trade_count, price_x_vol)
)
ORDER BY (trader, condition_id, asset_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_trade_agg_maker_mv
TO polymarket.trader_trade_agg AS
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', toFloat64(size), -toFloat64(size)) AS net_tokens,
    if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != '';

CREATE MATERIALIZED VIEW IF NOT EXISTS polymarket.trader_trade_agg_taker_mv
TO polymarket.trader_trade_agg AS
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', -toFloat64(size), toFloat64(size)) AS net_tokens,
    if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != ''
```

**Step 2: Verify migration file parses**

Run: `cat docker/clickhouse/migrations/003_derived_views.sql | head -5`
Expected: first 5 lines visible, no encoding issues.

**Step 3: Commit**

```bash
git add docker/clickhouse/migrations/003_derived_views.sql
git commit -m "feat: add ClickHouse migration 003 — derived views and MVs"
```

---

### Task 2: Backfill migration — populate from existing trades_raw

**Files:**
- Create: `docker/clickhouse/migrations/004_backfill_derived.sql`

**Step 1: Write the backfill migration**

```sql
-- 004_backfill_derived.sql
-- One-time backfill of derived tables from existing trades_raw data.
-- After this, MVs keep everything current automatically.

-- Backfill trader_volumes from existing data
INSERT INTO polymarket.trader_volumes
SELECT
    maker AS trader,
    sum(amount_usd) AS maker_vol,
    0 AS taker_vol
FROM polymarket.trades_raw FINAL
WHERE maker IS NOT NULL AND maker != ''
GROUP BY maker;

INSERT INTO polymarket.trader_volumes
SELECT
    taker AS trader,
    0 AS maker_vol,
    sum(amount_usd) AS taker_vol
FROM polymarket.trades_raw FINAL
WHERE taker IS NOT NULL AND taker != ''
GROUP BY taker;

-- Backfill trader_trade_agg — maker perspective
INSERT INTO polymarket.trader_trade_agg
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    sum(if(side = 'BUY', toFloat64(size), -toFloat64(size))) AS net_tokens,
    sum(if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd))) AS net_usd,
    sum(toFloat64(fee_usd)) AS total_fees,
    sum(toFloat64(amount_usd)) AS volume,
    toUInt64(count()) AS trade_count,
    min(timestamp) AS first_trade,
    max(timestamp) AS last_trade,
    sum(toFloat64(price) * toFloat64(amount_usd)) AS price_x_vol
FROM polymarket.trades_raw FINAL
WHERE maker IS NOT NULL AND maker != ''
GROUP BY maker, condition_id, asset_id;

-- Backfill trader_trade_agg — taker perspective
INSERT INTO polymarket.trader_trade_agg
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    sum(if(side = 'BUY', -toFloat64(size), toFloat64(size))) AS net_tokens,
    sum(if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd))) AS net_usd,
    sum(toFloat64(fee_usd)) AS total_fees,
    sum(toFloat64(amount_usd)) AS volume,
    toUInt64(count()) AS trade_count,
    min(timestamp) AS first_trade,
    max(timestamp) AS last_trade,
    sum(toFloat64(price) * toFloat64(amount_usd)) AS price_x_vol
FROM polymarket.trades_raw FINAL
WHERE taker IS NOT NULL AND taker != ''
GROUP BY taker, condition_id, asset_id
```

**Step 2: Commit**

```bash
git add docker/clickhouse/migrations/004_backfill_derived.sql
git commit -m "feat: add ClickHouse migration 004 — backfill derived tables"
```

---

### Task 3: Register DDL in live schema

**Files:**
- Modify: `src/polymarket_pipeline/live/schema.py`

**Step 1: Write failing test**

Create test that verifies schema module exports the new DDL constants.

Test: `tests/test_live_schema_derived.py`

```python
"""Tests for derived table DDL in live schema."""

from __future__ import annotations

from polymarket_pipeline.live.schema import (
    MARKETS_RESOLVED_VIEW,
    TRADER_TRADE_AGG_MAKER_MV,
    TRADER_TRADE_AGG_TABLE,
    TRADER_TRADE_AGG_TAKER_MV,
    TRADER_VOLUMES_MAKER_MV,
    TRADER_VOLUMES_TABLE,
    TRADER_VOLUMES_TAKER_MV,
)


def test_ddl_constants_are_nonempty_strings() -> None:
    for name, ddl in [
        ("MARKETS_RESOLVED_VIEW", MARKETS_RESOLVED_VIEW),
        ("TRADER_VOLUMES_TABLE", TRADER_VOLUMES_TABLE),
        ("TRADER_VOLUMES_MAKER_MV", TRADER_VOLUMES_MAKER_MV),
        ("TRADER_VOLUMES_TAKER_MV", TRADER_VOLUMES_TAKER_MV),
        ("TRADER_TRADE_AGG_TABLE", TRADER_TRADE_AGG_TABLE),
        ("TRADER_TRADE_AGG_MAKER_MV", TRADER_TRADE_AGG_MAKER_MV),
        ("TRADER_TRADE_AGG_TAKER_MV", TRADER_TRADE_AGG_TAKER_MV),
    ]:
        assert isinstance(ddl, str), f"{name} should be a string"
        assert len(ddl.strip()) > 50, f"{name} should contain real DDL"


def test_ddl_contains_correct_table_names() -> None:
    assert "markets_resolved" in MARKETS_RESOLVED_VIEW
    assert "trader_volumes" in TRADER_VOLUMES_TABLE
    assert "SummingMergeTree" in TRADER_VOLUMES_TABLE
    assert "trader_trade_agg" in TRADER_TRADE_AGG_TABLE
    assert "SummingMergeTree" in TRADER_TRADE_AGG_TABLE
    assert "TO trader_volumes" in TRADER_VOLUMES_MAKER_MV
    assert "TO trader_trade_agg" in TRADER_TRADE_AGG_MAKER_MV
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_live_schema_derived.py -x -q`
Expected: FAIL with `ImportError: cannot import name 'MARKETS_RESOLVED_VIEW'`

**Step 3: Add DDL constants to schema.py**

Add to `src/polymarket_pipeline/live/schema.py` after the existing constants, before `apply_schema()`:

```python
# ======================================================================
# Derived feature tables (live-updating from trades_raw)
# ======================================================================

MARKETS_RESOLVED_VIEW = """
CREATE OR REPLACE VIEW markets_resolved AS
SELECT
    m.condition_id,
    m.resolution_value,
    m.winner_outcome,
    m.resolved_at,
    tm.asset_id,
    tm.outcome,
    tm.winner AS token_won
FROM markets m
INNER JOIN token_market_map tm ON m.condition_id = tm.condition_id
WHERE m.resolution_value = 1
"""

TRADER_VOLUMES_TABLE = """
CREATE TABLE IF NOT EXISTS trader_volumes (
    trader          String,
    maker_vol       Float64,
    taker_vol       Float64
) ENGINE = SummingMergeTree((maker_vol, taker_vol))
ORDER BY trader
"""

TRADER_VOLUMES_MAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_volumes_maker_mv
TO trader_volumes AS
SELECT
    maker AS trader,
    amount_usd AS maker_vol,
    0 AS taker_vol
FROM trades_raw
WHERE maker IS NOT NULL AND maker != ''
"""

TRADER_VOLUMES_TAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_volumes_taker_mv
TO trader_volumes AS
SELECT
    taker AS trader,
    0 AS maker_vol,
    amount_usd AS taker_vol
FROM trades_raw
WHERE taker IS NOT NULL AND taker != ''
"""

TRADER_TRADE_AGG_TABLE = """
CREATE TABLE IF NOT EXISTS trader_trade_agg (
    trader          String,
    condition_id    LowCardinality(String),
    asset_id        String,
    net_tokens      Float64,
    net_usd         Float64,
    total_fees      Float64,
    volume          Float64,
    trade_count     UInt64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3)),
    price_x_vol     Float64
) ENGINE = SummingMergeTree(
    (net_tokens, net_usd, total_fees, volume, trade_count, price_x_vol)
)
ORDER BY (trader, condition_id, asset_id)
"""

TRADER_TRADE_AGG_MAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_trade_agg_maker_mv
TO trader_trade_agg AS
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', toFloat64(size), -toFloat64(size)) AS net_tokens,
    if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM trades_raw
WHERE maker IS NOT NULL AND maker != ''
"""

TRADER_TRADE_AGG_TAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_trade_agg_taker_mv
TO trader_trade_agg AS
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', -toFloat64(size), toFloat64(size)) AS net_tokens,
    if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM trades_raw
WHERE taker IS NOT NULL AND taker != ''
"""
```

Also update `apply_schema()` to create these after the existing tables:

```python
def apply_schema(clickhouse: object, broker_list: str = "localhost:19092") -> None:
    # ... existing code ...

    # Derived feature tables
    clickhouse.execute(TRADER_VOLUMES_TABLE)
    clickhouse.execute(TRADER_TRADE_AGG_TABLE)
    clickhouse.execute(MARKETS_RESOLVED_VIEW)
    clickhouse.execute(TRADER_VOLUMES_MAKER_MV)
    clickhouse.execute(TRADER_VOLUMES_TAKER_MV)
    clickhouse.execute(TRADER_TRADE_AGG_MAKER_MV)
    clickhouse.execute(TRADER_TRADE_AGG_TAKER_MV)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_live_schema_derived.py -x -q`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: all pass

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/schema.py tests/test_live_schema_derived.py
git commit -m "feat: add derived feature DDL to live schema module"
```

---

### Task 4: Add PnL + MVF query helpers to ClickHouseBackend

**Files:**
- Modify: `src/polymarket_pipeline/strategies/features/backend_clickhouse.py`
- Create: `tests/test_backend_clickhouse_queries.py`

**Step 1: Write failing test**

Test: `tests/test_backend_clickhouse_queries.py`

```python
"""Tests for ClickHouseBackend derived-view query builders."""

from __future__ import annotations

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend


def test_mvf_query_is_valid_sql() -> None:
    """MVF query should use trader_volumes FINAL."""
    sql = ClickHouseBackend.mvf_query()
    assert "trader_volumes" in sql
    assert "FINAL" in sql
    assert "maker_vol" in sql
    assert "taker_vol" in sql


def test_mvf_query_with_traders_filter() -> None:
    """MVF query should accept trader filter."""
    sql = ClickHouseBackend.mvf_query(traders=["0xA", "0xB"])
    assert "WHERE" in sql
    assert "'0xA'" in sql
    assert "'0xB'" in sql


def test_trader_pnl_query_is_valid_sql() -> None:
    """PnL query should join trader_trade_agg with markets_resolved."""
    sql = ClickHouseBackend.trader_pnl_query()
    assert "trader_trade_agg" in sql
    assert "FINAL" in sql
    assert "markets_resolved" in sql
    assert "market_pnl" in sql


def test_trader_pnl_query_with_traders_filter() -> None:
    """PnL query should accept trader filter."""
    sql = ClickHouseBackend.trader_pnl_query(traders=["0xC"])
    assert "'0xC'" in sql


def test_trader_pnl_query_with_condition_filter() -> None:
    """PnL query should accept condition_id filter."""
    sql = ClickHouseBackend.trader_pnl_query(condition_ids=["0xm1"])
    assert "'0xm1'" in sql
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backend_clickhouse_queries.py -x -q`
Expected: FAIL with `AttributeError: type object 'ClickHouseBackend' has no attribute 'mvf_query'`

**Step 3: Add static query builder methods to ClickHouseBackend**

Add to `src/polymarket_pipeline/strategies/features/backend_clickhouse.py`, as static methods on the class:

```python
@staticmethod
def mvf_query(traders: list[str] | None = None) -> str:
    """Build SQL for maker volume fractions from trader_volumes."""
    where = ""
    if traders:
        ids = ", ".join(f"'{t}'" for t in traders)
        where = f"WHERE trader IN ({ids})"
    return f"""
        SELECT
            trader,
            maker_vol,
            taker_vol,
            maker_vol + taker_vol AS total_vol,
            maker_vol / (maker_vol + taker_vol) AS mvf
        FROM trader_volumes FINAL
        {where}
    """

@staticmethod
def trader_pnl_query(
    traders: list[str] | None = None,
    condition_ids: list[str] | None = None,
) -> str:
    """Build SQL for trader-market PnL from derived views."""
    filters = []
    if traders:
        ids = ", ".join(f"'{t}'" for t in traders)
        filters.append(f"a.trader IN ({ids})")
    if condition_ids:
        cids = ", ".join(f"'{c}'" for c in condition_ids)
        filters.append(f"a.condition_id IN ({cids})")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return f"""
        SELECT
            a.trader,
            a.condition_id,
            sum(a.net_tokens * if(mr.token_won, 1.0, 0.0)
                + a.net_usd - a.total_fees) AS market_pnl,
            sum(a.volume) AS market_volume,
            sum(a.trade_count) AS trade_count,
            min(a.first_trade) AS first_trade,
            max(a.last_trade) AS last_trade,
            sum(a.price_x_vol) / nullIf(sum(a.volume), 0) AS wavg_entry_price
        FROM trader_trade_agg FINAL AS a
        INNER JOIN markets_resolved AS mr
            ON a.condition_id = mr.condition_id AND a.asset_id = mr.asset_id
        {where}
        GROUP BY a.trader, a.condition_id
    """
```

Also add convenience async methods:

```python
async def query_mvf(self, traders: list[str] | None = None) -> pl.DataFrame:
    """Query maker volume fractions from derived views."""
    return await self._execute(self.mvf_query(traders=traders))

async def query_trader_pnl(
    self,
    traders: list[str] | None = None,
    condition_ids: list[str] | None = None,
) -> pl.DataFrame:
    """Query trader-market PnL from derived views."""
    return await self._execute(
        self.trader_pnl_query(traders=traders, condition_ids=condition_ids)
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backend_clickhouse_queries.py -x -q`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: all pass

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/features/backend_clickhouse.py tests/test_backend_clickhouse_queries.py
git commit -m "feat: add MVF and PnL query builders to ClickHouseBackend"
```

---

### Task 5: Smoke test — apply migrations against Docker ClickHouse

**Files:**
- None created/modified (manual verification)

**Step 1: Ensure Docker is running**

Run: `docker compose ps --format '{{.Name}} {{.Status}}' | grep -i clickhouse`
Expected: clickhouse container running

**Step 2: Apply migrations via ch_migrate**

Run: `uv run python -c "
from polymarket_pipeline.live.ch_migrate import run_ch_migrations
import clickhouse_connect
client = clickhouse_connect.get_client(host='localhost', port=18123, database='polymarket')
n = run_ch_migrations(client)
print(f'Applied {n} migrations')
"`
Expected: `Applied 2 migrations` (003 + 004, since 001 and 002 already applied)

**Step 3: Verify tables exist**

Run: `curl -s 'http://localhost:18123/?query=SHOW+TABLES+FROM+polymarket' | sort`
Expected output includes: `markets_resolved`, `trader_volumes`, `trader_trade_agg`, plus the `_mv` views

**Step 4: Verify markets_resolved view returns data**

Run: `curl -s 'http://localhost:18123/?query=SELECT+count()+FROM+polymarket.markets_resolved'`
Expected: non-zero count (should match resolved markets in PostgreSQL)

**Step 5: Verify trader_volumes has backfilled data**

Run: `curl -s 'http://localhost:18123/?query=SELECT+count()+FROM+polymarket.trader_volumes'`
Expected: non-zero (should be populated from backfill migration)

**Step 6: Verify PnL query works end-to-end**

Run: `curl -s 'http://localhost:18123/' --data "SELECT trader, condition_id, market_pnl FROM (SELECT a.trader, a.condition_id, sum(a.net_tokens * if(mr.token_won, 1.0, 0.0) + a.net_usd - a.total_fees) AS market_pnl FROM polymarket.trader_trade_agg FINAL AS a INNER JOIN polymarket.markets_resolved AS mr ON a.condition_id = mr.condition_id AND a.asset_id = mr.asset_id GROUP BY a.trader, a.condition_id) LIMIT 5 FORMAT Pretty"`
Expected: 5 rows of trader-market PnL

**Step 7: Spot-check PnL against parquet baseline**

Run: `uv run python -c "
import polars as pl
pnl = pl.read_parquet('data/derived/trader_market_pnl.parquet')
# Pick a trader with many markets for comparison
top = pnl.group_by('trader').agg(pl.col('market_pnl').sum().alias('total')).sort('total', descending=True).head(3)
print(top)
print('Use these traders to compare against ClickHouse query above')
"`
Expected: prints top 3 traders by total PnL for manual cross-reference

**No commit for this task** — it's a verification step.

---

### Task 6: Cross-validate ClickHouse PnL vs parquet PnL

**Files:**
- Create: `scripts/validate_ch_derived.py`

**Step 1: Write validation script**

```python
"""Cross-validate ClickHouse derived views against parquet baselines.

Usage:
    uv run python scripts/validate_ch_derived.py
"""

from __future__ import annotations

import asyncio

import polars as pl
import structlog

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend

logger = structlog.get_logger(__name__)


async def main() -> None:
    ch = ClickHouseBackend(host="localhost", port=18123, database="polymarket")

    # --- MVF comparison ---
    parquet_mvf = pl.read_parquet("data/derived/maker_volume_fractions.parquet")
    ch_mvf = await ch.query_mvf()

    if ch_mvf.is_empty():
        print("ERROR: ClickHouse trader_volumes is empty. Run migration 004.")
        await ch.close()
        return

    # Join on trader, compare mvf values
    compare = parquet_mvf.select("trader", pl.col("mvf").alias("parquet_mvf")).join(
        ch_mvf.select("trader", pl.col("mvf").alias("ch_mvf")),
        on="trader",
        how="inner",
    )
    compare = compare.with_columns(
        (pl.col("ch_mvf") - pl.col("parquet_mvf")).abs().alias("mvf_diff")
    )

    print(f"MVF comparison: {len(compare)} traders matched")
    print(f"  Median diff:  {compare['mvf_diff'].median():.6f}")
    print(f"  P99 diff:     {compare['mvf_diff'].quantile(0.99):.6f}")
    print(f"  Max diff:     {compare['mvf_diff'].max():.6f}")
    print()

    # --- PnL comparison (sample 100 traders) ---
    parquet_pnl = pl.read_parquet("data/derived/trader_market_pnl.parquet")
    sample_traders = (
        parquet_pnl.group_by("trader")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") > 5)
        .sample(min(100, parquet_pnl.n_unique("trader")), seed=42)["trader"]
        .to_list()
    )

    ch_pnl = await ch.query_trader_pnl(traders=sample_traders)

    if ch_pnl.is_empty():
        print("ERROR: ClickHouse PnL query returned empty.")
        await ch.close()
        return

    pnl_compare = (
        parquet_pnl.filter(pl.col("trader").is_in(sample_traders))
        .select("trader", "condition_id", pl.col("market_pnl").alias("parquet_pnl"))
        .join(
            ch_pnl.select(
                "trader", "condition_id", pl.col("market_pnl").alias("ch_pnl")
            ),
            on=["trader", "condition_id"],
            how="inner",
        )
    )
    pnl_compare = pnl_compare.with_columns(
        (pl.col("ch_pnl") - pl.col("parquet_pnl")).abs().alias("pnl_diff")
    )

    print(f"PnL comparison: {len(pnl_compare)} (trader, market) pairs matched")
    print(f"  Median diff:  ${pnl_compare['pnl_diff'].median():.4f}")
    print(f"  P99 diff:     ${pnl_compare['pnl_diff'].quantile(0.99):.4f}")
    print(f"  Max diff:     ${pnl_compare['pnl_diff'].max():.4f}")

    # Flag if diffs are unexpectedly large (>$0.01 tolerance for float rounding)
    large_diffs = pnl_compare.filter(pl.col("pnl_diff") > 0.01)
    if large_diffs.is_empty():
        print("\n  OK — all PnL values within $0.01 tolerance")
    else:
        print(f"\n  WARNING: {len(large_diffs)} pairs exceed $0.01 tolerance")
        print(large_diffs.sort("pnl_diff", descending=True).head(10))

    await ch.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Commit**

```bash
git add scripts/validate_ch_derived.py
git commit -m "feat: add ClickHouse derived views cross-validation script"
```

**Step 3: Run validation (after Task 5 migrations applied)**

Run: `uv run python scripts/validate_ch_derived.py`
Expected: MVF and PnL diffs within float tolerance ($0.01)

---
