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


def _ob(cid: str = "0xabc", bid: float = 0.58, ask: float = 0.62) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        condition_id=cid, best_bid=bid, best_ask=ask,
        bid_depth=1000.0, ask_depth=500.0, timestamp=1_700_000_000.0,
    )


def _make_intent(
    *,
    side: str = "BUY",
    outcome: str = "YES",
    max_price: float | None = 0.65,
    size_usd: float = 100.0,
    condition_id: str = "0xabc",
) -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id=condition_id,
        side=side,
        outcome=outcome,
        size_usd=size_usd,
        urgency="immediate",
        max_price=max_price,
        reason="test",
        signal_time=1_700_000_000.0,
    )


@pytest.fixture
def ctx() -> InMemoryContext:
    ctx = InMemoryContext()
    ctx.set_orderbook("0xabc", _ob())
    return ctx


@pytest.fixture
def executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(ctx=ctx, fee_pct=0.02)


async def test_satisfies_executor_protocol(executor: PaperExecutor) -> None:
    assert isinstance(executor, Executor)


async def test_fills_at_orderbook_ask_for_buy_yes(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(side="BUY", outcome="YES"))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62  # best_ask
    assert fill.filled_size_usd == 100.0


async def test_fills_at_orderbook_bid_for_sell_yes(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(side="SELL", outcome="YES"))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.58  # best_bid


async def test_buy_no_uses_flipped_price(ctx: InMemoryContext) -> None:
    """BUY NO → NO ask = 1 - YES bid."""
    ctx.set_orderbook("0xabc", _ob(bid=0.10, ask=0.12))
    executor = PaperExecutor(ctx=ctx)
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="NO", max_price=0.95)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == pytest.approx(0.90)  # 1 - 0.10


async def test_rejects_when_no_orderbook() -> None:
    ctx = InMemoryContext()  # no orderbook set
    executor = PaperExecutor(ctx=ctx)
    fill = await executor.execute(_make_intent())
    assert fill.status == FillStatus.REJECTED
    assert "no orderbook" in fill.error


async def test_rejects_when_market_exceeds_limit(ctx: InMemoryContext) -> None:
    """max_price acts as limit — reject if market price is higher."""
    fill = await ctx_executor(ctx).execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.55)  # ask=0.62 > 0.55
    )
    assert fill.status == FillStatus.REJECTED
    assert "market" in fill.error


async def test_fills_when_market_within_limit(ctx: InMemoryContext) -> None:
    fill = await ctx_executor(ctx).execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.70)  # ask=0.62 < 0.70
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_no_limit_when_max_price_none(ctx: InMemoryContext) -> None:
    fill = await ctx_executor(ctx).execute(
        _make_intent(side="BUY", outcome="YES", max_price=None)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_fee_calculation(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.70, size_usd=100.0))
    expected = 0.02 * min(0.62, 1.0 - 0.62) * 100.0  # fills at ask=0.62
    assert fill.fee_usd == pytest.approx(expected)


def ctx_executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(ctx=ctx)
