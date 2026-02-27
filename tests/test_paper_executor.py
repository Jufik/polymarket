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
    return InMemoryContext()


@pytest.fixture
def executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(ctx=ctx, fee_pct=0.02)


async def test_satisfies_executor_protocol(executor: PaperExecutor) -> None:
    assert isinstance(executor, Executor)


async def test_fills_at_max_price_when_no_orderbook(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.65))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.65
    assert fill.filled_size_usd == 100.0


async def test_rejects_when_no_ob_no_max_price(
    executor: PaperExecutor,
) -> None:
    fill = await executor.execute(_make_intent(max_price=None))
    assert fill.status == FillStatus.REJECTED


async def test_fills_at_best_ask_for_buy_yes(
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
    fill = await executor.execute(_make_intent(side="BUY", outcome="YES", condition_id="0xabc"))
    assert fill.filled_price == 0.62


async def test_fills_at_best_bid_for_sell_yes(
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
    fill = await executor.execute(_make_intent(side="SELL", outcome="YES", condition_id="0xabc"))
    assert fill.filled_price == 0.58


async def test_buy_no_uses_flipped_price(
    ctx: InMemoryContext,
) -> None:
    """BUY NO should use 1 - YES_bid as the fill price."""
    ctx.set_orderbook(
        "0xabc",
        OrderbookSnapshot(
            condition_id="0xabc",
            best_bid=0.10,
            best_ask=0.12,
            bid_depth=1000.0,
            ask_depth=500.0,
            timestamp=1_700_000_000.0,
        ),
    )
    executor = PaperExecutor(ctx=ctx)
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="NO", max_price=0.95, condition_id="0xabc")
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == pytest.approx(0.90)  # 1 - 0.10


async def test_rejects_large_price_gap(
    ctx: InMemoryContext,
) -> None:
    """Reject fill when market price diverges too much from max_price."""
    ctx.set_orderbook(
        "0xabc",
        OrderbookSnapshot(
            condition_id="0xabc",
            best_bid=0.05,
            best_ask=0.07,
            bid_depth=1000.0,
            ask_depth=500.0,
            timestamp=1_700_000_000.0,
        ),
    )
    executor = PaperExecutor(ctx=ctx, max_price_gap=0.10)
    # max_price=0.50 but market ask=0.07 → gap=0.43 > 0.10
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.50, condition_id="0xabc")
    )
    assert fill.status == FillStatus.REJECTED


async def test_fee_calculation(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.65, size_usd=100.0))
    expected = 0.02 * min(0.65, 1.0 - 0.65) * 100.0
    assert fill.fee_usd == pytest.approx(expected)
