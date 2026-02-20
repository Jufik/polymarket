# Live Sync Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a streaming data pipeline that ingests live Polymarket trades from RTDS WebSocket + Alchemy eth_subscribe, flows through Redpanda, and fans out to ClickHouse + strategy consumers via FastStream, with a data quality gate controlling live execution.

**Architecture:** Hub-and-spoke with Redpanda as the central message bus. Three ingestors (RTDS, Alchemy, Subgraph recovery) publish `NormalizedTrade` JSON to a `trades.raw` topic. FastStream consumers (ClickHouse sink, signal evaluator, dashboard, quality checker) subscribe with independent consumer groups. See `docs/plans/2026-02-20-live-sync-architecture-design.md` for full design.

**Tech Stack:** FastStream (Kafka broker), Redpanda, ClickHouse (Kafka engine + ReplacingMergeTree), Pydantic v2, eth-abi, websockets, gql, structlog.

---

## Phase 1: Foundation (Settings, Models, Infrastructure)

### Task 1: Add Redpanda to Docker Compose

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add Redpanda service to docker-compose.yml**

Add this service block after the existing `mlflow` service:

```yaml
  redpanda:
    image: redpandadata/redpanda:v24.3.1
    command:
      - redpanda start
      - --smp 2
      - --memory 4G
      - --overprovisioned
      - --kafka-addr 0.0.0.0:19092
      - --advertise-kafka-addr 192.168.0.148:19092
      - --pandaproxy-addr 0.0.0.0:18082
      - --advertise-pandaproxy-addr 192.168.0.148:18082
    ports:
      - "19092:19092"
      - "18082:18082"
    volumes:
      - ./docker-drives/redpanda:/var/lib/redpanda/data

  redpanda-console:
    image: redpandadata/console:v2.8.0
    ports:
      - "18080:8080"
    environment:
      KAFKA_BROKERS: redpanda:19092
    depends_on:
      - redpanda
```

**Step 2: Start Redpanda and verify**

Run:
```bash
docker compose up -d redpanda redpanda-console
docker compose exec redpanda rpk topic list
```
Expected: Empty topic list, no errors.

**Step 3: Create topics**

```bash
docker compose exec redpanda rpk topic create trades.raw -p 8 -r 1 --topic-config retention.ms=604800000
docker compose exec redpanda rpk topic create pipeline.status -p 1 -r 1 --topic-config retention.ms=86400000
docker compose exec redpanda rpk topic list
```
Expected: Two topics listed with correct partition counts.

**Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "infra: add Redpanda + Console to Docker Compose"
```

---

### Task 2: Add new dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add `live` optional dependency group**

Add after the existing `exploration` group:

```toml
live = [
    "faststream[kafka]>=0.5",
    "eth-abi>=5.0",
    "websockets>=13.0",
    "gql[aiohttp]>=3.5",
    "pydantic-settings>=2.0",
]
```

**Step 2: Add CLI entry point**

In `[project.scripts]`, add:

```toml
pm-live = "polymarket_pipeline.cli.live:main"
```

**Step 3: Sync dependencies**

Run:
```bash
uv sync --all-extras
```
Expected: All packages install successfully.

**Step 4: Verify imports work**

Run:
```bash
uv run python -c "from faststream.kafka import KafkaBroker; print('FastStream OK')"
uv run python -c "from eth_abi import decode; print('eth_abi OK')"
uv run python -c "from pydantic_settings import BaseSettings; print('pydantic_settings OK')"
```
Expected: All print OK.

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add faststream, eth-abi, gql, pydantic-settings for live pipeline"
```

---

### Task 3: Add `ALCHEMY` to Source enum

**Files:**
- Modify: `src/polymarket_pipeline/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the test**

Add to `tests/test_models.py`:

```python
def test_source_alchemy_exists():
    from polymarket_pipeline.models import Source
    assert Source.ALCHEMY == "alchemy"
    assert "alchemy" in [s.value for s in Source]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_source_alchemy_exists -v`
Expected: FAIL with `AttributeError: ALCHEMY`

**Step 3: Add ALCHEMY to Source enum**

In `src/polymarket_pipeline/models.py`, add to `Source`:

```python
class Source(StrEnum):
    GOLDSKY_SINK = "goldsky_sink"
    GOLDSKY_SUBGRAPH = "goldsky_subgraph"
    WEBSOCKET = "websocket"
    RTDS = "rtds"
    ALCHEMY = "alchemy"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::test_source_alchemy_exists -v`
Expected: PASS

**Step 5: Run all model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/models.py tests/test_models.py
git commit -m "feat: add ALCHEMY source to Source enum"
```

---

### Task 4: Create Settings module

**Files:**
- Create: `src/polymarket_pipeline/live/__init__.py`
- Create: `src/polymarket_pipeline/live/settings.py`
- Test: `tests/test_live_settings.py`

**Step 1: Create package init**

Create empty `src/polymarket_pipeline/live/__init__.py`.

**Step 2: Write the failing test**

Create `tests/test_live_settings.py`:

```python
"""Tests for live pipeline settings."""

import os

import pytest


def test_settings_defaults():
    """Settings should have sensible defaults for local dev."""
    from polymarket_pipeline.live.settings import Settings

    s = Settings(alchemy_ws_url="wss://test.example.com")
    assert s.redpanda_url == "localhost:19092"
    assert s.ch_host == "192.168.0.148"
    assert s.ch_port == 18123
    assert s.quality_check_interval_s == 900
    assert s.gap_threshold_s == 600


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch):
    """Settings should read from PM_ prefixed env vars."""
    monkeypatch.setenv("PM_REDPANDA_URL", "redpanda:9092")
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://custom.alchemy.com")
    monkeypatch.setenv("PM_CH_HOST", "10.0.0.1")

    from polymarket_pipeline.live.settings import Settings

    s = Settings()
    assert s.redpanda_url == "redpanda:9092"
    assert s.alchemy_ws_url == "wss://custom.alchemy.com"
    assert s.ch_host == "10.0.0.1"


def test_settings_alchemy_url_required():
    """alchemy_ws_url has no default and must be provided."""
    from polymarket_pipeline.live.settings import Settings

    with pytest.raises(Exception):  # ValidationError
        Settings()
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_live_settings.py -v`
Expected: FAIL (module not found)

**Step 4: Implement Settings**

Create `src/polymarket_pipeline/live/settings.py`:

```python
"""Live pipeline configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the live sync pipeline.

    All values can be overridden via environment variables with PM_ prefix.
    Example: PM_REDPANDA_URL=redpanda:9092
    """

    model_config = SettingsConfigDict(env_prefix="PM_")

    # Redpanda
    redpanda_url: str = "localhost:19092"

    # Alchemy (required, no default — contains API key)
    alchemy_ws_url: str

    # Goldsky Subgraph (recovery)
    subgraph_url: str = (
        "https://api.goldsky.com/api/public/"
        "project_cl6mb8i9h0003e201j6li0diw/"
        "subgraphs/orderbook-subgraph/0.0.1/gn"
    )

    # ClickHouse
    ch_host: str = "192.168.0.148"
    ch_port: int = 18123
    ch_database: str = "polymarket"

    # PostgreSQL
    pg_dsn: str = "postgresql://polymarket:polymarket@192.168.0.148:15432/polymarket"

    # Quality thresholds
    quality_check_interval_s: int = 900
    source_liveness_timeout_s: int = 30
    volume_drop_warn_pct: float = 0.50
    volume_drop_red_pct: float = 0.10
    enrichment_ratio_min: float = 0.80

    # Recovery
    gap_threshold_s: int = 600

    # Batching (ClickHouse consumer)
    ch_batch_size: int = 100
    ch_flush_interval_s: float = 1.0
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_live_settings.py -v`
Expected: All 3 pass.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/__init__.py src/polymarket_pipeline/live/settings.py tests/test_live_settings.py
git commit -m "feat(live): add Settings module with env-based configuration"
```

---

## Phase 2: New Normalizers

### Task 5: Extract shared constants to a common module

**Files:**
- Create: `src/polymarket_pipeline/constants.py`
- Modify: `src/polymarket_pipeline/normalizers/sink.py` (import from constants)
- Test: existing tests still pass

The exchange addresses and USDC scale are needed by both `sink.py` and the new `polygon_rpc.py` normalizer. Extract them.

**Step 1: Create constants module**

Create `src/polymarket_pipeline/constants.py`:

```python
"""Shared constants for the Polymarket pipeline."""

from decimal import Decimal

# CTF Exchange contract addresses (used to detect taker-focused duplicates)
EXCHANGE_ADDRS: frozenset[str] = frozenset(
    {
        "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
        "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
    }
)

# USDC uses 6 decimals (1e6), NOT 1e18
USDC_SCALE = Decimal("1000000")

# OrderFilled event signature (keccak256)
# OrderFilled(bytes32 orderHash, address maker, address taker, uint256 makerAssetId,
#             uint256 takerAssetId, uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)
ORDER_FILLED_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0a08e8c493f9c94f29311"
```

Note: The ORDER_FILLED_TOPIC will be computed exactly in Task 6. Use a placeholder here.

**Step 2: Update sink.py to import from constants**

In `src/polymarket_pipeline/normalizers/sink.py`, replace local definitions:

```python
from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
```

Remove the local `EXCHANGE_ADDRS` and `_USDC_SCALE` definitions.

**Step 3: Run existing tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass (no behavioral change).

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/constants.py src/polymarket_pipeline/normalizers/sink.py
git commit -m "refactor: extract EXCHANGE_ADDRS and USDC_SCALE to shared constants"
```

---

### Task 6: PolygonRPCNormalizer — ABI decode OrderFilled events

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/__init__.py`
- Create: `src/polymarket_pipeline/live/normalizers/polygon_rpc.py`
- Test: `tests/test_normalizer_polygon_rpc.py`

**Step 1: Compute the correct OrderFilled event topic**

Run:
```bash
uv run python -c "
from eth_abi import encode
from hashlib import sha256
import hashlib
# keccak256 of OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)
from eth_abi.tools import abi_to_selector
# Actually use web3 or manual keccak
import struct
sig = 'OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)'
k = hashlib.sha3_256(sig.encode()).hexdigest()
print(f'keccak256: 0x{k}')
"
```

Note: `hashlib.sha3_256` is NOT keccak256. Use `eth_abi` or compute manually. The implementing agent should verify this by searching for the known topic hash online or from the CTF Exchange ABI. The correct topic can also be derived from the Goldsky subgraph data by checking existing `topics[0]` values.

**Step 2: Write the failing test**

Create `tests/test_normalizer_polygon_rpc.py`:

```python
"""Tests for PolygonRPCNormalizer — ABI decoding of raw Polygon log events."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

    return PolygonRPCNormalizer()


def _make_log(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = 12345,
    taker_asset_id: int = 0,
    maker_amount: int = 500_000_000,  # 500 USDC (1e6 scale)
    taker_amount: int = 1000_000_000,  # 1000 tokens
    fee: int = 5_000_000,  # 5 USDC
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
    timestamp: int = 1706800000,
) -> dict:
    """Build a mock raw Polygon log event for OrderFilled."""
    from eth_abi import encode

    # Encode non-indexed params: makerAssetId, takerAssetId, makerAmountFilled,
    # takerAmountFilled, fee
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )

    # topics[0] = event sig, topics[1] = orderHash (indexed bytes32),
    # topics[2] = maker (indexed address), topics[3] = taker (indexed address)
    return {
        "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        "topics": [
            "0x",  # placeholder — normalizer shouldn't validate topic[0]
            order_hash,
            "0x" + "00" * 12 + maker[2:],  # address padded to 32 bytes
            "0x" + "00" * 12 + taker[2:],  # address padded to 32 bytes
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "transactionIndex": "0x1",
        "logIndex": "0x0",
        # We inject timestamp from block data (not part of raw log)
        "_timestamp": timestamp,
    }


class TestPolygonRPCNormalizer:
    def test_basic_buy_trade(self, normalizer):
        """BUY: taker pays USDC (takerAssetId=0), maker provides tokens."""
        log = _make_log(
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount=1000_000_000,  # 1000 tokens
            taker_amount=500_000_000,  # 500 USDC
            fee=5_000_000,  # 5 USDC
        )
        trade = normalizer.normalize(log)
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.price == Decimal("0.5000")  # 500/1000
        assert trade.size == Decimal("1000")
        assert trade.amount_usd == Decimal("500.00")
        assert trade.fee_usd == Decimal("5.00")
        assert trade.source == Source.ALCHEMY
        assert trade.version == 2
        assert trade.maker is not None
        assert trade.taker is not None
        assert trade.tx_hash is not None
        assert trade.trade_id.startswith("chain:")

    def test_sell_trade(self, normalizer):
        """SELL: taker provides tokens (takerAssetId!=0), maker pays USDC."""
        log = _make_log(
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount=300_000_000,  # 300 USDC
            taker_amount=500_000_000,  # 500 tokens
            fee=3_000_000,
        )
        trade = normalizer.normalize(log)
        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.price == Decimal("0.6000")  # 300/500
        assert trade.size == Decimal("500")
        assert trade.amount_usd == Decimal("300.00")

    def test_taker_duplicate_dropped(self, normalizer):
        """Taker-perspective events (taker == exchange contract) return None."""
        log = _make_log(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        trade = normalizer.normalize(log)
        assert trade is None

    def test_negrisk_exchange_taker_dropped(self, normalizer):
        """NegRisk exchange taker also dropped."""
        log = _make_log(
            taker="0xc5d563a36ae78145c45a50134d48a1215220f80a",
        )
        trade = normalizer.normalize(log)
        assert trade is None

    def test_asset_id_extracted(self, normalizer):
        """Non-USDC asset_id becomes the trade's asset_id."""
        log = _make_log(maker_asset_id=99999, taker_asset_id=0)
        trade = normalizer.normalize(log)
        assert trade.asset_id == "99999"

    def test_condition_id_empty_without_map(self, normalizer):
        """Without a token_map, condition_id defaults to empty string."""
        log = _make_log()
        trade = normalizer.normalize(log)
        # Without token_map, condition_id should be the asset_id (best effort)
        assert trade.condition_id != ""

    def test_with_token_map(self):
        """With token_map, asset_id is resolved to condition_id."""
        from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

        token_map = {"12345": ("cond_abc", "YES")}
        n = PolygonRPCNormalizer(token_market_map=token_map)
        log = _make_log(maker_asset_id=12345, taker_asset_id=0)
        trade = n.normalize(log)
        assert trade.condition_id == "cond_abc"
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_polygon_rpc.py -v`
Expected: FAIL (import error)

**Step 4: Implement PolygonRPCNormalizer**

Create `src/polymarket_pipeline/live/normalizers/__init__.py` (empty).

Create `src/polymarket_pipeline/live/normalizers/polygon_rpc.py`:

```python
"""Normalizer for raw Polygon RPC log events (eth_subscribe)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from eth_abi import decode

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain


class PolygonRPCNormalizer:
    """Normalizes raw Polygon log events for OrderFilled into NormalizedTrade."""

    def __init__(
        self,
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._token_map = token_market_map or {}

    def normalize(self, log: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single raw log event.

        Args:
            log: Raw Polygon log dict with keys: address, topics, data,
                 blockNumber, transactionHash. Must also have _timestamp
                 (Unix seconds, injected by the ingestor from block data).

        Returns:
            NormalizedTrade or None if this is a taker-perspective duplicate.
        """
        topics = log["topics"]
        raw_data = bytes.fromhex(log["data"][2:])

        # Decode indexed params from topics
        order_hash = topics[1]
        maker = "0x" + topics[2][-40:]
        taker = "0x" + topics[3][-40:]

        # Drop taker-perspective duplicates
        if taker.lower() in EXCHANGE_ADDRS:
            return None

        # Decode non-indexed params
        maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = decode(
            ["uint256", "uint256", "uint256", "uint256", "uint256"],
            raw_data,
        )

        # Determine side: BUY if taker pays USDC (taker_asset_id == 0)
        is_buy = taker_asset_id == 0
        if is_buy:
            asset_id = str(maker_asset_id)
            usdc_raw = taker_amount
            token_amount = maker_amount
        else:
            asset_id = str(taker_asset_id)
            usdc_raw = maker_amount
            token_amount = taker_amount

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        fee_usd = Decimal(fee) / USDC_SCALE
        size = Decimal(token_amount) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        # Resolve condition_id
        if asset_id in self._token_map:
            condition_id = self._token_map[asset_id][0]
        else:
            condition_id = asset_id  # best effort fallback

        tx_hash = log["transactionHash"]
        block_number = int(log["blockNumber"], 16)
        timestamp = datetime.fromtimestamp(log["_timestamp"], tz=UTC)

        return NormalizedTrade(
            trade_id=make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash),
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=maker,
            taker=taker,
            timestamp=timestamp,
            source=Source.ALCHEMY,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=block_number,
            is_backfill=False,
            version=2,
        )
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_normalizer_polygon_rpc.py -v`
Expected: All pass.

**Step 6: Run all tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/ tests/test_normalizer_polygon_rpc.py
git commit -m "feat(live): add PolygonRPCNormalizer for Alchemy eth_subscribe events"
```

---

### Task 7: SubgraphNormalizer — GraphQL JSON to NormalizedTrade

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/subgraph.py`
- Test: `tests/test_normalizer_subgraph.py`

**Step 1: Write the failing test**

Create `tests/test_normalizer_subgraph.py`:

```python
"""Tests for SubgraphNormalizer — Goldsky GraphQL orderFilledEvents."""

from decimal import Decimal

import pytest

from polymarket_pipeline.models import Side, Source


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer

    token_map = {"12345": ("cond_abc", "YES")}
    return SubgraphNormalizer(token_market_map=token_map)


def _make_event(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    maker_asset_id: str = "0",
    taker_asset_id: str = "12345",
    maker_amount: str = "500000000",  # 500 USDC
    taker_amount: str = "1000000000",  # 1000 tokens
    fee: str = "5000000",
    timestamp: str = "1706800000",
    transaction_hash: str = "0x" + "dd" * 32,
    order_hash: str = "0x" + "cc" * 32,
    event_id: str = "evt_001",
) -> dict:
    """Build a mock Goldsky subgraph orderFilledEvent."""
    return {
        "id": event_id,
        "maker": maker,
        "taker": taker,
        "makerAssetId": maker_asset_id,
        "takerAssetId": taker_asset_id,
        "makerAmountFilled": maker_amount,
        "takerAmountFilled": taker_amount,
        "fee": fee,
        "timestamp": timestamp,
        "transactionHash": transaction_hash,
        "orderHash": order_hash,
    }


class TestSubgraphNormalizer:
    def test_buy_trade(self, normalizer):
        """BUY: maker provides USDC (makerAssetId=0)."""
        event = _make_event(
            maker_asset_id="0",
            taker_asset_id="12345",
            maker_amount="500000000",
            taker_amount="1000000000",
        )
        trade = normalizer.normalize(event)
        assert trade is not None
        assert trade.side == Side.SELL  # maker sells USDC = taker buys tokens
        assert trade.asset_id == "12345"
        assert trade.condition_id == "cond_abc"
        assert trade.source == Source.GOLDSKY_SUBGRAPH
        assert trade.version == 2
        assert trade.trade_id.startswith("chain:")

    def test_taker_duplicate_dropped(self, normalizer):
        """Taker == exchange contract → None."""
        event = _make_event(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        assert normalizer.normalize(event) is None

    def test_trade_id_matches_sink(self, normalizer):
        """Subgraph trade_id should match GoldskySinkNormalizer for same tx."""
        from polymarket_pipeline.trade_id import make_trade_id_chain

        tx = "0x" + "dd" * 32
        oh = "0x" + "cc" * 32
        event = _make_event(transaction_hash=tx, order_hash=oh)
        trade = normalizer.normalize(event)
        expected_id = make_trade_id_chain(tx_hash=tx, order_hash=oh)
        assert trade.trade_id == expected_id
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_subgraph.py -v`
Expected: FAIL (import error)

**Step 3: Implement SubgraphNormalizer**

Create `src/polymarket_pipeline/live/normalizers/subgraph.py`:

```python
"""Normalizer for Goldsky Subgraph orderFilledEvent responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain


class SubgraphNormalizer:
    """Normalizes Goldsky Subgraph orderFilledEvent JSON into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._token_map = token_market_map

    def normalize(self, event: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single subgraph orderFilledEvent.

        Args:
            event: Dict with keys: maker, taker, makerAssetId, takerAssetId,
                   makerAmountFilled, takerAmountFilled, fee, timestamp,
                   transactionHash, orderHash.

        Returns:
            NormalizedTrade or None for taker-perspective duplicates.
        """
        taker = event["taker"]
        if taker.lower() in EXCHANGE_ADDRS:
            return None

        maker_asset_id = event["makerAssetId"]
        taker_asset_id = event["takerAssetId"]

        # Determine side: if maker provides USDC (asset_id=0), it's a SELL
        is_buy = maker_asset_id == "0"
        if is_buy:
            asset_id = taker_asset_id
            usdc_raw = int(event["makerAmountFilled"])
            token_amount = int(event["takerAmountFilled"])
        else:
            asset_id = maker_asset_id
            usdc_raw = int(event["takerAmountFilled"])
            token_amount = int(event["makerAmountFilled"])

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        fee_usd = Decimal(int(event["fee"])) / USDC_SCALE
        size = Decimal(token_amount) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        condition_id, _ = self._token_map.get(asset_id, (asset_id, ""))

        tx_hash = event["transactionHash"]
        order_hash = event.get("orderHash", "")

        return NormalizedTrade(
            trade_id=make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash),
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=event["maker"],
            taker=taker,
            timestamp=datetime.fromtimestamp(int(event["timestamp"]), tz=UTC),
            source=Source.GOLDSKY_SUBGRAPH,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=None,
            is_backfill=False,
            version=2,
        )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_normalizer_subgraph.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/subgraph.py tests/test_normalizer_subgraph.py
git commit -m "feat(live): add SubgraphNormalizer for Goldsky GraphQL recovery"
```

---

## Phase 3: FastStream App Scaffold

### Task 8: FastStream app skeleton with lifespan

**Files:**
- Create: `src/polymarket_pipeline/live/app.py`
- Test: `tests/test_live_app.py`

**Step 1: Write the failing test**

Create `tests/test_live_app.py`:

```python
"""Tests for FastStream app scaffold."""

import pytest


def test_app_importable():
    """App module should be importable without side effects."""
    from polymarket_pipeline.live.app import app, broker

    assert app is not None
    assert broker is not None


def test_broker_has_correct_url(monkeypatch: pytest.MonkeyPatch):
    """Broker should use the configured Redpanda URL."""
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")
    monkeypatch.setenv("PM_REDPANDA_URL", "localhost:19092")

    # Force reimport to pick up env
    import importlib

    import polymarket_pipeline.live.app as app_mod

    importlib.reload(app_mod)
    assert app_mod.broker is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_live_app.py -v`
Expected: FAIL

**Step 3: Implement app.py**

Create `src/polymarket_pipeline/live/app.py`:

```python
"""FastStream application for the live sync pipeline."""

from __future__ import annotations

import structlog
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

# Settings loaded at import time — overridable via PM_ env vars
settings = Settings()

broker = KafkaBroker(settings.redpanda_url)
app = FastStream(broker)


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize shared resources on startup."""
    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)


@app.on_shutdown
async def on_shutdown() -> None:
    """Cleanup on shutdown."""
    log.info("live_pipeline.stopping")
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_live_app.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/app.py tests/test_live_app.py
git commit -m "feat(live): FastStream app scaffold with lifespan hooks"
```

---

### Task 9: CLI entry point

**Files:**
- Create: `src/polymarket_pipeline/cli/live.py`

**Step 1: Create the CLI wrapper**

Create `src/polymarket_pipeline/cli/live.py`:

```python
"""CLI entry point for the live sync pipeline."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the FastStream live pipeline.

    Equivalent to: faststream run polymarket_pipeline.live.app:app
    """
    from faststream.cli.main import cli

    sys.argv = ["faststream", "run", "polymarket_pipeline.live.app:app"]
    cli()


if __name__ == "__main__":
    main()
```

**Step 2: Verify it starts (requires Redpanda running)**

Run:
```bash
uv run python -m polymarket_pipeline.cli.live
```
Expected: Should attempt to connect to Redpanda. If Redpanda is running, it starts. If not, connection error — that's fine for now.

Press Ctrl+C to stop.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/cli/live.py
git commit -m "feat(live): add CLI entry point for live pipeline"
```

---

## Phase 4: Ingestors (Producers)

### Task 10: RTDS Ingestor — wraps existing consumer, publishes to Redpanda

**Files:**
- Create: `src/polymarket_pipeline/live/ingestors/__init__.py`
- Create: `src/polymarket_pipeline/live/ingestors/rtds.py`
- Test: `tests/test_ingestor_rtds.py`

**Step 1: Write the failing test**

Create `tests/test_ingestor_rtds.py`:

```python
"""Tests for RTDS ingestor — WS connection management + Redpanda publish."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_pipeline.models import Source


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestRTDSIngestor:
    async def test_trade_published_to_redpanda(self, mock_broker):
        """Normalized trades should be published as JSON to trades.raw."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")

        # Simulate a single trade message
        trade_msg = {
            "type": "trades",
            "payload": {
                "asset": "12345",
                "side": "BUY",
                "price": 0.72,
                "size": 100.0,
                "timestamp": 1706800000,
                "conditionId": "cond_abc",
                "proxyWallet": "0xmaker",
                "transactionHash": "0xtx",
            },
            "timestamp": 1706800001,
        }

        await ingestor._handle_message(json.dumps(trade_msg))

        # Should have published one trade
        assert mock_broker.publish.call_count == 1
        call_args = mock_broker.publish.call_args
        # Verify the published message is valid JSON containing expected fields
        published = call_args.kwargs.get("message") or call_args.args[0]
        assert "cond_abc" in str(published)

    async def test_ping_pong_not_published(self, mock_broker):
        """PING/PONG messages should not produce trades."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")
        await ingestor._handle_message("PING")
        assert mock_broker.publish.call_count == 0

    async def test_non_trade_type_not_published(self, mock_broker):
        """Messages with type != 'trades' should be ignored."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")
        await ingestor._handle_message(json.dumps({"type": "other", "data": {}}))
        assert mock_broker.publish.call_count == 0

    async def test_heartbeat_published_to_status(self, mock_broker):
        """Ingestor should publish heartbeat to pipeline.status."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(
            broker=mock_broker,
            topic="trades.raw",
            status_topic="pipeline.status",
        )
        await ingestor._publish_heartbeat()
        # Should publish to pipeline.status
        assert mock_broker.publish.call_count == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ingestor_rtds.py -v`
Expected: FAIL

**Step 3: Implement RTDSIngestor**

Create `src/polymarket_pipeline/live/ingestors/__init__.py` (empty).

Create `src/polymarket_pipeline/live/ingestors/rtds.py`:

```python
"""RTDS WebSocket ingestor — connects, normalizes, publishes to Redpanda."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class RTDSIngestor:
    """Manages RTDS WebSocket lifecycle and publishes trades to Redpanda."""

    def __init__(
        self,
        broker: Any,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = RTDSNormalizer()
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WebSocket message."""
        if raw in ("PING", "PONG", "pong"):
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("rtds.invalid_json", raw=raw[:100])
            return

        if msg.get("type") != "trades":
            return

        try:
            trade = self._normalizer.normalize(msg)
        except Exception:
            log.exception("rtds.normalize_error")
            return

        trade_json = trade.model_dump_json()
        await self._broker.publish(
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
        )
        self._last_trade_ts = time.time()
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status topic."""
        heartbeat = json.dumps({
            "source": "rtds",
            "event": "heartbeat",
            "last_trade_ts": self._last_trade_ts,
            "trade_count": self._trade_count,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"rtds",
        )

    async def _ping_loop(self, ws: Any) -> None:
        """Send PING every 5s to keep RTDS connection alive."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send("PING")

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the RTDS ingestor with auto-reconnect."""
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("rtds.connecting", url=RTDS_URL)
                async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                    backoff = RECONNECT_BASE
                    log.info("rtds.connected")

                    subscribe = json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "activity", "type": "trades"}],
                    })
                    await ws.send(subscribe)

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        ping_task.cancel()
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning("rtds.disconnected", reason=str(e), backoff=backoff)
            except Exception:
                log.exception("rtds.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_ingestor_rtds.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/ tests/test_ingestor_rtds.py
git commit -m "feat(live): add RTDS ingestor with reconnect and Redpanda publish"
```

---

### Task 11: Alchemy Ingestor — eth_subscribe + ABI decode + publish

**Files:**
- Create: `src/polymarket_pipeline/live/ingestors/alchemy.py`
- Test: `tests/test_ingestor_alchemy.py`

**Step 1: Write the failing test**

Create `tests/test_ingestor_alchemy.py`:

```python
"""Tests for Alchemy eth_subscribe ingestor."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from eth_abi import encode


def _make_subscription_result(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = 12345,
    taker_asset_id: int = 0,
    maker_amount: int = 500_000_000,
    taker_amount: int = 1000_000_000,
    fee: int = 5_000_000,
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
) -> dict:
    """Build a mock eth_subscribe log notification."""
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )
    return {
        "jsonrpc": "2.0",
        "method": "eth_subscription",
        "params": {
            "subscription": "0xabc123",
            "result": {
                "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
                "topics": [
                    "0x",  # event sig
                    order_hash,
                    "0x" + "00" * 12 + maker[2:],
                    "0x" + "00" * 12 + taker[2:],
                ],
                "data": "0x" + data.hex(),
                "blockNumber": hex(block_number),
                "transactionHash": tx_hash,
                "transactionIndex": "0x1",
                "logIndex": "0x0",
            },
        },
    }


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestAlchemyIngestor:
    async def test_log_event_published(self, mock_broker):
        """Valid OrderFilled log should be published to trades.raw."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        msg = _make_subscription_result()
        await ingestor._handle_message(json.dumps(msg))

        assert mock_broker.publish.call_count == 1

    async def test_taker_duplicate_not_published(self, mock_broker):
        """Taker-perspective events should be dropped."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        msg = _make_subscription_result(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        await ingestor._handle_message(json.dumps(msg))

        assert mock_broker.publish.call_count == 0

    async def test_subscription_response_ignored(self, mock_broker):
        """Subscription confirmation (has 'result', no 'method') should be ignored."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        confirm = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0xabc123"})
        await ingestor._handle_message(confirm)

        assert mock_broker.publish.call_count == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ingestor_alchemy.py -v`
Expected: FAIL

**Step 3: Implement AlchemyIngestor**

Create `src/polymarket_pipeline/live/ingestors/alchemy.py`:

```python
"""Alchemy eth_subscribe ingestor — Polygon RPC logs for OrderFilled events."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.constants import EXCHANGE_ADDRS
from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

log = structlog.get_logger()

# Both CTF Exchange contracts
CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class AlchemyIngestor:
    """Subscribes to Polygon OrderFilled logs via Alchemy WebSocket."""

    def __init__(
        self,
        broker: Any,
        ws_url: str,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._broker = broker
        self._ws_url = ws_url
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = PolygonRPCNormalizer(token_market_map=token_market_map)
        self._last_block: int = 0
        self._trade_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WebSocket message from Alchemy."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("alchemy.invalid_json", raw=raw[:100])
            return

        # Skip subscription confirmations
        if "method" not in msg:
            return

        if msg.get("method") != "eth_subscription":
            return

        result = msg.get("params", {}).get("result")
        if not result:
            return

        # Inject timestamp — in production, fetch block timestamp via eth_getBlockByNumber.
        # For MVP, use current time (block timestamps are ~2s delayed anyway).
        result["_timestamp"] = int(time.time())

        trade = self._normalizer.normalize(result)
        if trade is None:
            return  # taker duplicate dropped

        trade_json = trade.model_dump_json()
        await self._broker.publish(
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
        )

        self._last_block = trade.block_number or self._last_block
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps({
            "source": "alchemy",
            "event": "heartbeat",
            "last_block": self._last_block,
            "trade_count": self._trade_count,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"alchemy",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the Alchemy ingestor with auto-reconnect."""
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("alchemy.connecting")
                async with websockets.connect(self._ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    log.info("alchemy.connected")

                    subscribe = json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": [
                            "logs",
                            {"address": [CTF_EXCHANGE, NEGRISK_EXCHANGE]},
                        ],
                    })
                    await ws.send(subscribe)

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning("alchemy.disconnected", reason=str(e), backoff=backoff)
            except Exception:
                log.exception("alchemy.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_ingestor_alchemy.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/alchemy.py tests/test_ingestor_alchemy.py
git commit -m "feat(live): add Alchemy eth_subscribe ingestor for Polygon OrderFilled"
```

---

### Task 12: Subgraph Recovery Poller

**Files:**
- Create: `src/polymarket_pipeline/live/ingestors/subgraph.py`
- Test: `tests/test_ingestor_subgraph.py`

**Step 1: Write the failing test**

Create `tests/test_ingestor_subgraph.py`:

```python
"""Tests for Goldsky Subgraph recovery poller."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestSubgraphPoller:
    async def test_single_batch_published(self, mock_broker):
        """A batch of events should produce published trades."""
        from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

        events = [
            {
                "id": "evt_1",
                "maker": "0x" + "a1" * 20,
                "taker": "0x" + "b2" * 20,
                "makerAssetId": "0",
                "takerAssetId": "12345",
                "makerAmountFilled": "500000000",
                "takerAmountFilled": "1000000000",
                "fee": "5000000",
                "timestamp": "1706800000",
                "transactionHash": "0x" + "dd" * 32,
                "orderHash": "0x" + "cc" * 32,
            }
        ]

        token_map = {"12345": ("cond_abc", "YES")}
        poller = SubgraphPoller(
            broker=mock_broker,
            subgraph_url="http://test.example.com/graphql",
            token_market_map=token_map,
            topic="trades.raw",
        )

        count = await poller._process_batch(events)
        assert count == 1
        assert mock_broker.publish.call_count == 1

    async def test_taker_duplicates_skipped(self, mock_broker):
        """Exchange contract takers should be skipped."""
        from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

        events = [
            {
                "id": "evt_1",
                "maker": "0x" + "a1" * 20,
                "taker": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
                "makerAssetId": "0",
                "takerAssetId": "12345",
                "makerAmountFilled": "500000000",
                "takerAmountFilled": "1000000000",
                "fee": "0",
                "timestamp": "1706800000",
                "transactionHash": "0x" + "dd" * 32,
                "orderHash": "0x" + "cc" * 32,
            }
        ]

        poller = SubgraphPoller(
            broker=mock_broker,
            subgraph_url="http://test.example.com/graphql",
            token_market_map={},
            topic="trades.raw",
        )
        count = await poller._process_batch(events)
        assert count == 0
        assert mock_broker.publish.call_count == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ingestor_subgraph.py -v`
Expected: FAIL

**Step 3: Implement SubgraphPoller**

Create `src/polymarket_pipeline/live/ingestors/subgraph.py`:

```python
"""Goldsky Subgraph recovery poller — cursor-based catch-up for gap filling."""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport

from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer

log = structlog.get_logger()

BATCH_SIZE = 1000

QUERY_TEMPLATE = gql("""
query FetchOrders($timestamp_gt: String!, $first: Int!) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp_gt: $timestamp_gt }
    ) {
        id
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
        timestamp
        transactionHash
        orderHash
    }
}
""")

QUERY_STICKY = gql("""
query FetchOrdersSticky($timestamp: String!, $id_gt: String!, $first: Int!) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp: $timestamp, id_gt: $id_gt }
    ) {
        id
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
        timestamp
        transactionHash
        orderHash
    }
}
""")


class SubgraphPoller:
    """Polls Goldsky Subgraph to recover missed trades after an outage."""

    def __init__(
        self,
        broker: Any,
        subgraph_url: str,
        token_market_map: dict[str, tuple[str, str]],
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
    ) -> None:
        self._broker = broker
        self._subgraph_url = subgraph_url
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = SubgraphNormalizer(token_market_map=token_market_map)

    async def _process_batch(self, events: list[dict[str, Any]]) -> int:
        """Normalize and publish a batch of subgraph events. Returns count published."""
        published = 0
        for event in events:
            trade = self._normalizer.normalize(event)
            if trade is None:
                continue
            await self._broker.publish(
                message=trade.model_dump_json(),
                topic=self._topic,
                key=trade.condition_id.encode(),
            )
            published += 1
        return published

    async def recover(self, from_timestamp: int) -> int:
        """Run recovery from a given Unix timestamp until caught up.

        Args:
            from_timestamp: Unix seconds to start recovery from.

        Returns:
            Total number of trades published.
        """
        transport = AIOHTTPTransport(url=self._subgraph_url)
        async with Client(transport=transport, fetch_schema_from_transport=False) as client:
            total = 0
            cursor_ts = str(from_timestamp)
            cursor_id = ""

            while True:
                # Fetch a batch
                if cursor_id:
                    # Sticky mode: same timestamp, advance by id
                    result = await client.execute_async(
                        QUERY_STICKY,
                        variable_values={
                            "timestamp": cursor_ts,
                            "id_gt": cursor_id,
                            "first": BATCH_SIZE,
                        },
                    )
                else:
                    result = await client.execute_async(
                        QUERY_TEMPLATE,
                        variable_values={
                            "timestamp_gt": cursor_ts,
                            "first": BATCH_SIZE,
                        },
                    )

                events = result.get("orderFilledEvents", [])
                if not events:
                    break

                published = await self._process_batch(events)
                total += published

                # Advance cursor
                last = events[-1]
                new_ts = last["timestamp"]

                if new_ts == cursor_ts:
                    # Same second — use sticky mode
                    cursor_id = last["id"]
                else:
                    cursor_ts = new_ts
                    cursor_id = ""

                log.info(
                    "subgraph.batch",
                    fetched=len(events),
                    published=published,
                    total=total,
                    cursor_ts=cursor_ts,
                )

                # If batch was smaller than BATCH_SIZE, we've caught up
                if len(events) < BATCH_SIZE:
                    break

            # Publish caught-up signal
            await self._broker.publish(
                message=json.dumps({
                    "source": "subgraph",
                    "event": "caught_up",
                    "total_recovered": total,
                    "ts": time.time(),
                }),
                topic=self._status_topic,
                key=b"subgraph",
            )

            log.info("subgraph.recovery_complete", total=total)
            return total
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_ingestor_subgraph.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/subgraph.py tests/test_ingestor_subgraph.py
git commit -m "feat(live): add Subgraph recovery poller for gap filling"
```

---

## Phase 5: Quality Gate

### Task 13: Readiness state machine

**Files:**
- Create: `src/polymarket_pipeline/live/quality/__init__.py`
- Create: `src/polymarket_pipeline/live/quality/state.py`
- Test: `tests/test_quality_state.py`

**Step 1: Write the failing test**

Create `tests/test_quality_state.py`:

```python
"""Tests for the readiness state machine."""

import pytest


class TestReadinessState:
    def test_initial_state_is_checking(self):
        from polymarket_pipeline.live.quality.state import PipelineState, ReadinessState

        state = ReadinessState()
        assert state.current == PipelineState.CHECKING

    def test_all_checks_pass_transitions_to_ready(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        results = {
            "resolved_completeness": CheckResult(ok=True),
            "volume_reconciliation": CheckResult(ok=True),
            "source_liveness": CheckResult(ok=True),
            "metadata_freshness": CheckResult(ok=True),
            "dedup_sanity": CheckResult(ok=True),
        }
        state.update(results)
        assert state.current == PipelineState.READY

    def test_any_check_fails_transitions_to_degraded(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        results = {
            "resolved_completeness": CheckResult(ok=True),
            "volume_reconciliation": CheckResult(ok=False, reason="Volume < 10% of average"),
            "source_liveness": CheckResult(ok=True),
            "metadata_freshness": CheckResult(ok=True),
            "dedup_sanity": CheckResult(ok=True),
        }
        state.update(results)
        assert state.current == PipelineState.DEGRADED

    def test_recovery_from_degraded_to_ready(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        # First: degraded
        state.update({"check1": CheckResult(ok=False, reason="bad")})
        assert state.current == PipelineState.DEGRADED
        # Then: all good
        state.update({"check1": CheckResult(ok=True)})
        assert state.current == PipelineState.READY

    def test_failures_list(self):
        from polymarket_pipeline.live.quality.state import CheckResult, ReadinessState

        state = ReadinessState()
        state.update({
            "a": CheckResult(ok=True),
            "b": CheckResult(ok=False, reason="broken"),
        })
        assert state.failures == ["b: broken"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_quality_state.py -v`
Expected: FAIL

**Step 3: Implement state machine**

Create `src/polymarket_pipeline/live/quality/__init__.py` (empty).

Create `src/polymarket_pipeline/live/quality/state.py`:

```python
"""Readiness state machine for the live pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PipelineState(StrEnum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"


@dataclass
class CheckResult:
    """Result of a single health check."""

    ok: bool
    reason: str = ""


class ReadinessState:
    """Tracks pipeline readiness based on health check results."""

    def __init__(self) -> None:
        self._state = PipelineState.CHECKING
        self._last_results: dict[str, CheckResult] = {}

    @property
    def current(self) -> PipelineState:
        return self._state

    @property
    def failures(self) -> list[str]:
        return [
            f"{name}: {r.reason}" for name, r in self._last_results.items() if not r.ok
        ]

    def update(self, results: dict[str, CheckResult]) -> None:
        """Update state based on new check results."""
        self._last_results = results
        all_ok = all(r.ok for r in results.values())
        self._state = PipelineState.READY if all_ok else PipelineState.DEGRADED
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_quality_state.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/quality/ tests/test_quality_state.py
git commit -m "feat(live): add readiness state machine for data quality gate"
```

---

### Task 14: Quality checker — health check implementations

**Files:**
- Create: `src/polymarket_pipeline/live/quality/checker.py`
- Test: `tests/test_quality_checker.py`

**Step 1: Write the failing test**

Create `tests/test_quality_checker.py`:

```python
"""Tests for quality checker health checks."""

import time
from unittest.mock import MagicMock

import pytest

from polymarket_pipeline.live.quality.state import PipelineState


@pytest.fixture
def checker():
    from polymarket_pipeline.live.quality.checker import QualityChecker
    from polymarket_pipeline.live.settings import Settings

    settings = Settings(alchemy_ws_url="wss://test.example.com")
    ch = MagicMock()
    return QualityChecker(settings=settings, clickhouse=ch)


class TestSourceLiveness:
    def test_all_sources_alive(self, checker):
        """Both sources reporting recently → check passes."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        result = checker.check_source_liveness()
        assert result.ok

    def test_one_source_stale(self, checker):
        """One source stale > threshold → check fails."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now - 120)  # 2 min ago
        result = checker.check_source_liveness()
        assert not result.ok
        assert "alchemy" in result.reason

    def test_no_heartbeats_yet(self, checker):
        """No heartbeats received → check fails."""
        result = checker.check_source_liveness()
        assert not result.ok


class TestFullCheck:
    def test_run_all_checks(self, checker):
        """run_all_checks should return results dict and update state."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        # Mock ClickHouse queries to return reasonable data
        checker._ch.query.return_value = [{"cnt": 1000}]
        results = checker.run_all_checks()
        assert isinstance(results, dict)
        assert "source_liveness" in results
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_quality_checker.py -v`
Expected: FAIL

**Step 3: Implement QualityChecker**

Create `src/polymarket_pipeline/live/quality/checker.py`:

```python
"""Health check implementations for the data quality gate."""

from __future__ import annotations

import time
from typing import Any

import structlog

from polymarket_pipeline.live.quality.state import CheckResult, ReadinessState
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


class QualityChecker:
    """Runs health checks and manages pipeline readiness state."""

    def __init__(self, settings: Settings, clickhouse: Any) -> None:
        self._settings = settings
        self._ch = clickhouse
        self._state = ReadinessState()
        self._heartbeats: dict[str, float] = {}

    @property
    def state(self) -> ReadinessState:
        return self._state

    def record_heartbeat(self, source: str, ts: float) -> None:
        """Record a heartbeat from an ingestor source."""
        self._heartbeats[source] = ts

    def check_source_liveness(self) -> CheckResult:
        """Check that all ingestor sources are reporting heartbeats."""
        now = time.time()
        timeout = self._settings.source_liveness_timeout_s

        required = ["rtds", "alchemy"]
        stale = []
        for src in required:
            last = self._heartbeats.get(src)
            if last is None:
                stale.append(f"{src} (no heartbeat)")
            elif now - last > timeout:
                stale.append(f"{src} ({now - last:.0f}s ago)")

        if stale:
            return CheckResult(ok=False, reason=f"Stale sources: {', '.join(stale)}")
        return CheckResult(ok=True)

    def check_volume_reconciliation(self) -> CheckResult:
        """Check current hour volume against trailing 24h average."""
        try:
            result = self._ch.query(
                "SELECT count() as cnt FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 1 HOUR"
            )
            current = result[0]["cnt"] if result else 0

            result_24h = self._ch.query(
                "SELECT count() / 24 as avg_hourly FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 24 HOUR"
            )
            avg_hourly = result_24h[0]["avg_hourly"] if result_24h else 0

            if avg_hourly == 0:
                return CheckResult(ok=True, reason="No 24h baseline yet")

            ratio = current / avg_hourly
            if ratio < self._settings.volume_drop_red_pct:
                return CheckResult(
                    ok=False,
                    reason=f"Volume {ratio:.1%} of average (< {self._settings.volume_drop_red_pct:.0%})",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    def check_metadata_freshness(self) -> CheckResult:
        """Check that token_map has coverage for recent trades."""
        # Placeholder — will be implemented when metadata sync is integrated
        return CheckResult(ok=True)

    def check_dedup_sanity(self) -> CheckResult:
        """Check version=2/version=1 enrichment ratio."""
        try:
            result = self._ch.query(
                "SELECT "
                "  countIf(_version = 2) as v2, "
                "  countIf(_version = 1) as v1 "
                "FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 1 HOUR"
            )
            row = result[0] if result else {"v1": 0, "v2": 0}
            total = row["v1"] + row["v2"]
            if total == 0:
                return CheckResult(ok=True, reason="No recent trades")

            ratio = row["v2"] / total
            if ratio < self._settings.enrichment_ratio_min:
                return CheckResult(
                    ok=False,
                    reason=f"Enrichment ratio {ratio:.1%} < {self._settings.enrichment_ratio_min:.0%}",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    def check_resolved_completeness(self) -> CheckResult:
        """Check that closed markets have trades in ClickHouse."""
        # Placeholder — will be implemented when PostgreSQL metadata is integrated
        return CheckResult(ok=True)

    def run_all_checks(self) -> dict[str, CheckResult]:
        """Run all health checks and update readiness state."""
        results = {
            "source_liveness": self.check_source_liveness(),
            "volume_reconciliation": self.check_volume_reconciliation(),
            "metadata_freshness": self.check_metadata_freshness(),
            "dedup_sanity": self.check_dedup_sanity(),
            "resolved_completeness": self.check_resolved_completeness(),
        }
        self._state.update(results)
        log.info(
            "quality.check_complete",
            state=self._state.current,
            failures=self._state.failures,
        )
        return results
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_quality_checker.py -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/quality/checker.py tests/test_quality_checker.py
git commit -m "feat(live): add quality checker with source liveness and volume checks"
```

---

## Phase 6: ClickHouse Kafka Engine

### Task 15: ClickHouse DDL for Kafka engine + materialized view

**Files:**
- Create: `src/polymarket_pipeline/live/schema.py`

**Step 1: Create schema module with DDL statements**

Create `src/polymarket_pipeline/live/schema.py`:

```python
"""ClickHouse DDL for Kafka engine integration with Redpanda."""

TRADES_KAFKA_TABLE = """
CREATE TABLE IF NOT EXISTS trades_kafka (
    trade_id        String,
    condition_id    String,
    asset_id        String,
    side            String,
    price           Float64,
    size            Float64,
    amount_usd      Float64,
    fee_usd         Float64,
    maker           Nullable(String),
    taker           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    source          String,
    tx_hash         Nullable(String),
    order_hash      Nullable(String),
    block_number    Nullable(UInt64),
    is_backfill     UInt8,
    _version        UInt16
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = '{broker_list}',
    kafka_topic_list = 'trades.raw',
    kafka_group_name = 'clickhouse',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 4
"""

TRADES_RAW_TABLE = """
CREATE TABLE IF NOT EXISTS trades_raw (
    trade_id        String,
    condition_id    String,
    asset_id        String,
    side            String,
    price           Float64,
    size            Float64,
    amount_usd      Float64,
    fee_usd         Float64,
    maker           Nullable(String),
    taker           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    source          String,
    tx_hash         Nullable(String),
    order_hash      Nullable(String),
    block_number    Nullable(UInt64),
    is_backfill     UInt8,
    _version        UInt16
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
PARTITION BY toYYYYMM(timestamp)
"""

TRADES_KAFKA_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trades_kafka_mv TO trades_raw AS
SELECT * FROM trades_kafka
"""


def apply_schema(clickhouse: object, broker_list: str = "localhost:19092") -> None:
    """Create all Kafka engine tables and materialized views.

    Args:
        clickhouse: ClickHouseSink instance with execute() method.
        broker_list: Redpanda broker address.
    """
    clickhouse.execute(TRADES_RAW_TABLE)
    clickhouse.execute(TRADES_KAFKA_TABLE.format(broker_list=broker_list))
    clickhouse.execute(TRADES_KAFKA_MV)
```

**Step 2: Verify it's importable**

Run: `uv run python -c "from polymarket_pipeline.live.schema import apply_schema; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/schema.py
git commit -m "feat(live): add ClickHouse Kafka engine DDL for Redpanda integration"
```

---

## Phase 7: Wire Everything Together

### Task 16: Integrate ingestors into FastStream app lifespan

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py`

**Step 1: Update app.py to launch ingestors on startup**

Update `src/polymarket_pipeline/live/app.py` to:

```python
"""FastStream application for the live sync pipeline."""

from __future__ import annotations

import asyncio
import json
import time

import structlog
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor
from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor
from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.quality.state import PipelineState
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

settings = Settings()
broker = KafkaBroker(settings.redpanda_url)
app = FastStream(broker)

# Shared state
_quality_checker: QualityChecker | None = None
_ingestor_tasks: list[asyncio.Task] = []


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize ingestors and quality checker."""
    global _quality_checker

    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)

    # TODO: Load token_map from PostgreSQL for condition_id resolution
    token_map: dict[str, tuple[str, str]] = {}

    # Initialize quality checker
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=settings.ch_host, port=settings.ch_port, database=settings.ch_database)
    _quality_checker = QualityChecker(settings=settings, clickhouse=ch)
    context.set_global("quality_checker", _quality_checker)

    # Launch ingestors as background tasks
    rtds = RTDSIngestor(broker=broker, topic="trades.raw", status_topic="pipeline.status")
    alchemy = AlchemyIngestor(
        broker=broker,
        ws_url=settings.alchemy_ws_url,
        topic="trades.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
    )

    _ingestor_tasks.append(asyncio.create_task(rtds.run()))
    _ingestor_tasks.append(asyncio.create_task(alchemy.run()))

    log.info("live_pipeline.ingestors_started", count=len(_ingestor_tasks))


@app.on_shutdown
async def on_shutdown() -> None:
    """Cancel ingestors and clean up."""
    for task in _ingestor_tasks:
        task.cancel()
    _ingestor_tasks.clear()
    log.info("live_pipeline.stopped")


# ── Status consumer: process heartbeats and quality signals ──────────


@broker.subscriber("pipeline.status", group_id="quality-gate")
async def handle_status(msg: str) -> None:
    """Process heartbeat and status messages from ingestors."""
    if _quality_checker is None:
        return
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return

    event = data.get("event")
    source = data.get("source", "")

    if event == "heartbeat":
        _quality_checker.record_heartbeat(source, data.get("ts", time.time()))
    elif event == "caught_up":
        log.info("status.caught_up", source=source)
        # Trigger quality check after catch-up
        results = _quality_checker.run_all_checks()
        state = _quality_checker.state.current
        await broker.publish(
            message=json.dumps({
                "event": state.value,
                "failures": _quality_checker.state.failures,
                "ts": time.time(),
            }),
            topic="pipeline.status",
            key=b"quality",
        )
```

**Step 2: Run all tests to verify no regressions**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/app.py
git commit -m "feat(live): wire ingestors + quality gate into FastStream app lifespan"
```

---

### Task 17: Integration smoke test

**Files:**
- Create: `tests/test_live_integration.py`

**Step 1: Write an integration test that verifies the full publish path**

Create `tests/test_live_integration.py`:

```python
"""Integration smoke test — verifies normalizer → JSON → deserialize round-trip."""

import json

from polymarket_pipeline.models import NormalizedTrade, Source


class TestNormalizedTradeRoundTrip:
    def test_rtds_trade_json_roundtrip(self):
        """RTDS-normalized trade survives JSON serialization for Redpanda."""
        from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

        normalizer = RTDSNormalizer()
        msg = {
            "type": "trades",
            "payload": {
                "asset": "12345",
                "side": "BUY",
                "price": 0.72,
                "size": 100.0,
                "timestamp": 1706800000,
                "conditionId": "cond_abc",
                "proxyWallet": "0xmaker123",
                "transactionHash": "0xtx123",
            },
            "timestamp": 1706800001,
        }
        trade = normalizer.normalize(msg)

        # Serialize to JSON (what gets published to Redpanda)
        trade_json = trade.model_dump_json()

        # Deserialize back (what consumers receive)
        restored = NormalizedTrade.model_validate_json(trade_json)

        assert restored.trade_id == trade.trade_id
        assert restored.condition_id == trade.condition_id
        assert restored.price == trade.price
        assert restored.source == Source.RTDS
        assert restored.version == 1

    def test_polygon_rpc_trade_json_roundtrip(self):
        """Alchemy-normalized trade survives JSON serialization for Redpanda."""
        from eth_abi import encode

        from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

        normalizer = PolygonRPCNormalizer()
        data = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256"],
            [12345, 0, 500_000_000, 1000_000_000, 5_000_000],
        )
        log = {
            "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
            "topics": [
                "0x",
                "0x" + "cc" * 32,
                "0x" + "00" * 12 + "a1" * 20,
                "0x" + "00" * 12 + "b2" * 20,
            ],
            "data": "0x" + data.hex(),
            "blockNumber": hex(50_000_000),
            "transactionHash": "0x" + "dd" * 32,
            "transactionIndex": "0x1",
            "logIndex": "0x0",
            "_timestamp": 1706800000,
        }
        trade = normalizer.normalize(log)

        trade_json = trade.model_dump_json()
        restored = NormalizedTrade.model_validate_json(trade_json)

        assert restored.trade_id == trade.trade_id
        assert restored.source == Source.ALCHEMY
        assert restored.version == 2
        assert restored.maker is not None
```

**Step 2: Run test**

Run: `uv run pytest tests/test_live_integration.py -v`
Expected: All pass.

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 4: Commit**

```bash
git add tests/test_live_integration.py
git commit -m "test(live): add integration smoke test for normalizer→JSON round-trip"
```

---

## Summary

| Phase | Tasks | What's built |
|-------|-------|-------------|
| **1. Foundation** | Tasks 1-4 | Redpanda infra, deps, Source.ALCHEMY, Settings |
| **2. Normalizers** | Tasks 5-7 | PolygonRPCNormalizer, SubgraphNormalizer, shared constants |
| **3. App Scaffold** | Tasks 8-9 | FastStream app, CLI entry point |
| **4. Ingestors** | Tasks 10-12 | RTDS, Alchemy, Subgraph recovery poller |
| **5. Quality Gate** | Tasks 13-14 | State machine, health checks |
| **6. CH Schema** | Task 15 | Kafka engine DDL |
| **7. Integration** | Tasks 16-17 | Wire everything, smoke tests |

**After this plan**: The pipeline can ingest live trades from RTDS + Alchemy, flow through Redpanda, auto-insert into ClickHouse, and evaluate pipeline readiness. The signal evaluator, dashboard consumer, and derived refresher consumers are **not** in this plan — they build on top of this foundation and should be a separate plan.
