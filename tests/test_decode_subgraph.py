"""Tests for decode_subgraph_event — pure Goldsky GraphQL decoding into DecodedTrade."""

from decimal import Decimal

from polymarket_pipeline.live.normalizers.decode.subgraph import decode_subgraph_event
from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source


def _make_event(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    maker_asset_id: str = "0",
    taker_asset_id: str = "12345",
    maker_amount: str = "500000000",
    taker_amount: str = "1000000000",
    fee: str = "5000000",
    timestamp: str = "1706800000",
    transaction_hash: str = "0x" + "dd" * 32,
    order_hash: str = "0x" + "cc" * 32,
    event_id: str = "evt_001",
) -> dict:
    """Build a mock Goldsky subgraph orderFilledEvent."""
    return {
        "id": event_id,
        "maker": maker,
        "taker": taker,
        "makerAssetId": maker_asset_id,
        "takerAssetId": taker_asset_id,
        "makerAmountFilled": maker_amount,
        "takerAmountFilled": taker_amount,
        "fee": fee,
        "timestamp": timestamp,
        "transactionHash": transaction_hash,
        "orderHash": order_hash,
    }


class TestDecodeSubgraphEvent:
    def test_basic_buy_decode(self):
        """BUY: makerAssetId='0' means maker provides USDC."""
        event = _make_event(
            maker_asset_id="0",
            taker_asset_id="12345",
            maker_amount="500000000",
            taker_amount="1000000000",
            fee="5000000",
        )
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert isinstance(decoded, DecodedTrade)
        assert decoded.asset_id == "12345"
        assert decoded.maker_asset_id == "0"
        assert decoded.taker_asset_id == "12345"
        assert decoded.maker_amount == Decimal("500000000")
        assert decoded.taker_amount == Decimal("1000000000")
        assert decoded.fee_raw == Decimal("5000000")
        assert decoded.source == Source.GOLDSKY_SUBGRAPH
        assert decoded.is_backfill is False

    def test_sell_decode(self):
        """SELL: makerAssetId != '0' means maker provides tokens."""
        event = _make_event(
            maker_asset_id="12345",
            taker_asset_id="0",
            maker_amount="1000000000",
            taker_amount="500000000",
        )
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.asset_id == "12345"
        assert decoded.maker_asset_id == "12345"
        assert decoded.taker_asset_id == "0"

    def test_price_size_are_none(self):
        """On-chain source: price/size computed by enrich, not decoder."""
        event = _make_event()
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.price is None
        assert decoded.size is None

    def test_maker_taker_preserved(self):
        """Maker and taker addresses are preserved."""
        maker = "0x" + "a1" * 20
        taker = "0x" + "b2" * 20
        event = _make_event(maker=maker, taker=taker)
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.maker == maker
        assert decoded.taker == taker

    def test_tx_hash_and_order_hash(self):
        """tx_hash and order_hash are preserved."""
        tx = "0x" + "dd" * 32
        oh = "0x" + "cc" * 32
        event = _make_event(transaction_hash=tx, order_hash=oh)
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.tx_hash == tx
        assert decoded.order_hash == oh

    def test_block_number_is_none(self):
        """Subgraph does not provide block_number."""
        event = _make_event()
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.block_number is None

    def test_timestamp_parsed(self):
        """Timestamp parsed from string seconds."""
        event = _make_event(timestamp="1706800000")
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.timestamp.year == 2024

    def test_taker_dedup_not_done(self):
        """Decoder does NOT filter taker duplicates (that is enrich's job)."""
        event = _make_event(taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e")
        decoded = decode_subgraph_event(event)
        # Should still return a DecodedTrade (enrich will filter)
        assert decoded is not None
        assert decoded.taker == "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"

    def test_missing_order_hash_defaults_empty(self):
        """Missing orderHash defaults to empty string."""
        event = _make_event()
        del event["orderHash"]
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        assert decoded.order_hash == ""
