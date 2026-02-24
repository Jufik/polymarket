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
        avg_entry_no=0.65,
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


async def test_get_position_different_key(ctx: InMemoryContext, sample_position: Position) -> None:
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


async def test_get_market_different_key(ctx: InMemoryContext, sample_market: MarketInfo) -> None:
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


# ---------------------------------------------------------------------------
# get_features / update_features
# ---------------------------------------------------------------------------


async def test_get_features_returns_none_when_empty(ctx: InMemoryContext) -> None:
    result = await ctx.get_features("skilled_traders")
    assert result is None


async def test_update_features_then_get(ctx: InMemoryContext) -> None:
    ctx.update_features({"skilled_traders": frozenset({"0xalice", "0xbob"})})
    result = await ctx.get_features("skilled_traders")
    assert result == frozenset({"0xalice", "0xbob"})


async def test_update_features_merges(ctx: InMemoryContext) -> None:
    ctx.update_features({"a": 1})
    ctx.update_features({"b": 2})
    assert await ctx.get_features("a") == 1
    assert await ctx.get_features("b") == 2


async def test_update_features_overwrites(ctx: InMemoryContext) -> None:
    ctx.update_features({"a": 1})
    ctx.update_features({"a": 99})
    assert await ctx.get_features("a") == 99


# ---------------------------------------------------------------------------
# set_orderbook / get_orderbook with actual data
# ---------------------------------------------------------------------------


async def test_get_orderbook_returns_snapshot_after_set(ctx: InMemoryContext) -> None:
    from polymarket_pipeline.strategies.types import OrderbookSnapshot

    ob = OrderbookSnapshot(
        condition_id="0xabc",
        best_bid=0.58,
        best_ask=0.62,
        bid_depth=1000.0,
        ask_depth=500.0,
        timestamp=1_700_000_000.0,
    )
    ctx.set_orderbook("0xabc", ob)
    result = await ctx.get_orderbook("0xabc")
    assert result is ob


# ---------------------------------------------------------------------------
# get_all_positions
# ---------------------------------------------------------------------------


async def test_get_all_positions_empty(ctx: InMemoryContext) -> None:
    """get_all_positions returns empty dict when no positions set."""
    result = ctx.get_all_positions()
    assert result == {}


async def test_get_all_positions_after_set(ctx: InMemoryContext, sample_position: Position) -> None:
    """get_all_positions returns stored positions."""
    ctx.set_position("0xabc", sample_position)
    result = ctx.get_all_positions()
    assert result == {"0xabc": sample_position}


async def test_get_all_positions_returns_copy(
    ctx: InMemoryContext, sample_position: Position
) -> None:
    """get_all_positions returns a copy, not the internal dict."""
    ctx.set_position("0xabc", sample_position)
    result = ctx.get_all_positions()
    result["0xnew"] = sample_position  # mutate the copy
    # Internal state should be unaffected
    assert "0xnew" not in ctx.get_all_positions()
