"""Tests for PaperExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.protocol import Executor
from polymarket_pipeline.strategies.types import (
    FillStatus,
    OrderbookSnapshot,
    TradeIntent,
)

CID = "0xabc"
YES_ASSET = "yes_token_123"
NO_ASSET = "no_token_456"


def _make_intent(
    *,
    side: str = "BUY",
    outcome: str = "YES",
    max_price: float | None = 0.65,
    size_usd: float = 100.0,
    condition_id: str = CID,
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


def _ob(asset_id: str, cid: str = CID, bid: float = 0.58, ask: float = 0.62) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        condition_id=cid, best_bid=bid, best_ask=ask,
        bid_depth=1000.0, ask_depth=500.0, timestamp=1_700_000_000.0,
    )


def _token_map() -> dict[str, dict[str, str]]:
    return {CID: {"YES": YES_ASSET, "NO": NO_ASSET}}


@pytest.fixture
def ctx() -> InMemoryContext:
    """Context with YES and NO orderbooks stored by asset_id."""
    ctx = InMemoryContext()
    # YES token: bid=0.58, ask=0.62
    ctx.set_orderbook(CID, _ob(YES_ASSET), asset_id=YES_ASSET)
    # NO token: bid=0.35, ask=0.40
    ctx.set_orderbook(CID, _ob(NO_ASSET, bid=0.35, ask=0.40), asset_id=NO_ASSET)
    return ctx


@pytest.fixture
def executor(ctx: InMemoryContext) -> PaperExecutor:
    return PaperExecutor(ctx=ctx, token_map=_token_map(), fee_pct=0.02)


async def test_satisfies_executor_protocol(executor: PaperExecutor) -> None:
    assert isinstance(executor, Executor)


async def test_fills_at_yes_ask_for_buy_yes(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(side="BUY", outcome="YES"))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62  # YES best_ask
    assert fill.filled_size_usd == 100.0


async def test_fills_at_yes_bid_for_sell_yes(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(side="SELL", outcome="YES"))
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.58  # YES best_bid


async def test_buy_no_uses_no_token_orderbook(executor: PaperExecutor) -> None:
    """BUY NO reads the NO token's orderbook directly — no flipping."""
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="NO", max_price=0.50)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == pytest.approx(0.40)  # NO best_ask


async def test_sell_no_uses_no_token_bid(executor: PaperExecutor) -> None:
    fill = await executor.execute(
        _make_intent(side="SELL", outcome="NO", max_price=None)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == pytest.approx(0.35)  # NO best_bid


async def test_rejects_when_no_orderbook() -> None:
    """No WS snapshot and no CLOB client → reject."""
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx, token_map=_token_map())
    fill = await executor.execute(_make_intent(max_price=None))
    assert fill.status == FillStatus.REJECTED
    assert "no orderbook" in fill.error


async def test_rejects_when_market_exceeds_limit(executor: PaperExecutor) -> None:
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.55)  # ask=0.62 > 0.55
    )
    assert fill.status == FillStatus.REJECTED
    assert "market" in fill.error


async def test_fills_when_market_within_limit(executor: PaperExecutor) -> None:
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=0.70)  # ask=0.62 < 0.70
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_no_limit_when_max_price_none(executor: PaperExecutor) -> None:
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="YES", max_price=None)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.62


async def test_fee_calculation(executor: PaperExecutor) -> None:
    fill = await executor.execute(_make_intent(max_price=0.70, size_usd=100.0))
    expected = 0.02 * min(0.62, 1.0 - 0.62) * 100.0
    assert fill.fee_usd == pytest.approx(expected)


async def test_clob_api_fallback_when_no_ws() -> None:
    """When WS has no snapshot, falls back to CLOB REST API /price."""
    from unittest.mock import MagicMock

    ctx = InMemoryContext()  # empty — no WS snapshots
    clob = MagicMock()
    # Mock the internal httpx AsyncClient.get() as an async method
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"price": "0.45"}
    mock_resp.raise_for_status = MagicMock()

    async def mock_get(*args: object, **kwargs: object) -> object:
        return mock_resp

    clob._client.get = mock_get

    executor = PaperExecutor(ctx=ctx, clob_client=clob, token_map=_token_map())
    fill = await executor.execute(
        _make_intent(side="BUY", outcome="NO", max_price=0.50)
    )
    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.45
