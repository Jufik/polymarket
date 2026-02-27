"""Tests for PaperExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.execution.clob_client import ClobOrderbook
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.protocol import Executor
from polymarket_pipeline.strategies.types import (
    FillStatus,
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


def _mock_clob(bid: float = 0.58, ask: float = 0.62) -> AsyncMock:
    """Create a mock ClobClient that returns a fixed orderbook."""
    clob = AsyncMock()
    clob.get_orderbook.return_value = ClobOrderbook(
        best_bid=bid, best_ask=ask, spread=round(ask - bid, 6), fetched_at=1_700_000_000.0,
    )
    return clob


def _token_map(cid: str = "0xabc") -> dict[str, dict[str, str]]:
    return {cid: {"YES": "yes_token", "NO": "no_token"}}


@pytest.fixture
def ctx() -> InMemoryContext:
    return InMemoryContext()


@pytest.fixture
def executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(
        ctx=ctx, clob_client=_mock_clob(), token_map=_token_map(), fee_pct=0.02,
    )


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


async def test_buy_no_uses_no_token_book(ctx: InMemoryContext) -> None:
    """BUY NO queries the NO token book directly — no flipping."""
    clob = _mock_clob(bid=0.85, ask=0.90)  # NO book: bid=0.85, ask=0.90
    executor = PaperExecutor(ctx=ctx, clob_client=clob, token_map=_token_map())
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="NO", max_price=0.95)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == pytest.approx(0.90)  # NO best_ask
    # Verify the NO token was queried
    clob.get_orderbook.assert_called_with("no_token")


async def test_rejects_when_no_clob_client() -> None:
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)  # no clob_client
    fill = await executor.execute(_make_intent())
    assert fill.status == FillStatus.REJECTED
    assert "no orderbook" in fill.error


async def test_rejects_when_api_returns_none(ctx: InMemoryContext) -> None:
    clob = AsyncMock()
    clob.get_orderbook.return_value = None
    executor = PaperExecutor(ctx=ctx, clob_client=clob, token_map=_token_map())
    fill = await executor.execute(_make_intent())
    assert fill.status == FillStatus.REJECTED


async def test_rejects_wide_spread_book(ctx: InMemoryContext) -> None:
    """Books with spread >= 0.50 are skipped (illiquid)."""
    clob = _mock_clob(bid=0.01, ask=0.99)  # spread = 0.98
    executor = PaperExecutor(ctx=ctx, clob_client=clob, token_map=_token_map())
    fill = await executor.execute(_make_intent())
    assert fill.status == FillStatus.REJECTED


async def test_rejects_when_market_exceeds_limit(ctx: InMemoryContext) -> None:
    """max_price acts as limit — reject if market price is higher."""
    executor = PaperExecutor(ctx=ctx, clob_client=_mock_clob(), token_map=_token_map())
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.55)  # ask=0.62 > 0.55
    )
    assert fill.status == FillStatus.REJECTED
    assert "market" in fill.error


async def test_fills_when_market_within_limit(ctx: InMemoryContext) -> None:
    executor = PaperExecutor(ctx=ctx, clob_client=_mock_clob(), token_map=_token_map())
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.70)  # ask=0.62 < 0.70
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_no_limit_when_max_price_none(ctx: InMemoryContext) -> None:
    executor = PaperExecutor(ctx=ctx, clob_client=_mock_clob(), token_map=_token_map())
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=None)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_fee_calculation(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.70, size_usd=100.0))
    expected = 0.02 * min(0.62, 1.0 - 0.62) * 100.0  # fills at ask=0.62
    assert fill.fee_usd == pytest.approx(expected)
