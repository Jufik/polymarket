"""Tests for SkilledTradersProvider."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.protocol import FeatureProvider
from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
    SkilledTradersProvider,
)


def _make_backend(traders_data: list[dict[str, Any]]) -> PolarsBackend:
    """Build a PolarsBackend from a list of trade dicts."""
    trades = (
        pl.DataFrame(traders_data)
        if traders_data
        else pl.DataFrame({"condition_id": [], "maker": [], "side": [], "published_at": []})
    )
    markets = pl.DataFrame({"condition_id": [], "question": [], "active": []})
    return PolarsBackend(trades=trades, markets=markets)


@pytest.fixture
def backend_with_skilled() -> PolarsBackend:
    """Backend where alice has 10 distinct markets (skilled) and bob has 2."""
    trades = []
    for i in range(10):
        trades.append(
            {
                "condition_id": f"0xmarket_{i}",
                "maker": "0xalice",
                "side": "BUY",
                "published_at": float(i),
            }
        )
    for i in range(2):
        trades.append(
            {
                "condition_id": f"0xmarket_{i}",
                "maker": "0xbob",
                "side": "BUY",
                "published_at": float(100 + i),
            }
        )
    return _make_backend(trades)


async def test_satisfies_feature_provider_protocol() -> None:
    provider = SkilledTradersProvider(min_trades=5)
    assert isinstance(provider, FeatureProvider)


async def test_provider_name() -> None:
    provider = SkilledTradersProvider()
    assert provider.name == "skilled_traders"


async def test_compute_identifies_skilled(backend_with_skilled: PolarsBackend) -> None:
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    features = provider.get_features()
    skilled = features["skilled_traders"]
    assert "0xalice" in skilled
    assert "0xbob" not in skilled


async def test_compute_empty_backend() -> None:
    backend = _make_backend([])
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend)
    assert provider.get_features()["skilled_traders"] == frozenset()


async def test_on_trade_is_noop(backend_with_skilled: PolarsBackend) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    from polymarket_pipeline.models import NormalizedTrade, Side, Source

    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    before = provider.get_features()["skilled_traders"]

    trade = NormalizedTrade(
        trade_id="test:1",
        condition_id="0xnew",
        asset_id="a1",
        side=Side.BUY,
        price=Decimal("0.50"),
        size=Decimal("100"),
        amount_usd=Decimal("50"),
        fee_usd=Decimal("0"),
        maker="0xnewtrader",
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(999, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=999.0,
    )
    await provider.on_trade(trade)
    after = provider.get_features()["skilled_traders"]
    assert before == after


async def test_refresh_updates(backend_with_skilled: PolarsBackend) -> None:
    provider = SkilledTradersProvider(min_trades=5)
    await provider.compute(backend_with_skilled)
    assert "0xalice" in provider.get_features()["skilled_traders"]

    # Refresh with empty backend -> clears the set
    empty_backend = _make_backend([])
    await provider.refresh(empty_backend)
    assert provider.get_features()["skilled_traders"] == frozenset()


async def test_get_features_before_compute() -> None:
    provider = SkilledTradersProvider(min_trades=5)
    assert provider.get_features()["skilled_traders"] == frozenset()
