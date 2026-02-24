"""Tests for strategy framework protocol definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from polymarket_pipeline.strategies.protocol import (
    Executor,
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


# ---------------------------------------------------------------------------
# Dummy implementations that satisfy each protocol
# ---------------------------------------------------------------------------


class DummyContext:
    """Minimal class satisfying StrategyContext protocol."""

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

    async def get_features(self, key: str) -> Any:
        return None


class DummyStrategy:
    """Minimal class satisfying Strategy protocol."""

    name: str = "dummy"

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None


class DummyVectorized:
    """Minimal class satisfying VectorizedStrategy protocol."""

    def compute_signals(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
        return pl.DataFrame()


class DummyExecutor:
    """Minimal class satisfying Executor protocol."""

    async def execute(self, intent: TradeIntent) -> Any:
        return None


# ---------------------------------------------------------------------------
# Incomplete implementations (should NOT satisfy protocols)
# ---------------------------------------------------------------------------


class IncompleteStrategy:
    """Missing on_timer method — should NOT satisfy Strategy."""

    name: str = "bad"

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        return None

    # on_timer intentionally missing


class IncompleteContext:
    """Missing get_price and now — should NOT satisfy StrategyContext."""

    async def get_position(self, condition_id: str) -> Position | None:
        return None

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return None

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        return None

    # get_price and now intentionally missing


class IncompleteVectorized:
    """Missing compute_signals — should NOT satisfy VectorizedStrategy."""

    pass


class IncompleteExecutor:
    """Missing execute — should NOT satisfy Executor."""

    pass


# ---------------------------------------------------------------------------
# StrategyContext protocol
# ---------------------------------------------------------------------------


class TestStrategyContext:
    def test_isinstance_check(self) -> None:
        ctx = DummyContext()
        assert isinstance(ctx, StrategyContext)

    def test_incomplete_fails(self) -> None:
        obj = IncompleteContext()
        assert not isinstance(obj, StrategyContext)

    async def test_get_position(self) -> None:
        ctx = DummyContext()
        result = await ctx.get_position("0xabc")
        assert result is None

    async def test_get_market(self) -> None:
        ctx = DummyContext()
        result = await ctx.get_market("0xabc")
        assert result is None

    async def test_get_orderbook(self) -> None:
        ctx = DummyContext()
        result = await ctx.get_orderbook("0xabc")
        assert result is None

    async def test_get_price(self) -> None:
        ctx = DummyContext()
        result = await ctx.get_price("0xabc", "YES")
        assert result is None

    async def test_now(self) -> None:
        ctx = DummyContext()
        result = await ctx.now()
        assert result == 0.0


# ---------------------------------------------------------------------------
# Strategy protocol
# ---------------------------------------------------------------------------


class TestStrategy:
    def test_isinstance_check(self) -> None:
        strat = DummyStrategy()
        assert isinstance(strat, Strategy)

    def test_incomplete_fails(self) -> None:
        obj = IncompleteStrategy()
        assert not isinstance(obj, Strategy)

    def test_has_name(self) -> None:
        strat = DummyStrategy()
        assert strat.name == "dummy"

    async def test_on_trade_returns_none(self) -> None:
        strat = DummyStrategy()
        ctx = DummyContext()
        # We pass a mock-like object; protocol only cares about structural type
        result = await strat.on_trade(None, ctx)  # type: ignore[arg-type]
        assert result is None

    async def test_on_market_update_returns_none(self) -> None:
        strat = DummyStrategy()
        ctx = DummyContext()
        result = await strat.on_market_update({"price": 0.5}, ctx)
        assert result is None

    async def test_on_timer_returns_none(self) -> None:
        strat = DummyStrategy()
        ctx = DummyContext()
        result = await strat.on_timer(1700000000.0, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# VectorizedStrategy protocol
# ---------------------------------------------------------------------------


class TestVectorizedStrategy:
    def test_isinstance_check(self) -> None:
        vs = DummyVectorized()
        assert isinstance(vs, VectorizedStrategy)

    def test_incomplete_fails(self) -> None:
        obj = IncompleteVectorized()
        assert not isinstance(obj, VectorizedStrategy)

    def test_compute_signals_returns_dataframe(self) -> None:
        vs = DummyVectorized()
        trades = pl.LazyFrame()
        markets = pl.LazyFrame()
        result = vs.compute_signals(trades, markets)
        assert isinstance(result, pl.DataFrame)


# ---------------------------------------------------------------------------
# Executor protocol
# ---------------------------------------------------------------------------


class TestExecutor:
    def test_isinstance_check(self) -> None:
        ex = DummyExecutor()
        assert isinstance(ex, Executor)

    def test_incomplete_fails(self) -> None:
        obj = IncompleteExecutor()
        assert not isinstance(obj, Executor)

    async def test_execute_returns_none(self) -> None:
        ex = DummyExecutor()
        intent = TradeIntent(
            strategy="test",
            condition_id="0xabc",
            side="BUY",
            outcome="YES",
            size_usd=100.0,
            urgency="immediate",
            max_price=0.65,
            reason="test",
            signal_time=1700000000.0,
        )
        result = await ex.execute(intent)
        assert result is None


# ---------------------------------------------------------------------------
# FeatureBackend protocol
# ---------------------------------------------------------------------------


class _StubBackend:
    async def query_trades(self, condition_ids: list[str] | None = None) -> pl.DataFrame:
        return pl.DataFrame()

    async def query_markets(self) -> pl.DataFrame:
        return pl.DataFrame()

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        return pl.DataFrame()


async def test_stub_backend_satisfies_feature_backend_protocol() -> None:
    from polymarket_pipeline.strategies.protocol import FeatureBackend

    assert isinstance(_StubBackend(), FeatureBackend)


# ---------------------------------------------------------------------------
# FeatureProvider protocol
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
# StrategyContext.get_features
# ---------------------------------------------------------------------------


class _CtxWithFeatures:
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

    async def get_features(self, key: str) -> Any:
        return None


async def test_ctx_with_get_features_satisfies_protocol() -> None:
    assert isinstance(_CtxWithFeatures(), StrategyContext)
