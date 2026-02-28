"""Tests for normalization pipeline types."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.live.normalizers.types import (
    DecodedTrade,
    MarketResolution,
    Rejection,
    ResolutionOutcome,
)
from polymarket_pipeline.models import Source

# ---------------------------------------------------------------------------
# ResolutionOutcome enum
# ---------------------------------------------------------------------------


class TestResolutionOutcome:
    def test_all_values(self) -> None:
        assert ResolutionOutcome.YES == "YES"
        assert ResolutionOutcome.NO == "NO"
        assert ResolutionOutcome.VOIDED == "VOIDED"

    def test_is_str_enum(self) -> None:
        # StrEnum values are also str
        assert isinstance(ResolutionOutcome.YES, str)

    def test_exactly_three_members(self) -> None:
        assert len(ResolutionOutcome) == 3


# ---------------------------------------------------------------------------
# DecodedTrade frozen dataclass
# ---------------------------------------------------------------------------


class TestDecodedTrade:
    @pytest.fixture()
    def sample_trade(self) -> DecodedTrade:
        return DecodedTrade(
            asset_id="464341101558",
            maker_asset_id="464341101558",
            taker_asset_id="789012345678",
            price=Decimal("0.68"),
            size=Decimal("100.5"),
            maker_amount=None,
            taker_amount=None,
            fee_raw=Decimal("0"),
            maker="0xabc",
            taker="0xdef",
            tx_hash="0x123",
            order_hash="0x456",
            block_number=12345,
            timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
            source=Source.WEBSOCKET,
            is_backfill=False,
        )

    def test_creation(self, sample_trade: DecodedTrade) -> None:
        assert sample_trade.asset_id == "464341101558"
        assert sample_trade.price == Decimal("0.68")
        assert sample_trade.source == Source.WEBSOCKET
        assert sample_trade.is_backfill is False

    def test_frozen(self, sample_trade: DecodedTrade) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_trade.asset_id = "other"  # type: ignore[misc]

    def test_nullable_fields(self) -> None:
        """On-chain sources have maker/taker amounts, WS sources have price/size."""
        trade = DecodedTrade(
            asset_id="464341101558",
            maker_asset_id=None,
            taker_asset_id=None,
            price=None,
            size=None,
            maker_amount=Decimal("68000000"),
            taker_amount=Decimal("100500000"),
            fee_raw=Decimal("0"),
            maker=None,
            taker=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
            source=Source.ALCHEMY,
            is_backfill=False,
        )
        assert trade.price is None
        assert trade.maker_amount == Decimal("68000000")
        assert trade.maker is None
        assert trade.block_number is None

    def test_is_dataclass(self, sample_trade: DecodedTrade) -> None:
        assert dataclasses.is_dataclass(sample_trade)


# ---------------------------------------------------------------------------
# Rejection frozen dataclass
# ---------------------------------------------------------------------------


class TestRejection:
    @pytest.fixture()
    def sample_rejection(self) -> Rejection:
        return Rejection(
            reason="taker_dedup",
            asset_id="464341101558",
            source=Source.ALCHEMY,
            timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        )

    def test_creation(self, sample_rejection: Rejection) -> None:
        assert sample_rejection.reason == "taker_dedup"
        assert sample_rejection.asset_id == "464341101558"
        assert sample_rejection.source == Source.ALCHEMY

    def test_frozen(self, sample_rejection: Rejection) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_rejection.reason = "other"  # type: ignore[misc]

    def test_default_details(self, sample_rejection: Rejection) -> None:
        assert sample_rejection.details == ""

    def test_custom_details(self) -> None:
        rej = Rejection(
            reason="price_out_of_range",
            asset_id="464341101558",
            source=Source.WEBSOCKET,
            timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
            details="price=1.5 exceeds [0, 1]",
        )
        assert rej.details == "price=1.5 exceeds [0, 1]"

    def test_is_dataclass(self, sample_rejection: Rejection) -> None:
        assert dataclasses.is_dataclass(sample_rejection)


# ---------------------------------------------------------------------------
# MarketResolution frozen dataclass
# ---------------------------------------------------------------------------


class TestMarketResolution:
    @pytest.fixture()
    def sample_resolution(self) -> MarketResolution:
        return MarketResolution(
            condition_id="0x204d24f3",
            outcome=ResolutionOutcome.YES,
            payout_per_token=1.0,
            source="clob_ws",
            timestamp=1718450400.0,
        )

    def test_creation(self, sample_resolution: MarketResolution) -> None:
        assert sample_resolution.condition_id == "0x204d24f3"
        assert sample_resolution.outcome == ResolutionOutcome.YES
        assert sample_resolution.payout_per_token == 1.0
        assert sample_resolution.source == "clob_ws"

    def test_frozen(self, sample_resolution: MarketResolution) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_resolution.condition_id = "other"  # type: ignore[misc]

    def test_default_settled_price_raw(self, sample_resolution: MarketResolution) -> None:
        assert sample_resolution.settled_price_raw is None

    def test_custom_settled_price_raw(self) -> None:
        res = MarketResolution(
            condition_id="0x204d24f3",
            outcome=ResolutionOutcome.NO,
            payout_per_token=0.0,
            source="rpc",
            timestamp=1718450400.0,
            settled_price_raw=1000000,
        )
        assert res.settled_price_raw == 1000000

    def test_voided_resolution(self) -> None:
        res = MarketResolution(
            condition_id="0x204d24f3",
            outcome=ResolutionOutcome.VOIDED,
            payout_per_token=0.5,
            source="clob_api",
            timestamp=1718450400.0,
        )
        assert res.outcome == ResolutionOutcome.VOIDED
        assert res.payout_per_token == 0.5

    def test_is_dataclass(self, sample_resolution: MarketResolution) -> None:
        assert dataclasses.is_dataclass(sample_resolution)
