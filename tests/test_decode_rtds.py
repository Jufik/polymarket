"""Tests for decode_rtds_payload — pure RTDS payload decoding into DecodedTrade."""

from datetime import UTC
from decimal import Decimal

from polymarket_pipeline.live.normalizers.decode.rtds import decode_rtds_payload
from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source

PAYLOAD: dict = {
    "asset": "90918587638565982552721929191997567810368069523533497523028836373246267159037",
    "conditionId": "0xaa6f622e00c696078424494dbcd331b8435275ef97d8dde2a0f66696db53a75d",
    "side": "BUY",
    "price": 0.36,
    "size": 164.67,
    "timestamp": 1770537659,
    "transactionHash": "0x2d5a647433c051d18ca7d855737f42d51f1301090b711e774d34da1f06fd9ffb",
    "proxyWallet": "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584",
    "outcome": "Up",
    "outcomeIndex": 0,
}


class TestDecodeRtdsPayload:
    def test_basic_decode(self):
        """Valid payload produces a DecodedTrade with pre-computed price/size."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert isinstance(decoded, DecodedTrade)
        assert decoded.asset_id == PAYLOAD["asset"]
        assert decoded.price == Decimal("0.36")
        assert decoded.size == Decimal("164.67")
        assert decoded.source == Source.RTDS
        assert decoded.is_backfill is False

    def test_ws_fields_are_none(self):
        """WS sources leave on-chain amount fields as None."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.maker_asset_id is None
        assert decoded.taker_asset_id is None
        assert decoded.maker_amount is None
        assert decoded.taker_amount is None

    def test_fee_is_zero(self):
        """RTDS does not provide fee info; fee_raw should be zero."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.fee_raw == Decimal("0")

    def test_maker_from_proxy_wallet(self):
        """maker comes from proxyWallet; taker is always None."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.maker == "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584"
        assert decoded.taker is None

    def test_tx_hash_preserved(self):
        """transactionHash from payload is preserved."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.tx_hash == PAYLOAD["transactionHash"]

    def test_order_hash_none(self):
        """RTDS does not provide order hash."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.order_hash is None

    def test_block_number_none(self):
        """RTDS does not provide block number."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.block_number is None

    def test_timestamp_from_payload(self):
        """Timestamp uses payload.timestamp (trade time), not delivery time."""
        decoded = decode_rtds_payload(PAYLOAD)
        assert decoded is not None
        assert decoded.timestamp.tzinfo == UTC
        assert decoded.timestamp.year == 2026

    def test_float_imprecision_rounded(self):
        """Float prices like 0.3996... are rounded to 2dp."""
        p = {**PAYLOAD, "price": 0.3996666666666667}
        decoded = decode_rtds_payload(p)
        assert decoded is not None
        assert decoded.price == Decimal("0.40")

    def test_dust_trade_returns_none(self):
        """Size rounding to zero returns None."""
        p = {**PAYLOAD, "size": 0.001}
        decoded = decode_rtds_payload(p)
        assert decoded is None

    def test_no_proxy_wallet(self):
        """Missing proxyWallet results in maker=None."""
        p = {k: v for k, v in PAYLOAD.items() if k != "proxyWallet"}
        decoded = decode_rtds_payload(p)
        assert decoded is not None
        assert decoded.maker is None

    def test_no_tx_hash(self):
        """Missing transactionHash results in tx_hash=None."""
        p = {k: v for k, v in PAYLOAD.items() if k != "transactionHash"}
        decoded = decode_rtds_payload(p)
        assert decoded is not None
        assert decoded.tx_hash is None
