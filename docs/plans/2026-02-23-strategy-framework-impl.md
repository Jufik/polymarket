# Strategy Execution Framework Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a strategy execution framework where the same strategy code runs identically across backtest, replay, paper trading, and live execution modes.

**Architecture:** Hybrid direct-Kafka + shared services. Strategies implement `Strategy` (event-driven) and optionally `VectorizedStrategy` (batch). Mode-specific backends (`InMemoryContext` vs `RedisContext`) are injected via `StrategyContext` protocol. An `ExecutionGateway` routes `TradeIntent` to mode-appropriate executors.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen models), Polars (vectorized path), structlog, pytest-asyncio, mypy strict.

**Design doc:** `docs/plans/2026-02-23-strategy-framework-design.md`

---

## Task 1: Core types — TradeIntent, Position, MarketInfo, Fill

Foundation types used by every other module. No dependencies.

**Files:**
- Create: `src/polymarket_pipeline/strategies/__init__.py`
- Create: `src/polymarket_pipeline/strategies/types.py`
- Test: `tests/test_strategy_types.py`

**Step 1: Write the failing test**

```python
# tests/test_strategy_types.py
"""Tests for strategy framework types."""

from polymarket_pipeline.strategies.types import (
    ExecutionMode,
    Fill,
    FillStatus,
    MarketInfo,
    OrderbookSnapshot,
    Position,
    TradeIntent,
)


def test_trade_intent_creation() -> None:
    intent = TradeIntent(
        strategy="consensus_copy",
        condition_id="0xabc123",
        side="BUY",
        outcome="YES",
        size_usd=50.0,
        urgency="patient",
        max_price=0.65,
        reason="5 skilled traders agree at 80% on YES",
        signal_time=1708700000.0,
    )
    assert intent.strategy == "consensus_copy"
    assert intent.side == "BUY"
    assert intent.outcome == "YES"
    assert intent.size_usd == 50.0


def test_trade_intent_is_frozen() -> None:
    intent = TradeIntent(
        strategy="test",
        condition_id="0x1",
        side="BUY",
        outcome="YES",
        size_usd=10.0,
        urgency="immediate",
        max_price=None,
        reason="test",
        signal_time=0.0,
    )
    assert intent == intent  # frozen = hashable


def test_position_default_empty() -> None:
    pos = Position(condition_id="0xabc", strategy="test")
    assert pos.qty_yes == 0.0
    assert pos.qty_no == 0.0
    assert pos.avg_entry_price == 0.0
    assert pos.realized_pnl == 0.0
    assert pos.cost_basis == 0.0


def test_position_net_exposure() -> None:
    pos = Position(
        condition_id="0xabc",
        strategy="test",
        qty_yes=100.0,
        avg_entry_price=0.60,
        cost_basis=60.0,
    )
    assert pos.cost_basis == 60.0


def test_market_info_creation() -> None:
    info = MarketInfo(
        condition_id="0xabc",
        question="Will X happen?",
        active=True,
        yes_price=0.65,
        no_price=0.35,
    )
    assert info.condition_id == "0xabc"
    assert info.active is True


def test_orderbook_snapshot() -> None:
    snap = OrderbookSnapshot(
        condition_id="0xabc",
        best_bid=0.63,
        best_ask=0.65,
        bid_depth=1000.0,
        ask_depth=800.0,
        timestamp=1708700000.0,
    )
    assert snap.spread == pytest.approx(0.02)


def test_fill_creation() -> None:
    fill = Fill(
        intent_id="intent_001",
        strategy="test",
        condition_id="0xabc",
        side="BUY",
        outcome="YES",
        filled_price=0.64,
        filled_size_usd=50.0,
        fee_usd=0.36,
        status=FillStatus.FILLED,
        filled_at=1708700005.0,
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.64


def test_execution_mode_enum() -> None:
    assert ExecutionMode.VECTORIZED == "vectorized"
    assert ExecutionMode.REPLAY == "replay"
    assert ExecutionMode.PAPER_DEV == "paper_dev"
    assert ExecutionMode.PAPER_PROD == "paper_prod"
    assert ExecutionMode.LIVE == "live"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_types.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_pipeline.strategies'`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/__init__.py
"""Strategy execution framework."""
```

```python
# src/polymarket_pipeline/strategies/types.py
"""Core types for the strategy execution framework.

All types are frozen (immutable) Pydantic models or dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class ExecutionMode(StrEnum):
    """Strategy execution modes — determines injected backends."""

    VECTORIZED = "vectorized"
    REPLAY = "replay"
    PAPER_DEV = "paper_dev"
    PAPER_PROD = "paper_prod"
    LIVE = "live"


class FillStatus(StrEnum):
    """Execution fill status."""

    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TradeIntent:
    """What a strategy wants to do — not an order, but a signal of intent.

    The ExecutionGateway turns intents into actual orders/fills.
    """

    strategy: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    size_usd: float
    urgency: Literal["immediate", "patient"]
    max_price: float | None
    reason: str
    signal_time: float


@dataclass(frozen=True)
class Position:
    """Current position for a strategy in a specific market."""

    condition_id: str
    strategy: str
    qty_yes: float = 0.0
    qty_no: float = 0.0
    avg_entry_price: float = 0.0
    cost_basis: float = 0.0
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class MarketInfo:
    """Market metadata available to strategies."""

    condition_id: str
    question: str
    active: bool
    yes_price: float | None = None
    no_price: float | None = None
    event_id: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class OrderbookSnapshot:
    """Point-in-time orderbook state."""

    condition_id: str
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float
    timestamp: float

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class Fill:
    """Result of executing a TradeIntent."""

    intent_id: str
    strategy: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    filled_price: float
    filled_size_usd: float
    fee_usd: float
    status: FillStatus
    filled_at: float
    error: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_types.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/types.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/__init__.py \
        src/polymarket_pipeline/strategies/types.py \
        tests/test_strategy_types.py
git commit -m "feat(strategies): add core types — TradeIntent, Position, Fill"
```

---

## Task 2: Protocols — Strategy, VectorizedStrategy, StrategyContext

Defines the interfaces all strategies and contexts must satisfy.

**Files:**
- Create: `src/polymarket_pipeline/strategies/protocol.py`
- Test: `tests/test_strategy_protocol.py`

**Depends on:** Task 1 (types)

**Step 1: Write the failing test**

```python
# tests/test_strategy_protocol.py
"""Tests for strategy protocol conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from polymarket_pipeline.strategies.protocol import (
    Strategy,
    StrategyContext,
    VectorizedStrategy,
)
from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
    TradeIntent,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade


class DummyContext:
    """Minimal context that satisfies StrategyContext protocol."""

    async def get_position(self, condition_id: str) -> Position | None:
        return None

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return None

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return 0.0


class DummyStrategy:
    """Minimal strategy that satisfies both Strategy and VectorizedStrategy."""

    name: str = "dummy"

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_market_update(
        self, update: dict, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame()


def test_dummy_context_satisfies_protocol() -> None:
    ctx = DummyContext()
    assert isinstance(ctx, StrategyContext)


def test_dummy_strategy_satisfies_protocol() -> None:
    strat = DummyStrategy()
    assert isinstance(strat, Strategy)


def test_dummy_strategy_satisfies_vectorized_protocol() -> None:
    strat = DummyStrategy()
    assert isinstance(strat, VectorizedStrategy)


def test_incomplete_strategy_does_not_satisfy_protocol() -> None:
    class Incomplete:
        name: str = "bad"

    assert not isinstance(Incomplete(), Strategy)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_protocol.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/protocol.py
"""Strategy framework protocols.

These are the interfaces that strategies and execution contexts must implement.
Using Protocol (structural subtyping) so strategies don't need to inherit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import polars as pl

from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
    TradeIntent,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade


@runtime_checkable
class StrategyContext(Protocol):
    """Mode-agnostic data access layer injected into strategies.

    InMemoryContext for backtest/paper-dev, RedisContext for paper-prod/live.
    """

    async def get_position(self, condition_id: str) -> Position | None: ...
    async def get_market(self, condition_id: str) -> MarketInfo | None: ...
    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None: ...
    async def get_price(self, condition_id: str, outcome: str) -> float | None: ...
    async def now(self) -> float: ...


@runtime_checkable
class Strategy(Protocol):
    """Event-driven strategy interface — same code for backtest, paper, and live."""

    name: str

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to an incoming normalized trade. HOT PATH — keep fast."""
        ...

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to orderbook/price changes."""
        ...

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Periodic callback (e.g. every 60s for delayed-entry strategies)."""
        ...


@runtime_checkable
class VectorizedStrategy(Protocol):
    """Optional fast-path for research: Polars batch computation."""

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        """Return signal table with columns: condition_id, signal_time, side, outcome, size_usd."""
        ...


@runtime_checkable
class Executor(Protocol):
    """Executes TradeIntents — different impl per mode."""

    async def execute(self, intent: TradeIntent) -> Any:
        """Execute a trade intent. Returns Fill or logs to file depending on mode."""
        ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_protocol.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/protocol.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/protocol.py \
        tests/test_strategy_protocol.py
git commit -m "feat(strategies): add Strategy, VectorizedStrategy, StrategyContext protocols"
```

---

## Task 3: InMemoryContext — backtest + paper-dev backend

The context that strategies use in backtest and paper-dev mode. Backed by dicts, no Redis.

**Files:**
- Create: `src/polymarket_pipeline/strategies/context/__init__.py`
- Create: `src/polymarket_pipeline/strategies/context/memory.py`
- Test: `tests/test_strategy_context_memory.py`

**Depends on:** Task 1 (types), Task 2 (protocol)

**Step 1: Write the failing test**

```python
# tests/test_strategy_context_memory.py
"""Tests for InMemoryContext."""

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import MarketInfo, Position


@pytest.fixture
def ctx() -> InMemoryContext:
    return InMemoryContext()


def test_satisfies_protocol(ctx: InMemoryContext) -> None:
    assert isinstance(ctx, StrategyContext)


async def test_get_position_returns_none_by_default(ctx: InMemoryContext) -> None:
    pos = await ctx.get_position("0xabc")
    assert pos is None


async def test_set_and_get_position(ctx: InMemoryContext) -> None:
    pos = Position(condition_id="0xabc", strategy="test", qty_yes=100.0, cost_basis=65.0)
    ctx.set_position("0xabc", pos)
    result = await ctx.get_position("0xabc")
    assert result is not None
    assert result.qty_yes == 100.0


async def test_get_market_returns_none_by_default(ctx: InMemoryContext) -> None:
    result = await ctx.get_market("0xabc")
    assert result is None


async def test_set_and_get_market(ctx: InMemoryContext) -> None:
    info = MarketInfo(
        condition_id="0xabc",
        question="Will X?",
        active=True,
        yes_price=0.60,
    )
    ctx.set_market("0xabc", info)
    result = await ctx.get_market("0xabc")
    assert result is not None
    assert result.yes_price == 0.60


async def test_get_price_from_market(ctx: InMemoryContext) -> None:
    ctx.set_market(
        "0xabc",
        MarketInfo(condition_id="0xabc", question="?", active=True, yes_price=0.65, no_price=0.35),
    )
    assert await ctx.get_price("0xabc", "YES") == pytest.approx(0.65)
    assert await ctx.get_price("0xabc", "NO") == pytest.approx(0.35)


async def test_get_price_missing_market(ctx: InMemoryContext) -> None:
    assert await ctx.get_price("0xmissing", "YES") is None


async def test_now_returns_simulated_time(ctx: InMemoryContext) -> None:
    assert await ctx.now() == 0.0
    ctx.set_time(1708700000.0)
    assert await ctx.now() == 1708700000.0


async def test_get_orderbook_returns_none(ctx: InMemoryContext) -> None:
    """InMemoryContext has no orderbook data — always None."""
    assert await ctx.get_orderbook("0xabc") is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_context_memory.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/context/__init__.py
"""Strategy context backends."""
```

```python
# src/polymarket_pipeline/strategies/context/memory.py
"""In-memory strategy context for backtest and paper-dev modes.

No external dependencies (no Redis, no Kafka). Deterministic for replay.
"""

from __future__ import annotations

from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
)


class InMemoryContext:
    """Dict-backed StrategyContext for backtest and paper-dev modes."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._markets: dict[str, MarketInfo] = {}
        self._time: float = 0.0

    # --- StrategyContext protocol methods ---

    async def get_position(self, condition_id: str) -> Position | None:
        return self._positions.get(condition_id)

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return self._markets.get(condition_id)

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        return None  # no orderbook in backtest mode

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        market = self._markets.get(condition_id)
        if market is None:
            return None
        if outcome == "YES":
            return market.yes_price
        return market.no_price

    async def now(self) -> float:
        return self._time

    # --- Mutation methods (used by runners, not by strategies) ---

    def set_position(self, condition_id: str, position: Position) -> None:
        self._positions[condition_id] = position

    def set_market(self, condition_id: str, market: MarketInfo) -> None:
        self._markets[condition_id] = market

    def set_time(self, t: float) -> None:
        self._time = t
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_context_memory.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/context/memory.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/context/__init__.py \
        src/polymarket_pipeline/strategies/context/memory.py \
        tests/test_strategy_context_memory.py
git commit -m "feat(strategies): add InMemoryContext for backtest + paper-dev"
```

---

## Task 4: ExecutionGateway + SimulatedExecutor

Routes TradeIntents to the active executor. SimulatedExecutor fills instantly at signal price (for backtesting).

**Files:**
- Create: `src/polymarket_pipeline/strategies/execution/__init__.py`
- Create: `src/polymarket_pipeline/strategies/execution/gateway.py`
- Create: `src/polymarket_pipeline/strategies/execution/simulated.py`
- Test: `tests/test_strategy_execution.py`

**Depends on:** Task 1 (types), Task 2 (protocol)

**Step 1: Write the failing test**

```python
# tests/test_strategy_execution.py
"""Tests for ExecutionGateway and SimulatedExecutor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.types import FillStatus, TradeIntent


def _make_intent(**overrides: object) -> TradeIntent:
    defaults = {
        "strategy": "test_strat",
        "condition_id": "0xabc",
        "side": "BUY",
        "outcome": "YES",
        "size_usd": 50.0,
        "urgency": "patient",
        "max_price": 0.65,
        "reason": "test signal",
        "signal_time": 1708700000.0,
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


async def test_simulated_executor_fills_at_max_price() -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    intent = _make_intent(max_price=0.65)
    fill = await executor.execute(intent)
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.65
    assert fill.filled_size_usd == 50.0


async def test_simulated_executor_fills_at_midpoint_when_no_max_price() -> None:
    executor = SimulatedExecutor(fee_pct=0.02, default_price=0.50)
    intent = _make_intent(max_price=None)
    fill = await executor.execute(intent)
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.50


async def test_simulated_executor_computes_fee() -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    intent = _make_intent(max_price=0.40, size_usd=100.0)
    fill = await executor.execute(intent)
    # fee = 0.02 * min(0.40, 0.60) * 100.0 = 0.80
    assert fill.fee_usd == pytest.approx(0.80)


async def test_gateway_routes_to_executor_and_logs(tmp_path: Path) -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    log_file = tmp_path / "intents.jsonl"
    gateway = ExecutionGateway(executor=executor, log_path=log_file)

    intent = _make_intent()
    fill = await gateway.submit(intent)

    assert fill.status == FillStatus.FILLED

    # Verify intent was logged
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["strategy"] == "test_strat"
    assert logged["condition_id"] == "0xabc"


async def test_gateway_logs_multiple_intents(tmp_path: Path) -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    log_file = tmp_path / "intents.jsonl"
    gateway = ExecutionGateway(executor=executor, log_path=log_file)

    await gateway.submit(_make_intent(condition_id="0x1"))
    await gateway.submit(_make_intent(condition_id="0x2"))
    await gateway.submit(_make_intent(condition_id="0x3"))

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_execution.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/execution/__init__.py
"""Execution backends for the strategy framework."""
```

```python
# src/polymarket_pipeline/strategies/execution/simulated.py
"""Simulated executor for backtesting — instant fill at signal price."""

from __future__ import annotations

import time
import uuid

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


class SimulatedExecutor:
    """Fills immediately at max_price (or default_price if None). No slippage."""

    def __init__(self, fee_pct: float = 0.02, default_price: float = 0.50) -> None:
        self._fee_pct = fee_pct
        self._default_price = default_price

    async def execute(self, intent: TradeIntent) -> Fill:
        price = intent.max_price if intent.max_price is not None else self._default_price
        fee = self._fee_pct * min(price, 1.0 - price) * intent.size_usd
        return Fill(
            intent_id=uuid.uuid4().hex[:12],
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
```

```python
# src/polymarket_pipeline/strategies/execution/gateway.py
"""Execution gateway — routes TradeIntents to the active executor and logs everything."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.types import Fill

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.protocol import Executor
    from polymarket_pipeline.strategies.types import TradeIntent

logger = structlog.get_logger()


class ExecutionGateway:
    """Routes TradeIntents to an Executor and logs every intent to a JSONL file."""

    def __init__(self, executor: Executor, log_path: Path | None = None) -> None:
        self._executor = executor
        self._log_path = log_path

    async def submit(self, intent: TradeIntent) -> Fill:
        self._log_intent(intent)
        fill = await self._executor.execute(intent)
        return fill

    def _log_intent(self, intent: TradeIntent) -> None:
        if self._log_path is None:
            return
        record = asdict(intent)
        with self._log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_execution.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/execution/`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/execution/ \
        tests/test_strategy_execution.py
git commit -m "feat(strategies): add ExecutionGateway + SimulatedExecutor"
```

---

## Task 5: Strategy config + registry

TOML-based config and strategy discovery/instantiation.

**Files:**
- Create: `src/polymarket_pipeline/strategies/config.py`
- Create: `src/polymarket_pipeline/strategies/registry.py`
- Test: `tests/test_strategy_config.py`

**Depends on:** Task 1 (types)

**Step 1: Write the failing test**

```python
# tests/test_strategy_config.py
"""Tests for strategy config loading and registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from polymarket_pipeline.strategies.config import StrategyConfig, load_strategy_configs
from polymarket_pipeline.strategies.registry import StrategyRegistry
from polymarket_pipeline.strategies.types import ExecutionMode


SAMPLE_TOML = """\
[strategy.consensus_copy]
enabled = true
mode = "replay"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300

[strategy.consensus_copy.params]
min_traders = 5
agreement_pct = 0.80
direction = "NO"
mvf_band = "pure_taker"
delay_s = 60

[strategy.disabled_strat]
enabled = false
mode = "vectorized"
capital_usd = 500.0
max_position_usd = 50.0
max_open_positions = 10
cooldown_s = 60
"""


def test_load_configs_from_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "strategies.toml"
    config_file.write_text(SAMPLE_TOML)
    configs = load_strategy_configs(config_file)
    assert len(configs) == 2
    assert "consensus_copy" in configs
    assert configs["consensus_copy"].mode == ExecutionMode.REPLAY
    assert configs["consensus_copy"].capital_usd == 1000.0
    assert configs["consensus_copy"].params["min_traders"] == 5


def test_load_configs_filters_enabled(tmp_path: Path) -> None:
    config_file = tmp_path / "strategies.toml"
    config_file.write_text(SAMPLE_TOML)
    configs = load_strategy_configs(config_file, enabled_only=True)
    assert len(configs) == 1
    assert "consensus_copy" in configs


def test_registry_register_and_create() -> None:
    registry = StrategyRegistry()

    class FakeStrat:
        name = "fake"

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    registry.register("fake", FakeStrat)
    config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=100.0,
        max_position_usd=10.0,
        max_open_positions=5,
        cooldown_s=60,
        params={"x": 1},
    )
    instance = registry.create("fake", config)
    assert instance.name == "fake"
    assert instance.kwargs == {"x": 1}


def test_registry_unknown_strategy_raises() -> None:
    registry = StrategyRegistry()
    config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=100.0,
        max_position_usd=10.0,
        max_open_positions=5,
        cooldown_s=60,
    )
    with pytest.raises(KeyError, match="unknown_strat"):
        registry.create("unknown_strat", config)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_config.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/config.py
"""Strategy configuration — TOML-based, per-strategy settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polymarket_pipeline.strategies.types import ExecutionMode


@dataclass(frozen=True)
class StrategyConfig:
    """Per-strategy configuration."""

    enabled: bool
    mode: ExecutionMode
    capital_usd: float
    max_position_usd: float
    max_open_positions: int
    cooldown_s: int
    params: dict[str, Any] = field(default_factory=dict)


def load_strategy_configs(
    path: Path,
    *,
    enabled_only: bool = False,
) -> dict[str, StrategyConfig]:
    """Load strategy configs from a TOML file.

    Expected format:
        [strategy.<name>]
        enabled = true
        mode = "replay"
        ...
        [strategy.<name>.params]
        key = value
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)

    configs: dict[str, StrategyConfig] = {}
    for name, section in raw.get("strategy", {}).items():
        params = section.pop("params", {})
        config = StrategyConfig(
            enabled=section["enabled"],
            mode=ExecutionMode(section["mode"]),
            capital_usd=section["capital_usd"],
            max_position_usd=section["max_position_usd"],
            max_open_positions=section["max_open_positions"],
            cooldown_s=section["cooldown_s"],
            params=params,
        )
        if enabled_only and not config.enabled:
            continue
        configs[name] = config

    return configs
```

```python
# src/polymarket_pipeline/strategies/registry.py
"""Strategy registry — discover and instantiate strategies by name."""

from __future__ import annotations

from typing import Any

from polymarket_pipeline.strategies.config import StrategyConfig


class StrategyRegistry:
    """Maps strategy names to their classes. Creates instances from config."""

    def __init__(self) -> None:
        self._registry: dict[str, type] = {}

    def register(self, name: str, cls: type) -> None:
        self._registry[name] = cls

    def create(self, name: str, config: StrategyConfig) -> Any:
        if name not in self._registry:
            msg = f"Unknown strategy: {name!r}. Registered: {list(self._registry)}"
            raise KeyError(msg)
        cls = self._registry[name]
        return cls(**config.params)

    def list_registered(self) -> list[str]:
        return list(self._registry.keys())
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_config.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/config.py src/polymarket_pipeline/strategies/registry.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/config.py \
        src/polymarket_pipeline/strategies/registry.py \
        tests/test_strategy_config.py
git commit -m "feat(strategies): add TOML config loader + strategy registry"
```

---

## Task 6: BacktestRunner — event-driven replay

Feeds historical NormalizedTrades through `strategy.on_trade()` in timestamp order.

**Files:**
- Create: `src/polymarket_pipeline/strategies/runners/__init__.py`
- Create: `src/polymarket_pipeline/strategies/runners/backtest.py`
- Test: `tests/test_strategy_runner_backtest.py`

**Depends on:** Tasks 1-4

**Step 1: Write the failing test**

```python
# tests/test_strategy_runner_backtest.py
"""Tests for the event-driven backtest runner."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
from polymarket_pipeline.strategies.types import TradeIntent


def _make_trade(condition_id: str, price: float, ts: float) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{condition_id}:{ts}",
        condition_id=condition_id,
        asset_id="asset_1",
        side=Side.BUY,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


class CountingStrategy:
    """Strategy that counts on_trade calls and emits an intent every N trades."""

    name = "counting"

    def __init__(self, emit_every: int = 3) -> None:
        self.call_count = 0
        self._emit_every = emit_every

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        self.call_count += 1
        if self.call_count % self._emit_every == 0:
            return [
                TradeIntent(
                    strategy="counting",
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="patient",
                    max_price=float(trade.price),
                    reason=f"trade #{self.call_count}",
                    signal_time=trade.published_at,
                )
            ]
        return None

    async def on_market_update(self, update: object, ctx: StrategyContext) -> None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> None:
        return None


async def test_runner_feeds_trades_in_order() -> None:
    trades = [
        _make_trade("0xa", 0.60, 1000.0),
        _make_trade("0xb", 0.40, 500.0),   # earlier timestamp
        _make_trade("0xa", 0.65, 1500.0),
    ]
    strategy = CountingStrategy(emit_every=1)
    ctx = InMemoryContext()
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    result = await runner.run(trades)
    assert strategy.call_count == 3
    # Trades should be fed in timestamp order: 500, 1000, 1500
    assert result.total_trades == 3
    assert result.total_intents == 3


async def test_runner_collects_intents(tmp_path: Path) -> None:
    trades = [_make_trade("0xa", 0.50, float(i)) for i in range(9)]
    strategy = CountingStrategy(emit_every=3)
    ctx = InMemoryContext()
    executor = SimulatedExecutor()
    log_file = tmp_path / "intents.jsonl"
    gateway = ExecutionGateway(executor=executor, log_path=log_file)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    result = await runner.run(trades)
    assert strategy.call_count == 9
    assert result.total_intents == 3  # every 3rd trade
    assert result.total_fills == 3


async def test_runner_updates_context_time() -> None:
    trades = [
        _make_trade("0xa", 0.50, 1000.0),
        _make_trade("0xa", 0.55, 2000.0),
    ]
    strategy = CountingStrategy(emit_every=100)  # never emits
    ctx = InMemoryContext()
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    await runner.run(trades)
    # After replay, context time should be at last trade's timestamp
    assert await ctx.now() == 2000.0


async def test_runner_empty_trades() -> None:
    strategy = CountingStrategy()
    ctx = InMemoryContext()
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    result = await runner.run([])
    assert result.total_trades == 0
    assert result.total_intents == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_runner_backtest.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/runners/__init__.py
"""Strategy runners — drive strategies in different execution modes."""
```

```python
# src/polymarket_pipeline/strategies/runners/backtest.py
"""Event-driven backtest runner — replays historical trades through a strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.protocol import Strategy
    from polymarket_pipeline.strategies.types import Fill

logger = structlog.get_logger()


@dataclass
class BacktestResult:
    """Summary of a backtest run."""

    total_trades: int = 0
    total_intents: int = 0
    total_fills: int = 0
    fills: list[Fill] = field(default_factory=list)


class BacktestRunner:
    """Replays NormalizedTrades in timestamp order through strategy.on_trade()."""

    def __init__(
        self,
        strategy: Strategy,
        ctx: InMemoryContext,
        gateway: ExecutionGateway,
    ) -> None:
        self._strategy = strategy
        self._ctx = ctx
        self._gateway = gateway

    async def run(self, trades: list[NormalizedTrade]) -> BacktestResult:
        """Replay trades sorted by published_at. Returns summary."""
        sorted_trades = sorted(trades, key=lambda t: t.published_at)
        result = BacktestResult()

        for trade in sorted_trades:
            self._ctx.set_time(trade.published_at)
            result.total_trades += 1

            intents = await self._strategy.on_trade(trade, self._ctx)
            if intents:
                for intent in intents:
                    result.total_intents += 1
                    fill = await self._gateway.submit(intent)
                    result.total_fills += 1
                    result.fills.append(fill)

        return result
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_runner_backtest.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/runners/backtest.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/ \
        tests/test_strategy_runner_backtest.py
git commit -m "feat(strategies): add BacktestRunner for event-driven replay"
```

---

## Task 7: VectorizedRunner — Polars batch execution

Runs `VectorizedStrategy.compute_signals()` and converts output to fills.

**Files:**
- Create: `src/polymarket_pipeline/strategies/runners/vectorized.py`
- Test: `tests/test_strategy_runner_vectorized.py`

**Depends on:** Tasks 1-2

**Step 1: Write the failing test**

```python
# tests/test_strategy_runner_vectorized.py
"""Tests for the vectorized (Polars batch) runner."""

from __future__ import annotations

import polars as pl
import pytest

from polymarket_pipeline.strategies.runners.vectorized import (
    VectorizedResult,
    VectorizedRunner,
)


class MockVectorizedStrategy:
    """Returns a fixed signal table."""

    name = "mock_vec"

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame({
            "condition_id": ["0xa", "0xb", "0xc"],
            "signal_time": [1000.0, 2000.0, 3000.0],
            "side": ["BUY", "BUY", "SELL"],
            "outcome": ["YES", "NO", "YES"],
            "size_usd": [50.0, 30.0, 20.0],
            "price": [0.60, 0.40, 0.70],
            "reason": ["sig1", "sig2", "sig3"],
        })


class EmptyVectorizedStrategy:
    name = "empty_vec"

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame({
            "condition_id": [],
            "signal_time": [],
            "side": [],
            "outcome": [],
            "size_usd": [],
            "price": [],
            "reason": [],
        })


def test_vectorized_runner_produces_signals() -> None:
    strat = MockVectorizedStrategy()
    trades = pl.LazyFrame({"condition_id": ["0xa"], "published_at": [1.0]})
    markets = pl.LazyFrame({"condition_id": ["0xa"], "active": [True]})
    runner = VectorizedRunner(strategy=strat)

    result = runner.run(trades, markets)
    assert isinstance(result, VectorizedResult)
    assert result.total_signals == 3
    assert len(result.signals) == 3
    assert result.signals["condition_id"].to_list() == ["0xa", "0xb", "0xc"]


def test_vectorized_runner_empty_result() -> None:
    strat = EmptyVectorizedStrategy()
    trades = pl.LazyFrame({"condition_id": []})
    markets = pl.LazyFrame({"condition_id": []})
    runner = VectorizedRunner(strategy=strat)

    result = runner.run(trades, markets)
    assert result.total_signals == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_runner_vectorized.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/runners/vectorized.py
"""Vectorized runner — Polars batch execution for fast research iteration."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from polymarket_pipeline.strategies.protocol import VectorizedStrategy


@dataclass
class VectorizedResult:
    """Summary of a vectorized strategy run."""

    total_signals: int
    signals: pl.DataFrame


class VectorizedRunner:
    """Runs VectorizedStrategy.compute_signals() and wraps the result."""

    def __init__(self, strategy: VectorizedStrategy) -> None:
        self._strategy = strategy

    def run(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> VectorizedResult:
        signals = self._strategy.compute_signals(trades, markets)
        return VectorizedResult(
            total_signals=len(signals),
            signals=signals,
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_runner_vectorized.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/runners/vectorized.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/vectorized.py \
        tests/test_strategy_runner_vectorized.py
git commit -m "feat(strategies): add VectorizedRunner for Polars batch execution"
```

---

## Task 8: Parity gate — validate event-driven vs vectorized

Compares outputs of both runners to ensure the same strategy produces the same signals.

**Files:**
- Create: `src/polymarket_pipeline/strategies/runners/parity.py`
- Test: `tests/test_strategy_parity.py`

**Depends on:** Tasks 6-7

**Step 1: Write the failing test**

```python
# tests/test_strategy_parity.py
"""Tests for the parity gate between vectorized and event-driven execution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.runners.parity import ParityReport, validate_parity
from polymarket_pipeline.strategies.types import TradeIntent


def _make_trade(cid: str, price: float, ts: float) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=Side.BUY,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


class AlignedStrategy:
    """Strategy where event-driven and vectorized produce identical signals."""

    name = "aligned"

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id
        self._seen[cid] = self._seen.get(cid, 0) + 1
        if self._seen[cid] == 2:  # emit on second trade per market
            return [
                TradeIntent(
                    strategy="aligned",
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="patient",
                    max_price=float(trade.price),
                    reason="2nd trade",
                    signal_time=trade.published_at,
                )
            ]
        return None

    async def on_market_update(self, update: object, ctx: StrategyContext) -> None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> None:
        return None

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        """Vectorized equivalent: second trade per condition_id."""
        df = trades.sort("published_at").collect()
        df = df.with_columns(
            pl.col("condition_id").cum_count().over("condition_id").alias("nth")
        )
        signals = df.filter(pl.col("nth") == 2)
        return signals.select([
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.lit("YES").alias("outcome"),
            pl.lit(10.0).alias("size_usd"),
        ])


class MisalignedStrategy:
    """Strategy where event-driven emits MORE signals than vectorized (bug)."""

    name = "misaligned"

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        # Always emits (buggy — doesn't match vectorized)
        return [
            TradeIntent(
                strategy="misaligned",
                condition_id=trade.condition_id,
                side="BUY",
                outcome="YES",
                size_usd=10.0,
                urgency="patient",
                max_price=0.50,
                reason="always",
                signal_time=trade.published_at,
            )
        ]

    async def on_market_update(self, update: object, ctx: StrategyContext) -> None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> None:
        return None

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame({
            "condition_id": ["0xa"],
            "signal_time": [1000.0],
            "side": ["BUY"],
            "outcome": ["YES"],
            "size_usd": [10.0],
        })


async def test_parity_passes_for_aligned_strategy() -> None:
    trades = [
        _make_trade("0xa", 0.50, 100.0),
        _make_trade("0xa", 0.55, 200.0),  # triggers signal
        _make_trade("0xb", 0.40, 300.0),
        _make_trade("0xb", 0.45, 400.0),  # triggers signal
    ]
    markets = pl.LazyFrame({"condition_id": ["0xa", "0xb"]})
    strategy = AlignedStrategy()

    report = await validate_parity(strategy, trades, markets)
    assert report.vectorized_count == 2
    assert report.replay_count == 2
    assert report.is_aligned


async def test_parity_fails_for_misaligned_strategy() -> None:
    trades = [
        _make_trade("0xa", 0.50, 100.0),
        _make_trade("0xa", 0.55, 200.0),
    ]
    markets = pl.LazyFrame({"condition_id": ["0xa"]})
    strategy = MisalignedStrategy()

    report = await validate_parity(strategy, trades, markets)
    assert report.vectorized_count == 1
    assert report.replay_count == 2  # buggy: emits on every trade
    assert not report.is_aligned


async def test_parity_empty_signals() -> None:
    strategy = AlignedStrategy()
    trades = [_make_trade("0xa", 0.50, 100.0)]  # only 1 trade, no signal
    markets = pl.LazyFrame({"condition_id": ["0xa"]})

    report = await validate_parity(strategy, trades, markets)
    assert report.vectorized_count == 0
    assert report.replay_count == 0
    assert report.is_aligned
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_parity.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies/runners/parity.py
"""Parity gate — validates event-driven replay matches vectorized output.

Run this after developing a strategy to ensure both code paths produce
the same signals before promoting to paper/live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl
import structlog

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.runners.vectorized import VectorizedRunner

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import Strategy, VectorizedStrategy
    from polymarket_pipeline.strategies.types import TradeIntent

logger = structlog.get_logger()


@dataclass
class ParityReport:
    """Result of comparing vectorized vs event-driven outputs."""

    vectorized_count: int
    replay_count: int
    is_aligned: bool
    vectorized_markets: set[str] = field(default_factory=set)
    replay_markets: set[str] = field(default_factory=set)
    missing_in_replay: set[str] = field(default_factory=set)
    extra_in_replay: set[str] = field(default_factory=set)


async def validate_parity(
    strategy: Strategy & VectorizedStrategy,
    trades: list[NormalizedTrade],
    markets: pl.LazyFrame,
) -> ParityReport:
    """Compare vectorized signals vs event-driven replay intents.

    Checks: same number of signals, same condition_ids triggered.
    """
    # --- Vectorized path ---
    trades_df = pl.DataFrame([
        {
            "condition_id": t.condition_id,
            "published_at": t.published_at,
            "price": float(t.price),
            "side": t.side.value,
        }
        for t in trades
    ])
    vec_runner = VectorizedRunner(strategy=strategy)
    vec_result = vec_runner.run(trades_df.lazy(), markets)
    vec_markets = set(vec_result.signals["condition_id"].to_list()) if vec_result.total_signals > 0 else set()

    # --- Event-driven replay path ---
    ctx = InMemoryContext()
    sorted_trades = sorted(trades, key=lambda t: t.published_at)
    replay_intents: list[TradeIntent] = []

    for trade in sorted_trades:
        ctx.set_time(trade.published_at)
        intents = await strategy.on_trade(trade, ctx)
        if intents:
            replay_intents.extend(intents)

    replay_markets = {i.condition_id for i in replay_intents}

    # --- Compare ---
    is_aligned = (
        vec_result.total_signals == len(replay_intents)
        and vec_markets == replay_markets
    )

    return ParityReport(
        vectorized_count=vec_result.total_signals,
        replay_count=len(replay_intents),
        is_aligned=is_aligned,
        vectorized_markets=vec_markets,
        replay_markets=replay_markets,
        missing_in_replay=vec_markets - replay_markets,
        extra_in_replay=replay_markets - vec_markets,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_parity.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/runners/parity.py`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/parity.py \
        tests/test_strategy_parity.py
git commit -m "feat(strategies): add parity gate — vectorized vs replay validation"
```

---

## Task 9: ConsensusCopyStrategy — port from research/

Port the consensus copy signal logic into the new strategy protocol. This is the first real strategy implementation.

**Reference files:**
- Signal logic: `research/strategies/consistency_copy/backtester/signal_table.py` (lines 34-182)
- Sizing: `research/strategies/consistency_copy/backtester/sizing.py` (lines 14-97)
- Sweep: `research/strategies/consistency_copy/backtester/sweep.py` (lines 50-225)
- Runner: `research/strategies/consistency_copy/backtester/runner.py` (lines 459-684)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/consensus_copy/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/consensus_copy/config.py`
- Create: `src/polymarket_pipeline/strategies_impl/consensus_copy/strategy.py`
- Test: `tests/test_strategy_consensus_copy.py`

**Depends on:** Tasks 1-8

**Step 1: Write the failing test**

```python
# tests/test_strategy_consensus_copy.py
"""Tests for the ConsensusCopy strategy implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.protocol import Strategy, VectorizedStrategy
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
    ConsensusCopyStrategy,
)


def _make_trade(
    condition_id: str,
    maker: str,
    price: float,
    ts: float,
    side: Side = Side.BUY,
    size: float = 100.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{maker}:{condition_id}:{ts}",
        condition_id=condition_id,
        asset_id="asset_1",
        side=side,
        price=Decimal(str(price)),
        size=Decimal(str(size)),
        amount_usd=Decimal(str(price * size)),
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
        published_at=ts,
    )


@pytest.fixture
def config() -> ConsensusCopyConfig:
    return ConsensusCopyConfig(
        skilled_traders={"0xtrader_a", "0xtrader_b", "0xtrader_c", "0xtrader_d", "0xtrader_e"},
        min_traders=3,
        agreement_pct=0.60,
        direction="NO",
        delay_s=0,  # no delay for testing
    )


@pytest.fixture
def strategy(config: ConsensusCopyConfig) -> ConsensusCopyStrategy:
    return ConsensusCopyStrategy(config=config)


def test_satisfies_strategy_protocol(strategy: ConsensusCopyStrategy) -> None:
    assert isinstance(strategy, Strategy)


def test_satisfies_vectorized_protocol(strategy: ConsensusCopyStrategy) -> None:
    assert isinstance(strategy, VectorizedStrategy)


async def test_no_signal_below_min_traders(strategy: ConsensusCopyStrategy) -> None:
    """Need 3 skilled traders; only 2 arrive."""
    ctx = InMemoryContext()
    # Trader A buys (YES bet)
    t1 = _make_trade("0xmarket1", "0xtrader_a", 0.60, 1000.0, Side.BUY)
    assert await strategy.on_trade(t1, ctx) is None

    # Trader B buys (YES bet) — still only 2
    t2 = _make_trade("0xmarket1", "0xtrader_b", 0.62, 1001.0, Side.BUY)
    assert await strategy.on_trade(t2, ctx) is None


async def test_signal_fires_at_threshold(strategy: ConsensusCopyStrategy) -> None:
    """3 skilled traders all sell (NO direction), config direction=NO → signal fires."""
    ctx = InMemoryContext()

    # 3 skilled traders all SELL on same market (indicating NO bet)
    t1 = _make_trade("0xmarket1", "0xtrader_a", 0.60, 1000.0, Side.SELL)
    assert await strategy.on_trade(t1, ctx) is None

    t2 = _make_trade("0xmarket1", "0xtrader_b", 0.58, 1001.0, Side.SELL)
    assert await strategy.on_trade(t2, ctx) is None

    t3 = _make_trade("0xmarket1", "0xtrader_c", 0.55, 1002.0, Side.SELL)
    result = await strategy.on_trade(t3, ctx)

    # 3/3 agree on NO (100% agreement > 60% threshold), direction=NO matches
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"  # Buying NO tokens
    assert result[0].outcome == "NO"
    assert result[0].condition_id == "0xmarket1"


async def test_ignores_non_skilled_traders(strategy: ConsensusCopyStrategy) -> None:
    """Trades from unknown makers are ignored."""
    ctx = InMemoryContext()

    # Non-skilled trader
    t1 = _make_trade("0xmarket1", "0xrandom_guy", 0.60, 1000.0, Side.SELL)
    assert await strategy.on_trade(t1, ctx) is None


async def test_no_duplicate_signal_per_market(strategy: ConsensusCopyStrategy) -> None:
    """Once a signal fires for a market, no more signals even if more traders arrive."""
    ctx = InMemoryContext()

    for i, trader in enumerate(["0xtrader_a", "0xtrader_b", "0xtrader_c"]):
        t = _make_trade("0xmarket1", trader, 0.60, 1000.0 + i, Side.SELL)
        await strategy.on_trade(t, ctx)

    # 4th trader arrives — should NOT fire again
    t4 = _make_trade("0xmarket1", "0xtrader_d", 0.55, 2000.0, Side.SELL)
    assert await strategy.on_trade(t4, ctx) is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_consensus_copy.py -x -q`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/strategies_impl/__init__.py
"""Strategy implementations."""
```

```python
# src/polymarket_pipeline/strategies_impl/consensus_copy/__init__.py
"""Consensus copy trading strategy."""
```

```python
# src/polymarket_pipeline/strategies_impl/consensus_copy/config.py
"""Configuration for the consensus copy strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConsensusCopyConfig:
    """Parameters for the consensus copy strategy.

    Reference: research/strategies/consistency_copy/backtester/ sweep params.
    """

    # Trader pool (pre-computed from historical consistency analysis)
    skilled_traders: set[str]

    # Signal thresholds
    min_traders: int = 5
    agreement_pct: float = 0.80
    direction: str = "NO"  # "YES", "NO", or "both"

    # Execution
    delay_s: float = 60.0
    sizing: str = "fixed"
    base_bet_usd: float = 10.0
    fee_pct: float = 0.02
```

```python
# src/polymarket_pipeline/strategies_impl/consensus_copy/strategy.py
"""Consensus copy strategy — event-driven + vectorized implementations.

Ports signal logic from research/strategies/consistency_copy/backtester/signal_table.py
into the new strategy framework protocols.

Core logic: track arrivals of skilled traders per market. When min_traders
reach agreement_pct consensus in the configured direction, emit a TradeIntent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl
import structlog

from polymarket_pipeline.models import Side
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger()


@dataclass
class _MarketState:
    """Per-market tracking state for the event-driven path."""

    n_yes: int = 0
    n_no: int = 0
    seen_traders: set[str] = field(default_factory=set)
    signal_fired: bool = False


class ConsensusCopyStrategy:
    """Consensus copy trading: follow skilled traders when they agree.

    Implements both Strategy and VectorizedStrategy protocols.
    """

    name: str = "consensus_copy"

    def __init__(self, config: ConsensusCopyConfig) -> None:
        self._config = config
        self._markets: dict[str, _MarketState] = {}

    # --- Strategy protocol (event-driven) ---

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        # Only process trades from skilled traders
        if trade.maker not in self._config.skilled_traders:
            return None

        cid = trade.condition_id
        state = self._markets.setdefault(cid, _MarketState())

        # Skip if already signaled for this market
        if state.signal_fired:
            return None

        # Skip duplicate maker per market
        if trade.maker in state.seen_traders:
            return None
        state.seen_traders.add(trade.maker)

        # Infer direction from trade side:
        # BUY = betting YES (buying YES tokens)
        # SELL = betting NO (selling YES tokens = buying NO)
        is_yes_bet = trade.side == Side.BUY
        if is_yes_bet:
            state.n_yes += 1
        else:
            state.n_no += 1

        n_traders = state.n_yes + state.n_no
        if n_traders < self._config.min_traders:
            return None

        # Check agreement
        n_majority = max(state.n_yes, state.n_no)
        agreement = n_majority / n_traders
        if agreement < self._config.agreement_pct:
            return None

        # Determine signal direction
        signal_dir = "YES" if state.n_yes >= state.n_no else "NO"

        # Filter by configured direction preference
        if self._config.direction != "both" and signal_dir != self._config.direction:
            return None

        state.signal_fired = True

        # Emit intent
        intent_side = "BUY"  # always buying tokens
        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side=intent_side,
                outcome=signal_dir,
                size_usd=self._config.base_bet_usd,
                urgency="patient",
                max_price=float(trade.price) if signal_dir == "YES" else 1.0 - float(trade.price),
                reason=(
                    f"{n_traders} skilled traders, {agreement:.0%} agree on {signal_dir}"
                ),
                signal_time=trade.published_at,
            ),
        ]

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None  # consensus copy doesn't use orderbook

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None  # delay handled by ExecutionGateway in future

    # --- VectorizedStrategy protocol (batch) ---

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        """Vectorized signal computation — mirrors event-driven logic.

        Ported from research/strategies/consistency_copy/backtester/signal_table.py.
        """
        cfg = self._config
        skilled = list(cfg.skilled_traders)

        df = (
            trades
            .filter(pl.col("maker").is_in(skilled))
            .sort("published_at")
            .collect()
        )

        if len(df) == 0:
            return pl.DataFrame({
                "condition_id": [],
                "signal_time": [],
                "side": [],
                "outcome": [],
                "size_usd": [],
            })

        # Deduplicate: first trade per (maker, condition_id)
        df = df.unique(subset=["maker", "condition_id"], keep="first")
        df = df.sort(["condition_id", "published_at"])

        # Infer direction from side
        df = df.with_columns(
            (pl.col("side") == "BUY").alias("bet_yes")
        )

        # Arrival order within each market
        df = df.with_columns(
            pl.col("published_at").cum_count().over("condition_id").alias("n_traders")
        )

        # Cumulative YES/NO counts
        df = df.with_columns([
            pl.col("bet_yes").cast(pl.Int64).cum_sum().over("condition_id").alias("n_yes"),
            (~pl.col("bet_yes")).cast(pl.Int64).cum_sum().over("condition_id").alias("n_no"),
        ])

        # Agreement fraction
        df = df.with_columns(
            (pl.max_horizontal("n_yes", "n_no").cast(pl.Float64) / pl.col("n_traders").cast(pl.Float64))
            .alias("agreement_frac")
        )

        # Signal direction
        df = df.with_columns(
            pl.when(pl.col("n_yes") >= pl.col("n_no"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("signal_direction")
        )

        # Filter: min_traders, agreement, direction
        signals = df.filter(
            (pl.col("n_traders") >= cfg.min_traders)
            & (pl.col("agreement_frac") >= cfg.agreement_pct)
        )

        if cfg.direction != "both":
            signals = signals.filter(pl.col("signal_direction") == cfg.direction)

        # Take first qualifying row per market
        signals = signals.sort("published_at").unique(subset=["condition_id"], keep="first")

        return signals.select([
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.col("signal_direction").alias("outcome"),
            pl.lit(cfg.base_bet_usd).alias("size_usd"),
        ])
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_consensus_copy.py -x -q`
Expected: All PASS

**Step 5: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies_impl/`
Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/ \
        tests/test_strategy_consensus_copy.py
git commit -m "feat(strategies): port ConsensusCopy strategy to new framework"
```

---

## Task 10: Full integration test — backtest + parity

End-to-end test: create trades, run vectorized, run replay, validate parity.

**Files:**
- Create: `tests/test_strategy_integration.py`

**Depends on:** All previous tasks

**Step 1: Write the integration test**

```python
# tests/test_strategy_integration.py
"""Integration tests for the strategy framework — full pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig, load_strategy_configs
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.registry import StrategyRegistry
from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
from polymarket_pipeline.strategies.runners.parity import validate_parity
from polymarket_pipeline.strategies.runners.vectorized import VectorizedRunner
from polymarket_pipeline.strategies.types import ExecutionMode
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import ConsensusCopyStrategy


SKILLED = {"0xt_a", "0xt_b", "0xt_c", "0xt_d", "0xt_e"}


def _make_trade(cid: str, maker: str, price: float, ts: float, side: Side) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"t:{maker}:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexch",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=ts,
    )


def _build_scenario() -> list[NormalizedTrade]:
    """3 skilled traders sell on market1 (NO signal), 2 on market2 (below threshold)."""
    return [
        # Market 1: 3 sell (NO consensus)
        _make_trade("0xm1", "0xt_a", 0.60, 100.0, Side.SELL),
        _make_trade("0xm1", "0xt_b", 0.58, 200.0, Side.SELL),
        _make_trade("0xm1", "0xt_c", 0.55, 300.0, Side.SELL),
        # Market 2: only 2 traders (below min_traders=3)
        _make_trade("0xm2", "0xt_d", 0.70, 150.0, Side.BUY),
        _make_trade("0xm2", "0xt_e", 0.72, 250.0, Side.BUY),
        # Non-skilled noise
        _make_trade("0xm1", "0xrandom", 0.50, 350.0, Side.BUY),
    ]


async def test_backtest_runner_full_pipeline(tmp_path: Path) -> None:
    """Run ConsensusCopy through BacktestRunner, verify 1 signal fires."""
    config = ConsensusCopyConfig(
        skilled_traders=SKILLED, min_traders=3, agreement_pct=0.60, direction="NO",
        delay_s=0, base_bet_usd=10.0,
    )
    strategy = ConsensusCopyStrategy(config=config)
    ctx = InMemoryContext()
    executor = SimulatedExecutor(fee_pct=0.02)
    log_file = tmp_path / "intents.jsonl"
    gateway = ExecutionGateway(executor=executor, log_path=log_file)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    trades = _build_scenario()
    result = await runner.run(trades)

    # Only market1 should fire (3 sellers, 100% NO agreement)
    assert result.total_intents == 1
    assert result.fills[0].outcome == "NO"
    assert result.fills[0].condition_id == "0xm1"

    # Intent was logged
    assert log_file.exists()


def test_vectorized_runner_same_result() -> None:
    """Run ConsensusCopy through VectorizedRunner, verify same signal."""
    config = ConsensusCopyConfig(
        skilled_traders=SKILLED, min_traders=3, agreement_pct=0.60, direction="NO",
        delay_s=0, base_bet_usd=10.0,
    )
    strategy = ConsensusCopyStrategy(config=config)
    trades = _build_scenario()

    trades_df = pl.DataFrame([
        {
            "condition_id": t.condition_id,
            "published_at": t.published_at,
            "price": float(t.price),
            "side": t.side.value,
            "maker": t.maker,
        }
        for t in trades
    ])
    markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})
    runner = VectorizedRunner(strategy=strategy)
    result = runner.run(trades_df.lazy(), markets)

    assert result.total_signals == 1
    assert result.signals["condition_id"][0] == "0xm1"
    assert result.signals["outcome"][0] == "NO"


async def test_parity_gate_consensus_copy() -> None:
    """Verify event-driven and vectorized produce same signals."""
    config = ConsensusCopyConfig(
        skilled_traders=SKILLED, min_traders=3, agreement_pct=0.60, direction="NO",
        delay_s=0, base_bet_usd=10.0,
    )
    strategy = ConsensusCopyStrategy(config=config)
    trades = _build_scenario()
    markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})

    report = await validate_parity(strategy, trades, markets)
    assert report.is_aligned, (
        f"Parity failed: vec={report.vectorized_count}, replay={report.replay_count}, "
        f"missing={report.missing_in_replay}, extra={report.extra_in_replay}"
    )


def test_registry_creates_consensus_copy() -> None:
    """Verify ConsensusCopy can be created via registry."""
    registry = StrategyRegistry()
    registry.register("consensus_copy", ConsensusCopyStrategy)

    config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=20,
        cooldown_s=300,
        params={
            "config": ConsensusCopyConfig(
                skilled_traders=SKILLED,
                min_traders=3,
                agreement_pct=0.60,
                direction="NO",
            ),
        },
    )
    instance = registry.create("consensus_copy", config)
    assert instance.name == "consensus_copy"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_strategy_integration.py -x -q`
Expected: FAIL (until all prior tasks are complete)

**Step 3: Implementation is already done** — this test validates the integration.

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_strategy_integration.py -x -q`
Expected: All PASS (after Tasks 1-9 complete)

**Step 5: Run full test suite**

Run: `uv run pytest tests/test_strategy_*.py -x -q`
Expected: All PASS

**Step 6: Run type checker on everything**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies/ src/polymarket_pipeline/strategies_impl/`
Expected: Success

**Step 7: Run linter**

Run: `uv run ruff check src/polymarket_pipeline/strategies/ src/polymarket_pipeline/strategies_impl/ tests/test_strategy_*.py`
Expected: Clean

**Step 8: Commit**

```bash
git add tests/test_strategy_integration.py
git commit -m "test(strategies): add full integration test — backtest + parity gate"
```

---

## Task 11: Add `strategy` optional dependency group + update pyproject.toml

Register the strategy module as an installable extra.

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add the `strategy` extra to pyproject.toml**

Add to `[project.optional-dependencies]`:
```toml
strategy = [
    "polars>=1.15.0",
    "pydantic>=2.0",
    "structlog>=24.0",
]
```

Update `all` to include `strategy`:
```toml
all = [
    "polymarket-pipeline[sink,clickhouse,postgres,websocket,http,compact,exploration,live,strategy,dev]",
]
```

**Step 2: Sync dependencies**

Run: `uv sync --all-extras`
Expected: Success

**Step 3: Run full test suite**

Run: `uv run pytest tests/test_strategy_*.py -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add strategy dependency group"
```
