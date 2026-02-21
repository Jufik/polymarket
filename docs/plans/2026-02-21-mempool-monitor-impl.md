# Mempool Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Rust PyO3 extension module that connects to Polygon's devp2p network, receives pending transaction gossip, filters for Polymarket CTF Exchange trades, decodes calldata, and yields structured dicts to the existing Python live pipeline.

**Architecture:** Rust handles p2p networking (reth crates) and calldata decoding (alloy). Python handles normalization, publishing to Redpanda, and quality checks. The Rust code is a PyO3 extension built via maturin, installed into the venv by `uv sync`.

**Tech Stack:** Rust (reth-ethereum 1.11, alloy 1.0, pyo3 0.23, maturin), Python (existing FastStream pipeline)

**Design doc:** `docs/plans/2026-02-21-mempool-monitor-design.md`

---

## Task 1: Python model changes (Source enum + version field)

**Files:**
- Modify: `src/polymarket_pipeline/models.py:19-24` (Source enum)
- Modify: `src/polymarket_pipeline/models.py:61` (version field constraint)
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_mempool_source_exists():
    from polymarket_pipeline.models import Source
    assert Source.MEMPOOL == "mempool"


def test_version_zero_allowed():
    """Mempool trades use version=0 (lowest priority in ReplacingMergeTree)."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from polymarket_pipeline.models import NormalizedTrade, Side, Source

    trade = NormalizedTrade(
        trade_id="mempool:abc123",
        condition_id="cond_1",
        asset_id="12345",
        side=Side.BUY,
        price=Decimal("0.5000"),
        size=Decimal("100"),
        amount_usd=Decimal("50"),
        fee_usd=Decimal("0"),
        maker="0x" + "a1" * 20,
        taker="0x" + "b2" * 20,
        timestamp=datetime(2026, 2, 21, tzinfo=UTC),
        source=Source.MEMPOOL,
        tx_hash="0x" + "dd" * 32,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=0,
    )
    assert trade.version == 0
    assert trade.source == Source.MEMPOOL
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_mempool_source_exists tests/test_models.py::test_version_zero_allowed -x -v`
Expected: FAIL — `Source` has no `MEMPOOL`, version rejects 0

**Step 3: Implement changes**

In `src/polymarket_pipeline/models.py`:

Line 24 — add after `ALCHEMY = "alchemy"`:
```python
    MEMPOOL = "mempool"
```

Line 61 — change version constraint:
```python
    # ReplacingMergeTree version: on-chain (2) > off-chain (1) > mempool (0)
    version: int = Field(ge=0, le=2)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -x -v`
Expected: ALL PASS

**Step 5: Run existing tests to check no regressions**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/models.py tests/test_models.py
git commit -m "feat(models): add MEMPOOL source + allow version=0"
```

---

## Task 2: MempoolNormalizer (Python)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/mempool.py`
- Test: `tests/test_normalizer_mempool.py`

**Step 1: Write the failing tests**

Create `tests/test_normalizer_mempool.py`:

```python
"""Tests for MempoolNormalizer — decode pending fillOrder calldata dicts."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source


def _make_mempool_trade(
    *,
    tx_hash: str = "0x" + "dd" * 32,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    token_id: str = "12345",
    maker_amount: int = 1_000_000_000,  # 1000 tokens (USDC scale)
    taker_amount: int = 500_000_000,    # 500 USDC
    fee_rate_bps: int = 150,
    side: int = 0,  # 0=BUY, 1=SELL
    expiration: int = 1708500000,
    seen_at: float = 1706800000.123,
) -> dict:
    """Build a mock decoded mempool trade dict (as yielded by Rust sidecar)."""
    return {
        "tx_hash": tx_hash,
        "maker": maker,
        "taker": taker,
        "token_id": token_id,
        "maker_amount": maker_amount,
        "taker_amount": taker_amount,
        "fee_rate_bps": fee_rate_bps,
        "side": side,
        "expiration": expiration,
        "seen_at": seen_at,
    }


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

    return MempoolNormalizer()


@pytest.fixture
def normalizer_with_map():
    from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

    token_map = {"12345": ("cond_abc", "YES")}
    return MempoolNormalizer(token_market_map=token_map)


class TestMempoolNormalizer:
    def test_basic_buy_trade(self, normalizer_with_map):
        """BUY: side=0, taker pays USDC, maker provides tokens."""
        raw = _make_mempool_trade(
            maker_amount=1_000_000_000,  # 1000 tokens
            taker_amount=500_000_000,    # 500 USDC
            side=0,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.price == Decimal("0.5000")
        assert trade.size == Decimal("1000")
        assert trade.amount_usd == Decimal("500")
        assert trade.source == Source.MEMPOOL
        assert trade.version == 0
        assert trade.trade_id.startswith("mempool:")
        assert trade.block_number is None
        assert trade.is_backfill is False

    def test_sell_trade(self, normalizer_with_map):
        """SELL: side=1, maker pays USDC, taker provides tokens."""
        raw = _make_mempool_trade(
            maker_amount=300_000_000,    # 300 USDC
            taker_amount=500_000_000,    # 500 tokens
            side=1,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.price == Decimal("0.6000")  # 300/500
        assert trade.size == Decimal("500")
        assert trade.amount_usd == Decimal("300")

    def test_taker_duplicate_dropped(self, normalizer_with_map):
        """Taker == exchange contract returns None."""
        raw = _make_mempool_trade(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_negrisk_taker_dropped(self, normalizer_with_map):
        """NegRisk exchange taker also dropped."""
        raw = _make_mempool_trade(
            taker="0xc5d563a36ae78145c45a50134d48a1215220f80a",
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_unknown_token_id_returns_none(self, normalizer_with_map):
        """Unknown token_id (not in token_map) returns None."""
        raw = _make_mempool_trade(token_id="999999")
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_without_token_map_returns_none(self, normalizer):
        """Without token_map, all trades return None (can't resolve condition_id)."""
        raw = _make_mempool_trade()
        trade = normalizer.normalize(raw)
        assert trade is None

    def test_with_token_map_resolves_condition_id(self, normalizer_with_map):
        """Token map resolves asset_id to condition_id."""
        raw = _make_mempool_trade(token_id="12345")
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.condition_id == "cond_abc"
        assert trade.asset_id == "12345"

    def test_fee_usd_is_zero(self, normalizer_with_map):
        """Mempool trades have fee_usd=0 (fee not yet charged)."""
        raw = _make_mempool_trade()
        trade = normalizer_with_map.normalize(raw)
        assert trade.fee_usd == Decimal("0")

    def test_timestamp_from_seen_at(self, normalizer_with_map):
        """Timestamp comes from seen_at (when Rust sidecar saw the tx)."""
        raw = _make_mempool_trade(seen_at=1706800000.0)
        trade = normalizer_with_map.normalize(raw)
        assert trade.timestamp == datetime(2024, 2, 1, 14, 26, 40, tzinfo=UTC)

    def test_maker_taker_lowercased(self, normalizer_with_map):
        """Addresses are lowercased."""
        raw = _make_mempool_trade(
            maker="0xABCDEF" + "a1" * 17,
            taker="0xFEDCBA" + "b2" * 17,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade.maker == raw["maker"].lower()
        assert trade.taker == raw["taker"].lower()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_mempool.py -x -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_pipeline.live.normalizers.mempool'`

**Step 3: Implement MempoolNormalizer**

Create `src/polymarket_pipeline/live/normalizers/mempool.py`:

```python
"""Normalizer for decoded mempool fillOrder calldata dicts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source


class MempoolNormalizer:
    """Normalizes decoded mempool trade dicts into NormalizedTrade.

    Input dicts are produced by the Rust PyO3 sidecar (polymarket_mempool).
    """

    def __init__(
        self,
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._token_map = token_market_map or {}

    def normalize(self, raw: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single decoded mempool trade.

        Args:
            raw: Dict with keys: tx_hash, maker, taker, token_id,
                 maker_amount, taker_amount, fee_rate_bps, side,
                 expiration, seen_at.

        Returns:
            NormalizedTrade or None if unknown token or taker duplicate.
        """
        token_id = raw["token_id"]

        # Must resolve condition_id via token_map
        if token_id not in self._token_map:
            return None
        condition_id = self._token_map[token_id][0]

        # Drop taker-perspective duplicates
        taker = raw["taker"].lower()
        if taker in EXCHANGE_ADDRS:
            return None

        maker = raw["maker"].lower()

        # Side from calldata: 0=BUY, 1=SELL
        is_buy = raw["side"] == 0

        if is_buy:
            # BUY: taker pays USDC (taker_amount), maker provides tokens (maker_amount)
            usdc_raw = raw["taker_amount"]
            token_raw = raw["maker_amount"]
        else:
            # SELL: maker pays USDC (maker_amount), taker provides tokens (taker_amount)
            usdc_raw = raw["maker_amount"]
            token_raw = raw["taker_amount"]

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        size = Decimal(token_raw) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        # Deterministic trade_id: mempool:{sha256(tx_hash)[:16]}
        digest = sha256(raw["tx_hash"].encode()).hexdigest()[:16]
        trade_id = f"mempool:{digest}"

        timestamp = datetime.fromtimestamp(raw["seen_at"], tz=UTC)

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=token_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=Decimal("0"),  # fee not yet charged (pending tx)
            maker=maker,
            taker=taker,
            timestamp=timestamp,
            source=Source.MEMPOOL,
            tx_hash=raw["tx_hash"],
            order_hash=None,  # not available from calldata
            block_number=None,  # not mined yet
            is_backfill=False,
            version=0,  # lowest priority: mempool(0) < off-chain(1) < on-chain(2)
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_mempool.py -x -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/mempool.py tests/test_normalizer_mempool.py
git commit -m "feat(normalizer): add MempoolNormalizer for pending tx data"
```

---

## Task 3: Mempool ingestor (Python wrapper)

**Files:**
- Create: `src/polymarket_pipeline/live/ingestors/mempool.py`
- Test: `tests/test_ingestor_mempool.py`

This is the Python glue that imports the Rust `MempoolMonitor`, iterates its async stream, normalizes, and publishes. For testing, we mock the Rust module.

**Step 1: Write the failing tests**

Create `tests/test_ingestor_mempool.py`:

```python
"""Tests for the mempool ingestor (Python wrapper around Rust sidecar)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_raw_trade(
    *,
    tx_hash: str = "0x" + "dd" * 32,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    token_id: str = "12345",
    maker_amount: int = 1_000_000_000,
    taker_amount: int = 500_000_000,
    fee_rate_bps: int = 150,
    side: int = 0,
    expiration: int = 1708500000,
    seen_at: float = 1706800000.123,
) -> dict:
    return {
        "tx_hash": tx_hash,
        "maker": maker,
        "taker": taker,
        "token_id": token_id,
        "maker_amount": maker_amount,
        "taker_amount": taker_amount,
        "fee_rate_bps": fee_rate_bps,
        "side": side,
        "expiration": expiration,
        "seen_at": seen_at,
    }


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


@pytest.fixture
def token_map():
    return {"12345": ("cond_abc", "YES")}


class TestMempoolIngestor:
    async def test_valid_trade_published(self, mock_broker, token_map):
        """Valid mempool trade should be normalized and published."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade()
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 1
        call_kwargs = mock_broker.publish.call_args.kwargs
        assert call_kwargs["topic"] == "mempool.raw"
        assert b"cond_abc" == call_kwargs["key"]

    async def test_unknown_token_not_published(self, mock_broker, token_map):
        """Trade with unknown token_id should be dropped."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade(token_id="999999")
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 0

    async def test_taker_duplicate_not_published(self, mock_broker, token_map):
        """Taker == exchange contract should be dropped."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 0

    async def test_heartbeat_published(self, mock_broker, token_map):
        """Heartbeat should include peers_active and trade count."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        ingestor._trade_count = 5
        ingestor._peers_active = 3
        await ingestor._publish_heartbeat()

        assert mock_broker.publish.call_count == 1
        call_kwargs = mock_broker.publish.call_args.kwargs
        payload = json.loads(call_kwargs["message"])
        assert payload["source"] == "mempool"
        assert payload["event"] == "heartbeat"
        assert payload["trade_count"] == 5
        assert payload["peers_active"] == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ingestor_mempool.py -x -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement MempoolIngestor**

Create `src/polymarket_pipeline/live/ingestors/mempool.py`:

```python
"""Mempool ingestor — wraps Rust PyO3 sidecar for pending tx gossip."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

log = structlog.get_logger()

HEARTBEAT_INTERVAL = 10.0


class MempoolIngestor:
    """Consumes decoded pending txs from the Rust mempool monitor.

    The Rust PyO3 module (polymarket_mempool) handles:
    - devp2p peer discovery and connection (Polygon network)
    - Pending tx filtering (CTF/NegRisk Exchange addresses)
    - fillOrder/fillOrders calldata decoding (alloy sol! macro)

    This Python wrapper handles:
    - Normalization to NormalizedTrade
    - Publishing to Redpanda (mempool.raw topic)
    - Heartbeat reporting to pipeline.status
    """

    def __init__(
        self,
        broker: Any,
        topic: str = "mempool.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        listen_port: int = 30304,
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = MempoolNormalizer(token_market_map=token_market_map)
        self._listen_port = listen_port
        self._trade_count: int = 0
        self._peers_active: int = 0

    async def _handle_trade(self, raw: dict[str, Any]) -> None:
        """Process a single decoded trade dict from the Rust sidecar."""
        trade = self._normalizer.normalize(raw)
        if trade is None:
            return

        trade = trade.model_copy(update={"published_at": time.time()})
        trade_json = trade.model_dump_json()
        await self._broker.publish(
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
        )
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps({
            "source": "mempool",
            "event": "heartbeat",
            "trade_count": self._trade_count,
            "peers_active": self._peers_active,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"mempool",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the mempool ingestor.

        Imports the Rust PyO3 module and iterates its async stream.
        Falls back to a warning if the Rust module is not installed.
        """
        try:
            from polymarket_mempool import MempoolMonitor
        except ImportError:
            log.error(
                "mempool.rust_module_not_installed",
                hint="Install with: cd crates/polymarket-mempool && maturin develop --release",
            )
            return

        monitor = MempoolMonitor(listen_port=self._listen_port)
        log.info("mempool.starting", port=self._listen_port)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            async for raw in monitor.stream():
                # Update peer count from sidecar metadata (if present)
                if "_peers_active" in raw:
                    peers = raw.pop("_peers_active")
                    self._peers_active = peers
                    if peers == 0:
                        log.warning("mempool.zero_peers")

                await self._handle_trade(raw)
        except Exception:
            log.exception("mempool.error")
        finally:
            heartbeat_task.cancel()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ingestor_mempool.py -x -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/mempool.py tests/test_ingestor_mempool.py
git commit -m "feat(ingestor): add MempoolIngestor wrapping Rust sidecar"
```

---

## Task 4: Pipeline integration (settings, app.py, quality checker)

**Files:**
- Modify: `src/polymarket_pipeline/live/settings.py:15` (add mempool settings)
- Modify: `src/polymarket_pipeline/live/app.py:14-15,122-133` (add mempool ingestor)
- Modify: `src/polymarket_pipeline/live/quality/checker.py:53` (add mempool to liveness)

**Step 1: Write the failing test**

Add to `tests/test_dashboard.py` or create a small integration test. Actually, the existing test structure tests components in isolation. Let's add a settings test.

Create `tests/test_mempool_settings.py`:

```python
"""Tests for mempool settings integration."""

import os


class TestMempoolSettings:
    def test_mempool_disabled_by_default(self):
        """Mempool should be opt-in."""
        from polymarket_pipeline.live.settings import Settings

        # Settings requires PM_ALCHEMY_WS_URL
        os.environ.setdefault("PM_ALCHEMY_WS_URL", "wss://test")
        s = Settings()
        assert s.mempool_enabled is False

    def test_mempool_port_default(self):
        """Default listen port is 30304."""
        os.environ.setdefault("PM_ALCHEMY_WS_URL", "wss://test")
        from polymarket_pipeline.live.settings import Settings

        s = Settings()
        assert s.mempool_listen_port == 30304

    def test_mempool_enabled_via_env(self, monkeypatch):
        """Can enable mempool via PM_MEMPOOL_ENABLED=true."""
        monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test")
        monkeypatch.setenv("PM_MEMPOOL_ENABLED", "true")
        from polymarket_pipeline.live.settings import Settings

        s = Settings()
        assert s.mempool_enabled is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mempool_settings.py -x -v`
Expected: FAIL — `mempool_enabled` not found on Settings

**Step 3: Implement settings changes**

In `src/polymarket_pipeline/live/settings.py`, add after line 50 (`dashboard_port`):

```python
    # Mempool monitor (Rust PyO3 sidecar)
    mempool_enabled: bool = False
    mempool_listen_port: int = 30304
```

**Step 4: Run settings test**

Run: `uv run pytest tests/test_mempool_settings.py -x -v`
Expected: ALL PASS

**Step 5: Implement app.py changes**

In `src/polymarket_pipeline/live/app.py`:

Line 15 — add import:
```python
from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor
```

After line 133 (after alchemy task creation), add:
```python
    if settings.mempool_enabled:
        mempool = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
            listen_port=settings.mempool_listen_port,
        )
        _ingestor_tasks.append(asyncio.create_task(mempool.run()))
```

**Step 6: Implement quality checker changes**

In `src/polymarket_pipeline/live/quality/checker.py`:

Line 53 — change required sources to be dynamic. Replace:
```python
        required = ["rtds", "alchemy"]
```
with:
```python
        required = ["rtds", "alchemy"]
        # Mempool is optional — only check if we've ever seen a heartbeat
        if "mempool" in self._heartbeats:
            required.append("mempool")
```

This way mempool liveness is only checked when the sidecar is actually running (heartbeat seen).

**Step 7: Run all tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add src/polymarket_pipeline/live/settings.py src/polymarket_pipeline/live/app.py src/polymarket_pipeline/live/quality/checker.py tests/test_mempool_settings.py
git commit -m "feat(pipeline): integrate mempool ingestor (opt-in via PM_MEMPOOL_ENABLED)"
```

---

## Task 5: Rust project scaffold (Cargo.toml + pyproject.toml + lib.rs stub)

**Files:**
- Create: `crates/polymarket-mempool/Cargo.toml`
- Create: `crates/polymarket-mempool/pyproject.toml`
- Create: `crates/polymarket-mempool/src/lib.rs` (stub — compiles but panics with "not yet implemented")

**Step 1: Create directory structure**

```bash
mkdir -p crates/polymarket-mempool/src
```

**Step 2: Create Cargo.toml**

Create `crates/polymarket-mempool/Cargo.toml`:

```toml
[package]
name = "polymarket-mempool"
version = "0.1.0"
edition = "2021"

[lib]
name = "polymarket_mempool"
crate-type = ["cdylib"]

[dependencies]
# PyO3 bridge
pyo3 = { version = "0.23", features = ["extension-module"] }
pyo3-async-runtimes = { version = "0.23", features = ["tokio-runtime"] }

# Reth networking
reth-ethereum = { version = "1.11", features = ["network"] }
reth-discv4 = "1.11"
reth-tracing = "1.11"
alloy-genesis = "0.12"

# ABI decoding
alloy = { version = "1.0", features = ["sol-types"] }

# Crypto
secp256k1 = { version = "0.29", features = ["global-context", "std", "recovery"] }

# Runtime
tokio = { version = "1", features = ["full"] }

# Serialization
serde = { version = "1", features = ["derive"] }
serde_json = "1"

# Logging
tracing = "0.1"
```

**Step 3: Create pyproject.toml**

Create `crates/polymarket-mempool/pyproject.toml`:

```toml
[build-system]
requires = ["maturin>=1.7,<2.0"]
build-backend = "maturin"

[project]
name = "polymarket-mempool"
requires-python = ">=3.11"

[tool.maturin]
features = ["pyo3/extension-module"]
```

**Step 4: Create lib.rs stub**

Create `crates/polymarket-mempool/src/lib.rs`:

```rust
use pyo3::prelude::*;

/// Polygon devp2p mempool monitor for Polymarket CTF Exchange trades.
///
/// Connects to Polygon peers, receives pending transaction gossip,
/// filters for fillOrder/fillOrders calls to CTF/NegRisk Exchange,
/// decodes calldata, and yields structured dicts to Python.
#[pyclass]
struct MempoolMonitor {
    listen_port: u16,
}

#[pymethods]
impl MempoolMonitor {
    #[new]
    #[pyo3(signature = (listen_port=30304, log_level="info"))]
    fn new(listen_port: u16, log_level: &str) -> Self {
        let _ = log_level; // TODO: configure tracing
        Self { listen_port }
    }

    /// Returns an async iterator of decoded pending trade dicts.
    fn stream<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // Stub: will be implemented with reth networking
        Err(pyo3::exceptions::PyNotImplementedError::new_err(
            "Rust mempool monitor not yet implemented. \
             This stub confirms the PyO3 build pipeline works.",
        ))
    }
}

#[pymodule]
fn polymarket_mempool(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MempoolMonitor>()?;
    Ok(())
}
```

**Step 5: Verify Rust compiles**

```bash
cd crates/polymarket-mempool && cargo check
```

Expected: Compiles (may take 3-5 min first time for dependency resolution).

Note: `cargo check` verifies the code compiles without producing a binary. The first run downloads and compiles all dependencies. Subsequent runs are fast (~5s).

**Step 6: Build and install the Python extension**

```bash
cd crates/polymarket-mempool && maturin develop --release
```

Expected: Builds `.so`/`.dylib` and installs `polymarket_mempool` into the active venv.

**Step 7: Verify Python can import**

```bash
uv run python -c "from polymarket_mempool import MempoolMonitor; m = MempoolMonitor(); print('OK')"
```

Expected: Prints `OK`

**Step 8: Commit**

```bash
git add crates/
git commit -m "feat(rust): scaffold polymarket-mempool PyO3 crate (stub)"
```

---

## Task 6: Rust chain spec (Polygon constants)

**Files:**
- Create: `crates/polymarket-mempool/src/chain_spec.rs`

**Step 1: Implement chain_spec.rs**

Create `crates/polymarket-mempool/src/chain_spec.rs`:

```rust
//! Polygon PoS chain specification for devp2p handshake.

use alloy_genesis::Genesis;
use reth_ethereum::chainspec::ChainSpec;
use reth_network_peers::NodeRecord;
use std::sync::Arc;

/// Polygon mainnet network ID.
pub const POLYGON_NETWORK_ID: u64 = 137;

/// Polygon mainnet genesis hash.
pub const POLYGON_GENESIS_HASH: &str =
    "a9c28ce2141b56c474f1dc504bee9b01eb1bd7d1a507580d5519d4437a97de1b";

/// Build the Polygon PoS chain spec for devp2p handshake.
///
/// Maps Polygon fork blocks to Ethereum-equivalent fork names so that
/// reth can compute a valid ForkID for the handshake.
pub fn polygon_chain_spec() -> Arc<ChainSpec> {
    let genesis_json = serde_json::json!({
        "config": {
            "chainId": POLYGON_NETWORK_ID,
            "homesteadBlock": 0,
            "eip150Block": 0,
            "eip155Block": 0,
            "eip158Block": 0,
            "byzantiumBlock": 0,
            "constantinopleBlock": 0,
            "petersburgBlock": 0,
            "istanbulBlock": 3_395_000,
            "muirGlacierBlock": 3_395_000,
            "berlinBlock": 14_750_000,
            "londonBlock": 23_850_000,
            "shanghaiTime": 0
        },
        "nonce": "0x0",
        "timestamp": "0x0",
        "gasLimit": "0x989680",
        "difficulty": "0x1",
        "alloc": {}
    });

    let genesis: Genesis = serde_json::from_value(genesis_json)
        .expect("hardcoded genesis is valid");

    Arc::new(genesis.into())
}

/// Polygon mainnet bootnodes.
///
/// 4 from reth's official polygon-p2p example + 4 from Polygon docs.
pub fn boot_nodes() -> Vec<NodeRecord> {
    let enodes = [
        // reth example bootnodes
        "enode://b8f1cc9c5d4403703fbf377116469667d2b1823c0daf16b7250aa576bacf399e42c3930ccfcb02c5df6879565a2b8931335565f0e8d3f8e72385ecf4a4bf160a@3.36.224.80:30303",
        "enode://8729e0c825f3d9cad382555f3e46dcff21af323e89025a0e6312df541f4a9e73abfa562d64906f5e59c51fe6f0501b3e61b07979606c56329c020ed739910759@54.194.245.5:30303",
        "enode://76316d1cb93c8ed407d3332d595233401250d48f8fbb1d9c65bd18c0495eca1b43ec38ee0ea1c257c0abb7d1f25d649d359cdfe5a805842159cfe36c5f66b7e8@52.78.36.216:30303",
        "enode://681ebac58d8dd2d8a6eef15329dfbad0ab960561524cf2dfde40ad646736fe5c244020f20b87e7c1520820bc625cfb487dd71d63a3a3bf0baea2dbb8ec7c79f1@34.240.245.39:30303",
        // Polygon official docs bootnodes
        "enode://0cb82b395094ee4a2915e9714894d0347ee0040435b9ad0808b4acedac3aa50c@0x3.0xa.0xa8.0x43:30303",
        "enode://f0f48a8781629f95ff02606081e6e43e4afe179211b67bcb34bb457aeae158168ee13e3c95f9c0a2c12e0a4bda0d44b4318afbe3eb7ea1a7f8aaef7e214e1c95@3.10.168.67:30303",
        "enode://be188e31b3be13a0a8d47ed2dd46e27c42a73eed903ecd7b92cd84ddd90a3e1951ebca8cc9d6f7b40ecd3e26b7c49a3cfc1b40f804c4a0c53c8bb17ad3f84e5e@35.178.50.50:30303",
        "enode://2fa57d22e1bfeb23ca9c4fdcc15099ae35f4bffa990b3cb6a28c80e7f5e4f6fc4e6b2db4e27ed80d3e9be38f37d60ddd1c5a7f37defa95b0d57f27b395700386@3.9.20.133:30303",
    ];

    enodes
        .iter()
        .filter_map(|e| e.parse().ok())
        .collect()
}

/// Build a `Head` for the status handshake.
///
/// Uses a recent but not necessarily current block. Peers tolerate
/// slightly stale heads; we refresh periodically via public RPC.
pub fn head() -> reth_ethereum::chainspec::Head {
    use alloy_primitives::B256;

    // A reasonably recent Polygon block. Doesn't need to be exact —
    // peers accept nodes that are a few blocks behind.
    reth_ethereum::chainspec::Head {
        number: 70_000_000,
        hash: B256::ZERO, // Will be refreshed from RPC at startup
        difficulty: alloy_primitives::U256::from(1),
        total_difficulty: alloy_primitives::U256::from(70_000_000),
        timestamp: 1708000000,
    }
}
```

**Step 2: Add module to lib.rs**

Add at top of `crates/polymarket-mempool/src/lib.rs`:
```rust
mod chain_spec;
```

**Step 3: Verify compiles**

```bash
cd crates/polymarket-mempool && cargo check
```

Expected: Compiles successfully.

**Step 4: Commit**

```bash
git add crates/polymarket-mempool/src/chain_spec.rs crates/polymarket-mempool/src/lib.rs
git commit -m "feat(rust): add Polygon chain spec, bootnodes, fork blocks"
```

---

## Task 7: Rust filter + decoder

**Files:**
- Create: `crates/polymarket-mempool/src/filter.rs`
- Create: `crates/polymarket-mempool/src/decoder.rs`

**Step 1: Implement filter.rs**

Create `crates/polymarket-mempool/src/filter.rs`:

```rust
//! Transaction filter: only pass CTF/NegRisk Exchange trades.

use alloy::primitives::Address;
use std::collections::HashSet;
use std::sync::LazyLock;

/// CTF Exchange contract address.
const CTF_EXCHANGE: &str = "4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e";
/// NegRisk CTF Exchange contract address.
const NEGRISK_EXCHANGE: &str = "c5d563a36ae78145c45a50134d48a1215220f80a";

/// Set of exchange addresses for fast lookup.
static EXCHANGE_ADDRS: LazyLock<HashSet<Address>> = LazyLock::new(|| {
    let mut set = HashSet::new();
    set.insert(CTF_EXCHANGE.parse().unwrap());
    set.insert(NEGRISK_EXCHANGE.parse().unwrap());
    set
});

/// fillOrder(Order,Sig) selector: first 4 bytes of keccak256.
const FILL_ORDER_SELECTOR: [u8; 4] = [0xfe, 0x72, 0x9a, 0xee]; // TODO: verify
/// fillOrders(Order[],Sig[]) selector.
const FILL_ORDERS_SELECTOR: [u8; 4] = [0xd7, 0x98, 0xb1, 0x06]; // TODO: verify

/// Check if a transaction targets a Polymarket exchange contract.
pub fn is_exchange_tx(to: &Address) -> bool {
    EXCHANGE_ADDRS.contains(to)
}

/// Check if calldata starts with a fillOrder/fillOrders selector.
pub fn is_fill_order(calldata: &[u8]) -> bool {
    if calldata.len() < 4 {
        return false;
    }
    let selector = &calldata[..4];
    selector == FILL_ORDER_SELECTOR || selector == FILL_ORDERS_SELECTOR
}
```

**Step 2: Implement decoder.rs**

Create `crates/polymarket-mempool/src/decoder.rs`:

```rust
//! Decode fillOrder/fillOrders calldata into structured trade data.

use alloy::sol;
use alloy::sol_types::SolCall;
use pyo3::types::PyDict;
use pyo3::{Bound, PyResult, Python};
use std::time::{SystemTime, UNIX_EPOCH};

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
        uint8 side;          // 0 = BUY, 1 = SELL
        uint8 signatureType;
    }

    struct Sig {
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    #[derive(Debug)]
    function fillOrder(Order order, Sig sig);

    #[derive(Debug)]
    function fillOrders(Order[] orders, Sig[] sigs);
}

/// Decode a single Order into a Python dict.
fn order_to_dict<'py>(
    py: Python<'py>,
    order: &Order,
    tx_hash: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();

    dict.set_item("tx_hash", tx_hash)?;
    dict.set_item("maker", format!("0x{:x}", order.maker))?;
    dict.set_item("taker", format!("0x{:x}", order.taker))?;
    dict.set_item("token_id", order.tokenId.to_string())?;
    dict.set_item("maker_amount", order.makerAmount.to::<u128>())?;
    dict.set_item("taker_amount", order.takerAmount.to::<u128>())?;
    dict.set_item("fee_rate_bps", order.feeRateBps.to::<u64>())?;
    dict.set_item("side", order.side)?;
    dict.set_item("expiration", order.expiration.to::<u64>())?;
    dict.set_item("seen_at", now)?;

    Ok(dict)
}

/// Decode calldata and return a vec of Python dicts (one per order).
pub fn decode_calldata<'py>(
    py: Python<'py>,
    calldata: &[u8],
    tx_hash: &str,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let selector = &calldata[..4];

    // Try fillOrder (single order)
    if let Ok(call) = fillOrderCall::abi_decode(&calldata[4..], true) {
        let dict = order_to_dict(py, &call.order, tx_hash)?;
        return Ok(vec![dict]);
    }

    // Try fillOrders (batch)
    if let Ok(call) = fillOrdersCall::abi_decode(&calldata[4..], true) {
        let mut results = Vec::with_capacity(call.orders.len());
        for order in &call.orders {
            results.push(order_to_dict(py, order, tx_hash)?);
        }
        return Ok(results);
    }

    // Neither decoded — unknown function
    Ok(vec![])
}
```

**Step 3: Add modules to lib.rs**

Add at top of `crates/polymarket-mempool/src/lib.rs`:
```rust
mod chain_spec;
mod decoder;
mod filter;
```

**Step 4: Verify compiles**

```bash
cd crates/polymarket-mempool && cargo check
```

Expected: Compiles. May need to adjust alloy imports based on exact API surface.

**Step 5: Commit**

```bash
git add crates/polymarket-mempool/src/
git commit -m "feat(rust): add tx filter + fillOrder calldata decoder"
```

---

## Task 8: Rust network runner + PyO3 async stream

**Files:**
- Create: `crates/polymarket-mempool/src/network/mod.rs`
- Create: `crates/polymarket-mempool/src/network/runner.rs`
- Modify: `crates/polymarket-mempool/src/lib.rs` (wire up real stream)

This is the core networking task. It connects to Polygon peers via reth and streams decoded trades to Python.

**Step 1: Implement network/mod.rs**

Create `crates/polymarket-mempool/src/network/mod.rs`:
```rust
pub mod runner;
```

**Step 2: Implement network/runner.rs**

Create `crates/polymarket-mempool/src/network/runner.rs`:

```rust
//! Polygon devp2p network runner.
//!
//! Connects to Polygon peers, receives pending transaction gossip,
//! filters for CTF Exchange txs, and sends decoded trades through a channel.

use crate::chain_spec::{boot_nodes, head, polygon_chain_spec};
use crate::decoder::decode_calldata;
use crate::filter::{is_exchange_tx, is_fill_order};

use reth_discv4::Discv4ConfigBuilder;
use reth_ethereum::network::{
    NetworkConfig, NetworkEvent, NetworkEventListenerProvider, NetworkManager,
};
use reth_ethereum::tasks::Runtime;

use pyo3::types::PyDict;
use pyo3::{Bound, PyResult, Python};
use secp256k1::{rand, SecretKey};
use std::net::{Ipv4Addr, SocketAddr};
use std::time::Duration;
use tokio::sync::mpsc;
use tokio_stream::StreamExt;

/// Run the network manager and send decoded trades through the channel.
pub async fn run_network(
    listen_port: u16,
    tx: mpsc::Sender<serde_json::Value>,
) -> eyre::Result<()> {
    let secret_key = SecretKey::new(&mut rand::thread_rng());
    let local_addr = SocketAddr::new(Ipv4Addr::UNSPECIFIED.into(), listen_port);

    let net_cfg = NetworkConfig::builder(secret_key, Runtime::test())
        .set_head(head())
        .listener_addr(local_addr)
        .build_with_noop_provider(polygon_chain_spec());

    let mut discv4_cfg = Discv4ConfigBuilder::default();
    discv4_cfg
        .add_boot_nodes(boot_nodes())
        .lookup_interval(Duration::from_secs(1));
    let net_cfg = net_cfg.set_discovery_v4(discv4_cfg.build());

    let net_manager = NetworkManager::eth(net_cfg).await?;
    let net_handle = net_manager.handle();
    let mut events = net_handle.event_listener();

    tokio::spawn(net_manager);

    tracing::info!("Looking for Polygon peers on port {}", listen_port);

    // Track active peers
    let mut peers_active: usize = 0;

    while let Some(evt) = events.next().await {
        match evt {
            NetworkEvent::ActivePeerSession { info, .. } => {
                peers_active += 1;
                tracing::debug!(
                    chain = ?info.status.chain,
                    client = ?info.client_version,
                    peers = peers_active,
                    "Peer connected"
                );
            }
            NetworkEvent::SessionClosed { .. } => {
                peers_active = peers_active.saturating_sub(1);
                if peers_active == 0 {
                    tracing::warn!("All peers disconnected — zero visibility");
                }
            }
            // TODO: Handle transaction gossip events here.
            // reth-network's transaction pool integration receives
            // NewPooledTransactionHashes and fetches full txs.
            // For Phase 1, we need to wire up the tx pool listener.
            _ => {}
        }

        // Send peer count updates through channel
        let mut status = serde_json::Map::new();
        status.insert("_peers_active".to_string(), peers_active.into());
        let _ = tx.try_send(serde_json::Value::Object(status));
    }

    Ok(())
}
```

**Step 3: Update lib.rs with real async stream**

Replace `crates/polymarket-mempool/src/lib.rs`:

```rust
mod chain_spec;
mod decoder;
mod filter;
mod network;

use pyo3::prelude::*;
use pyo3::types::PyDict;
use tokio::sync::mpsc;

/// Polygon devp2p mempool monitor for Polymarket CTF Exchange trades.
#[pyclass]
struct MempoolMonitor {
    listen_port: u16,
}

#[pymethods]
impl MempoolMonitor {
    #[new]
    #[pyo3(signature = (listen_port=30304, log_level="info"))]
    fn new(listen_port: u16, log_level: &str) -> Self {
        // Initialize tracing subscriber (once)
        let _ = tracing_subscriber::fmt()
            .with_env_filter(log_level)
            .try_init();
        Self { listen_port }
    }

    /// Returns an async iterator of decoded pending trade dicts.
    ///
    /// Each dict contains: tx_hash, maker, taker, token_id,
    /// maker_amount, taker_amount, fee_rate_bps, side, expiration, seen_at.
    ///
    /// Also yields status dicts with _peers_active key.
    fn stream(&self) -> MempoolStream {
        MempoolStream {
            listen_port: self.listen_port,
            started: false,
            rx: None,
        }
    }
}

/// Async iterator that yields trade dicts from the Rust network runner.
#[pyclass]
struct MempoolStream {
    listen_port: u16,
    started: bool,
    rx: Option<mpsc::Receiver<serde_json::Value>>,
}

#[pymethods]
impl MempoolStream {
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __anext__<'py>(&mut self, py: Python<'py>) -> PyResult<Option<PyObject>> {
        if !self.started {
            let (tx, rx) = mpsc::channel(10_000);
            self.rx = Some(rx);
            self.started = true;

            let port = self.listen_port;
            // Spawn the network runner on the tokio runtime
            std::thread::spawn(move || {
                let rt = tokio::runtime::Runtime::new().unwrap();
                rt.block_on(async {
                    if let Err(e) = network::runner::run_network(port, tx).await {
                        tracing::error!("Network runner failed: {}", e);
                    }
                });
            });
        }

        // Try to receive the next value
        if let Some(rx) = &mut self.rx {
            match rx.try_recv() {
                Ok(val) => {
                    let dict = pythonize::pythonize(py, &val)?;
                    Ok(Some(dict.into()))
                }
                Err(mpsc::error::TryRecvError::Empty) => {
                    // No data yet — return None to signal "not ready"
                    // Python async for will retry
                    Ok(None)
                }
                Err(mpsc::error::TryRecvError::Disconnected) => {
                    Err(pyo3::exceptions::PyStopAsyncIteration::new_err(
                        "Network runner disconnected",
                    ))
                }
            }
        } else {
            Err(pyo3::exceptions::PyRuntimeError::new_err("Stream not initialized"))
        }
    }
}

#[pymodule]
fn polymarket_mempool(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MempoolMonitor>()?;
    m.add_class::<MempoolStream>()?;
    Ok(())
}
```

Note: The `__anext__` implementation above is simplified. The actual PyO3 async integration needs `pyo3-async-runtimes` for proper async/await bridging. The exact API may need adjustment based on the pyo3-async-runtimes version. The key pattern is: Rust tokio runtime runs in a background thread, communicates via mpsc channel, Python polls the channel.

**Step 4: Add pythonize dependency**

Add to `Cargo.toml` under `[dependencies]`:
```toml
pythonize = "0.23"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
eyre = "0.6"
tokio-stream = "0.1"
```

**Step 5: Verify compiles**

```bash
cd crates/polymarket-mempool && cargo check
```

Expected: Compiles. Some API adjustments may be needed based on exact reth 1.11 event types.

**Step 6: Build and test end-to-end**

```bash
cd crates/polymarket-mempool && maturin develop --release
uv run python -c "
from polymarket_mempool import MempoolMonitor
m = MempoolMonitor(listen_port=30304)
s = m.stream()
print(type(s))
print('Stream created OK')
"
```

Expected: Prints `MempoolStream` type and "Stream created OK".

**Step 7: Commit**

```bash
git add crates/polymarket-mempool/
git commit -m "feat(rust): implement network runner + PyO3 async stream"
```

---

## Task 9: Integration test (Rust → Python → Redpanda)

**Files:**
- Create: `tests/test_mempool_integration.py`

This test verifies the full Python path works (normalizer + ingestor) without requiring the Rust module or actual Polygon peers. It mocks `polymarket_mempool.MempoolMonitor`.

**Step 1: Write the integration test**

Create `tests/test_mempool_integration.py`:

```python
"""Integration test: mempool trade flow from raw dict to Redpanda publish."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_raw_trade(**overrides):
    base = {
        "tx_hash": "0x" + "dd" * 32,
        "maker": "0x" + "a1" * 20,
        "taker": "0x" + "b2" * 20,
        "token_id": "12345",
        "maker_amount": 1_000_000_000,
        "taker_amount": 500_000_000,
        "fee_rate_bps": 150,
        "side": 0,
        "expiration": 1708500000,
        "seen_at": 1706800000.123,
    }
    base.update(overrides)
    return base


class TestMempoolIntegration:
    async def test_full_flow_normalized_and_published(self):
        """Raw mempool dict → MempoolNormalizer → broker.publish."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        raw = _make_raw_trade()
        await ingestor._handle_trade(raw)

        assert broker.publish.call_count == 1
        call_kwargs = broker.publish.call_args.kwargs
        payload = json.loads(call_kwargs["message"])

        assert payload["source"] == "mempool"
        assert payload["version"] == 0
        assert payload["condition_id"] == "cond_abc"
        assert payload["side"] == "BUY"
        assert payload["trade_id"].startswith("mempool:")
        assert payload["block_number"] is None
        assert payload["order_hash"] is None

    async def test_multiple_trades_counted(self):
        """Trade count increments correctly."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        for _ in range(5):
            await ingestor._handle_trade(_make_raw_trade())

        assert ingestor._trade_count == 5
        assert broker.publish.call_count == 5

    async def test_peers_active_updated_from_metadata(self):
        """_peers_active field in raw dict updates ingestor state."""
        from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor

        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        # Simulate what run() does with _peers_active
        raw = _make_raw_trade()
        raw["_peers_active"] = 7
        peers = raw.pop("_peers_active")
        ingestor._peers_active = peers
        await ingestor._handle_trade(raw)

        assert ingestor._peers_active == 7
```

**Step 2: Run integration test**

Run: `uv run pytest tests/test_mempool_integration.py -x -v`
Expected: ALL PASS

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/test_mempool_integration.py
git commit -m "test: add mempool integration test (full normalize + publish flow)"
```

---

## Task 10: Root pyproject.toml integration + .gitignore

**Files:**
- Modify: `pyproject.toml` (root) — add polymarket-mempool as path dependency
- Modify: `.gitignore` — add Rust build artifacts

**Step 1: Update root pyproject.toml**

Add under `[project.dependencies]` (or `[tool.uv.sources]`):
```toml
[tool.uv.sources]
polymarket-mempool = { path = "crates/polymarket-mempool", editable = true }
```

And under `[project]` dependencies list:
```toml
"polymarket-mempool",
```

**Step 2: Update .gitignore**

Add:
```
# Rust build artifacts
crates/*/target/
```

**Step 3: Verify uv sync works**

```bash
uv sync --all-extras
```

Expected: Builds Rust extension via maturin and installs all deps.

**Step 4: Commit**

```bash
git add pyproject.toml .gitignore uv.lock
git commit -m "build: add polymarket-mempool crate as uv path dependency"
```

---

## Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | Source.MEMPOOL + version=0 | None |
| 2 | MempoolNormalizer | Task 1 |
| 3 | MempoolIngestor (Python wrapper) | Task 2 |
| 4 | Pipeline integration (settings, app, quality) | Task 3 |
| 5 | Rust scaffold (Cargo.toml, pyproject.toml, stub) | None |
| 6 | Rust chain spec (Polygon constants) | Task 5 |
| 7 | Rust filter + decoder | Task 5 |
| 8 | Rust network runner + PyO3 stream | Tasks 6, 7 |
| 9 | Integration test | Tasks 3, 4 |
| 10 | Root pyproject.toml + .gitignore | Task 5 |

Tasks 1-4 (Python) and 5-8 (Rust) can proceed in parallel. Task 9 depends on both tracks. Task 10 is final wiring.
