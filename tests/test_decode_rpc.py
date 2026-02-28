"""Tests for decode_rpc_log — pure ABI decoding into DecodedTrade."""

from decimal import Decimal

from eth_abi import encode

from polymarket_pipeline.live.normalizers.decode.rpc import decode_rpc_log
from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source

ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"


def _make_log(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = 12345,
    taker_asset_id: int = 0,
    maker_amount: int = 1_000_000_000,
    taker_amount: int = 500_000_000,
    fee: int = 5_000_000,
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
    timestamp: int = 1706800000,
) -> dict:
    """Build a mock raw Polygon log event for OrderFilled."""
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )
    return {
        "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        "topics": [
            ORDER_FILLED_SIG,
            order_hash,
            "0x" + "00" * 12 + maker[2:],
            "0x" + "00" * 12 + taker[2:],
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "transactionIndex": "0x1",
        "logIndex": "0x0",
        "_timestamp": timestamp,
    }


class TestDecodeRpcLog:
    def test_basic_buy_decode(self):
        """BUY trade (taker_asset_id=0) decodes into DecodedTrade."""
        log = _make_log(
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount=1_000_000_000,
            taker_amount=500_000_000,
            fee=5_000_000,
        )
        decoded = decode_rpc_log(log)
        assert decoded is not None
        assert isinstance(decoded, DecodedTrade)
        assert decoded.asset_id == "12345"
        assert decoded.maker_asset_id == "12345"
        assert decoded.taker_asset_id == "0"
        assert decoded.maker_amount == Decimal("1000000000")
        assert decoded.taker_amount == Decimal("500000000")
        assert decoded.fee_raw == Decimal("5000000")
        assert decoded.source == Source.ALCHEMY
        assert decoded.is_backfill is False
        assert decoded.block_number == 50_000_000
        # price/size are None (computed by enrich)
        assert decoded.price is None
        assert decoded.size is None

    def test_sell_decode(self):
        """SELL trade (taker_asset_id != 0) extracts correct asset_id."""
        log = _make_log(maker_asset_id=0, taker_asset_id=99999)
        decoded = decode_rpc_log(log)
        assert decoded is not None
        assert decoded.asset_id == "99999"
        assert decoded.maker_asset_id == "0"
        assert decoded.taker_asset_id == "99999"

    def test_non_order_filled_returns_none(self):
        """Non-OrderFilled events return None."""
        log = _make_log()
        log["topics"][0] = "0x" + "00" * 32
        assert decode_rpc_log(log) is None

    def test_too_few_topics_returns_none(self):
        """Logs with < 4 topics return None."""
        log = _make_log()
        log["topics"] = log["topics"][:2]
        assert decode_rpc_log(log) is None

    def test_maker_taker_addresses_extracted(self):
        """Maker and taker addresses are extracted from padded topics."""
        maker = "0x" + "a1" * 20
        taker = "0x" + "b2" * 20
        log = _make_log(maker=maker, taker=taker)
        decoded = decode_rpc_log(log)
        assert decoded is not None
        assert decoded.maker == maker
        assert decoded.taker == taker

    def test_tx_hash_and_order_hash(self):
        """tx_hash and order_hash are preserved."""
        tx = "0x" + "dd" * 32
        oh = "0x" + "cc" * 32
        log = _make_log(tx_hash=tx, order_hash=oh)
        decoded = decode_rpc_log(log)
        assert decoded is not None
        assert decoded.tx_hash == tx
        assert decoded.order_hash == oh

    def test_block_timestamp_override(self):
        """Explicit block_timestamp arg takes precedence over _timestamp."""
        log = _make_log(timestamp=1000000)
        decoded = decode_rpc_log(log, block_timestamp=2000000.0)
        assert decoded is not None
        assert decoded.timestamp.timestamp() == 2000000.0

    def test_block_number_from_hex(self):
        """blockNumber hex string is correctly parsed."""
        log = _make_log(block_number=0xFF)
        decoded = decode_rpc_log(log)
        assert decoded is not None
        assert decoded.block_number == 255

    def test_taker_dedup_not_done(self):
        """Decoder does NOT filter taker duplicates (that is enrich's job)."""
        log = _make_log(taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e")
        decoded = decode_rpc_log(log)
        # Should still return a DecodedTrade (enrich will filter)
        assert decoded is not None
        assert decoded.taker == "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
