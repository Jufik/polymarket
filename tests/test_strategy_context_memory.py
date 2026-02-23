"""Tests for InMemoryContext — backtest + paper-dev backend."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.protocol import StrategyContext
from polymarket_pipeline.strategies.types import MarketInfo, Position

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> InMemoryContext:
    """Fresh in-memory context."""
    return InMemoryContext()


@pytest.fixture
def sample_position() -> Position:
    return Position(
        condition_id="0xabc",
        strategy="copy_no",
        qty_yes=0.0,
        qty_no=10.0,
        avg_entry_price=0.65,
        cost_basis=6.5,
        realized_pnl=0.0,
    )


@pytest.fixture
def sample_market() -> MarketInfo:
    return MarketInfo(
        condition_id="0xabc",
        question="Will X happen?",
        active=True,
        yes_price=0.40,
        no_price=0.60,
        event_id="evt_1",
        category="crypto",
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


async def test_satisfies_strategy_context_protocol(ctx: InMemoryContext) -> None:
    """InMemoryContext must be a structural subtype of StrategyContext."""
    assert isinstance(ctx, StrategyContext)


# ---------------------------------------------------------------------------
# get_position
# ---------------------------------------------------------------------------


async def test_get_position_returns_none_by_default(ctx: InMemoryContext) -> None:
    result = await ctx.get_position("0xmissing")
    assert result is None


async def test_get_position_returns_value_after_set(
    ctx: InMemoryContext, sample_position: Position
) -> None:
    ctx.set_position("0xabc", sample_position)
    result = await ctx.get_position("0xabc")
    assert result is sample_position


async def test_get_position_different_key(
    ctx: InMemoryContext, sample_position: Position
) -> None:
    ctx.set_position("0xabc", sample_position)
    result = await ctx.get_position("0xother")
    assert result is None


# ---------------------------------------------------------------------------
# get_market
# ---------------------------------------------------------------------------


async def test_get_market_returns_none_by_default(ctx: InMemoryContext) -> None:
    result = await ctx.get_market("0xmissing")
    assert result is None


async def test_get_market_returns_value_after_set(
    ctx: InMemoryContext, sample_market: MarketInfo
) -> None:
    ctx.set_market("0xabc", sample_market)
    result = await ctx.get_market("0xabc")
    assert result is sample_market


async def test_get_market_different_key(
    ctx: InMemoryContext, sample_market: MarketInfo
) -> None:
    ctx.set_market("0xabc", sample_market)
    result = await ctx.get_market("0xother")
    assert result is None


# ---------------------------------------------------------------------------
# get_price
# ---------------------------------------------------------------------------


async def test_get_price_yes(ctx: InMemoryContext, sample_market: MarketInfo) -> None:
    ctx.set_market("0xabc", sample_market)
    price = await ctx.get_price("0xabc", "YES")
    assert price == pytest.approx(0.40)


async def test_get_price_no(ctx: InMemoryContext, sample_market: MarketInfo) -> None:
    ctx.set_market("0xabc", sample_market)
    price = await ctx.get_price("0xabc", "NO")
    assert price == pytest.approx(0.60)


async def test_get_price_missing_market(ctx: InMemoryContext) -> None:
    price = await ctx.get_price("0xmissing", "YES")
    assert price is None


async def test_get_price_market_with_none_prices(ctx: InMemoryContext) -> None:
    """Market exists but prices are None."""
    market = MarketInfo(condition_id="0xabc", question="Q?", active=True)
    ctx.set_market("0xabc", market)
    assert await ctx.get_price("0xabc", "YES") is None
    assert await ctx.get_price("0xabc", "NO") is None


# ---------------------------------------------------------------------------
# now / set_time
# ---------------------------------------------------------------------------


async def test_now_returns_zero_by_default(ctx: InMemoryContext) -> None:
    assert await ctx.now() == 0.0


async def test_now_returns_updated_time(ctx: InMemoryContext) -> None:
    ctx.set_time(1700000000.0)
    assert await ctx.now() == 1700000000.0


async def test_set_time_multiple_updates(ctx: InMemoryContext) -> None:
    ctx.set_time(100.0)
    assert await ctx.now() == 100.0
    ctx.set_time(200.0)
    assert await ctx.now() == 200.0


# ---------------------------------------------------------------------------
# get_orderbook
# ---------------------------------------------------------------------------


async def test_get_orderbook_always_returns_none(ctx: InMemoryContext) -> None:
    result = await ctx.get_orderbook("0xanything")
    assert result is None


async def test_get_orderbook_returns_none_even_with_market_set(
    ctx: InMemoryContext, sample_market: MarketInfo
) -> None:
    ctx.set_market("0xabc", sample_market)
    result = await ctx.get_orderbook("0xabc")
    assert result is None
