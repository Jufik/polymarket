# Polymarket Unified Trade Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a pipeline that ingests Polymarket trade data from four sources (Goldsky Sink Parquet, Goldsky Subgraph, Market WebSocket, RTDS WebSocket), normalizes into a canonical model, deduplicates, and stores in ClickHouse for sub-second analytics.

**Architecture:** Source-specific normalizers convert raw data into a `NormalizedTrade` Pydantic model. All sources write to one ClickHouse `ReplacingMergeTree` table keyed on `trade_id` with a `_version` column (on-chain=2 overwrites off-chain=1). `transaction_hash` enables direct cross-source matching. Goldsky Sink is the bulk backfill (438M rows of Parquet files read with `fastparquet`). RTDS provides real-time trades with maker wallet.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, fastparquet, clickhouse-connect, asyncpg, websockets, structlog, Docker Compose (ClickHouse + PostgreSQL), pytest + pytest-asyncio

---

## Critical Context for the Implementer

### Empirical Findings (verified Feb 2026)

These were discovered empirically. The PRD (`explore.PRD.md`) has some wrong assumptions — trust THIS plan over the PRD when they conflict.

1. **Goldsky Parquet files use DECIMAL(100,18)** — Only `fastparquet` can read them. pyarrow fails (max precision 76). DuckDB casts to lossy DOUBLE. Do NOT attempt other readers.

2. **Amounts use 1e6 scaling** (USDC 6 decimals), NOT 1e18 as you might expect from Ethereum.

3. **Timestamps are valid Unix seconds** when read by fastparquet (e.g., `1680452705.0`).

4. **`transaction_hash` and `order_hash`** are raw bytes in Parquet → convert with `"0x" + value.hex()`.

5. **40.5% of Parquet rows are taker-focused duplicates** that must be dropped (taker is one of the Exchange contract addresses).

6. **Two complementary Parquet series** (0-* and 1-* files) with zero ID overlap — both must be processed.

7. **RTDS has `proxyWallet`** (NOT `maker_address`), `transactionHash`, `conditionId` directly. It does NOT have `maker_address`, `owner`, `taker_order_id`, or `match_time` as the PRD hypothesized.

8. **Market WS `last_trade_price` events HAVE `transaction_hash`** and `fee_rate_bps` — the PRD said tx_hash wasn't available.

9. **RTDS prices have float imprecision** (e.g., `0.3996666666666667`) — round to 2 decimal places.

10. **RTDS throughput**: ~50 trades/sec globally. PING/PONG heartbeat required.

### Key File Paths

- `explore.PRD.md` — Master PRD (some assumptions are wrong, see above)
- `explore_ws.py` — WebSocket exploration script (captures raw messages)
- `explore_ws_capture.json` — Sample WS captures for testing
- `order_filled/` — 2,033 Goldsky Parquet files (~100MB each, ~438M rows total)
- `.local/` — Prior trial code (gitignored, reference only, do NOT modify)

### Exchange Contract Addresses (for duplicate detection)

```
CTF Exchange:         0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e
NegRisk CTF Exchange: 0xc5d563a36ae78145c45a50134d48a1215220f80a
```

### Dev Commands

```bash
# Always use uv — never bare python3 or pip
uv sync                              # Install deps
uv run pytest                        # Tests
uv run pytest -v tests/test_x.py     # Single file
uv run mypy src                      # Type check (strict)
uv run ruff check src tests          # Lint
uv run ruff format src tests         # Format
```

---

## Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/polymarket_pipeline/__init__.py`
- Create: `src/polymarket_pipeline/py.typed`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create `pyproject.toml`**

```toml
[project]
name = "polymarket-pipeline"
version = "0.1.0"
description = "Polymarket unified trade data pipeline"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "structlog>=24.0",
]

[project.optional-dependencies]
sink = ["fastparquet>=2024.0", "pandas>=2.0"]
clickhouse = ["clickhouse-connect>=0.7"]
postgres = ["asyncpg>=0.29"]
websocket = ["websockets>=13.0"]
http = ["httpx>=0.27"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "mypy>=1.10",
    "ruff>=0.5",
    "polyfactory>=2.0",
]
all = [
    "polymarket-pipeline[sink,clickhouse,postgres,websocket,http,dev]",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Step 2: Create package files**

`src/polymarket_pipeline/__init__.py`:
```python
"""Polymarket unified trade data pipeline."""
```

`src/polymarket_pipeline/py.typed` — empty marker file.

`tests/__init__.py` — empty.

`tests/conftest.py`:
```python
"""Shared test fixtures."""
```

**Step 3: Install and verify**

Run: `cd /Users/kiefferjulien/git/polymarket && uv sync --all-extras`
Expected: Clean install, no errors.

Run: `uv run python -c "import polymarket_pipeline; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: project skeleton with pyproject.toml and package structure"
```

---

## Task 2: NormalizedTrade Model

**Files:**
- Create: `src/polymarket_pipeline/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

`tests/test_models.py`:
```python
"""Tests for NormalizedTrade model."""

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Source, Side


def test_normalized_trade_creation() -> None:
    trade = NormalizedTrade(
        trade_id="chain:abc123def456",
        condition_id="0x204d24f3a0f5dd5fca825292bdeab6a97af3978b2caa2b21bb37e610eddfff5d",
        asset_id="46434110155841033529384949983718980438706543876953886750286883506638610790525",
        side=Side.BUY,
        price=Decimal("0.68"),
        size=Decimal("100.50"),
        amount_usd=Decimal("68.34"),
        fee_usd=Decimal("0"),
        maker="0xa4a6fcb5df72529d4a",
        taker="0x1e057fb222bf2fdcb8",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        source=Source.GOLDSKY_SINK,
        tx_hash="0xbbcfa118b585eace1e",
        order_hash="0xdeadbeef",
        block_number=None,
        is_backfill=True,
        version=2,
    )
    assert trade.trade_id == "chain:abc123def456"
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.68")
    assert trade.version == 2


def test_normalized_trade_ws_no_addresses() -> None:
    """WebSocket trades have no maker/taker addresses."""
    trade = NormalizedTrade(
        trade_id="ws:abc123def456",
        condition_id="0x204d24f3",
        asset_id="46434110",
        side=Side.SELL,
        price=Decimal("0.32"),
        size=Decimal("786"),
        amount_usd=Decimal("251.52"),
        fee_usd=Decimal("0"),
        maker=None,
        taker=None,
        timestamp=datetime(2026, 2, 8, 8, 0, 0, tzinfo=timezone.utc),
        source=Source.WEBSOCKET,
        tx_hash="0x27837a1de096",
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
    )
    assert trade.maker is None
    assert trade.taker is None
    assert trade.version == 1


def test_normalized_trade_rtds_has_proxy_wallet() -> None:
    """RTDS trades have proxyWallet as maker."""
    trade = NormalizedTrade(
        trade_id="ws:xyz789",
        condition_id="0xaa6f622e",
        asset_id="90918587",
        side=Side.BUY,
        price=Decimal("0.36"),
        size=Decimal("164.67"),
        amount_usd=Decimal("59.28"),
        fee_usd=Decimal("0"),
        maker="0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584",
        taker=None,
        timestamp=datetime(2026, 2, 8, 3, 0, 59, tzinfo=timezone.utc),
        source=Source.RTDS,
        tx_hash="0x2d5a647433c0",
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
    )
    assert trade.maker == "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584"
    assert trade.source == Source.RTDS


def test_price_bounds_validation() -> None:
    """Price must be 0 <= price <= 1."""
    import pytest

    with pytest.raises(ValueError):
        NormalizedTrade(
            trade_id="chain:x",
            condition_id="0x1",
            asset_id="1",
            side=Side.BUY,
            price=Decimal("1.5"),
            size=Decimal("10"),
            amount_usd=Decimal("15"),
            fee_usd=Decimal("0"),
            maker=None,
            taker=None,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            source=Source.GOLDSKY_SINK,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            is_backfill=True,
            version=2,
        )


def test_size_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError):
        NormalizedTrade(
            trade_id="chain:x",
            condition_id="0x1",
            asset_id="1",
            side=Side.BUY,
            price=Decimal("0.5"),
            size=Decimal("-10"),
            amount_usd=Decimal("5"),
            fee_usd=Decimal("0"),
            maker=None,
            taker=None,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            source=Source.GOLDSKY_SINK,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            is_backfill=True,
            version=2,
        )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_pipeline.models'`

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/models.py`:
```python
"""Canonical trade model — all sources normalize into this shape."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Source(StrEnum):
    GOLDSKY_SINK = "goldsky_sink"
    GOLDSKY_SUBGRAPH = "goldsky_subgraph"
    WEBSOCKET = "websocket"
    RTDS = "rtds"


class NormalizedTrade(BaseModel):
    """Single canonical trade shape for all sources."""

    model_config = {"frozen": True}

    # Identity (dedup key for ClickHouse)
    trade_id: str

    # Market context
    condition_id: str
    asset_id: str

    # Trade data
    side: Side
    price: Decimal = Field(ge=0, le=1)
    size: Decimal = Field(gt=0)
    amount_usd: Decimal
    fee_usd: Decimal

    # Participants (nullable for WS sources)
    maker: str | None
    taker: str | None

    # Timing
    timestamp: datetime

    # Provenance
    source: Source
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None
    is_backfill: bool

    # ReplacingMergeTree version: on-chain (2) > off-chain (1)
    version: int = Field(ge=1, le=2)

    @field_validator("price")
    @classmethod
    def round_price(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/models.py tests/test_models.py
git commit -m "feat: NormalizedTrade canonical model with validation"
```

---

## Task 3: Trade ID Generation

**Files:**
- Create: `src/polymarket_pipeline/trade_id.py`
- Create: `tests/test_trade_id.py`

**Step 1: Write the failing test**

`tests/test_trade_id.py`:
```python
"""Tests for trade_id generation."""

from polymarket_pipeline.trade_id import make_trade_id_chain, make_trade_id_ws


def test_chain_trade_id_deterministic() -> None:
    """Same tx_hash + order_hash always produce same trade_id."""
    id1 = make_trade_id_chain(
        tx_hash="0xbbcfa118b585eace1e341715",
        order_hash="0xdeadbeef",
    )
    id2 = make_trade_id_chain(
        tx_hash="0xbbcfa118b585eace1e341715",
        order_hash="0xdeadbeef",
    )
    assert id1 == id2
    assert id1.startswith("chain:")
    assert len(id1) == len("chain:") + 16


def test_chain_trade_id_different_order_hash() -> None:
    """Different order_hash produces different trade_id."""
    id1 = make_trade_id_chain(tx_hash="0xabc", order_hash="0x111")
    id2 = make_trade_id_chain(tx_hash="0xabc", order_hash="0x222")
    assert id1 != id2


def test_ws_trade_id_deterministic() -> None:
    """Same composite key always produces same trade_id."""
    id1 = make_trade_id_ws(
        asset_id="46434110155841",
        timestamp_ms=1770537665076,
        price="0.32",
        size="786",
    )
    id2 = make_trade_id_ws(
        asset_id="46434110155841",
        timestamp_ms=1770537665076,
        price="0.32",
        size="786",
    )
    assert id1 == id2
    assert id1.startswith("ws:")


def test_ws_and_chain_never_collide() -> None:
    """WS and chain trade_ids can never collide due to prefix."""
    chain_id = make_trade_id_chain(tx_hash="0xabc", order_hash="0xdef")
    ws_id = make_trade_id_ws(
        asset_id="123", timestamp_ms=1000, price="0.5", size="10"
    )
    assert chain_id[:6] == "chain:"
    assert ws_id[:3] == "ws:"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trade_id.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/trade_id.py`:
```python
"""Deterministic trade_id generation for cross-source deduplication."""

from hashlib import sha256


def make_trade_id_chain(*, tx_hash: str, order_hash: str) -> str:
    """Generate trade_id for on-chain sources (Sink/Subgraph).

    Same tx_hash + order_hash from Sink and Subgraph produce identical IDs,
    enabling automatic deduplication via ClickHouse ReplacingMergeTree.
    """
    raw = f"{tx_hash}:{order_hash}"
    digest = sha256(raw.encode()).hexdigest()[:16]
    return f"chain:{digest}"


def make_trade_id_ws(
    *,
    asset_id: str,
    timestamp_ms: int,
    price: str,
    size: str,
) -> str:
    """Generate trade_id for off-chain sources (Market WS / RTDS).

    Uses composite key since WS sources don't have order_hash.
    RTDS and Market WS for the same trade produce identical IDs.
    """
    raw = f"{asset_id}:{timestamp_ms}:{price}:{size}"
    digest = sha256(raw.encode()).hexdigest()[:16]
    return f"ws:{digest}"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trade_id.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/trade_id.py tests/test_trade_id.py
git commit -m "feat: deterministic trade_id generation for chain and ws sources"
```

---

## Task 4: Goldsky Sink Normalizer

This is the most critical normalizer — processes the 373M-row Parquet backfill.

**Files:**
- Create: `src/polymarket_pipeline/normalizers/__init__.py`
- Create: `src/polymarket_pipeline/normalizers/sink.py`
- Create: `tests/test_normalizer_sink.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/sink_rows.py`

**Step 1: Create test fixtures from real Parquet data**

`tests/fixtures/__init__.py` — empty.

`tests/fixtures/sink_rows.py`:
```python
"""Real row samples from order_filled/ Parquet files (via fastparquet).

These were captured from the actual Goldsky Sink data. The bytes values
for transaction_hash and order_hash are real.
"""

# BUY trade: maker provides USDC (maker_asset_id == "0")
SINK_ROW_BUY: dict = {
    "vid": 63022,
    "block_range": "[41062881,)",
    "id": "some-unique-id-buy",
    "transaction_hash": bytes.fromhex(
        "bbcfa118b585eace1e34171583d72320c9a75d36a32e935f063d018c1ce20213"
    ),
    "timestamp": 1680452705.0,  # 2023-04-02 16:25:05 UTC
    "order_hash": bytes.fromhex(
        "aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666777788889999aabb"
    ),
    "maker": "0xa4a6fcb5df72529d4a",
    "taker": "0x1e057fb222bf2fdcb8",
    "maker_asset_id": "0",
    "taker_asset_id": "46434110155841033529384949983718980438706543876953886750286883506638610790525",
    "maker_amount_filled": 110_000_000.0,  # 110 USDC (6 decimals)
    "taker_amount_filled": 200_000_000.0,  # 200 tokens
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "996a321be875025713244d9377ada141",
}

# SELL trade: maker provides tokens (maker_asset_id != "0")
SINK_ROW_SELL: dict = {
    "vid": 202299,
    "block_range": "[47861179,)",
    "id": "some-unique-id-sell",
    "transaction_hash": bytes.fromhex(
        "7fe3e09d2c1dfeca72f62f3a780cb1352b066d94c5976a31f20a3a135915a1c1"
    ),
    "timestamp": 1695411604.0,  # 2023-09-22 19:40:04 UTC
    "order_hash": bytes.fromhex(
        "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
    ),
    "maker": "0x6cf31245801f2a053b",
    "taker": "0x08adb952cede72402b",
    "maker_asset_id": "46434110155841033529384949983718980438706543876953886750286883506638610790525",
    "taker_asset_id": "0",
    "maker_amount_filled": 117_440_000.0,  # 117.44 tokens
    "taker_amount_filled": 91_560_000.0,   # 91.56 USDC
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "bf2929897e3cfc24ed3ff9443f5de31",
}

# Taker-focused DUPLICATE: taker is CTF Exchange contract
SINK_ROW_DUP_CTF: dict = {
    "vid": 62955,
    "block_range": "[41060688,)",
    "id": "some-unique-id-dup-ctf",
    "transaction_hash": bytes.fromhex(
        "f1eb2777da76fac15875a7997d1732928d1d7b38eb557a160bd0469a1568a36e"
    ),
    "timestamp": 1680447830.0,
    "order_hash": bytes.fromhex(
        "ccccddddeeeeffffaaaabbbb000011112222333344445555666677778888cccc"
    ),
    "maker": "0xa4a6fcb5df72529d4a",
    "taker": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
    "maker_asset_id": "0",
    "taker_asset_id": "12345",
    "maker_amount_filled": 300_000_000.0,
    "taker_amount_filled": 600_000_000.0,
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "75addd6d3729fe2845af6eeb5e4a2de3",
}

# Taker-focused DUPLICATE: taker is NegRisk CTF Exchange
SINK_ROW_DUP_NEGRISK: dict = {
    "vid": 62933,
    "block_range": "[41059717,)",
    "id": "some-unique-id-dup-neg",
    "transaction_hash": bytes.fromhex(
        "d09a2fed582c55722685e81ff2ecd8019ae8e96f4a47a7f523d5c4e50cf5146b"
    ),
    "timestamp": 1680445702.0,
    "order_hash": bytes.fromhex(
        "ddddeeeeffff000011112222333344445555666677778888999900001111aaaa"
    ),
    "maker": "0x6c7eafee6f03867c0b",
    "taker": "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk
    "maker_asset_id": "0",
    "taker_asset_id": "67890",
    "maker_amount_filled": 200_000_000.0,
    "taker_amount_filled": 408_160_000.0,
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "8b8c516a640f493d627f48342dcc37ed",
}
```

**Step 2: Write the failing test**

`tests/test_normalizer_sink.py`:
```python
"""Tests for Goldsky Sink normalizer."""

from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.sink import GoldskySinkNormalizer
from tests.fixtures.sink_rows import (
    SINK_ROW_BUY,
    SINK_ROW_DUP_CTF,
    SINK_ROW_DUP_NEGRISK,
    SINK_ROW_SELL,
)

TOKEN_MAP = {
    "46434110155841033529384949983718980438706543876953886750286883506638610790525": (
        "0x204d24f3a0f5dd5fca",
        "YES",
    ),
}


def _make_normalizer() -> GoldskySinkNormalizer:
    return GoldskySinkNormalizer(token_market_map=TOKEN_MAP)


def test_buy_trade_normalization() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.55")  # 110/200
    assert trade.size == Decimal("200")     # 200_000_000 / 1e6
    assert trade.amount_usd == Decimal("110")
    assert trade.fee_usd == Decimal("0")
    assert trade.maker == "0xa4a6fcb5df72529d4a"
    assert trade.taker == "0x1e057fb222bf2fdcb8"
    assert trade.source == Source.GOLDSKY_SINK
    assert trade.is_backfill is True
    assert trade.version == 2
    assert trade.tx_hash is not None
    assert trade.tx_hash.startswith("0x")
    assert trade.order_hash is not None
    assert trade.trade_id.startswith("chain:")
    assert trade.condition_id == "0x204d24f3a0f5dd5fca"
    assert trade.asset_id == "46434110155841033529384949983718980438706543876953886750286883506638610790525"


def test_sell_trade_normalization() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_SELL)
    assert trade is not None
    assert trade.side == Side.SELL
    # price = 91.56 / 117.44 ≈ 0.7797
    assert abs(trade.price - Decimal("0.7797")) < Decimal("0.001")
    assert trade.size == Decimal("117.44")
    assert trade.amount_usd == Decimal("91.56")


def test_duplicate_ctf_exchange_dropped() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_DUP_CTF)
    assert trade is None


def test_duplicate_negrisk_exchange_dropped() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_DUP_NEGRISK)
    assert trade is None


def test_unknown_token_gets_unknown_condition_id() -> None:
    norm = GoldskySinkNormalizer(token_market_map={})
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.condition_id == "unknown"


def test_trade_id_uses_hex_hashes() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    tx = "0x" + SINK_ROW_BUY["transaction_hash"].hex()
    oh = "0x" + SINK_ROW_BUY["order_hash"].hex()
    assert trade.tx_hash == tx
    assert trade.order_hash == oh


def test_timestamp_is_utc() -> None:
    from datetime import timezone

    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.timestamp.tzinfo == timezone.utc
    assert trade.timestamp.year == 2023
    assert trade.timestamp.month == 4
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_sink.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Write minimal implementation**

`src/polymarket_pipeline/normalizers/__init__.py`:
```python
"""Source-specific normalizers."""
```

`src/polymarket_pipeline/normalizers/sink.py`:
```python
"""Goldsky Sink Parquet normalizer.

Reads rows from fastparquet DataFrames and produces NormalizedTrade instances.

IMPORTANT: Only fastparquet can read these files. pyarrow fails on DECIMAL(100,18)
and DuckDB casts to lossy DOUBLE. See docs/plans/ for empirical evidence.
"""

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain

_EXCHANGE_ADDRS: frozenset[str] = frozenset({
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
})

_USDC_SCALE = Decimal("1000000")  # 1e6


class GoldskySinkNormalizer:
    """Normalizes Goldsky Sink Parquet rows into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        """Args:
            token_market_map: asset_id → (condition_id, outcome).
        """
        self._token_map = token_market_map

    def normalize(self, raw: dict) -> NormalizedTrade | None:
        """Normalize a single Parquet row. Returns None for taker-focused duplicates."""
        # 1. Drop taker-focused duplicates
        if raw["taker"].lower() in _EXCHANGE_ADDRS:
            return None

        # 2. Determine side and extract amounts
        is_buy = str(raw["maker_asset_id"]) == "0"
        usdc_raw = raw["maker_amount_filled"] if is_buy else raw["taker_amount_filled"]
        token_raw = raw["taker_amount_filled"] if is_buy else raw["maker_amount_filled"]
        token_asset_id = str(raw["taker_asset_id"] if is_buy else raw["maker_asset_id"])

        # 3. Scale amounts (USDC uses 6 decimals)
        usdc = Decimal(str(usdc_raw)) / _USDC_SCALE
        tokens = Decimal(str(token_raw)) / _USDC_SCALE
        price = (usdc / tokens).quantize(Decimal("0.0001")) if tokens else Decimal(0)
        fee = Decimal(str(raw["fee"])) / _USDC_SCALE

        # 4. Convert byte fields to hex strings
        tx_hash = "0x" + raw["transaction_hash"].hex()
        order_hash = "0x" + raw["order_hash"].hex()

        # 5. Generate trade_id
        trade_id = make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash)

        # 6. Map token → market
        condition_id, _ = self._token_map.get(token_asset_id, ("unknown", "unknown"))

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=token_asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=tokens,
            amount_usd=usdc,
            fee_usd=fee,
            maker=raw["maker"],
            taker=raw["taker"],
            timestamp=datetime.fromtimestamp(raw["timestamp"], tz=timezone.utc),
            source=Source.GOLDSKY_SINK,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=None,
            is_backfill=True,
            version=2,
        )
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_sink.py -v`
Expected: All 8 tests PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/normalizers/ tests/test_normalizer_sink.py tests/fixtures/
git commit -m "feat: Goldsky Sink normalizer with duplicate detection"
```

---

## Task 5: RTDS Normalizer

**Files:**
- Create: `src/polymarket_pipeline/normalizers/rtds.py`
- Create: `tests/test_normalizer_rtds.py`

**Step 1: Write the failing test**

`tests/test_normalizer_rtds.py`:
```python
"""Tests for RTDS WebSocket normalizer."""

from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

RTDS_MSG: dict = {
    "connection_id": "Yc9dWdUJLPECJPg=",
    "timestamp": 1770537659939,
    "topic": "activity",
    "type": "trades",
    "payload": {
        "asset": "90918587638565982552721929191997567810368069523533497523028836373246267159037",
        "conditionId": "0xaa6f622e00c696078424494dbcd331b8435275ef97d8dde2a0f66696db53a75d",
        "side": "BUY",
        "price": 0.36,
        "size": 164.67,
        "timestamp": 1770537659,
        "transactionHash": "0x2d5a647433c051d18ca7d855737f42d51f1301090b711e774d34da1f06fd9ffb",
        "proxyWallet": "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584",
        "outcome": "Up",
        "outcomeIndex": 0,
        "name": "MtKanin",
        "pseudonym": "Forsaken-Moth",
        "bio": "",
        "profileImage": "",
        "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/BTC+fullsize.png",
        "title": "Bitcoin Up or Down",
        "eventSlug": "btc-updown-15m-1770537600",
        "slug": "btc-updown-15m-1770537600",
    },
}


def test_rtds_basic_normalization() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.36")
    assert trade.size == Decimal("164.67")
    assert trade.amount_usd == Decimal("59.28")  # 0.36 * 164.67 rounded
    assert trade.source == Source.RTDS
    assert trade.version == 1
    assert trade.is_backfill is False


def test_rtds_proxy_wallet_becomes_maker() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.maker == "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584"
    assert trade.taker is None


def test_rtds_condition_id_from_payload() -> None:
    """RTDS provides conditionId directly — no token_market_map needed."""
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.condition_id == "0xaa6f622e00c696078424494dbcd331b8435275ef97d8dde2a0f66696db53a75d"


def test_rtds_tx_hash_preserved() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.tx_hash == "0x2d5a647433c051d18ca7d855737f42d51f1301090b711e774d34da1f06fd9ffb"


def test_rtds_float_imprecision_rounded() -> None:
    """RTDS sometimes sends prices like 0.3996666666666667."""
    msg = {**RTDS_MSG, "payload": {**RTDS_MSG["payload"], "price": 0.3996666666666667}}
    norm = RTDSNormalizer()
    trade = norm.normalize(msg)
    assert trade.price == Decimal("0.40")


def test_rtds_trade_id_uses_ws_format() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.trade_id.startswith("ws:")


def test_rtds_timestamp_uses_payload_seconds() -> None:
    """Use payload.timestamp (trade time), not top-level timestamp (delivery time)."""
    from datetime import timezone

    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.timestamp.tzinfo == timezone.utc
    assert trade.timestamp.year == 2026
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_rtds.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/normalizers/rtds.py`:
```python
"""RTDS WebSocket normalizer.

RTDS provides rich trade data including proxyWallet (maker's on-chain address),
conditionId (no lookup needed), and transactionHash. Prices arrive as floats
with occasional imprecision that must be rounded.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_ws


class RTDSNormalizer:
    """Normalizes RTDS WebSocket messages into NormalizedTrade."""

    def normalize(self, msg: dict) -> NormalizedTrade:
        """Normalize a single RTDS trade message."""
        payload = msg["payload"]

        asset_id = str(payload["asset"])
        side = Side(payload["side"])

        # Round price to 2 decimal places to fix float imprecision
        price = Decimal(str(payload["price"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        size = Decimal(str(payload["size"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        amount_usd = (price * size).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Use payload.timestamp (seconds) — the actual trade time
        # Top-level msg["timestamp"] is delivery time (~500ms later)
        ts_seconds = int(payload["timestamp"])

        trade_id = make_trade_id_ws(
            asset_id=asset_id,
            timestamp_ms=ts_seconds * 1000,
            price=str(price),
            size=str(size),
        )

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=payload["conditionId"],
            asset_id=asset_id,
            side=side,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=Decimal("0"),
            maker=payload.get("proxyWallet"),
            taker=None,
            timestamp=datetime.fromtimestamp(ts_seconds, tz=timezone.utc),
            source=Source.RTDS,
            tx_hash=payload.get("transactionHash"),
            order_hash=None,
            block_number=None,
            is_backfill=False,
            version=1,
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_rtds.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/normalizers/rtds.py tests/test_normalizer_rtds.py
git commit -m "feat: RTDS normalizer with float rounding and proxyWallet mapping"
```

---

## Task 6: Market WebSocket Normalizer

**Files:**
- Create: `src/polymarket_pipeline/normalizers/market_ws.py`
- Create: `tests/test_normalizer_market_ws.py`

**Step 1: Write the failing test**

`tests/test_normalizer_market_ws.py`:
```python
"""Tests for Market WebSocket normalizer."""

from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.market_ws import MarketWSNormalizer

TOKEN_MAP = {
    "57625936606489185661652559589880983710918172021553907271126623944716577292773": (
        "0x204d24f3a0f5dd5fca",
        "NO",
    ),
}

LAST_TRADE_MSG: dict = {
    "market": "0x204d24f3a0f5dd5fca825292bdeab6a97af3978b2caa2b21bb37e610eddfff5d",
    "asset_id": "57625936606489185661652559589880983710918172021553907271126623944716577292773",
    "price": "0.32",
    "size": "786",
    "fee_rate_bps": "0",
    "side": "BUY",
    "timestamp": "1770537665076",
    "event_type": "last_trade_price",
    "transaction_hash": "0x27837a1de09654241b0483089feb1dc08729d6864ec407c7c48c689263098343",
}


def test_last_trade_price_normalization() -> None:
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.32")
    assert trade.size == Decimal("786")
    assert trade.source == Source.WEBSOCKET
    assert trade.maker is None
    assert trade.taker is None
    assert trade.version == 1
    assert trade.is_backfill is False


def test_tx_hash_preserved() -> None:
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.tx_hash == "0x27837a1de09654241b0483089feb1dc08729d6864ec407c7c48c689263098343"


def test_non_trade_events_return_none() -> None:
    """price_change and book events are not trades — skip them."""
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    assert norm.normalize({"event_type": "price_change", "price_changes": []}) is None
    assert norm.normalize({"event_type": "book", "bids": [], "asks": []}) is None


def test_timestamp_is_milliseconds() -> None:
    """Market WS timestamps are millisecond strings."""
    from datetime import timezone

    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.timestamp.tzinfo == timezone.utc
    assert trade.timestamp.year == 2026


def test_fee_rate_bps_converted_to_usd() -> None:
    msg = {**LAST_TRADE_MSG, "fee_rate_bps": "200"}  # 2%
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(msg)
    assert trade is not None
    # fee = price * size * bps / 10000 = 0.32 * 786 * 200 / 10000 = 5.03
    assert trade.fee_usd == Decimal("5.03")


def test_unknown_token_gets_unknown_condition_id() -> None:
    norm = MarketWSNormalizer(token_market_map={})
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.condition_id == "unknown"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_market_ws.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/normalizers/market_ws.py`:
```python
"""Market WebSocket normalizer.

Handles last_trade_price events from the Market WS. Other event types
(book, price_change) are skipped since they're orderbook data, not trades.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_ws


class MarketWSNormalizer:
    """Normalizes Market WebSocket trade messages into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._token_map = token_market_map

    def normalize(self, msg: dict) -> NormalizedTrade | None:
        """Normalize a Market WS message. Returns None for non-trade events."""
        if msg.get("event_type") != "last_trade_price":
            return None

        asset_id = str(msg["asset_id"])
        price = Decimal(msg["price"])
        size = Decimal(msg["size"])
        timestamp_ms = int(msg["timestamp"])

        # Fee: bps of notional
        fee_bps = int(msg.get("fee_rate_bps", "0"))
        amount_usd = (price * size).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fee_usd = (amount_usd * fee_bps / 10000).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        trade_id = make_trade_id_ws(
            asset_id=asset_id,
            timestamp_ms=timestamp_ms,
            price=str(price),
            size=str(size),
        )

        condition_id, _ = self._token_map.get(asset_id, ("unknown", "unknown"))

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side(msg["side"]),
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=None,
            taker=None,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
            source=Source.WEBSOCKET,
            tx_hash=msg.get("transaction_hash"),
            order_hash=None,
            block_number=None,
            is_backfill=False,
            version=1,
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_market_ws.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/normalizers/market_ws.py tests/test_normalizer_market_ws.py
git commit -m "feat: Market WebSocket normalizer with fee_rate_bps support"
```

---

## Task 7: Docker Compose for ClickHouse + PostgreSQL

**Files:**
- Create: `docker-compose.yml`
- Create: `docker/clickhouse/init.sql`
- Create: `docker/postgres/init.sql`

**Step 1: Create `docker-compose.yml`**

```yaml
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.8
    ports:
      - "8123:8123"   # HTTP
      - "9000:9000"   # Native
    volumes:
      - clickhouse_data:/var/lib/clickhouse
      - ./docker/clickhouse/init.sql:/docker-entrypoint-initdb.d/init.sql
    environment:
      CLICKHOUSE_DB: polymarket
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    environment:
      POSTGRES_DB: polymarket
      POSTGRES_USER: polymarket
      POSTGRES_PASSWORD: polymarket

volumes:
  clickhouse_data:
  postgres_data:
```

**Step 2: Create ClickHouse init SQL**

`docker/clickhouse/init.sql`:
```sql
CREATE TABLE IF NOT EXISTS polymarket.trades_raw (
    trade_id String,

    -- Market
    condition_id LowCardinality(String),
    asset_id String,

    -- Trade
    side Enum8('BUY' = 1, 'SELL' = 2),
    price Float32 CODEC(Gorilla, LZ4),
    size Float32,
    amount_usd Float32,
    fee_usd Float32,

    -- Participants
    maker Nullable(String),
    taker Nullable(String),

    -- Timing
    timestamp DateTime64(3) CODEC(DoubleDelta, LZ4),

    -- Provenance
    source LowCardinality(String),
    tx_hash Nullable(String),
    order_hash Nullable(String),
    block_number Nullable(UInt64),
    is_backfill Bool,

    -- ReplacingMergeTree version: on-chain (2) > off-chain (1)
    _version UInt8,

    -- Ingestion
    ingested_at DateTime64(3) DEFAULT now64()
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (condition_id, timestamp, trade_id)
SETTINGS index_granularity = 8192;

-- Bloom filters for point lookups
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_maker maker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_taker taker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_trade_id trade_id TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE polymarket.trades_raw ADD INDEX IF NOT EXISTS idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 1;

-- Deduplicated view
CREATE VIEW IF NOT EXISTS polymarket.trades AS SELECT * FROM polymarket.trades_raw FINAL;
```

**Step 3: Create PostgreSQL init SQL**

`docker/postgres/init.sql`:
```sql
-- Market registry
CREATE TABLE IF NOT EXISTS markets (
    condition_id VARCHAR(66) PRIMARY KEY,
    question TEXT,
    slug VARCHAR(200),
    category VARCHAR(100),
    token_yes VARCHAR(80),
    token_no VARCHAR(80),
    neg_risk BOOLEAN DEFAULT false,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Token → Market lookup (critical for normalizers)
CREATE TABLE IF NOT EXISTS token_market_map (
    asset_id VARCHAR(80) PRIMARY KEY,
    condition_id VARCHAR(66) NOT NULL REFERENCES markets(condition_id),
    outcome VARCHAR(10) NOT NULL  -- 'YES' or 'NO'
);

-- Backfill progress tracking
CREATE TABLE IF NOT EXISTS backfill_progress (
    file_name VARCHAR(200) PRIMARY KEY,
    rows_total INTEGER NOT NULL,
    rows_normalized INTEGER NOT NULL,
    rows_dropped INTEGER NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Pipeline health
CREATE TABLE IF NOT EXISTS pipeline_health (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL,
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    last_event_at TIMESTAMPTZ,
    events_last_hour INTEGER,
    gap_seconds INTEGER,
    status VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS idx_token_map_condition ON token_market_map(condition_id);
CREATE INDEX IF NOT EXISTS idx_health_source ON pipeline_health(source, checked_at);
```

**Step 4: Start containers and verify**

Run: `docker compose up -d`
Expected: Both containers start successfully.

Run: `docker compose exec clickhouse clickhouse-client --query "SELECT 1"`
Expected: `1`

Run: `docker compose exec postgres psql -U polymarket -c "SELECT 1"`
Expected: `1`

Run: `docker compose exec clickhouse clickhouse-client --database polymarket --query "DESCRIBE trades_raw"`
Expected: Table schema displayed.

**Step 5: Commit**

```bash
git add docker-compose.yml docker/
git commit -m "infra: Docker Compose with ClickHouse and PostgreSQL schemas"
```

---

## Task 8: ClickHouse Client + Insert Logic

**Files:**
- Create: `src/polymarket_pipeline/sinks/__init__.py`
- Create: `src/polymarket_pipeline/sinks/clickhouse.py`
- Create: `tests/test_sink_clickhouse.py`

**Step 1: Write the failing test**

`tests/test_sink_clickhouse.py`:
```python
"""Tests for ClickHouse sink.

These are integration tests that require a running ClickHouse instance.
Mark with pytest.mark.integration and skip if not available.
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink


def _make_trade(trade_id: str = "chain:test123", **overrides) -> NormalizedTrade:
    defaults = dict(
        trade_id=trade_id,
        condition_id="0xtest",
        asset_id="12345",
        side=Side.BUY,
        price=Decimal("0.55"),
        size=Decimal("100"),
        amount_usd=Decimal("55"),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        source=Source.GOLDSKY_SINK,
        tx_hash="0xtxhash",
        order_hash="0xorderhash",
        block_number=None,
        is_backfill=True,
        version=2,
    )
    defaults.update(overrides)
    return NormalizedTrade(**defaults)


@pytest.fixture
def sink():
    """Create a ClickHouse sink connected to local Docker instance."""
    try:
        s = ClickHouseSink(host="localhost", port=8123, database="polymarket")
        # Clean up test data before each test
        s.execute("DELETE FROM trades_raw WHERE condition_id = '0xtest'")
        yield s
    except Exception:
        pytest.skip("ClickHouse not available")


class TestClickHouseSink:
    def test_insert_single_trade(self, sink: ClickHouseSink) -> None:
        trade = _make_trade()
        sink.insert_trades([trade])
        result = sink.query(
            "SELECT trade_id, price, size FROM trades_raw FINAL WHERE trade_id = {id:String}",
            parameters={"id": "chain:test123"},
        )
        assert len(result) == 1
        assert result[0]["trade_id"] == "chain:test123"

    def test_insert_batch(self, sink: ClickHouseSink) -> None:
        trades = [_make_trade(trade_id=f"chain:batch{i}") for i in range(100)]
        sink.insert_trades(trades)
        result = sink.query(
            "SELECT count() as cnt FROM trades_raw WHERE condition_id = '0xtest'"
        )
        assert result[0]["cnt"] == 100

    def test_replacing_merge_tree_dedup(self, sink: ClickHouseSink) -> None:
        """Version 2 (on-chain) should overwrite version 1 (off-chain)."""
        # Insert WS version first (version=1, no maker)
        ws_trade = _make_trade(version=1, maker=None, taker=None, source=Source.WEBSOCKET)
        sink.insert_trades([ws_trade])

        # Insert on-chain version (version=2, with maker)
        chain_trade = _make_trade(version=2, maker="0xmaker", source=Source.GOLDSKY_SINK)
        sink.insert_trades([chain_trade])

        # Query with FINAL — should get version 2
        result = sink.query(
            "SELECT maker, _version FROM trades_raw FINAL WHERE trade_id = {id:String}",
            parameters={"id": "chain:test123"},
        )
        assert len(result) == 1
        assert result[0]["maker"] == "0xmaker"
        assert result[0]["_version"] == 2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sink_clickhouse.py -v`
Expected: FAIL (module not found or ClickHouse not running)

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/sinks/__init__.py`:
```python
"""Data sinks."""
```

`src/polymarket_pipeline/sinks/clickhouse.py`:
```python
"""ClickHouse sink for inserting NormalizedTrade batches."""

from typing import Any

import clickhouse_connect

from polymarket_pipeline.models import NormalizedTrade


class ClickHouseSink:
    """Inserts NormalizedTrade batches into ClickHouse trades_raw table."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8123,
        database: str = "polymarket",
    ) -> None:
        self._client = clickhouse_connect.get_client(
            host=host, port=port, database=database
        )

    def insert_trades(self, trades: list[NormalizedTrade]) -> None:
        """Insert a batch of normalized trades."""
        if not trades:
            return

        columns = [
            "trade_id", "condition_id", "asset_id", "side", "price", "size",
            "amount_usd", "fee_usd", "maker", "taker", "timestamp", "source",
            "tx_hash", "order_hash", "block_number", "is_backfill", "_version",
        ]

        rows = []
        for t in trades:
            rows.append([
                t.trade_id,
                t.condition_id,
                t.asset_id,
                t.side.value,
                float(t.price),
                float(t.size),
                float(t.amount_usd),
                float(t.fee_usd),
                t.maker,
                t.taker,
                t.timestamp,
                t.source.value,
                t.tx_hash,
                t.order_hash,
                t.block_number,
                t.is_backfill,
                t.version,
            ])

        self._client.insert("trades_raw", rows, column_names=columns)

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        """Execute a query and return results as list of dicts."""
        result = self._client.query(sql, parameters=parameters or {})
        col_names = result.column_names
        return [dict(zip(col_names, row)) for row in result.result_rows]

    def execute(self, sql: str) -> None:
        """Execute a statement (no return value)."""
        self._client.command(sql)
```

**Step 4: Ensure Docker is running, then run tests**

Run: `docker compose up -d && sleep 3 && uv run pytest tests/test_sink_clickhouse.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/sinks/ tests/test_sink_clickhouse.py
git commit -m "feat: ClickHouse sink with batch insert and ReplacingMergeTree dedup"
```

---

## Task 9: Parquet File Loader (Backfill)

**Files:**
- Create: `src/polymarket_pipeline/loaders/__init__.py`
- Create: `src/polymarket_pipeline/loaders/parquet.py`
- Create: `tests/test_loader_parquet.py`

**Step 1: Write the failing test**

`tests/test_loader_parquet.py`:
```python
"""Tests for Parquet file loader.

Integration test — requires actual Parquet files in order_filled/.
"""

import pytest
from pathlib import Path

from polymarket_pipeline.loaders.parquet import ParquetLoader

PARQUET_DIR = Path("order_filled")
SAMPLE_FILE = PARQUET_DIR / "1769363325-969c3ff6-bad8-4578-95f4-6b371bd68e36-0-0.parquet"


@pytest.fixture
def loader():
    if not SAMPLE_FILE.exists():
        pytest.skip("Parquet files not available")
    return ParquetLoader(token_market_map={})


class TestParquetLoader:
    def test_load_single_file(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        # File has ~362K rows, ~40.5% duplicates → ~215K real trades
        assert len(trades) > 200_000
        assert len(trades) < 250_000

    def test_duplicates_filtered(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        exchange_addrs = {
            "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
            "0xc5d563a36ae78145c45a50134d48a1215220f80a",
        }
        for t in trades[:1000]:
            assert t.taker.lower() not in exchange_addrs if t.taker else True

    def test_all_trades_are_valid(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        for t in trades[:1000]:
            assert 0 <= float(t.price) <= 1
            assert float(t.size) > 0
            assert t.source.value == "goldsky_sink"
            assert t.version == 2

    def test_list_files(self, loader: ParquetLoader) -> None:
        files = loader.list_files(PARQUET_DIR)
        assert len(files) > 2000

    def test_stats_returned(self, loader: ParquetLoader) -> None:
        trades, stats = loader.load_file_with_stats(SAMPLE_FILE)
        assert stats["total_rows"] > 300_000
        assert stats["dropped_duplicates"] > 100_000
        assert stats["normalized"] == len(trades)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_loader_parquet.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/loaders/__init__.py`:
```python
"""Data loaders."""
```

`src/polymarket_pipeline/loaders/parquet.py`:
```python
"""Goldsky Sink Parquet file loader.

CRITICAL: Only fastparquet can read these files. pyarrow fails on DECIMAL(100,18)
precision (max 76). DuckDB casts to lossy DOUBLE. Do NOT use other readers.
"""

import structlog
from pathlib import Path

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.normalizers.sink import GoldskySinkNormalizer

log = structlog.get_logger()


class ParquetLoader:
    """Loads and normalizes Goldsky Sink Parquet files."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._normalizer = GoldskySinkNormalizer(token_market_map=token_market_map)

    def list_files(self, directory: Path) -> list[Path]:
        """List all Parquet files in directory, sorted by name."""
        return sorted(directory.glob("*.parquet"))

    def load_file(self, path: Path) -> list[NormalizedTrade]:
        """Load and normalize a single Parquet file. Filters duplicates."""
        trades, _ = self.load_file_with_stats(path)
        return trades

    def load_file_with_stats(
        self, path: Path
    ) -> tuple[list[NormalizedTrade], dict]:
        """Load a Parquet file, returning trades and processing stats."""
        import fastparquet

        pf = fastparquet.ParquetFile(str(path))
        df = pf.to_pandas()

        total_rows = len(df)
        trades: list[NormalizedTrade] = []
        dropped = 0

        for _, row in df.iterrows():
            raw = row.to_dict()
            trade = self._normalizer.normalize(raw)
            if trade is None:
                dropped += 1
            else:
                trades.append(trade)

        stats = {
            "file": path.name,
            "total_rows": total_rows,
            "dropped_duplicates": dropped,
            "normalized": len(trades),
        }

        log.info(
            "parquet_file_loaded",
            file=path.name,
            total=total_rows,
            dropped=dropped,
            normalized=len(trades),
        )

        return trades, stats
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_loader_parquet.py -v`
Expected: All 5 tests PASS (may take ~30-60s for the full file read)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/loaders/ tests/test_loader_parquet.py
git commit -m "feat: Parquet file loader with fastparquet for Goldsky Sink backfill"
```

---

## Task 10: Token-Market Map Bootstrap (Gamma API)

**Files:**
- Create: `src/polymarket_pipeline/market_sync.py`
- Create: `tests/test_market_sync.py`

**Step 1: Write the failing test**

`tests/test_market_sync.py`:
```python
"""Tests for Gamma API market syncer."""

import pytest
from polymarket_pipeline.market_sync import fetch_token_market_map


@pytest.mark.integration
async def test_fetch_token_market_map() -> None:
    """Integration test — fetches real data from Gamma API."""
    token_map = await fetch_token_market_map(limit=50)

    # Should have at least some mappings
    assert len(token_map) > 50

    # Each entry should map to (condition_id, outcome)
    for asset_id, (condition_id, outcome) in token_map.items():
        assert isinstance(asset_id, str)
        assert len(asset_id) > 10  # Token IDs are long numbers
        assert condition_id.startswith("0x")
        assert outcome in ("YES", "NO")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_market_sync.py -v -m integration`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/market_sync.py`:
```python
"""Gamma API market syncer — builds token_market_map for normalizers."""

import httpx
import structlog

log = structlog.get_logger()

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


async def fetch_token_market_map(
    limit: int = 0,
) -> dict[str, tuple[str, str]]:
    """Fetch all markets from Gamma API and build asset_id → (condition_id, outcome) map.

    Args:
        limit: Max markets to fetch. 0 = fetch all.

    Returns:
        Dict mapping each token's asset_id to (condition_id, outcome).
        Each market contributes 2 entries (YES and NO token).
    """
    token_map: dict[str, tuple[str, str]] = {}
    offset = 0
    page_size = 100

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{GAMMA_API_BASE}/markets",
                params={"limit": page_size, "offset": offset},
            )
            resp.raise_for_status()
            markets = resp.json()

            if not markets:
                break

            for m in markets:
                cid = m.get("conditionId", "")
                tokens = m.get("clobTokenIds")
                if not cid or not tokens:
                    continue

                # clobTokenIds is a JSON-encoded list: '["token_yes", "token_no"]'
                if isinstance(tokens, str):
                    import json
                    tokens = json.loads(tokens)

                if len(tokens) >= 2:
                    token_map[tokens[0]] = (cid, "YES")
                    token_map[tokens[1]] = (cid, "NO")

            offset += page_size

            if 0 < limit <= offset:
                break

            log.debug("gamma_markets_fetched", offset=offset, map_size=len(token_map))

    log.info("token_market_map_built", total_tokens=len(token_map))
    return token_map
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_market_sync.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/market_sync.py tests/test_market_sync.py
git commit -m "feat: Gamma API market syncer for token_market_map bootstrap"
```

---

## Task 11: End-to-End Backfill Integration Test

This validates the full chain: Parquet file → normalizer → ClickHouse.

**Files:**
- Create: `tests/test_e2e_backfill.py`

**Step 1: Write the integration test**

`tests/test_e2e_backfill.py`:
```python
"""End-to-end backfill test: Parquet → normalize → ClickHouse → query."""

import pytest
from pathlib import Path

from polymarket_pipeline.loaders.parquet import ParquetLoader
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

PARQUET_DIR = Path("order_filled")
SAMPLE_FILE = PARQUET_DIR / "1769363325-969c3ff6-bad8-4578-95f4-6b371bd68e36-0-0.parquet"


@pytest.fixture
def sink():
    try:
        s = ClickHouseSink(host="localhost", port=8123, database="polymarket")
        s.execute("DELETE FROM trades_raw WHERE is_backfill = true AND source = 'goldsky_sink'")
        yield s
    except Exception:
        pytest.skip("ClickHouse not available")


@pytest.mark.integration
def test_e2e_backfill_single_file(sink: ClickHouseSink) -> None:
    """Load one Parquet file end-to-end and verify in ClickHouse."""
    if not SAMPLE_FILE.exists():
        pytest.skip("Parquet files not available")

    # Load and normalize
    loader = ParquetLoader(token_market_map={})
    trades, stats = loader.load_file_with_stats(SAMPLE_FILE)

    # Insert into ClickHouse in batches of 10,000
    batch_size = 10_000
    for i in range(0, len(trades), batch_size):
        batch = trades[i : i + batch_size]
        sink.insert_trades(batch)

    # Verify counts
    result = sink.query(
        "SELECT count() as cnt FROM trades_raw WHERE source = 'goldsky_sink'"
    )
    assert result[0]["cnt"] == len(trades)

    # Verify no duplicates made it through
    dup_result = sink.query("""
        SELECT count() as cnt FROM trades_raw
        WHERE source = 'goldsky_sink'
        AND taker IN (
            '0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
            '0xc5d563a36ae78145c45a50134d48a1215220f80a'
        )
    """)
    assert dup_result[0]["cnt"] == 0

    # Verify price bounds
    price_result = sink.query("""
        SELECT min(price) as min_p, max(price) as max_p
        FROM trades_raw WHERE source = 'goldsky_sink'
    """)
    assert price_result[0]["min_p"] >= 0
    assert price_result[0]["max_p"] <= 1

    # Verify we can query by condition_id
    market_result = sink.query("""
        SELECT condition_id, count() as cnt, sum(amount_usd) as vol
        FROM trades_raw FINAL
        WHERE source = 'goldsky_sink'
        GROUP BY condition_id
        ORDER BY cnt DESC
        LIMIT 5
    """)
    assert len(market_result) > 0

    # Print summary
    print(f"\n  Rows loaded: {len(trades):,}")
    print(f"  Duplicates dropped: {stats['dropped_duplicates']:,}")
    print(f"  Top market trades: {market_result[0]['cnt']:,}")
```

**Step 2: Run to verify**

Run: `docker compose up -d && uv run pytest tests/test_e2e_backfill.py -v -m integration -s`
Expected: PASS with summary output showing loaded rows

**Step 3: Commit**

```bash
git add tests/test_e2e_backfill.py
git commit -m "test: end-to-end backfill integration test"
```

---

## Task 12: Backfill Runner Script

**Files:**
- Create: `src/polymarket_pipeline/cli/backfill.py`

**Step 1: Create the backfill runner**

`src/polymarket_pipeline/cli/__init__.py` — empty.

`src/polymarket_pipeline/cli/backfill.py`:
```python
"""Backfill runner — loads all Goldsky Sink Parquet files into ClickHouse.

Usage:
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.parquet import ParquetLoader
from polymarket_pipeline.market_sync import fetch_token_market_map
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

log = structlog.get_logger()


async def run_backfill(parquet_dir: Path, batch_size: int = 10_000) -> None:
    """Run the full backfill pipeline."""
    # 1. Build token-market map from Gamma API
    log.info("building_token_market_map")
    token_map = await fetch_token_market_map()
    log.info("token_market_map_ready", tokens=len(token_map))

    # 2. Set up loader and sink
    loader = ParquetLoader(token_market_map=token_map)
    sink = ClickHouseSink()

    # 3. List and process files
    files = loader.list_files(parquet_dir)
    log.info("files_found", count=len(files))

    total_trades = 0
    total_dropped = 0
    start = time.monotonic()

    for i, path in enumerate(files):
        file_start = time.monotonic()
        trades, stats = loader.load_file_with_stats(path)

        # Insert in batches
        for j in range(0, len(trades), batch_size):
            batch = trades[j : j + batch_size]
            sink.insert_trades(batch)

        total_trades += len(trades)
        total_dropped += stats["dropped_duplicates"]
        elapsed = time.monotonic() - file_start

        log.info(
            "file_complete",
            file=path.name,
            progress=f"{i + 1}/{len(files)}",
            trades=len(trades),
            elapsed_s=f"{elapsed:.1f}",
            total_trades=total_trades,
        )

    total_elapsed = time.monotonic() - start
    log.info(
        "backfill_complete",
        total_trades=total_trades,
        total_dropped=total_dropped,
        total_files=len(files),
        elapsed_min=f"{total_elapsed / 60:.1f}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Goldsky Sink data")
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        default=Path("order_filled"),
        help="Directory containing Parquet files",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="ClickHouse insert batch size",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    if not args.parquet_dir.exists():
        log.error("parquet_dir_not_found", path=str(args.parquet_dir))
        sys.exit(1)

    asyncio.run(run_backfill(args.parquet_dir, args.batch_size))


if __name__ == "__main__":
    main()
```

**Step 2: Test with a single file (dry run)**

Run: `uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/ 2>&1 | head -20`
Expected: Starts processing files, shows progress logs.

Note: Full backfill (~2024 files, ~100 min) should be run manually when ready.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/cli/
git commit -m "feat: backfill runner CLI for Goldsky Sink Parquet files"
```

---

## Task 13: RTDS WebSocket Consumer

**Files:**
- Create: `src/polymarket_pipeline/consumers/__init__.py`
- Create: `src/polymarket_pipeline/consumers/rtds.py`
- Create: `tests/test_consumer_rtds.py`

**Step 1: Write the failing test**

`tests/test_consumer_rtds.py`:
```python
"""Tests for RTDS WebSocket consumer."""

import asyncio
import json
import pytest

from polymarket_pipeline.consumers.rtds import RTDSConsumer


class FakeWebSocket:
    """Fake WS that yields canned messages then closes."""

    def __init__(self, messages: list[str]) -> None:
        self._msgs = messages
        self._idx = 0

    async def recv(self) -> str:
        if self._idx >= len(self._msgs):
            raise asyncio.CancelledError
        msg = self._msgs[self._idx]
        self._idx += 1
        return msg

    async def send(self, msg: str) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


SAMPLE_TRADE_MSG = json.dumps({
    "connection_id": "test",
    "timestamp": 1770537659939,
    "topic": "activity",
    "type": "trades",
    "payload": {
        "asset": "12345",
        "conditionId": "0xtest",
        "side": "BUY",
        "price": 0.5,
        "size": 10.0,
        "timestamp": 1770537659,
        "transactionHash": "0xabc",
        "proxyWallet": "0xmaker",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "name": "test",
        "pseudonym": "test",
        "bio": "",
        "profileImage": "",
        "icon": "",
        "title": "Test",
        "eventSlug": "test",
        "slug": "test",
    },
})


async def test_consumer_processes_trade_messages() -> None:
    collected: list = []
    consumer = RTDSConsumer(on_trade=collected.append)

    ws = FakeWebSocket(["", SAMPLE_TRADE_MSG, "PING", SAMPLE_TRADE_MSG])
    try:
        await consumer.consume(ws)
    except asyncio.CancelledError:
        pass

    assert len(collected) == 2
    assert collected[0].side.value == "BUY"
    assert collected[0].price.is_finite()


async def test_consumer_responds_to_ping() -> None:
    sent: list[str] = []

    class PingWS(FakeWebSocket):
        async def send(self, msg: str) -> None:
            sent.append(msg)

    ws = PingWS(["PING"])
    consumer = RTDSConsumer(on_trade=lambda t: None)
    try:
        await consumer.consume(ws)
    except asyncio.CancelledError:
        pass

    assert "PONG" in sent
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consumer_rtds.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/polymarket_pipeline/consumers/__init__.py`:
```python
"""WebSocket consumers."""
```

`src/polymarket_pipeline/consumers/rtds.py`:
```python
"""RTDS WebSocket consumer.

Connects to wss://ws-live-data.polymarket.com, subscribes to the global
activity/trades feed, normalizes messages, and calls back with NormalizedTrade.
"""

import json
from collections.abc import Callable
from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()


class RTDSConsumer:
    """Consumes RTDS WebSocket trade messages."""

    def __init__(self, on_trade: Callable[[NormalizedTrade], Any]) -> None:
        self._on_trade = on_trade
        self._normalizer = RTDSNormalizer()

    async def consume(self, ws: Any) -> None:
        """Consume messages from an open WebSocket connection."""
        while True:
            raw = await ws.recv()

            if raw == "PING":
                await ws.send("PONG")
                continue

            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("rtds_invalid_json", raw=raw[:100])
                continue

            if not isinstance(msg, dict) or msg.get("type") != "trades":
                continue

            try:
                trade = self._normalizer.normalize(msg)
                self._on_trade(trade)
            except Exception:
                log.exception("rtds_normalize_error", msg_keys=list(msg.keys()))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_consumer_rtds.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/consumers/ tests/test_consumer_rtds.py
git commit -m "feat: RTDS WebSocket consumer with PING/PONG handling"
```

---

## Task 14: Full Test Suite, Linting, Type Checking

**Step 1: Run all tests**

Run: `uv run pytest tests/ -v --ignore=tests/test_e2e_backfill.py --ignore=tests/test_sink_clickhouse.py -k "not integration"`
Expected: All unit tests PASS

**Step 2: Run type checker**

Run: `uv run mypy src/polymarket_pipeline/`
Expected: No errors (with `--strict` from pyproject.toml)

If there are mypy errors, fix them now.

**Step 3: Run linter and formatter**

Run: `uv run ruff check src/ tests/`
Expected: No issues (or fix them)

Run: `uv run ruff format src/ tests/`
Expected: Files formatted

**Step 4: Run integration tests (requires Docker)**

Run: `docker compose up -d && uv run pytest tests/ -v -m integration -s`
Expected: All integration tests PASS

**Step 5: Commit any fixes**

```bash
git add -A
git commit -m "chore: fix all lint, type, and formatting issues"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Project skeleton + pyproject.toml | Setup only |
| 2 | NormalizedTrade model | 5 tests |
| 3 | Trade ID generation | 4 tests |
| 4 | Goldsky Sink normalizer | 8 tests |
| 5 | RTDS normalizer | 7 tests |
| 6 | Market WS normalizer | 6 tests |
| 7 | Docker Compose (CH + PG) | Infra |
| 8 | ClickHouse sink | 3 integration tests |
| 9 | Parquet file loader | 5 integration tests |
| 10 | Token-market map (Gamma API) | 1 integration test |
| 11 | E2E backfill integration test | 1 test |
| 12 | Backfill runner CLI | Manual run |
| 13 | RTDS consumer | 2 tests |
| 14 | Full suite + lint + types | Quality gate |

**Total: 42 tests across 14 tasks.**

After this plan, the pipeline will be able to:
- Load all 373M historical trades from Parquet (Goldsky Sink) into ClickHouse
- Consume live trades from RTDS WebSocket and insert in real-time
- Normalize from Market WebSocket (when integrated)
- Deduplicate across all sources via ReplacingMergeTree

**Not in scope for this plan** (future tasks):
- Goldsky Subgraph poller (cursor-based GraphQL catch-up)
- Market WS live consumer (subscription manager)
- Address enrichment merge (tx_hash matching)
- Cross-source reconciliation job
- Observability and alerting
