"""Tests for the shared validate stage (price clamping, size checks)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from polymarket_pipeline.live.normalizers.types import Rejection
from polymarket_pipeline.live.normalizers.validate import validate
from polymarket_pipeline.models import NormalizedTrade, Side, Source


def _make_trade(**overrides: Any) -> NormalizedTrade:
    """Create a valid NormalizedTrade with sensible defaults.

    Uses ``model_construct`` to bypass field validators so that tests can
    inject out-of-range price / size values.
    """
    defaults: dict[str, Any] = {
        "trade_id": "test:001",
        "condition_id": "0xcondition",
        "asset_id": "12345",
        "side": Side.BUY,
        "price": Decimal("0.5000"),
        "size": Decimal("100"),
        "amount_usd": Decimal("50"),
        "fee_usd": Decimal("0"),
        "maker": "0xmaker",
        "taker": "0xtaker",
        "timestamp": datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        "source": Source.ALCHEMY,
        "tx_hash": "0xabc",
        "order_hash": "0xdef",
        "block_number": 100,
        "is_backfill": False,
        "version": 2,
        "published_at": 0.0,
    }
    defaults.update(overrides)
    return NormalizedTrade.model_construct(**defaults)


# ---------------------------------------------------------------------------
# Valid trade passes through unchanged
# ---------------------------------------------------------------------------


class TestValidTradePassthrough:
    def test_valid_trade_unchanged(self) -> None:
        trade = _make_trade()
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result is trade  # exact same object, no copy

    def test_price_exactly_zero_passes(self) -> None:
        trade = _make_trade(price=Decimal("0.0000"), amount_usd=Decimal("0"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.0000")

    def test_price_exactly_one_passes(self) -> None:
        trade = _make_trade(price=Decimal("1.0000"), amount_usd=Decimal("100"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("1.0000")


# ---------------------------------------------------------------------------
# Zero / negative size → Rejection
# ---------------------------------------------------------------------------


class TestSizeRejection:
    def test_zero_size_rejected(self) -> None:
        trade = _make_trade(size=Decimal("0"))
        result = validate(trade)
        assert isinstance(result, Rejection)
        assert result.reason == "zero_size"
        assert result.asset_id == "12345"
        assert result.source == Source.ALCHEMY

    def test_negative_size_rejected(self) -> None:
        trade = _make_trade(size=Decimal("-10"))
        result = validate(trade)
        assert isinstance(result, Rejection)
        assert result.reason == "zero_size"
        assert result.asset_id == "12345"
        assert result.source == Source.ALCHEMY


# ---------------------------------------------------------------------------
# Price clamping
# ---------------------------------------------------------------------------


class TestPriceClamping:
    def test_price_above_one_clamped(self) -> None:
        trade = _make_trade(price=Decimal("1.5"), size=Decimal("100"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("1.0000")
        # amount_usd recomputed: 1.0 * 100
        assert result.amount_usd == Decimal("100")

    def test_price_below_zero_clamped(self) -> None:
        trade = _make_trade(price=Decimal("-0.05"), size=Decimal("200"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.0000")
        # amount_usd recomputed: 0.0 * 200
        assert result.amount_usd == Decimal("0")

    def test_clamped_trade_preserves_other_fields(self) -> None:
        trade = _make_trade(
            price=Decimal("2.0"),
            size=Decimal("50"),
            trade_id="test:preserve",
            condition_id="0xpreserve",
            maker="0xmaker_orig",
        )
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.trade_id == "test:preserve"
        assert result.condition_id == "0xpreserve"
        assert result.maker == "0xmaker_orig"
        assert result.size == Decimal("50")
        assert result.price == Decimal("1.0000")
        assert result.amount_usd == Decimal("50")
