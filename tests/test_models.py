"""Tests for NormalizedTrade model."""

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source


def test_normalized_trade_creation() -> None:
    trade = NormalizedTrade(
        trade_id="chain:abc123def456",
        condition_id="0x204d24f3a0f5dd5fca825292bdeab6a97af3978b2caa2b21bb37e610eddfff5d",
        asset_id="46434110155841033529384949983718980438706543876953886750286883506638610790525",
        side=Side.BUY,
        price=Decimal("0.68"),
        size=Decimal("100.50"),
        amount_usd=Decimal("68.34"),
        fee_usd=Decimal("0"),
        maker="0xa4a6fcb5df72529d4a",
        taker="0x1e057fb222bf2fdcb8",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash="0xbbcfa118b585eace1e",
        order_hash="0xdeadbeef",
        block_number=None,
        is_backfill=True,
        version=2,
    )
    assert trade.trade_id == "chain:abc123def456"
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.68")
    assert trade.version == 2


def test_normalized_trade_ws_no_addresses() -> None:
    """WebSocket trades have no maker/taker addresses."""
    trade = NormalizedTrade(
        trade_id="ws:abc123def456",
        condition_id="0x204d24f3",
        asset_id="46434110",
        side=Side.SELL,
        price=Decimal("0.32"),
        size=Decimal("786"),
        amount_usd=Decimal("251.52"),
        fee_usd=Decimal("0"),
        maker=None,
        taker=None,
        timestamp=datetime(2026, 2, 8, 8, 0, 0, tzinfo=UTC),
        source=Source.WEBSOCKET,
        tx_hash="0x27837a1de096",
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
    )
    assert trade.maker is None
    assert trade.taker is None
    assert trade.version == 1


def test_normalized_trade_rtds_has_proxy_wallet() -> None:
    """RTDS trades have proxyWallet as maker."""
    trade = NormalizedTrade(
        trade_id="ws:xyz789",
        condition_id="0xaa6f622e",
        asset_id="90918587",
        side=Side.BUY,
        price=Decimal("0.36"),
        size=Decimal("164.67"),
        amount_usd=Decimal("59.28"),
        fee_usd=Decimal("0"),
        maker="0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584",
        taker=None,
        timestamp=datetime(2026, 2, 8, 3, 0, 59, tzinfo=UTC),
        source=Source.RTDS,
        tx_hash="0x2d5a647433c0",
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
    )
    assert trade.maker == "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584"
    assert trade.source == Source.RTDS


def test_price_bounds_validation() -> None:
    """Price must be 0 <= price <= 1."""
    import pytest

    with pytest.raises(ValueError):
        NormalizedTrade(
            trade_id="chain:x",
            condition_id="0x1",
            asset_id="1",
            side=Side.BUY,
            price=Decimal("1.5"),
            size=Decimal("10"),
            amount_usd=Decimal("15"),
            fee_usd=Decimal("0"),
            maker=None,
            taker=None,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            source=Source.GOLDSKY_SINK,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            is_backfill=True,
            version=2,
        )


def test_source_alchemy_exists() -> None:
    from polymarket_pipeline.models import Source

    assert Source.ALCHEMY == "alchemy"
    assert "alchemy" in [s.value for s in Source]


def test_size_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError):
        NormalizedTrade(
            trade_id="chain:x",
            condition_id="0x1",
            asset_id="1",
            side=Side.BUY,
            price=Decimal("0.5"),
            size=Decimal("-10"),
            amount_usd=Decimal("5"),
            fee_usd=Decimal("0"),
            maker=None,
            taker=None,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            source=Source.GOLDSKY_SINK,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            is_backfill=True,
            version=2,
        )
