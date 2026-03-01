"""Shared pytest fixtures for research tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode


@pytest.fixture
def permissive_config() -> StrategyConfig:
    """StrategyConfig with no risk limits for testing."""
    return StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=1e9,
        max_position_usd=1e9,
        max_open_positions=100000,
        cooldown_s=0,
    )


def _make_trade(
    cid: str,
    ts: int,
    price: float = 0.50,
    asset_id: str = "asset_1",
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=Side.BUY,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(round(price * 100, 2))),
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
        published_at=float(ts),
    )


@pytest.fixture
def sample_trades() -> list[NormalizedTrade]:
    """Small set of NormalizedTrade objects for unit tests."""
    return [
        _make_trade("cid_A", 1000, 0.60),
        _make_trade("cid_A", 1100, 0.62),
        _make_trade("cid_B", 1200, 0.25),
        _make_trade("cid_B", 1300, 0.28),
        _make_trade("cid_C", 1400, 0.85),
    ]


@pytest.fixture
def sample_resolutions() -> dict[str, tuple[str, float]]:
    """Resolution data matching sample_trades markets."""
    return {
        "cid_A": ("YES", 1700000000.0),
        "cid_B": ("NO", 1700000000.0),
        "cid_C": ("YES", 1700000000.0),
    }
