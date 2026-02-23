# Phase 2: Paper-Dev Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Connect strategy framework to live Kafka feed with FeatureProvider system, LiveRunner, PaperExecutor, and CLI entry point.

**Architecture:** FeatureProviders compute features independently (Polars/CH backends), LiveRunner dispatches trades to providers then strategies with hot-path timing enforcement, PaperExecutor simulates fills, CLI boots everything from TOML config.

**Tech Stack:** Python 3.11+, FastStream/Kafka, Polars, ClickHouse, Pydantic v2, structlog, Typer, pytest-asyncio, mypy strict

---

### Task 1: Add FeatureProvider and FeatureBackend Protocols

**Files:**
- Modify: `src/polymarket_pipeline/strategies/protocol.py`
- Test: `tests/test_strategy_protocol.py`

**Context:** The existing `protocol.py` defines `StrategyContext`, `Strategy`, `VectorizedStrategy`, and `Executor`. We need to add two new protocols: `FeatureProvider` (independent computation unit) and `FeatureBackend` (data access abstraction for Polars vs ClickHouse). We also add `get_feature()` to `StrategyContext`.

**Step 1: Write the failing tests**

Append to `tests/test_strategy_protocol.py`:

```python
# ---------------------------------------------------------------------------
# FeatureBackend
# ---------------------------------------------------------------------------


class _StubBackend:
    async def query_trades(
        self, condition_ids: list[str] | None = None
    ) -> pl.DataFrame:
        return pl.DataFrame()

    async def query_markets(self) -> pl.DataFrame:
        return pl.DataFrame()

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        return pl.DataFrame()


async def test_stub_backend_satisfies_feature_backend_protocol() -> None:
    from polymarket_pipeline.strategies.protocol import FeatureBackend

    assert isinstance(_StubBackend(), FeatureBackend)


# ---------------------------------------------------------------------------
# FeatureProvider
# ---------------------------------------------------------------------------


class _StubProvider:
    name = "stub"

    async def compute(self, backend: Any) -> None:
        pass

    async def on_trade(self, trade: Any) -> None:
        pass

    async def refresh(self, backend: Any) -> None:
        pass

    def get_features(self) -> dict[str, Any]:
        return {}


async def test_stub_provider_satisfies_feature_provider_protocol() -> None:
    from polymarket_pipeline.strategies.protocol import FeatureProvider

    assert isinstance(_StubProvider(), FeatureProvider)


# ---------------------------------------------------------------------------
# StrategyContext.get_feature
# ---------------------------------------------------------------------------


class _CtxWithFeature:
    async def get_position(self, condition_id: str) -> None:
        return None

    async def get_market(self, condition_id: str) -> None:
        return None

    async def get_orderbook(self, condition_id: str) -> None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return 0.0

    async def get_feature(self, key: str) -> Any:
        return None


async def test_ctx_with_get_feature_satisfies_protocol() -> None:
    assert isinstance(_CtxWithFeature(), StrategyContext)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_protocol.py -x -q`
Expected: ImportError or AttributeError — `FeatureBackend`, `FeatureProvider` not defined, `StrategyContext` doesn't require `get_feature`.

**Step 3: Add the protocols to `protocol.py`**

Add these imports near the top:

```python
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import polars as pl
```

Add `get_feature` to `StrategyContext`:

```python
    async def get_feature(self, key: str) -> Any:
        """Return a feature value by key, or ``None``."""
        ...
```

Add after `Executor` protocol:

```python
# ---------------------------------------------------------------------------
# FeatureBackend — data access layer for feature providers
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureBackend(Protocol):
    """Abstraction over Polars (backtest) vs ClickHouse (live) for batch queries."""

    async def query_trades(
        self, condition_ids: list[str] | None = None
    ) -> pl.DataFrame:
        """Return trades, optionally filtered by condition IDs."""
        ...

    async def query_markets(self) -> pl.DataFrame:
        """Return market metadata."""
        ...

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary query (SQL for CH, Polars expression for in-memory)."""
        ...


# ---------------------------------------------------------------------------
# FeatureProvider — independent feature computation unit
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureProvider(Protocol):
    """Independent computation that feeds features into StrategyContext.

    Lifecycle: compute() at startup, on_trade() per event (hot path),
    refresh() periodically for expensive recomputation.
    """

    name: str

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute features at startup."""
        ...

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """O(1) streaming update — hot path, in-memory only."""
        ...

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic expensive recomputation (e.g. every 15 min)."""
        ...

    def get_features(self) -> dict[str, Any]:
        """Return current feature values for context injection."""
        ...
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_protocol.py -x -q`
Expected: All pass (existing + 3 new).

**Step 5: Verify the old `_StubContext` in tests still passes**

The existing `_StubContext` class in `test_strategy_protocol.py` needs `get_feature`. Check if the test fails and add the method if needed:

```python
    async def get_feature(self, key: str) -> Any:
        return None
```

**Step 6: Run full protocol tests + mypy**

Run: `uv run pytest tests/test_strategy_protocol.py -x -q && uv run mypy --strict src/polymarket_pipeline/strategies/protocol.py`
Expected: All pass.

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/strategies/protocol.py tests/test_strategy_protocol.py
git commit -m "feat(strategies): add FeatureProvider, FeatureBackend protocols + get_feature to StrategyContext"
```

---

### Task 2: Extend InMemoryContext with Feature Support

**Files:**
- Modify: `src/polymarket_pipeline/strategies/context/memory.py`
- Modify: `tests/test_strategy_context_memory.py`

**Context:** `InMemoryContext` currently has `_positions`, `_markets`, `_time` dicts. We need to add `_features: dict[str, Any]` and implement `update_features()` (sync, for runner) + `get_feature()` (async, for protocol).

**Step 1: Write the failing tests**

Append to `tests/test_strategy_context_memory.py`:

```python
# ---------------------------------------------------------------------------
# get_feature / update_features
# ---------------------------------------------------------------------------


async def test_get_feature_returns_none_when_empty(ctx: InMemoryContext) -> None:
    result = await ctx.get_feature("skilled_traders")
    assert result is None


async def test_update_features_then_get(ctx: InMemoryContext) -> None:
    ctx.update_features({"skilled_traders": frozenset({"0xalice", "0xbob"})})
    result = await ctx.get_feature("skilled_traders")
    assert result == frozenset({"0xalice", "0xbob"})


async def test_update_features_merges(ctx: InMemoryContext) -> None:
    ctx.update_features({"a": 1})
    ctx.update_features({"b": 2})
    assert await ctx.get_feature("a") == 1
    assert await ctx.get_feature("b") == 2


async def test_update_features_overwrites(ctx: InMemoryContext) -> None:
    ctx.update_features({"a": 1})
    ctx.update_features({"a": 99})
    assert await ctx.get_feature("a") == 99
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_context_memory.py::test_get_feature_returns_none_when_empty -x -q`
Expected: AttributeError — `InMemoryContext` has no `get_feature`.

**Step 3: Implement the feature methods**

In `src/polymarket_pipeline/strategies/context/memory.py`:

Update `__slots__`:
```python
    __slots__ = ("_features", "_markets", "_positions", "_time")
```

In `__init__`:
```python
        self._features: dict[str, Any] = {}
```

Add the `Any` import:
```python
from typing import Any
```

Add async protocol method:
```python
    async def get_feature(self, key: str) -> Any:
        """Return a feature value by *key*, or ``None``."""
        return self._features.get(key)
```

Add sync mutation method:
```python
    def update_features(self, features: dict[str, Any]) -> None:
        """Merge *features* into the feature store."""
        self._features.update(features)
```

**Step 4: Run all context tests**

Run: `uv run pytest tests/test_strategy_context_memory.py -x -q`
Expected: All pass (existing 16 + 4 new = 20).

**Step 5: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/context/memory.py`
Expected: Success.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/context/memory.py tests/test_strategy_context_memory.py
git commit -m "feat(strategies): add feature store to InMemoryContext (update_features + get_feature)"
```

---

### Task 3: PolarsBackend — FeatureBackend for Backtest

**Files:**
- Create: `src/polymarket_pipeline/strategies/features/__init__.py`
- Create: `src/polymarket_pipeline/strategies/features/backend_polars.py`
- Create: `tests/test_feature_backend_polars.py`

**Context:** `PolarsBackend` implements `FeatureBackend` by scanning Polars DataFrames (in-memory or from parquet). Used in backtest and replay modes. `query_trades()` returns trades filtered by condition_ids, `query_markets()` returns markets, `query_custom()` raises `NotImplementedError` (custom queries are CH-specific).

**Step 1: Create the `__init__.py`**

```python
"""Feature providers and backends for the strategy framework."""
```

**Step 2: Write the failing tests**

Create `tests/test_feature_backend_polars.py`:

```python
"""Tests for PolarsBackend — in-memory FeatureBackend for backtest."""

from __future__ import annotations

import polars as pl
import pytest

from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.protocol import FeatureBackend


@pytest.fixture
def trades_df() -> pl.DataFrame:
    return pl.DataFrame({
        "condition_id": ["0xa", "0xa", "0xb"],
        "maker": ["alice", "bob", "charlie"],
        "side": ["BUY", "SELL", "BUY"],
        "published_at": [1.0, 2.0, 3.0],
    })


@pytest.fixture
def markets_df() -> pl.DataFrame:
    return pl.DataFrame({
        "condition_id": ["0xa", "0xb"],
        "question": ["Will A?", "Will B?"],
        "active": [True, True],
    })


@pytest.fixture
def backend(trades_df: pl.DataFrame, markets_df: pl.DataFrame) -> PolarsBackend:
    return PolarsBackend(trades=trades_df, markets=markets_df)


async def test_satisfies_feature_backend_protocol(backend: PolarsBackend) -> None:
    assert isinstance(backend, FeatureBackend)


async def test_query_trades_all(backend: PolarsBackend) -> None:
    df = await backend.query_trades()
    assert len(df) == 3


async def test_query_trades_filtered(backend: PolarsBackend) -> None:
    df = await backend.query_trades(condition_ids=["0xa"])
    assert len(df) == 2
    assert df["condition_id"].to_list() == ["0xa", "0xa"]


async def test_query_trades_empty_filter(backend: PolarsBackend) -> None:
    df = await backend.query_trades(condition_ids=["0xnonexistent"])
    assert len(df) == 0


async def test_query_markets(backend: PolarsBackend) -> None:
    df = await backend.query_markets()
    assert len(df) == 2
    assert "condition_id" in df.columns


async def test_query_custom_raises(backend: PolarsBackend) -> None:
    with pytest.raises(NotImplementedError):
        await backend.query_custom("SELECT 1")
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_feature_backend_polars.py -x -q`
Expected: ImportError — module not found.

**Step 4: Implement PolarsBackend**

Create `src/polymarket_pipeline/strategies/features/backend_polars.py`:

```python
"""Polars-backed FeatureBackend for backtest and replay modes.

Holds trades and markets as in-memory DataFrames. No external dependencies.
"""

from __future__ import annotations

from typing import Any

import polars as pl


class PolarsBackend:
    """FeatureBackend backed by in-memory Polars DataFrames.

    Parameters
    ----------
    trades:
        DataFrame of trades (must have ``condition_id`` column).
    markets:
        DataFrame of market metadata.
    """

    __slots__ = ("_markets", "_trades")

    def __init__(self, trades: pl.DataFrame, markets: pl.DataFrame) -> None:
        self._trades = trades
        self._markets = markets

    async def query_trades(
        self, condition_ids: list[str] | None = None
    ) -> pl.DataFrame:
        """Return trades, optionally filtered by *condition_ids*."""
        if condition_ids is None:
            return self._trades
        return self._trades.filter(pl.col("condition_id").is_in(condition_ids))

    async def query_markets(self) -> pl.DataFrame:
        """Return market metadata."""
        return self._markets

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Not supported for Polars backend — use ClickHouseBackend for SQL."""
        msg = "query_custom is not supported by PolarsBackend; use ClickHouseBackend"
        raise NotImplementedError(msg)
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_feature_backend_polars.py -x -q`
Expected: All 6 pass.

**Step 6: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/features/backend_polars.py`
Expected: Success.

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/strategies/features/__init__.py \
        src/polymarket_pipeline/strategies/features/backend_polars.py \
        tests/test_feature_backend_polars.py
git commit -m "feat(strategies): add PolarsBackend — in-memory FeatureBackend for backtest"
```

---

### Task 4: SkilledTradersProvider — First FeatureProvider

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`
- Create: `tests/test_skilled_traders_provider.py`

**Context:** This is the first concrete `FeatureProvider`. It computes a `frozenset[str]` of skilled trader addresses by querying trader PnL from the backend. `on_trade()` is a no-op (skilled set changes slowly). `refresh()` re-runs `compute()`. `get_features()` returns `{"skilled_traders": frozenset(...)}`.

For the MVP, "skilled" is defined as: traders with >= `min_trades` trades and > `min_pnl` net PnL across resolved markets. The exact PnL logic is simplified for now — we count wins and losses based on side vs outcome.

**Step 1: Write the failing tests**

Create `tests/test_skilled_traders_provider.py`:

```python
"""Tests for SkilledTradersProvider."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.protocol import FeatureProvider
from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
    SkilledTradersProvider,
)


def _make_backend(traders_data: list[dict[str, Any]]) -> PolarsBackend:
    """Build a PolarsBackend from a list of trade dicts."""
    trades = pl.DataFrame(traders_data)
    markets = pl.DataFrame({"condition_id": [], "question": [], "active": []})
    return PolarsBackend(trades=trades, markets=markets)


@pytest.fixture
def backend_with_skilled() -> PolarsBackend:
    """Backend where alice has 10 BUY trades (skilled) and bob has 2 (below threshold)."""
    trades = []
    for i in range(10):
        trades.append({
            "condition_id": f"0xmarket_{i}",
            "maker": "0xalice",
            "side": "BUY",
            "published_at": float(i),
        })
    for i in range(2):
        trades.append({
            "condition_id": f"0xmarket_{i}",
            "maker": "0xbob",
            "side": "BUY",
            "published_at": float(100 + i),
        })
    return _make_backend(trades)


async def test_satisfies_feature_provider_protocol() -> None:
    provider = SkilledTradersProvider(min_trades=5)
    assert isinstance(provider, FeatureProvider)


async def test_provider_name() -> None:
    provider = SkilledTradersProvider()
    assert provider.name == "skilled_traders"


async def test_compute_identifies_skilled(backend_with_skilled: PolarsBackend) -> None:
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    features = provider.get_features()
    skilled = features["skilled_traders"]
    assert "0xalice" in skilled
    assert "0xbob" not in skilled


async def test_compute_empty_backend() -> None:
    backend = _make_backend([])
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend)
    assert provider.get_features()["skilled_traders"] == frozenset()


async def test_on_trade_is_noop(backend_with_skilled: PolarsBackend) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    from polymarket_pipeline.models import NormalizedTrade, Side, Source

    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    before = provider.get_features()["skilled_traders"]

    trade = NormalizedTrade(
        trade_id="test:1",
        condition_id="0xnew",
        asset_id="a1",
        side=Side.BUY,
        price=Decimal("0.50"),
        size=Decimal("100"),
        amount_usd=Decimal("50"),
        fee_usd=Decimal("0"),
        maker="0xnewtrader",
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(999, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=999.0,
    )
    await provider.on_trade(trade)
    after = provider.get_features()["skilled_traders"]
    assert before == after


async def test_refresh_updates(backend_with_skilled: PolarsBackend) -> None:
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    assert "0xalice" in provider.get_features()["skilled_traders"]

    # Refresh with empty backend → clears the set
    empty_backend = _make_backend([])
    await provider.refresh(empty_backend)
    assert provider.get_features()["skilled_traders"] == frozenset()


async def test_get_features_before_compute() -> None:
    provider = SkilledTradersProvider(min_trades=5)
    assert provider.get_features()["skilled_traders"] == frozenset()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skilled_traders_provider.py -x -q`
Expected: ImportError — `providers` module not found.

**Step 3: Implement SkilledTradersProvider**

Create `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`:

```python
"""Feature providers for the consensus-copy strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class SkilledTradersProvider:
    """Computes and maintains the set of skilled trader addresses.

    A trader is "skilled" if they have at least *min_trades* distinct trades
    across different markets. The skilled set is recomputed periodically via
    ``refresh()``; ``on_trade()`` is a no-op because the set changes slowly.

    Parameters
    ----------
    min_trades:
        Minimum number of unique market trades to qualify as skilled.
    """

    name: str = "skilled_traders"

    def __init__(self, min_trades: int = 50) -> None:
        self._min_trades = min_trades
        self._skilled: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute the skilled traders set from historical trades."""
        trades = await backend.query_trades()

        if trades.is_empty():
            self._skilled = frozenset()
            logger.info("skilled_traders.compute", count=0)
            return

        import polars as pl

        # Count distinct markets per trader
        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_trades)
            .collect()
        )

        self._skilled = frozenset(trader_counts["maker"].to_list())
        logger.info("skilled_traders.compute", count=len(self._skilled))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — skilled set is refreshed periodically, not per-trade."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the skilled set."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Return ``{"skilled_traders": frozenset[str]}``."""
        return {"skilled_traders": self._skilled}
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_skilled_traders_provider.py -x -q`
Expected: All 7 pass.

**Step 5: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`
Expected: Success.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py \
        tests/test_skilled_traders_provider.py
git commit -m "feat(strategies): add SkilledTradersProvider — first FeatureProvider implementation"
```

---

### Task 5: PaperExecutor

**Files:**
- Create: `src/polymarket_pipeline/strategies/execution/paper.py`
- Create: `tests/test_paper_executor.py`

**Context:** `PaperExecutor` is similar to `SimulatedExecutor` but checks orderbook from context for more realistic pricing. In paper-dev mode (no Redis), orderbook is `None` — falls back to `max_price`/`default_price`. The main difference from `SimulatedExecutor`: it receives a `StrategyContext` at construction and checks orderbook per-intent.

**Step 1: Write the failing tests**

Create `tests/test_paper_executor.py`:

```python
"""Tests for PaperExecutor."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.protocol import Executor
from polymarket_pipeline.strategies.types import (
    FillStatus,
    OrderbookSnapshot,
    TradeIntent,
)


def _make_intent(
    *,
    side: str = "BUY",
    max_price: float | None = 0.65,
    size_usd: float = 100.0,
    condition_id: str = "0xabc",
) -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id=condition_id,
        side=side,
        outcome="YES",
        size_usd=size_usd,
        urgency="immediate",
        max_price=max_price,
        reason="test",
        signal_time=1_700_000_000.0,
    )


@pytest.fixture
def ctx() -> InMemoryContext:
    return InMemoryContext()


@pytest.fixture
def executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(ctx=ctx, fee_pct=0.02, default_price=0.50)


async def test_satisfies_executor_protocol(executor: PaperExecutor) -> None:
    assert isinstance(executor, Executor)


async def test_fills_at_max_price_when_no_orderbook(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.65))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.65
    assert fill.filled_size_usd == 100.0


async def test_fills_at_default_when_no_ob_no_max_price(
    executor: PaperExecutor,
) -> None:
    fill = await executor.execute(_make_intent(max_price=None))
    assert fill.filled_price == 0.50


async def test_fills_at_best_ask_for_buy_when_ob_available(
    ctx: InMemoryContext,
) -> None:
    ctx.set_orderbook(
        "0xabc",
        OrderbookSnapshot(
            condition_id="0xabc",
            best_bid=0.58,
            best_ask=0.62,
            bid_depth=1000.0,
            ask_depth=500.0,
            timestamp=1_700_000_000.0,
        ),
    )
    executor = PaperExecutor(ctx=ctx, fee_pct=0.02)
    fill = await executor.execute(_make_intent(side="BUY", condition_id="0xabc"))
    assert fill.filled_price == 0.62


async def test_fills_at_best_bid_for_sell_when_ob_available(
    ctx: InMemoryContext,
) -> None:
    ctx.set_orderbook(
        "0xabc",
        OrderbookSnapshot(
            condition_id="0xabc",
            best_bid=0.58,
            best_ask=0.62,
            bid_depth=1000.0,
            ask_depth=500.0,
            timestamp=1_700_000_000.0,
        ),
    )
    executor = PaperExecutor(ctx=ctx, fee_pct=0.02)
    fill = await executor.execute(_make_intent(side="SELL", condition_id="0xabc"))
    assert fill.filled_price == 0.58


async def test_fee_calculation(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.65, size_usd=100.0))
    expected = 0.02 * min(0.65, 1.0 - 0.65) * 100.0
    assert fill.fee_usd == pytest.approx(expected)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paper_executor.py -x -q`
Expected: ImportError — `paper` module not found.

**Step 3: Add `set_orderbook` to InMemoryContext**

First, `InMemoryContext` needs `_orderbooks` dict and `set_orderbook()`. Modify `src/polymarket_pipeline/strategies/context/memory.py`:

Update `__slots__`:
```python
    __slots__ = ("_features", "_markets", "_orderbooks", "_positions", "_time")
```

In `__init__`, add:
```python
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
```

Change `get_orderbook`:
```python
    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        """Return the latest order-book snapshot, or ``None``."""
        return self._orderbooks.get(condition_id)
```

Add mutation method:
```python
    def set_orderbook(self, condition_id: str, ob: OrderbookSnapshot) -> None:
        """Store order-book snapshot for *condition_id*."""
        self._orderbooks[condition_id] = ob
```

**Step 4: Implement PaperExecutor**

Create `src/polymarket_pipeline/strategies/execution/paper.py`:

```python
"""PaperExecutor — paper-trading executor with orderbook-aware pricing."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger(__name__)


class PaperExecutor:
    """Executor that simulates fills with market-aware pricing.

    Checks orderbook from context when available. Falls back to
    ``max_price`` or ``default_price`` otherwise.

    Parameters
    ----------
    ctx:
        Strategy context for orderbook lookups.
    fee_pct:
        Fee as fraction of ``min(price, 1-price) * size_usd``.
    default_price:
        Fallback price when neither orderbook nor max_price is available.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.02,
        default_price: float = 0.50,
    ) -> None:
        self._ctx = ctx
        self._fee_pct = fee_pct
        self._default_price = default_price

    async def execute(self, intent: TradeIntent) -> Fill:
        """Simulate a fill using orderbook or fallback pricing."""
        ob = await self._ctx.get_orderbook(intent.condition_id)

        if ob is not None:
            price = ob.best_ask if intent.side == "BUY" else ob.best_bid
        elif intent.max_price is not None:
            price = intent.max_price
        else:
            price = self._default_price

        fee = self._fee_pct * min(price, 1.0 - price) * intent.size_usd
        intent_id = uuid.uuid4().hex[:12]

        fill = Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=price,
            filled_size_usd=intent.size_usd,
            fee_usd=fee,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )

        logger.info(
            "paper_fill",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            price=price,
            source="orderbook" if ob is not None else "fallback",
        )

        return fill
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_paper_executor.py -x -q && uv run pytest tests/test_strategy_context_memory.py -x -q`
Expected: All pass.

**Step 6: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/execution/paper.py src/polymarket_pipeline/strategies/context/memory.py`
Expected: Success.

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/strategies/execution/paper.py \
        src/polymarket_pipeline/strategies/context/memory.py \
        tests/test_paper_executor.py
git commit -m "feat(strategies): add PaperExecutor with orderbook-aware pricing + set_orderbook on InMemoryContext"
```

---

### Task 6: Extend Config Loader with ProviderConfig and Feature Dependencies

**Files:**
- Modify: `src/polymarket_pipeline/strategies/config.py`
- Modify: `tests/test_strategy_config.py`

**Context:** The TOML config needs two extensions: (1) `[provider.<name>]` sections for feature providers, (2) `features` list on strategies declaring provider dependencies. We add `ProviderConfig` dataclass and `load_provider_configs()`, and extend `StrategyConfig` with an optional `features` field.

**Step 1: Write the failing tests**

Append to `tests/test_strategy_config.py`:

```python
# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------


def test_load_provider_configs(tmp_path: Path) -> None:
    from polymarket_pipeline.strategies.config import load_provider_configs

    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
[provider.skilled_traders.params]
min_trades = 50
min_pnl = 100.0

[provider.disabled_one]
enabled = false
refresh_interval_s = 60
""")
    configs = load_provider_configs(config_file)
    assert "skilled_traders" in configs
    assert "disabled_one" in configs
    assert configs["skilled_traders"].enabled is True
    assert configs["skilled_traders"].refresh_interval_s == 900.0
    assert configs["skilled_traders"].params == {"min_trades": 50, "min_pnl": 100.0}


def test_load_provider_configs_enabled_only(tmp_path: Path) -> None:
    from polymarket_pipeline.strategies.config import load_provider_configs

    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[provider.active]
enabled = true
refresh_interval_s = 300

[provider.inactive]
enabled = false
refresh_interval_s = 600
""")
    configs = load_provider_configs(config_file, enabled_only=True)
    assert "active" in configs
    assert "inactive" not in configs


# ---------------------------------------------------------------------------
# Strategy features field
# ---------------------------------------------------------------------------


def test_strategy_config_with_features(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[strategy.my_strat]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 10
cooldown_s = 300
features = ["skilled_traders", "mvf_bands"]
""")
    configs = load_strategy_configs(config_file)
    assert configs["my_strat"].features == ["skilled_traders", "mvf_bands"]


def test_strategy_config_features_defaults_empty(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[strategy.basic]
enabled = true
mode = "replay"
capital_usd = 500.0
max_position_usd = 50.0
max_open_positions = 5
cooldown_s = 60
""")
    configs = load_strategy_configs(config_file)
    assert configs["basic"].features == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_config.py::test_load_provider_configs -x -q`
Expected: ImportError — `load_provider_configs` not found.

**Step 3: Implement ProviderConfig and extend StrategyConfig**

Modify `src/polymarket_pipeline/strategies/config.py`:

Add `ProviderConfig` dataclass:

```python
@dataclass(frozen=True)
class ProviderConfig:
    """Immutable configuration for a single feature provider."""

    enabled: bool
    refresh_interval_s: float
    params: dict[str, Any] = field(default_factory=dict)
```

Add `features` field to `StrategyConfig`:

```python
@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a single strategy."""

    enabled: bool
    mode: ExecutionMode
    capital_usd: float
    max_position_usd: float
    max_open_positions: int
    cooldown_s: int
    params: dict[str, Any] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
```

In `load_strategy_configs`, extract `features` before building config:

```python
        features: list[str] = section.pop("features", [])
        # ... existing code ...
        cfg = StrategyConfig(
            ...,
            params=params,
            features=features,
        )
```

Add `load_provider_configs`:

```python
def load_provider_configs(
    path: Path,
    *,
    enabled_only: bool = False,
) -> dict[str, ProviderConfig]:
    """Parse a TOML file and return a mapping of provider name to config.

    Provider sections live under ``[provider.<name>]``.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    providers: dict[str, ProviderConfig] = {}
    for name, section in raw.get("provider", {}).items():
        params: dict[str, Any] = dict(section.pop("params", {}))

        cfg = ProviderConfig(
            enabled=section["enabled"],
            refresh_interval_s=float(section.get("refresh_interval_s", 900)),
            params=params,
        )

        if enabled_only and not cfg.enabled:
            continue

        providers[name] = cfg

    return providers
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_strategy_config.py -x -q`
Expected: All pass (existing 11 + 4 new = 15).

**Step 5: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/config.py`
Expected: Success.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/config.py tests/test_strategy_config.py
git commit -m "feat(strategies): add ProviderConfig + features dependency field to StrategyConfig"
```

---

### Task 7: LiveRunner — Kafka Consumer Dispatching to Providers and Strategies

**Files:**
- Create: `src/polymarket_pipeline/strategies/runners/live.py`
- Create: `tests/test_runner_live.py`

**Context:** This is the core of Phase 2. The `LiveRunner` dispatches trades to providers (updating features) then strategies (generating intents), with timing enforcement. It manages timer and refresh loops. For testability, the Kafka subscription is injected — tests pass trades directly via `_handle_trade()`.

**Step 1: Write the failing tests**

Create `tests/test_runner_live.py`:

```python
"""Tests for LiveRunner — dispatches trades to providers then strategies."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.runners.live import LiveRunner
from polymarket_pipeline.strategies.types import TradeIntent


def _trade(maker: str = "0xalice", ts: int = 1000) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{maker}:{ts}",
        condition_id="0xcond",
        asset_id="asset_1",
        side=Side.BUY,
        price=Decimal("0.60"),
        size=Decimal("100"),
        amount_usd=Decimal("60"),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


class RecordingProvider:
    """FeatureProvider that records calls."""

    name = "recorder"

    def __init__(self) -> None:
        self.compute_calls: int = 0
        self.on_trade_calls: list[str] = []
        self.refresh_calls: int = 0
        self._value: int = 0

    async def compute(self, backend: Any) -> None:
        self.compute_calls += 1
        self._value = 42

    async def on_trade(self, trade: NormalizedTrade) -> None:
        self.on_trade_calls.append(trade.trade_id)

    async def refresh(self, backend: Any) -> None:
        self.refresh_calls += 1

    def get_features(self) -> dict[str, Any]:
        return {"recorder_value": self._value}


class RecordingStrategy:
    """Strategy that records on_trade calls and optionally emits intents."""

    name = "recorder_strategy"

    def __init__(self, *, emit: bool = False) -> None:
        self.trades_seen: list[str] = []
        self._emit = emit

    async def on_trade(
        self, trade: NormalizedTrade, ctx: Any
    ) -> list[TradeIntent] | None:
        self.trades_seen.append(trade.trade_id)
        if self._emit:
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="patient",
                    max_price=0.60,
                    reason="test",
                    signal_time=trade.published_at,
                )
            ]
        return None

    async def on_market_update(self, update: Any, ctx: Any) -> None:
        return None

    async def on_timer(self, now: float, ctx: Any) -> list[TradeIntent] | None:
        return None


@pytest.fixture
def ctx() -> InMemoryContext:
    return InMemoryContext()


@pytest.fixture
def gateway() -> ExecutionGateway:
    return ExecutionGateway(executor=SimulatedExecutor())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_handle_trade_dispatches_to_provider(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    strategy = RecordingStrategy()
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=10,
        cooldown_s=300,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=None,  # type: ignore[arg-type]
    )

    await runner._handle_trade(_trade())
    assert len(provider.on_trade_calls) == 1


async def test_handle_trade_dispatches_to_strategy(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    strategy = RecordingStrategy()
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=10,
        cooldown_s=300,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=None,  # type: ignore[arg-type]
    )

    await runner._handle_trade(_trade())
    assert len(strategy.trades_seen) == 1


async def test_providers_run_before_strategies(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    """After handle_trade, context should have provider features."""
    provider = RecordingProvider()
    provider._value = 42  # Simulate compute

    class FeatureCheckStrategy:
        name = "checker"

        def __init__(self) -> None:
            self.feature_value: Any = None

        async def on_trade(
            self, trade: NormalizedTrade, ctx: InMemoryContext
        ) -> list[TradeIntent] | None:
            self.feature_value = await ctx.get_feature("recorder_value")
            return None

        async def on_market_update(self, update: Any, ctx: Any) -> None:
            return None

        async def on_timer(self, now: float, ctx: Any) -> None:
            return None

    strategy = FeatureCheckStrategy()
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=10,
        cooldown_s=300,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],  # type: ignore[list-item]
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=None,  # type: ignore[arg-type]
    )

    await runner._handle_trade(_trade())
    assert strategy.feature_value == 42


async def test_intents_submitted_to_gateway(
    ctx: InMemoryContext,
) -> None:
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    strategy = RecordingStrategy(emit=True)
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=10,
        cooldown_s=300,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=None,  # type: ignore[arg-type]
    )

    await runner._handle_trade(_trade())
    # Strategy emits intent → gateway processes it
    assert len(strategy.trades_seen) == 1


async def test_initialize_calls_provider_compute(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
    from polymarket_pipeline.strategies.types import ExecutionMode

    import polars as pl

    backend = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())

    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )

    await runner.initialize()
    assert provider.compute_calls == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner_live.py -x -q`
Expected: ImportError — `runners.live` not found.

**Step 3: Implement LiveRunner**

Create `src/polymarket_pipeline/strategies/runners/live.py`:

```python
"""LiveRunner — Kafka consumer dispatching trades to providers then strategies.

Connects to the existing trades.raw topic, runs FeatureProviders (hot path update),
then dispatches to strategies. Manages timer and refresh background loops.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.protocol import (
        FeatureBackend,
        FeatureProvider,
        Strategy,
    )

logger = structlog.get_logger(__name__)


class LiveRunner:
    """Dispatches trades from Kafka to feature providers then strategies.

    Parameters
    ----------
    strategies:
        List of (strategy, config) tuples to run.
    providers:
        Feature providers that update context before strategies run.
    gateway:
        Execution gateway for submitting trade intents.
    ctx:
        Strategy context (InMemoryContext for paper-dev).
    backend:
        Feature backend for provider compute/refresh calls.
    timer_interval_s:
        Seconds between strategy on_timer() calls.
    refresh_interval_s:
        Seconds between provider refresh() calls.
    hot_path_warn_ms:
        Threshold in milliseconds — log warning if on_trade exceeds this.
    """

    def __init__(
        self,
        strategies: list[tuple[Strategy, StrategyConfig]],
        providers: list[FeatureProvider],
        gateway: ExecutionGateway,
        ctx: InMemoryContext,
        backend: FeatureBackend,
        *,
        timer_interval_s: float = 60.0,
        refresh_interval_s: float = 900.0,
        hot_path_warn_ms: float = 5.0,
    ) -> None:
        self.strategies = strategies
        self.providers = providers
        self.gateway = gateway
        self.ctx = ctx
        self.backend = backend
        self.timer_interval_s = timer_interval_s
        self.refresh_interval_s = refresh_interval_s
        self.hot_path_warn_ms = hot_path_warn_ms
        self._tasks: list[asyncio.Task[Any]] = []
        self._trades_processed: int = 0
        self._intents_submitted: int = 0

    async def initialize(self) -> None:
        """Run provider compute() at startup."""
        for provider in self.providers:
            await provider.compute(self.backend)
            self.ctx.update_features(provider.get_features())
            logger.info("provider.initialized", provider=provider.name)

    async def _handle_trade(self, trade: NormalizedTrade) -> None:
        """Hot path: dispatch trade to providers then strategies."""
        # 1. Providers first — update features
        for provider in self.providers:
            t0 = time.monotonic()
            await provider.on_trade(trade)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                logger.warning(
                    "provider.slow_on_trade",
                    provider=provider.name,
                    elapsed_ms=round(elapsed_ms, 2),
                )

        # 2. Inject features into context
        for provider in self.providers:
            self.ctx.update_features(provider.get_features())

        # 3. Update context time
        self.ctx.set_time(trade.published_at)

        # 4. Strategies — read updated context
        for strategy, _config in self.strategies:
            t0 = time.monotonic()
            intents = await strategy.on_trade(trade, self.ctx)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                logger.warning(
                    "strategy.slow_on_trade",
                    strategy=strategy.name,
                    elapsed_ms=round(elapsed_ms, 2),
                )

            if intents:
                for intent in intents:
                    await self.gateway.submit(intent)
                    self._intents_submitted += 1

        self._trades_processed += 1

    async def _timer_loop(self) -> None:
        """Periodic timer callbacks for strategies."""
        while True:
            await asyncio.sleep(self.timer_interval_s)
            now = time.time()
            for strategy, _config in self.strategies:
                intents = await strategy.on_timer(now, self.ctx)
                if intents:
                    for intent in intents:
                        await self.gateway.submit(intent)
                        self._intents_submitted += 1

    async def _refresh_loop(self) -> None:
        """Periodic provider refresh (expensive recomputation)."""
        while True:
            await asyncio.sleep(self.refresh_interval_s)
            for provider in self.providers:
                logger.info("provider.refresh_start", provider=provider.name)
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
                logger.info("provider.refresh_done", provider=provider.name)

    async def start_background_loops(self) -> None:
        """Start timer and refresh loops as background tasks."""
        self._tasks.append(asyncio.create_task(self._timer_loop()))
        self._tasks.append(asyncio.create_task(self._refresh_loop()))

    async def stop(self) -> None:
        """Cancel background tasks."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info(
            "live_runner.stopped",
            trades_processed=self._trades_processed,
            intents_submitted=self._intents_submitted,
        )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_runner_live.py -x -q`
Expected: All 5 pass.

**Step 5: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/runners/live.py`
Expected: Success.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/live.py tests/test_runner_live.py
git commit -m "feat(strategies): add LiveRunner — dispatches trades to providers then strategies with hot-path timing"
```

---

### Task 8: CLI Entry Point — `pm-strategy run`

**Files:**
- Create: `src/polymarket_pipeline/cli/strategy.py`
- Modify: `pyproject.toml` (add `pm-strategy` entry point)
- Create: `tests/test_cli_strategy.py`

**Context:** Typer CLI that loads TOML config, creates strategies via registry, assembles LiveRunner with appropriate context/executor/backend, and starts consuming from Kafka. Follows the same pattern as `cli/explore.py`. For testability, we test the config loading and assembly logic — not Kafka connectivity.

**Step 1: Write the failing tests**

Create `tests/test_cli_strategy.py`:

```python
"""Tests for pm-strategy CLI."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_cli_module_imports() -> None:
    """CLI module should be importable."""
    from polymarket_pipeline.cli.strategy import app  # noqa: F401


def test_cli_build_runner_from_config(tmp_path: Path) -> None:
    """Test that _build_runner assembles the runner correctly from TOML."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
[provider.skilled_traders.params]
min_trades = 5

[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["skilled_traders"]
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    runner = _build_runner(config_file)
    assert len(runner.strategies) == 1
    assert len(runner.providers) == 1
    assert runner.providers[0].name == "skilled_traders"


def test_cli_build_runner_validates_features(tmp_path: Path) -> None:
    """Should raise if strategy declares a feature that isn't configured."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["nonexistent_provider"]
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    with pytest.raises(ValueError, match="nonexistent_provider"):
        _build_runner(config_file)


def test_cli_build_runner_only_filter(tmp_path: Path) -> None:
    """--only should filter to a single strategy."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0

[strategy.other_strat]
enabled = true
mode = "paper_dev"
capital_usd = 500.0
max_position_usd = 50.0
max_open_positions = 5
cooldown_s = 60
[strategy.other_strat.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    runner = _build_runner(config_file, only="consensus_copy")
    assert len(runner.strategies) == 1
    assert runner.strategies[0][0].name == "consensus_copy"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_strategy.py -x -q`
Expected: ImportError.

**Step 3: Implement the CLI**

Create `src/polymarket_pipeline/cli/strategy.py`:

```python
"""CLI entry point for running strategies against live Kafka feed.

Usage:
    uv run pm-strategy run --config strategies.toml
    uv run pm-strategy run --config strategies.toml --only consensus_copy
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer

from polymarket_pipeline.strategies.config import (
    StrategyConfig,
    load_provider_configs,
    load_strategy_configs,
)
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner
from polymarket_pipeline.strategies.types import ExecutionMode

logger = structlog.get_logger(__name__)

app = typer.Typer(name="pm-strategy", help="Strategy execution CLI.")


# ---------------------------------------------------------------------------
# Provider registry (manual for now — only SkilledTradersProvider)
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, type] = {}


def _register_providers() -> None:
    """Register known provider classes."""
    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )

    _PROVIDER_REGISTRY["skilled_traders"] = SkilledTradersProvider


# ---------------------------------------------------------------------------
# Strategy registry setup
# ---------------------------------------------------------------------------


def _register_strategies() -> None:
    """Register known strategy classes into the global registry."""
    from polymarket_pipeline.strategies.registry import StrategyRegistry

    from polymarket_pipeline.strategies_impl.consensus_copy.config import (
        ConsensusCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
        ConsensusCopyStrategy,
    )

    # We can't use the registry directly because ConsensusCopy takes a
    # ConsensusCopyConfig, not **kwargs. So we use a factory dict instead.
    _STRATEGY_FACTORIES["consensus_copy"] = _make_consensus_copy


_STRATEGY_FACTORIES: dict[str, object] = {}


def _make_consensus_copy(config: StrategyConfig) -> object:
    from polymarket_pipeline.strategies_impl.consensus_copy.config import (
        ConsensusCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
        ConsensusCopyStrategy,
    )

    cc_cfg = ConsensusCopyConfig(**config.params)
    return ConsensusCopyStrategy(config=cc_cfg)


# ---------------------------------------------------------------------------
# Runner assembly
# ---------------------------------------------------------------------------


def _build_runner(
    config_path: Path,
    *,
    only: str | None = None,
    log_dir: Path | None = None,
) -> LiveRunner:
    """Assemble a LiveRunner from TOML config.

    Parameters
    ----------
    config_path:
        Path to the TOML config file.
    only:
        If set, only run this strategy (by name).
    log_dir:
        Directory for intent logs. Defaults to no file logging.
    """
    _register_strategies()
    _register_providers()

    import polars as pl

    # Load configs
    strategy_configs = load_strategy_configs(config_path, enabled_only=True)
    provider_configs = load_provider_configs(config_path, enabled_only=True)

    # Filter if --only
    if only:
        strategy_configs = {
            k: v for k, v in strategy_configs.items() if k == only
        }

    # Validate feature dependencies
    for name, cfg in strategy_configs.items():
        for feat in cfg.features:
            if feat not in provider_configs and feat not in _PROVIDER_REGISTRY:
                msg = (
                    f"Strategy {name!r} requires feature provider {feat!r} "
                    f"but it is not configured"
                )
                raise ValueError(msg)

    # Create providers
    providers = []
    needed_providers = set()
    for cfg in strategy_configs.values():
        needed_providers.update(cfg.features)

    for pname in needed_providers:
        if pname in _PROVIDER_REGISTRY:
            pcfg = provider_configs.get(pname)
            params = pcfg.params if pcfg else {}
            provider = _PROVIDER_REGISTRY[pname](**params)
            providers.append(provider)

    # Create strategies
    strategies = []
    for sname, scfg in strategy_configs.items():
        if sname in _STRATEGY_FACTORIES:
            factory = _STRATEGY_FACTORIES[sname]
            strategy = factory(scfg)  # type: ignore[operator]
            strategies.append((strategy, scfg))
        else:
            logger.warning("strategy.unknown", name=sname)

    # Assemble
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    log_path = (log_dir / "intents.jsonl") if log_dir else None
    gateway = ExecutionGateway(executor=executor, log_path=log_path)
    backend = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())

    return LiveRunner(
        strategies=strategies,
        providers=providers,
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="Path to strategies TOML"),
    only: str | None = typer.Option(None, "--only", help="Run only this strategy"),
    log_dir: Path | None = typer.Option(None, "--log-dir", help="Intent log directory"),
) -> None:
    """Start strategies in paper-dev mode against live Kafka."""
    logger.info("strategy_cli.starting", config=str(config), only=only)

    runner = _build_runner(config, only=only, log_dir=log_dir)

    async def _run() -> None:
        from faststream.kafka import KafkaBroker

        from polymarket_pipeline.live.settings import Settings

        settings = Settings()
        broker = KafkaBroker(settings.redpanda_url)

        await runner.initialize()
        await runner.start_background_loops()

        @broker.subscriber("trades.raw", group_id="strategy-runner")
        async def handle_trade(msg: str) -> None:
            import json

            from polymarket_pipeline.models import NormalizedTrade

            data = json.loads(msg)
            trade = NormalizedTrade(**data)
            await runner._handle_trade(trade)

        await broker.start()
        logger.info(
            "strategy_cli.running",
            strategies=len(runner.strategies),
            providers=len(runner.providers),
        )

        try:
            await asyncio.Event().wait()  # Run forever
        except asyncio.CancelledError:
            pass
        finally:
            await runner.stop()
            await broker.close()

    asyncio.run(_run())
```

**Step 4: Add entry point to `pyproject.toml`**

Add to `[project.scripts]` section:

```toml
pm-strategy = "polymarket_pipeline.cli.strategy:app"
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_cli_strategy.py -x -q`
Expected: All 4 pass.

**Step 6: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/cli/strategy.py`
Expected: Success (may need `# type: ignore` for dynamic factory patterns).

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/cli/strategy.py tests/test_cli_strategy.py pyproject.toml
git commit -m "feat(strategies): add pm-strategy CLI — boots LiveRunner from TOML config"
```

---

### Task 9: ClickHouseBackend — FeatureBackend for Live Modes

**Files:**
- Create: `src/polymarket_pipeline/strategies/features/backend_clickhouse.py`
- Create: `tests/test_feature_backend_clickhouse.py`

**Context:** `ClickHouseBackend` implements `FeatureBackend` by running SQL against ClickHouse and returning Polars DataFrames. Uses the existing `ClickHouseSink` connection pattern. This is used in paper-prod and live modes.

Since this depends on ClickHouse Docker, the tests mock the HTTP client. We test the query assembly and result parsing.

**Step 1: Write the failing tests**

Create `tests/test_feature_backend_clickhouse.py`:

```python
"""Tests for ClickHouseBackend — FeatureBackend for live modes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from polymarket_pipeline.strategies.features.backend_clickhouse import (
    ClickHouseBackend,
)
from polymarket_pipeline.strategies.protocol import FeatureBackend


def test_satisfies_protocol() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    assert isinstance(backend, FeatureBackend)


async def test_query_custom_calls_execute() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame({"x": [1, 2, 3]})
        result = await backend.query_custom("SELECT 1 AS x")
        mock_exec.assert_called_once_with("SELECT 1 AS x")
        assert len(result) == 3


async def test_query_trades_no_filter() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame({
            "condition_id": ["0xa"],
            "maker": ["alice"],
        })
        result = await backend.query_trades()
        assert len(result) == 1
        # Should not have WHERE clause
        query = mock_exec.call_args[0][0]
        assert "WHERE" not in query


async def test_query_trades_with_filter() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame({
            "condition_id": ["0xa"],
            "maker": ["alice"],
        })
        await backend.query_trades(condition_ids=["0xa", "0xb"])
        query = mock_exec.call_args[0][0]
        assert "WHERE" in query
        assert "condition_id" in query
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_feature_backend_clickhouse.py -x -q`
Expected: ImportError.

**Step 3: Implement ClickHouseBackend**

Create `src/polymarket_pipeline/strategies/features/backend_clickhouse.py`:

```python
"""ClickHouse-backed FeatureBackend for live modes.

Runs SQL queries against ClickHouse and returns results as Polars DataFrames.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


class ClickHouseBackend:
    """FeatureBackend backed by ClickHouse for paper-prod and live modes.

    Parameters
    ----------
    host:
        ClickHouse HTTP host.
    port:
        ClickHouse HTTP port.
    database:
        ClickHouse database name.
    """

    def __init__(self, host: str, port: int, database: str) -> None:
        self._host = host
        self._port = port
        self._database = database

    async def _execute(self, query: str) -> pl.DataFrame:
        """Execute a SQL query and return results as a Polars DataFrame."""
        import httpx

        url = f"http://{self._host}:{self._port}"
        full_query = f"{query} FORMAT JSONEachRow"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=full_query,
                params={"database": self._database},
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()

        text = resp.text.strip()
        if not text:
            return pl.DataFrame()

        import json

        rows = [json.loads(line) for line in text.split("\n") if line.strip()]
        if not rows:
            return pl.DataFrame()

        return pl.DataFrame(rows)

    async def query_trades(
        self, condition_ids: list[str] | None = None
    ) -> pl.DataFrame:
        """Query trades from ClickHouse, optionally filtered."""
        query = "SELECT * FROM trades_raw FINAL"
        if condition_ids:
            ids_str = ", ".join(f"'{cid}'" for cid in condition_ids)
            query += f" WHERE condition_id IN ({ids_str})"
        return await self._execute(query)

    async def query_markets(self) -> pl.DataFrame:
        """Query market metadata from ClickHouse (via PG engine)."""
        return await self._execute("SELECT * FROM markets")

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary SQL query."""
        return await self._execute(query)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_feature_backend_clickhouse.py -x -q`
Expected: All 4 pass.

**Step 5: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/features/backend_clickhouse.py`
Expected: Success.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/features/backend_clickhouse.py \
        tests/test_feature_backend_clickhouse.py
git commit -m "feat(strategies): add ClickHouseBackend — SQL-based FeatureBackend for live modes"
```

---

### Task 10: Integration Test — Full Paper-Dev Flow

**Files:**
- Create: `tests/test_paper_dev_integration.py`

**Context:** End-to-end test: create a SkilledTradersProvider, ConsensusCopyStrategy, LiveRunner with InMemoryContext + PaperExecutor, feed trades through `_handle_trade()`, and verify the full pipeline produces intents and fills.

**Step 1: Write the integration test**

Create `tests/test_paper_dev_integration.py`:

```python
"""Integration test: full paper-dev flow with providers, strategies, and LiveRunner."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner
from polymarket_pipeline.strategies.types import ExecutionMode
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
    SkilledTradersProvider,
)
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
    ConsensusCopyStrategy,
)


CID = "0xintegration_market"
SKILLED = ["0xalice", "0xbob", "0xcharlie", "0xdave", "0xeve"]


def _trade(maker: str, ts: int, side: str = "SELL") -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"int:{maker}:{ts}",
        condition_id=CID,
        asset_id="asset_1",
        side=Side(side),
        price=Decimal("0.60"),
        size=Decimal("100"),
        amount_usd=Decimal("60"),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


@pytest.fixture
def backend() -> PolarsBackend:
    """Backend with enough trades to make all 5 traders 'skilled'."""
    trades = []
    for trader in SKILLED:
        for i in range(10):  # 10 markets each → above min_trades=5
            trades.append({
                "condition_id": f"0xhistory_{i}",
                "maker": trader,
                "side": "SELL",
                "published_at": float(i),
            })
    return PolarsBackend(
        trades=pl.DataFrame(trades),
        markets=pl.DataFrame(),
    )


async def test_full_paper_dev_flow(backend: PolarsBackend) -> None:
    """End-to-end: provider computes skilled → strategy fires on consensus."""
    # Setup
    provider = SkilledTradersProvider(min_trades=5)
    cfg = ConsensusCopyConfig(
        min_traders=3,
        agreement_pct=0.80,
        direction="NO",
        base_bet_usd=10.0,
    )
    strategy = ConsensusCopyStrategy(config=cfg)

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=20,
        cooldown_s=300,
        features=["skilled_traders"],
    )

    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    gateway = ExecutionGateway(executor=executor)

    runner = LiveRunner(
        strategies=[(strategy, strat_config)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )

    # Initialize — provider computes skilled traders
    await runner.initialize()

    # Verify provider computed
    features = provider.get_features()
    skilled = features["skilled_traders"]
    assert len(skilled) == 5
    for trader in SKILLED:
        assert trader in skilled

    # Feed trades — 3 skilled traders all SELL (= betting NO) should trigger signal
    # But wait: ConsensusCopy checks `trade.maker in self._cfg.skilled_traders`
    # The provider puts skilled_traders in context, but ConsensusCopy reads from
    # its own config.skilled_traders. So we need to wire them together.
    #
    # For this integration test, we'll create the strategy WITH the skilled_traders
    # from the provider.
    cfg_with_skilled = ConsensusCopyConfig(
        skilled_traders=skilled,
        min_traders=3,
        agreement_pct=0.80,
        direction="NO",
        base_bet_usd=10.0,
    )
    strategy_wired = ConsensusCopyStrategy(config=cfg_with_skilled)

    # Recreate runner with wired strategy
    runner = LiveRunner(
        strategies=[(strategy_wired, strat_config)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )
    await runner.initialize()

    # Feed 3 SELL trades from skilled traders → should trigger NO signal
    await runner._handle_trade(_trade("0xalice", 1000, "SELL"))
    await runner._handle_trade(_trade("0xbob", 1001, "SELL"))
    await runner._handle_trade(_trade("0xcharlie", 1002, "SELL"))

    # Verify signal fired
    assert runner._intents_submitted == 1


async def test_provider_features_visible_in_context(backend: PolarsBackend) -> None:
    """Context should expose provider features after handle_trade."""
    provider = SkilledTradersProvider(min_trades=5)
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    gateway = ExecutionGateway(executor=executor)

    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )
    await runner.initialize()

    # Feed a trade to trigger context update
    await runner._handle_trade(_trade("0xalice", 2000))

    # Check context has the feature
    skilled = await ctx.get_feature("skilled_traders")
    assert skilled is not None
    assert len(skilled) == 5
```

**Step 2: Run the integration test**

Run: `uv run pytest tests/test_paper_dev_integration.py -x -q`
Expected: All 2 pass.

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All tests pass (248 existing + ~45 new).

**Step 4: Commit**

```bash
git add tests/test_paper_dev_integration.py
git commit -m "test(strategies): add paper-dev integration test — full provider + strategy + LiveRunner flow"
```

---

### Task 11: Run Full Quality Gate (mypy + ruff + all tests)

**Files:**
- No new files — this is a validation task.

**Step 1: Run mypy on all new code**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/ src/polymarket_pipeline/strategies_impl/ src/polymarket_pipeline/cli/strategy.py`
Expected: Success with no errors.

**Step 2: Run ruff**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: No issues.

**Step 3: Fix any issues found**

If ruff or mypy report issues, fix them. Common fixes:
- Missing `from __future__ import annotations`
- `type: ignore` annotations for dynamic patterns
- Unused imports

**Step 4: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 5: Commit any fixes**

```bash
git add -u
git commit -m "chore: fix mypy/ruff issues from Phase 2 implementation"
```
