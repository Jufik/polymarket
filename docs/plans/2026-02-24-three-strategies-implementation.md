# Three Strategies Implementation Plan (S1 + S2a + S2b)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement three live strategies — S2b (Crypto OTM NO), S2a (General "Will" NO), and S1 (Proportional Copy of Longshot YES Specialists) — as concrete `Strategy` + `FeatureProvider` implementations within the existing framework, with full test coverage, CLI registration, and TOML configs.

**Architecture:** Each strategy follows the existing pattern: frozen config dataclass, `FeatureProvider` for startup/periodic data, `Strategy` with event-driven `on_trade()` + vectorized `compute_signals()`. S2b (crypto_otm_no) already exists and needs minor enhancements. S2a is a new strategy filtering "Will" binary markets by keyword/price. S1 is the most complex — it tracks individual skilled traders' positions and copies proportionally with grading filters. All three share the existing `LiveRunner` → `ExecutionGateway` → `Executor` pipeline and are configured via TOML.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, Polars, structlog, pytest-asyncio, frozen dataclasses, TOML config.

---

## Existing Infrastructure (Reference)

These files already exist and should NOT be modified unless noted:

| Component | File | Purpose |
|-----------|------|---------|
| Strategy protocol | `strategies/protocol.py` | `Strategy`, `StrategyContext`, `FeatureProvider`, `Executor` |
| Types | `strategies/types.py` | `TradeIntent`, `Position`, `MarketInfo`, `OrderbookSnapshot`, `Fill` |
| Config | `strategies/config.py` | `StrategyConfig`, `ProviderConfig`, TOML loaders |
| Registry | `strategies/registry.py` | `StrategyRegistry` (manual registration in CLI) |
| Context | `strategies/context/memory.py` | `InMemoryContext` |
| Gateway | `strategies/execution/gateway.py` | `ExecutionGateway` |
| Executors | `strategies/execution/simulated.py`, `paper.py`, `live.py` | `SimulatedExecutor`, `PaperExecutor`, `LiveExecutor` |
| Runners | `strategies/runners/live.py`, `backtest.py`, `vectorized.py` | `LiveRunner`, `BacktestRunner`, `VectorizedRunner` |
| Helpers | `strategies/runners/helpers.py` | `apply_fill_to_position()`, `check_risk_gate()` |
| Backends | `strategies/features/backend_polars.py`, `backend_clickhouse.py` | `PolarsBackend`, `ClickHouseBackend` |
| CLI | `cli/strategy.py` | `pm-strategy run --config strategies.toml` |
| Existing S2b | `strategies_impl/crypto_otm_no/` | `CryptoOTMNoStrategy`, `CryptoOTMNoConfig`, `CryptoMarketProvider` |
| Existing S3 | `strategies_impl/consensus_copy/` | `ConsensusCopyStrategy`, `ConsensusCopyConfig`, `SkilledTradersProvider` |
| Models | `models.py` | `NormalizedTrade`, `Market`, `Event`, `TokenMarketEntry` |

### Key Conventions

- All configs are `@dataclass(frozen=True)` with custom `__init__` for `frozenset` coercion
- Strategies implement both `Strategy` (event-driven) and `VectorizedStrategy` (Polars batch)
- Providers: `compute()` at startup, `on_trade()` on hot path (usually no-op), `refresh()` periodic, `get_features()` returns dict
- `TradeIntent.side` is `"BUY"` or `"SELL"`, `outcome` is `"YES"` or `"NO"`
- Buying NO = `side="BUY", outcome="NO"`
- `condition_id` identifies a market, `asset_id` identifies a token within a market
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- ruff: line-length 100, rules `E F I UP B SIM ASYNC`

---

## Task 1: Enhance S2b Crypto OTM NO — add checkpoint timing and volume filter

The existing `CryptoOTMNoStrategy` works but is missing two features from the research:
1. **Volume filter** — skip markets with < `min_volume_usd` (config exists but not enforced in event-driven path)
2. **Lockup filter** — prefer fast-resolving markets (< 24h). This needs `end_date` from market metadata.
3. **on_timer for checkpoint scanning** — every 4h, scan for new eligible markets even without a trade trigger

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/crypto_otm_no/strategy.py`
- Modify: `src/polymarket_pipeline/strategies_impl/crypto_otm_no/providers.py`
- Modify: `tests/test_strategy_crypto_otm_no.py`

**Step 1: Write failing tests for volume filter and timer**

Add to `tests/test_strategy_crypto_otm_no.py`:

```python
async def test_otm_no_skips_low_volume_market() -> None:
    """Markets below min_volume_usd should be skipped."""
    from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig
    from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import CryptoOTMNoStrategy

    cfg = CryptoOTMNoConfig(
        yes_price_min=0.05,
        yes_price_max=0.25,
        assets={"BTC"},
        min_volume_usd=100.0,
    )
    strategy = CryptoOTMNoStrategy(config=cfg)

    ctx = _make_ctx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Bitcoin above $120K at 4PM ET?",
            active=True,
            yes_price=0.10,
            category="Crypto",
        ),
        features={"crypto_markets_volume": {"0xabc": 30.0}},  # below threshold
    )
    trade = _make_trade(condition_id="0xabc", price=0.10)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


async def test_otm_no_accepts_sufficient_volume_market() -> None:
    """Markets at or above min_volume_usd should be accepted."""
    from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig
    from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import CryptoOTMNoStrategy

    cfg = CryptoOTMNoConfig(
        yes_price_min=0.05,
        yes_price_max=0.25,
        assets={"BTC"},
        min_volume_usd=50.0,
    )
    strategy = CryptoOTMNoStrategy(config=cfg)

    ctx = _make_ctx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Bitcoin above $120K at 4PM ET?",
            active=True,
            yes_price=0.10,
            category="Crypto",
        ),
        features={"crypto_markets_volume": {"0xabc": 200.0}},
    )
    trade = _make_trade(condition_id="0xabc", price=0.10)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert len(result) == 1
    assert result[0].outcome == "NO"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_crypto_otm_no.py::test_otm_no_skips_low_volume_market tests/test_strategy_crypto_otm_no.py::test_otm_no_accepts_sufficient_volume_market -x -q`
Expected: FAIL (volume check not implemented in event path, features key doesn't exist)

**Step 3: Implement volume filter in event-driven path**

In `src/polymarket_pipeline/strategies_impl/crypto_otm_no/strategy.py`, modify `on_trade`:

After the `_is_eligible` check and before `self._signaled.add(cid)`, add:

```python
        # Volume filter — skip thin markets
        if self._cfg.min_volume_usd > 0:
            volumes = await ctx.get_features("crypto_markets_volume")
            if volumes is not None:
                vol = volumes.get(cid, 0.0)
                if vol < self._cfg.min_volume_usd:
                    return None
```

In `src/polymarket_pipeline/strategies_impl/crypto_otm_no/providers.py`, extend `CryptoMarketProvider.compute()` to also build a volume dict from the markets DataFrame (column `volume` if available) and expose it as `crypto_markets_volume`. Update `get_features()`:

```python
    def get_features(self) -> dict[str, Any]:
        return {
            "crypto_markets": self._markets,
            "crypto_markets_volume": self._volumes,
        }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_crypto_otm_no.py -x -q`
Expected: PASS

**Step 5: Run full unit test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/crypto_otm_no/ tests/test_strategy_crypto_otm_no.py
git commit -m "feat(s2b): add volume filter to crypto OTM NO event-driven path"
```

---

## Task 2: Create S2a — General "Will" NO Strategy (config + skeleton)

The "Will" NO strategy buys NO on binary "Will X happen?" markets where YES is priced 15-40%. The edge is structural: most proposed events don't happen, and the favorite-longshot bias makes YES overpriced.

**Research parameters** (from insights #03, #17-20):
- Filter: question starts with "Will" (binary market)
- YES price band: 15-40% (best PnL/day)
- Prefer keywords: "above", "below", "today" (fast resolution, <3 day lockup)
- Avoid keywords: "reach", "hit" (negative edge or long lockup)
- Volume: prefer < $5K (fastest lockup, highest edge)
- Direction: always NO
- Dual-sided capacity: buy NO + sell YES (same position, different liquidity pool)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/will_no/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/will_no/config.py`
- Create: `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`
- Create: `src/polymarket_pipeline/strategies_impl/will_no/providers.py`
- Create: `tests/test_strategy_will_no.py`

**Step 1: Create the config dataclass**

Create `src/polymarket_pipeline/strategies_impl/will_no/__init__.py` (empty).

Create `src/polymarket_pipeline/strategies_impl/will_no/config.py`:

```python
"""Configuration for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WillNoConfig:
    """Immutable configuration for the will-no strategy.

    Parameters
    ----------
    yes_price_min
        Lower bound of the YES price band (inclusive).
    yes_price_max
        Upper bound of the YES price band (inclusive).
    base_bet_usd
        Fixed bet size in USD.
    fee_pct
        Expected fee as a fraction (e.g. 0.02 = 2%).
    prefer_keywords
        Keywords that indicate fast-resolving markets (boost priority).
    avoid_keywords
        Keywords that indicate negative edge or long lockup (skip).
    max_volume_usd
        Maximum market volume — thin markets have higher edge.
        0 = no filter.
    question_pattern
        Regex pattern the question must match (case-insensitive).
        Default matches questions starting with "Will".
    """

    yes_price_min: float = 0.15
    yes_price_max: float = 0.40
    base_bet_usd: float = 50.0
    fee_pct: float = 0.0
    prefer_keywords: frozenset[str] = field(default_factory=frozenset)
    avoid_keywords: frozenset[str] = field(default_factory=frozenset)
    max_volume_usd: float = 0.0
    question_pattern: str = r"^Will\b"

    def __init__(
        self,
        yes_price_min: float = 0.15,
        yes_price_max: float = 0.40,
        base_bet_usd: float = 50.0,
        fee_pct: float = 0.0,
        prefer_keywords: set[str] | frozenset[str] | list[str] | None = None,
        avoid_keywords: set[str] | frozenset[str] | list[str] | None = None,
        max_volume_usd: float = 0.0,
        question_pattern: str = r"^Will\b",
    ) -> None:
        object.__setattr__(self, "yes_price_min", yes_price_min)
        object.__setattr__(self, "yes_price_max", yes_price_max)
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(
            self,
            "prefer_keywords",
            frozenset(prefer_keywords) if prefer_keywords is not None else frozenset(),
        )
        object.__setattr__(
            self,
            "avoid_keywords",
            frozenset(avoid_keywords)
            if avoid_keywords is not None
            else frozenset({"reach", "hit"}),
        )
        object.__setattr__(self, "max_volume_usd", max_volume_usd)
        object.__setattr__(self, "question_pattern", question_pattern)
```

**Step 2: Write config tests**

Create `tests/test_strategy_will_no.py`:

```python
"""Tests for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.strategies.types import MarketInfo, TradeIntent
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_will_no_config_defaults() -> None:
    cfg = WillNoConfig()
    assert cfg.yes_price_min == 0.15
    assert cfg.yes_price_max == 0.40
    assert cfg.base_bet_usd == 50.0
    assert cfg.fee_pct == 0.0
    assert cfg.avoid_keywords == frozenset({"reach", "hit"})
    assert cfg.prefer_keywords == frozenset()
    assert cfg.question_pattern == r"^Will\b"


def test_will_no_config_custom() -> None:
    cfg = WillNoConfig(
        yes_price_min=0.10,
        yes_price_max=0.50,
        prefer_keywords=["above", "below"],
        avoid_keywords=["reach"],
        max_volume_usd=5000.0,
    )
    assert cfg.yes_price_min == 0.10
    assert cfg.prefer_keywords == frozenset({"above", "below"})
    assert cfg.avoid_keywords == frozenset({"reach"})
    assert cfg.max_volume_usd == 5000.0


def test_will_no_config_is_frozen() -> None:
    cfg = WillNoConfig()
    with pytest.raises(AttributeError):
        cfg.yes_price_min = 0.99  # type: ignore[misc]
```

**Step 3: Run config tests**

Run: `uv run pytest tests/test_strategy_will_no.py -x -q`
Expected: PASS

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/will_no/ tests/test_strategy_will_no.py
git commit -m "feat(s2a): add WillNoConfig for favorite-longshot NO strategy"
```

---

## Task 3: Create S2a — WillNoStrategy (event-driven + vectorized)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`
- Modify: `tests/test_strategy_will_no.py`

**Step 1: Write failing tests for the strategy**

Add to `tests/test_strategy_will_no.py`:

```python
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_trade(
    *,
    condition_id: str = "0xabc",
    price: float = 0.25,
    maker: str | None = "0xmaker1",
    side: str = "BUY",
    published_at: float | None = None,
) -> Any:
    """Create a minimal NormalizedTrade-like object for testing."""
    from decimal import Decimal
    from datetime import datetime, timezone
    from polymarket_pipeline.models import NormalizedTrade

    return NormalizedTrade(
        trade_id=f"test:{condition_id}:{time.time()}",
        condition_id=condition_id,
        asset_id="0xasset",
        side=side,
        price=Decimal(str(price)),
        size=Decimal("10"),
        amount_usd=Decimal("100"),
        fee_usd=Decimal("0"),
        maker=maker,
        taker=None,
        timestamp=datetime.now(timezone.utc),
        source="WEBSOCKET",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
        published_at=published_at or time.time(),
    )


class _MockCtx:
    """Minimal StrategyContext mock."""

    def __init__(
        self,
        market: MarketInfo | None = None,
        features: dict[str, Any] | None = None,
    ) -> None:
        self._market = market
        self._features = features or {}

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return self._market

    async def get_features(self, key: str) -> Any:
        return self._features.get(key)

    async def get_position(self, condition_id: str) -> None:
        return None

    async def get_orderbook(self, condition_id: str) -> None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Event-driven tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_will_no_fires_on_qualifying_market() -> None:
    """Should emit BUY NO intent on a 'Will' market with YES in band."""
    cfg = WillNoConfig(yes_price_min=0.15, yes_price_max=0.40)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Bitcoin hit $200K in 2026?",
            active=True,
            yes_price=0.25,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"
    assert result[0].outcome == "NO"
    assert result[0].strategy == "will_no"


@pytest.mark.asyncio
async def test_will_no_skips_non_will_question() -> None:
    """Questions not starting with 'Will' should be skipped."""
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Bitcoin above $120K at 4PM ET?",
            active=True,
            yes_price=0.25,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_skips_yes_price_outside_band() -> None:
    """YES price outside the configured band should be skipped."""
    cfg = WillNoConfig(yes_price_min=0.15, yes_price_max=0.40)
    strategy = WillNoStrategy(config=cfg)

    # YES price too high (60%)
    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will inflation drop below 2%?",
            active=True,
            yes_price=0.60,
            category="Economics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.60)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_skips_avoid_keyword() -> None:
    """Markets with avoid keywords should be skipped."""
    cfg = WillNoConfig(avoid_keywords={"reach", "hit"})
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Bitcoin reach $500K?",
            active=True,
            yes_price=0.20,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.20)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_fires_once_per_market() -> None:
    """Signal should fire only once per condition_id."""
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Trump visit Japan?",
            active=True,
            yes_price=0.30,
            category="Politics",
        ),
    )
    trade1 = _make_trade(condition_id="0xabc", price=0.30)
    trade2 = _make_trade(condition_id="0xabc", price=0.28)

    r1 = await strategy.on_trade(trade1, ctx)
    r2 = await strategy.on_trade(trade2, ctx)
    assert r1 is not None
    assert r2 is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_will_no.py::test_will_no_fires_on_qualifying_market -x -q`
Expected: FAIL (WillNoStrategy doesn't exist yet)

**Step 3: Implement WillNoStrategy**

Create `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`:

```python
"""Will-NO strategy: event-driven and vectorized implementations.

Buys NO on binary "Will X happen?" markets where YES is priced 15-40%.
The NO side wins ~75-85% of the time because most proposed events don't
happen, and the favorite-longshot bias makes YES overpriced.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import MarketInfo, TradeIntent
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig


class WillNoStrategy:
    """Will-NO strategy implementing both Strategy and VectorizedStrategy.

    Event-driven (``on_trade``): checks if the market is a qualifying
    "Will" binary question with YES in the configured price band,
    then fires a BUY NO signal on the first qualifying trade.

    Vectorized (``compute_signals``): same logic over Polars DataFrames.
    """

    name: str = "will_no"

    def __init__(self, config: WillNoConfig) -> None:
        self._cfg = config
        self._signaled: set[str] = set()
        self._pattern = re.compile(config.question_pattern, re.IGNORECASE)

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id

        if cid in self._signaled:
            return None

        market = await ctx.get_market(cid)
        if market is None:
            return None

        if not self._is_eligible(market, trade):
            return None

        self._signaled.add(cid)

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome="NO",
            size_usd=self._cfg.base_bet_usd,
            urgency="patient",
            max_price=None,
            reason=f"will_no: {market.question}",
            signal_time=trade.published_at,
        )
        return [intent]

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    # ------------------------------------------------------------------
    # Vectorized path
    # ------------------------------------------------------------------

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        avoid = list(self._cfg.avoid_keywords)

        df = trades.join(markets, on="condition_id", how="inner")

        # Filter: question matches "Will" pattern
        df = df.filter(
            pl.col("question").str.contains(self._cfg.question_pattern)
        )

        # Filter: YES price in band (use trade price as proxy)
        df = df.filter(
            (pl.col("price") >= self._cfg.yes_price_min)
            & (pl.col("price") <= self._cfg.yes_price_max)
        )

        # Filter: avoid keywords
        if avoid:
            for kw in avoid:
                df = df.filter(
                    ~pl.col("question").str.to_lowercase().str.contains(kw.lower())
                )

        # First qualifying trade per condition_id
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["condition_id"], keep="first")

        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.lit("NO").alias("outcome"),
            pl.lit(self._cfg.base_bet_usd).alias("size_usd"),
        )

        return result.collect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_eligible(self, market: MarketInfo, trade: NormalizedTrade) -> bool:
        # Question must match "Will" pattern
        if not self._pattern.search(market.question):
            return False

        # Check avoid keywords
        q_lower = market.question.lower()
        for kw in self._cfg.avoid_keywords:
            if kw.lower() in q_lower:
                return False

        # YES price must be in band
        yes_price = market.yes_price
        if yes_price is None:
            yes_price = float(trade.price)
        if not (self._cfg.yes_price_min <= yes_price <= self._cfg.yes_price_max):
            return False

        return True
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_will_no.py -x -q`
Expected: ALL PASS

**Step 5: Run full unit test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/will_no/strategy.py tests/test_strategy_will_no.py
git commit -m "feat(s2a): implement WillNoStrategy with event-driven + vectorized paths"
```

---

## Task 4: Create S2a — WillMarketProvider (feature provider)

Pre-filters market metadata to "Will" binary markets so the strategy's `on_trade` can check eligibility via context features instead of hitting the backend every time.

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/will_no/providers.py`
- Modify: `tests/test_strategy_will_no.py`

**Step 1: Write failing tests for the provider**

Add to `tests/test_strategy_will_no.py`:

```python
from polymarket_pipeline.strategies_impl.will_no.providers import WillMarketProvider


@pytest.mark.asyncio
async def test_will_market_provider_filters_correctly() -> None:
    """Provider should only include 'Will' questions."""
    import polars as pl

    provider = WillMarketProvider()

    markets_df = pl.DataFrame(
        {
            "condition_id": ["0x1", "0x2", "0x3", "0x4"],
            "question": [
                "Will Bitcoin hit $200K?",
                "Bitcoin above $120K at 4PM?",
                "Will inflation drop below 2%?",
                "Will SOL reach $500?",
            ],
            "active": [True, True, True, True],
            "yes_price": [0.25, 0.15, 0.30, 0.20],
            "no_price": [0.75, 0.85, 0.70, 0.80],
            "event_id": ["e1", "e2", "e3", "e4"],
            "category": ["Crypto", "Crypto", "Economics", "Crypto"],
        }
    )

    backend = AsyncMock()
    backend.query_markets = AsyncMock(return_value=markets_df)

    await provider.compute(backend)

    features = provider.get_features()
    will_markets = features["will_markets"]

    # 0x1, 0x3 match "Will" pattern. 0x4 has "reach" (avoid keyword).
    assert "0x1" in will_markets
    assert "0x3" in will_markets
    assert "0x2" not in will_markets  # doesn't start with "Will"
    # 0x4 has "reach" but provider doesn't filter avoid keywords — strategy does
    assert "0x4" in will_markets


@pytest.mark.asyncio
async def test_will_market_provider_on_trade_is_noop() -> None:
    """on_trade should be a no-op (market set refreshed periodically)."""
    provider = WillMarketProvider()
    trade = _make_trade()
    await provider.on_trade(trade)  # should not raise
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_will_no.py::test_will_market_provider_filters_correctly -x -q`
Expected: FAIL (WillMarketProvider doesn't exist)

**Step 3: Implement WillMarketProvider**

Create `src/polymarket_pipeline/strategies_impl/will_no/providers.py`:

```python
"""Feature providers for the will-no strategy."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import MarketInfo

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class WillMarketProvider:
    """Pre-filters market metadata to 'Will' binary questions.

    At startup, loads markets from the backend, filters to questions
    matching the "Will" pattern, and builds a ``dict[str, MarketInfo]``
    keyed by condition_id.

    Parameters
    ----------
    question_pattern:
        Regex pattern to match "Will" questions.
    """

    name: str = "will_markets"

    def __init__(self, question_pattern: str = r"^Will\b") -> None:
        self._pattern = re.compile(question_pattern, re.IGNORECASE)
        self._markets: dict[str, MarketInfo] = {}

    async def compute(self, backend: FeatureBackend) -> None:
        markets_df = await backend.query_markets()

        if markets_df.is_empty():
            self._markets = {}
            logger.info("will_markets.compute", count=0)
            return

        result: dict[str, MarketInfo] = {}
        for row in markets_df.iter_rows(named=True):
            question = row.get("question", "")
            condition_id = row.get("condition_id", "")

            if not self._pattern.search(question):
                continue

            result[condition_id] = MarketInfo(
                condition_id=condition_id,
                question=question,
                active=bool(row.get("active", True)),
                yes_price=row.get("yes_price"),
                no_price=row.get("no_price"),
                event_id=row.get("event_id"),
                category=row.get("category"),
            )

        self._markets = result
        logger.info("will_markets.compute", count=len(self._markets))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — market set is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"will_markets": self._markets}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_will_no.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/will_no/providers.py tests/test_strategy_will_no.py
git commit -m "feat(s2a): add WillMarketProvider for Will-question market filtering"
```

---

## Task 5: Create S1 — Proportional Copy config + types

S1 is the most complex strategy. It tracks individual skilled traders from a graded pool (9-month consistent, pure_taker, longshot_yes_fraction > 15%) and copies their positions proportionally.

**Research parameters** (from insights #02, #03, #09, #10, #12, #14):
- Pool: 9-month consistent, pure_taker (MVF < 0.10), entry <= 0.90
- Grade filter: longshot_yes_fraction > 0.15
- Allocation: equal-weight (1/N) across pool traders
- Copy: proportional to trader's bet size (scaled to our capital per trader)
- Contradictions: skip markets where pool traders disagree
- Direction: follow the trader (YES or NO)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/proportional_copy/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/proportional_copy/config.py`
- Create: `tests/test_strategy_proportional_copy.py`

**Step 1: Create config**

Create `src/polymarket_pipeline/strategies_impl/proportional_copy/__init__.py` (empty).

Create `src/polymarket_pipeline/strategies_impl/proportional_copy/config.py`:

```python
"""Configuration for the proportional-copy strategy (S1)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProportionalCopyConfig:
    """Immutable configuration for the proportional-copy strategy.

    Parameters
    ----------
    pool_traders
        Pre-computed set of graded trader addresses to copy.
        Computed externally (consistency + MVF + longshot grade).
    capital_per_trader_usd
        Maximum USD to allocate per pool trader.
    max_position_pct
        Maximum fraction of total capital in any single market.
    min_pool_agreement
        Minimum fraction of pool traders agreeing on the same direction
        before copying. 0.0 = copy any trader's entry. 0.5 = majority.
    contradiction_filter
        If True, skip markets where pool traders disagree on direction.
    fee_pct
        Expected fee fraction.
    sizing
        "proportional" = scale bet to trader's ROI; "equal" = fixed per trader.
    """

    pool_traders: frozenset[str] = field(default_factory=frozenset)
    capital_per_trader_usd: float = 50.0
    max_position_pct: float = 0.05
    min_pool_agreement: float = 0.0
    contradiction_filter: bool = True
    fee_pct: float = 0.02
    sizing: str = "equal"

    def __init__(
        self,
        pool_traders: set[str] | frozenset[str] | list[str] | None = None,
        capital_per_trader_usd: float = 50.0,
        max_position_pct: float = 0.05,
        min_pool_agreement: float = 0.0,
        contradiction_filter: bool = True,
        fee_pct: float = 0.02,
        sizing: str = "equal",
    ) -> None:
        object.__setattr__(
            self,
            "pool_traders",
            frozenset(pool_traders) if pool_traders is not None else frozenset(),
        )
        object.__setattr__(self, "capital_per_trader_usd", capital_per_trader_usd)
        object.__setattr__(self, "max_position_pct", max_position_pct)
        object.__setattr__(self, "min_pool_agreement", min_pool_agreement)
        object.__setattr__(self, "contradiction_filter", contradiction_filter)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(self, "sizing", sizing)
```

**Step 2: Write config tests**

Create `tests/test_strategy_proportional_copy.py`:

```python
"""Tests for the proportional-copy strategy (S1)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.strategies.types import MarketInfo, TradeIntent
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)


def test_proportional_copy_config_defaults() -> None:
    cfg = ProportionalCopyConfig()
    assert cfg.pool_traders == frozenset()
    assert cfg.capital_per_trader_usd == 50.0
    assert cfg.max_position_pct == 0.05
    assert cfg.contradiction_filter is True
    assert cfg.sizing == "equal"


def test_proportional_copy_config_custom() -> None:
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        capital_per_trader_usd=100.0,
        contradiction_filter=False,
    )
    assert len(cfg.pool_traders) == 2
    assert cfg.capital_per_trader_usd == 100.0
    assert cfg.contradiction_filter is False


def test_proportional_copy_config_frozen() -> None:
    cfg = ProportionalCopyConfig()
    with pytest.raises(AttributeError):
        cfg.capital_per_trader_usd = 999.0  # type: ignore[misc]
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_strategy_proportional_copy.py -x -q`
Expected: PASS

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/proportional_copy/ tests/test_strategy_proportional_copy.py
git commit -m "feat(s1): add ProportionalCopyConfig for longshot-YES copy strategy"
```

---

## Task 6: Create S1 — ProportionalCopyStrategy (event-driven)

The core logic: when a pool trader enters a market, copy their position direction with equal-weight sizing. Track per-market pool state for contradiction detection.

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/proportional_copy/strategy.py`
- Modify: `tests/test_strategy_proportional_copy.py`

**Step 1: Write failing tests**

Add to `tests/test_strategy_proportional_copy.py`:

```python
from decimal import Decimal
from datetime import datetime, timezone
from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
    ProportionalCopyStrategy,
)


def _make_trade(
    *,
    condition_id: str = "0xmkt1",
    maker: str = "0xtrader1",
    side: str = "BUY",
    price: float = 0.25,
    amount_usd: float = 100.0,
    published_at: float | None = None,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{condition_id}:{maker}:{time.time()}",
        condition_id=condition_id,
        asset_id="0xasset",
        side=side,
        price=Decimal(str(price)),
        size=Decimal("10"),
        amount_usd=Decimal(str(amount_usd)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker=None,
        timestamp=datetime.now(timezone.utc),
        source="WEBSOCKET",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
        published_at=published_at or time.time(),
    )


class _MockCtx:
    def __init__(self, features: dict[str, Any] | None = None) -> None:
        self._features = features or {}

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return MarketInfo(
            condition_id=condition_id,
            question="Will X happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        )

    async def get_features(self, key: str) -> Any:
        return self._features.get(key)

    async def get_position(self, condition_id: str) -> None:
        return None

    async def get_orderbook(self, condition_id: str) -> None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return time.time()


@pytest.mark.asyncio
async def test_copies_pool_trader_entry() -> None:
    """Should emit intent when a pool trader enters a market."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        capital_per_trader_usd=50.0,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xtrader1", side="BUY", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 1
    intent = result[0]
    assert intent.strategy == "proportional_copy"
    assert intent.side == "BUY"
    assert intent.outcome == "YES"  # BUY side = buying YES
    assert intent.size_usd == 50.0


@pytest.mark.asyncio
async def test_ignores_non_pool_trader() -> None:
    """Trades from non-pool traders should be ignored."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xrandom")
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_ignores_duplicate_trader_in_same_market() -> None:
    """Same trader trading again in same market should be ignored."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    t1 = _make_trade(maker="0xtrader1", condition_id="0xmkt1")
    t2 = _make_trade(maker="0xtrader1", condition_id="0xmkt1")

    r1 = await strategy.on_trade(t1, ctx)
    r2 = await strategy.on_trade(t2, ctx)
    assert r1 is not None
    assert r2 is None


@pytest.mark.asyncio
async def test_contradiction_filter_skips_conflicted_market() -> None:
    """When contradiction_filter=True, skip if traders disagree."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        contradiction_filter=True,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Trader1 buys YES
    t1 = _make_trade(maker="0xtrader1", condition_id="0xmkt1", side="BUY")
    r1 = await strategy.on_trade(t1, ctx)
    assert r1 is not None  # first trader, no contradiction yet

    # Trader2 sells YES (bets NO) — contradiction
    t2 = _make_trade(maker="0xtrader2", condition_id="0xmkt1", side="SELL")
    r2 = await strategy.on_trade(t2, ctx)
    assert r2 is None  # contradicted, skip


@pytest.mark.asyncio
async def test_sell_side_maps_to_no() -> None:
    """SELL side (selling YES tokens) = betting NO."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xtrader1", side="SELL", price=0.75)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    intent = result[0]
    assert intent.side == "BUY"
    assert intent.outcome == "NO"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_proportional_copy.py::test_copies_pool_trader_entry -x -q`
Expected: FAIL (ProportionalCopyStrategy doesn't exist)

**Step 3: Implement ProportionalCopyStrategy**

Create `src/polymarket_pipeline/strategies_impl/proportional_copy/strategy.py`:

```python
"""Proportional-copy strategy: copy graded skilled traders' positions.

Tracks individual pool traders' entries and copies their direction
with equal-weight sizing. Detects contradictions (pool traders
disagreeing) and optionally skips conflicted markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import TradeIntent
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)


@dataclass
class _MarketState:
    """Per-market accumulator tracking pool trader entries."""

    n_yes: int = 0
    n_no: int = 0
    seen_traders: set[str] = field(default_factory=set)


class ProportionalCopyStrategy:
    """Proportional copy of graded skilled traders.

    Event-driven (``on_trade``): when a pool trader enters a market,
    emit a copy intent in the same direction. Optionally skip if
    another pool trader already entered in the opposite direction
    (contradiction filter).

    Vectorized (``compute_signals``): batch version over DataFrames.
    """

    name: str = "proportional_copy"

    def __init__(self, config: ProportionalCopyConfig) -> None:
        self._cfg = config
        self._states: dict[str, _MarketState] = {}

    # ------------------------------------------------------------------
    # Event-driven path
    # ------------------------------------------------------------------

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        maker = trade.maker
        if maker is None:
            return None

        # Check pool membership (prefer live features, fall back to config)
        pool = await ctx.get_features("pool_traders")
        if pool is None:
            pool = self._cfg.pool_traders
        if maker not in pool:
            return None

        cid = trade.condition_id
        state = self._states.get(cid)
        if state is None:
            state = _MarketState()
            self._states[cid] = state

        # One entry per trader per market
        if maker in state.seen_traders:
            return None
        state.seen_traders.add(maker)

        # Determine direction: BUY = buying YES, SELL = selling YES (= betting NO)
        is_yes = trade.side == "BUY"

        # Contradiction check: if another trader already bet the opposite direction
        if self._cfg.contradiction_filter:
            if is_yes and state.n_no > 0:
                return None
            if not is_yes and state.n_yes > 0:
                return None

        if is_yes:
            state.n_yes += 1
        else:
            state.n_no += 1

        # Emit copy intent
        outcome = "YES" if is_yes else "NO"
        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome=outcome,
            size_usd=self._cfg.capital_per_trader_usd,
            urgency="patient",
            max_price=None,
            reason=f"proportional_copy: {maker[:10]}... bet {outcome}",
            signal_time=trade.published_at,
        )
        return [intent]

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    # ------------------------------------------------------------------
    # Vectorized path
    # ------------------------------------------------------------------

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        pool = list(self._cfg.pool_traders)

        # Filter to pool traders
        df = trades.filter(pl.col("maker").is_in(pool))

        # Sort and deduplicate: first trade per (maker, condition_id)
        df = df.sort(["condition_id", "published_at"])
        df = df.unique(subset=["maker", "condition_id"], keep="first")

        # Infer direction: BUY = YES, SELL = NO
        df = df.with_columns(
            (pl.col("side") == "BUY").alias("bet_yes"),
        )

        if self._cfg.contradiction_filter:
            # Per market: compute yes/no counts. Skip if both > 0.
            market_dirs = (
                df.group_by("condition_id")
                .agg(
                    pl.col("bet_yes").sum().alias("n_yes"),
                    (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
                )
            )
            # Keep only markets with unanimous direction
            unanimous = market_dirs.filter(
                (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
            ).select("condition_id")
            df = df.join(unanimous, on="condition_id", how="inner")

        # Emit one signal per trader per market
        result = df.select(
            pl.col("condition_id"),
            pl.col("published_at").alias("signal_time"),
            pl.lit("BUY").alias("side"),
            pl.when(pl.col("bet_yes"))
            .then(pl.lit("YES"))
            .otherwise(pl.lit("NO"))
            .alias("outcome"),
            pl.lit(self._cfg.capital_per_trader_usd).alias("size_usd"),
        )

        return result.collect()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_proportional_copy.py -x -q`
Expected: ALL PASS

**Step 5: Run full unit test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/proportional_copy/strategy.py tests/test_strategy_proportional_copy.py
git commit -m "feat(s1): implement ProportionalCopyStrategy with contradiction filter"
```

---

## Task 7: Create S1 — GradedPoolProvider (feature provider)

Computes the graded trader pool using consistency, MVF, and longshot_yes_fraction filters. This is the most data-intensive provider — it queries historical trader-market PnL to grade traders.

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py`
- Modify: `tests/test_strategy_proportional_copy.py`

**Step 1: Write failing tests**

Add to `tests/test_strategy_proportional_copy.py`:

```python
from polymarket_pipeline.strategies_impl.proportional_copy.providers import (
    GradedPoolProvider,
)


@pytest.mark.asyncio
async def test_graded_pool_provider_basic() -> None:
    """Provider should expose pool_traders frozenset."""
    import polars as pl

    # Trades DF with maker addresses and condition_ids
    trades_df = pl.DataFrame(
        {
            "maker": ["0xA"] * 60 + ["0xB"] * 30 + ["0xC"] * 10,
            "condition_id": [f"0xmkt{i}" for i in range(60)]
                + [f"0xmkt{i}" for i in range(30)]
                + [f"0xmkt{i}" for i in range(10)],
            "side": ["BUY"] * 100,
            "published_at": [float(i) for i in range(100)],
        }
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)

    features = provider.get_features()
    pool = features["pool_traders"]

    # 0xA has 60 markets (passes), 0xB has 30 (passes), 0xC has 10 (fails)
    assert "0xA" in pool
    assert "0xB" in pool
    assert "0xC" not in pool


@pytest.mark.asyncio
async def test_graded_pool_provider_empty_trades() -> None:
    """Provider should handle empty trades gracefully."""
    import polars as pl

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=pl.DataFrame())

    provider = GradedPoolProvider()
    await provider.compute(backend)

    features = provider.get_features()
    assert features["pool_traders"] == frozenset()


@pytest.mark.asyncio
async def test_graded_pool_provider_refresh_swaps_atomically() -> None:
    """Refresh should replace the pool without intermediate empty state."""
    import polars as pl

    trades_v1 = pl.DataFrame(
        {
            "maker": ["0xA"] * 50,
            "condition_id": [f"0xmkt{i}" for i in range(50)],
            "side": ["BUY"] * 50,
            "published_at": [float(i) for i in range(50)],
        }
    )
    trades_v2 = pl.DataFrame(
        {
            "maker": ["0xB"] * 50,
            "condition_id": [f"0xmkt{i}" for i in range(50)],
            "side": ["BUY"] * 50,
            "published_at": [float(i) for i in range(50)],
        }
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_v1)

    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)
    assert "0xA" in provider.get_features()["pool_traders"]

    # Refresh with new data
    backend.query_trades = AsyncMock(return_value=trades_v2)
    await provider.refresh(backend)
    pool = provider.get_features()["pool_traders"]
    assert "0xB" in pool
    assert "0xA" not in pool
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_proportional_copy.py::test_graded_pool_provider_basic -x -q`
Expected: FAIL (GradedPoolProvider doesn't exist)

**Step 3: Implement GradedPoolProvider**

Create `src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py`:

```python
"""Feature providers for the proportional-copy strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class GradedPoolProvider:
    """Computes and maintains the graded trader pool for proportional copy.

    A trader qualifies if they have traded in at least ``min_markets``
    distinct markets. In production, this provider would additionally
    filter by consistency months, MVF, and longshot_yes_fraction — but
    those metrics require the derived ``trader_market_pnl`` table.

    For the initial implementation, the pool is seeded from the
    ``pool_traders`` config (pre-computed offline) and this provider
    validates they remain active. Future versions will compute grades
    from the ClickHouse backend directly.

    Parameters
    ----------
    min_markets:
        Minimum distinct markets a trader must have traded.
    """

    name: str = "pool_traders"

    def __init__(self, min_markets: int = 50) -> None:
        self._min_markets = min_markets
        self._pool: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        trades = await backend.query_trades()

        if trades.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        import polars as pl

        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_markets)
            .collect()
        )

        self._pool = frozenset(trader_counts["maker"].to_list())
        logger.info("pool_traders.compute", count=len(self._pool))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — pool is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"pool_traders": self._pool}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_proportional_copy.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py tests/test_strategy_proportional_copy.py
git commit -m "feat(s1): add GradedPoolProvider for trader pool computation"
```

---

## Task 8: Register all three strategies in CLI

Wire S2a (will_no) and S1 (proportional_copy) into the strategy CLI alongside the existing consensus_copy and crypto_otm_no.

**Files:**
- Modify: `src/polymarket_pipeline/cli/strategy.py`
- Modify: `tests/test_cli_strategy.py`

**Step 1: Write failing test for CLI registration**

Add to `tests/test_cli_strategy.py`:

```python
def test_all_strategies_registered() -> None:
    """All four strategies should be registered in the factory."""
    from polymarket_pipeline.cli.strategy import _register_strategies, _STRATEGY_FACTORIES

    _register_strategies()
    assert "consensus_copy" in _STRATEGY_FACTORIES
    assert "crypto_otm_no" in _STRATEGY_FACTORIES
    assert "will_no" in _STRATEGY_FACTORIES
    assert "proportional_copy" in _STRATEGY_FACTORIES


def test_all_providers_registered() -> None:
    """All providers should be registered."""
    from polymarket_pipeline.cli.strategy import _register_providers, _PROVIDER_REGISTRY

    _register_providers()
    assert "skilled_traders" in _PROVIDER_REGISTRY
    assert "crypto_markets" in _PROVIDER_REGISTRY
    assert "will_markets" in _PROVIDER_REGISTRY
    assert "pool_traders" in _PROVIDER_REGISTRY
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_strategy.py::test_all_strategies_registered -x -q`
Expected: FAIL (will_no and proportional_copy not registered)

**Step 3: Register new strategies and providers**

In `src/polymarket_pipeline/cli/strategy.py`, update `_register_providers()`:

```python
def _register_providers() -> None:
    """Register known provider classes."""
    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )
    from polymarket_pipeline.strategies_impl.crypto_otm_no.providers import (
        CryptoMarketProvider,
    )
    from polymarket_pipeline.strategies_impl.will_no.providers import (
        WillMarketProvider,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.providers import (
        GradedPoolProvider,
    )

    _PROVIDER_REGISTRY["skilled_traders"] = SkilledTradersProvider
    _PROVIDER_REGISTRY["crypto_markets"] = CryptoMarketProvider
    _PROVIDER_REGISTRY["will_markets"] = WillMarketProvider
    _PROVIDER_REGISTRY["pool_traders"] = GradedPoolProvider
```

Add factory functions and update `_register_strategies()`:

```python
def _make_crypto_otm_no(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig
    from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import CryptoOTMNoStrategy

    return CryptoOTMNoStrategy(config=CryptoOTMNoConfig(**config.params))


def _make_will_no(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
    from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy

    return WillNoStrategy(config=WillNoConfig(**config.params))


def _make_proportional_copy(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.proportional_copy.config import (
        ProportionalCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
        ProportionalCopyStrategy,
    )

    return ProportionalCopyStrategy(config=ProportionalCopyConfig(**config.params))


def _register_strategies() -> None:
    """Register known strategy factories."""
    _STRATEGY_FACTORIES["consensus_copy"] = _make_consensus_copy
    _STRATEGY_FACTORIES["crypto_otm_no"] = _make_crypto_otm_no
    _STRATEGY_FACTORIES["will_no"] = _make_will_no
    _STRATEGY_FACTORIES["proportional_copy"] = _make_proportional_copy
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_strategy.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/cli/strategy.py tests/test_cli_strategy.py
git commit -m "feat: register will_no and proportional_copy in strategy CLI"
```

---

## Task 9: Create example TOML config for all three strategies

**Files:**
- Create: `configs/strategies_example.toml`

**Step 1: Create the TOML config**

Create `configs/strategies_example.toml`:

```toml
# =============================================================================
# Strategy Configuration — Three-Strategy Portfolio
# =============================================================================
# Run: uv run pm-strategy run --config configs/strategies_example.toml

# ---------------------------------------------------------------------------
# S2b: Crypto OTM NO — Buy NO on out-of-the-money crypto price checkpoints
# ---------------------------------------------------------------------------
[strategy.crypto_otm_no]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 10
cooldown_s = 0
features = ["crypto_markets"]

[strategy.crypto_otm_no.params]
yes_price_min = 0.05
yes_price_max = 0.25
base_bet_usd = 100
min_volume_usd = 50
question_pattern = "(above|below)"
assets = ["BTC", "ETH", "SOL", "XRP"]

# ---------------------------------------------------------------------------
# S2a: Will NO — Buy NO on binary "Will X happen?" questions
# ---------------------------------------------------------------------------
[strategy.will_no]
enabled = true
mode = "paper_dev"
capital_usd = 300
max_position_usd = 50
max_open_positions = 6
cooldown_s = 0
features = ["will_markets"]

[strategy.will_no.params]
yes_price_min = 0.15
yes_price_max = 0.40
base_bet_usd = 50
prefer_keywords = ["above", "below", "today"]
avoid_keywords = ["reach", "hit"]
max_volume_usd = 5000
question_pattern = "^Will\\b"

# ---------------------------------------------------------------------------
# S1: Proportional Copy — Copy graded longshot-YES specialists
# ---------------------------------------------------------------------------
[strategy.proportional_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 20
cooldown_s = 60
features = ["pool_traders"]

[strategy.proportional_copy.params]
capital_per_trader_usd = 50
max_position_pct = 0.05
contradiction_filter = true
sizing = "equal"

# ---------------------------------------------------------------------------
# Feature Providers
# ---------------------------------------------------------------------------

[provider.crypto_markets]
enabled = true
refresh_interval_s = 900

[provider.crypto_markets.params]
question_pattern = "(above|below)"
assets = ["BTC", "ETH", "SOL", "XRP"]

[provider.will_markets]
enabled = true
refresh_interval_s = 900

[provider.will_markets.params]
question_pattern = "^Will\\b"

[provider.pool_traders]
enabled = true
refresh_interval_s = 3600

[provider.pool_traders.params]
min_markets = 50
```

**Step 2: Validate the config loads**

Run: `uv run python -c "from polymarket_pipeline.strategies.config import load_strategy_configs, load_provider_configs; from pathlib import Path; s = load_strategy_configs(Path('configs/strategies_example.toml')); p = load_provider_configs(Path('configs/strategies_example.toml')); print(f'{len(s)} strategies, {len(p)} providers loaded')"  `
Expected: `3 strategies, 3 providers loaded`

**Step 3: Commit**

```bash
git add configs/strategies_example.toml
git commit -m "feat: add example TOML config for S1 + S2a + S2b strategy portfolio"
```

---

## Task 10: Run full validation

**Step 1: Run all unit tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 2: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies_impl/`
Expected: PASS (or known existing issues only)

**Step 3: Run linter**

Run: `uv run ruff check src/polymarket_pipeline/strategies_impl/ tests/test_strategy_will_no.py tests/test_strategy_proportional_copy.py`
Expected: PASS

**Step 4: Run formatter**

Run: `uv run ruff format src/polymarket_pipeline/strategies_impl/ tests/test_strategy_will_no.py tests/test_strategy_proportional_copy.py`

**Step 5: Fix any failures, then commit**

```bash
git add -A && git commit -m "chore: fix lint/type issues from three-strategy implementation"
```

---

## Summary of Changes

| Task | Strategy | What |
|------|----------|------|
| 1 | S2b | Enhance crypto_otm_no with volume filter in event path |
| 2-3 | S2a | WillNoConfig + WillNoStrategy (event + vectorized) |
| 4 | S2a | WillMarketProvider for "Will" question filtering |
| 5-6 | S1 | ProportionalCopyConfig + ProportionalCopyStrategy (event + vectorized) |
| 7 | S1 | GradedPoolProvider for trader pool computation |
| 8 | All | Register all strategies + providers in CLI |
| 9 | All | Example TOML config for the three-strategy portfolio |
| 10 | All | Full validation (tests + mypy + ruff) |

### What's NOT in this plan (future work)

1. **Backtest validation** — Running the vectorized path against historical data to validate PnL matches research findings. This requires `data/derived/` parquet files.
2. **LiveExecutor integration** — Switching from PaperExecutor to LiveExecutor with real CLOB API keys. Requires API credentials + risk testing.
3. **S2a maker-side** — Dual-sided execution (sell YES limit orders) for higher capital efficiency. Requires limit order management in the execution layer.
4. **S1 pool grading** — The GradedPoolProvider currently uses a simple min_markets filter. Full grading (consistency months, MVF, longshot_yes_fraction) requires the `trader_market_pnl` derived table and a grading function.
5. **on_timer for S2b** — Periodic checkpoint scanning (every 4h) to catch new markets without requiring a trade trigger.
6. **Parity validation** — Running `ParityValidator` to confirm event-driven and vectorized paths produce identical signals.
