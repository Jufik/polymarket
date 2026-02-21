"""Tests for PolygonRPCNormalizer — ABI decoding of raw Polygon log events."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source

ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

    return PolygonRPCNormalizer()


def _make_log(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = 12345,
    taker_asset_id: int = 0,
    maker_amount: int = 1000_000_000,  # 1000 tokens (when BUY: maker provides tokens)
    taker_amount: int = 500_000_000,  # 500 USDC (when BUY: taker pays USDC)
    fee: int = 5_000_000,  # 5 USDC
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
    timestamp: int = 1706800000,
) -> dict:
    """Build a mock raw Polygon log event for OrderFilled."""
    from eth_abi import encode

    # Encode non-indexed params: makerAssetId, takerAssetId, makerAmountFilled,
    # takerAmountFilled, fee
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )

    # topics[0] = event sig, topics[1] = orderHash (indexed bytes32),
    # topics[2] = maker (indexed address), topics[3] = taker (indexed address)
    return {
        "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        "topics": [
            ORDER_FILLED_SIG,
            order_hash,
            "0x" + "00" * 12 + maker[2:],  # address padded to 32 bytes
            "0x" + "00" * 12 + taker[2:],  # address padded to 32 bytes
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "transactionIndex": "0x1",
        "logIndex": "0x0",
        # We inject timestamp from block data (not part of raw log)
        "_timestamp": timestamp,
    }


class TestPolygonRPCNormalizer:
    def test_basic_buy_trade(self, normalizer):
        """BUY: taker pays USDC (takerAssetId=0), maker provides tokens."""
        log = _make_log(
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount=1000_000_000,  # 1000 tokens
            taker_amount=500_000_000,  # 500 USDC
            fee=5_000_000,  # 5 USDC
        )
        trade = normalizer.normalize(log)
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.price == Decimal("0.5000")  # 500/1000
        assert trade.size == Decimal("1000")
        assert trade.amount_usd == Decimal("500")
        assert trade.fee_usd == Decimal("5")
        assert trade.source == Source.ALCHEMY
        assert trade.version == 2
        assert trade.maker is not None
        assert trade.taker is not None
        assert trade.tx_hash is not None
        assert trade.trade_id.startswith("chain:")

    def test_sell_trade(self, normalizer):
        """SELL: taker provides tokens (takerAssetId!=0), maker pays USDC."""
        log = _make_log(
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount=300_000_000,  # 300 USDC
            taker_amount=500_000_000,  # 500 tokens
            fee=3_000_000,
        )
        trade = normalizer.normalize(log)
        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.price == Decimal("0.6000")  # 300/500
        assert trade.size == Decimal("500")
        assert trade.amount_usd == Decimal("300")

    def test_taker_duplicate_dropped(self, normalizer):
        """Taker-perspective events (taker == exchange contract) return None."""
        log = _make_log(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        trade = normalizer.normalize(log)
        assert trade is None

    def test_negrisk_exchange_taker_dropped(self, normalizer):
        """NegRisk exchange taker also dropped."""
        log = _make_log(
            taker="0xc5d563a36ae78145c45a50134d48a1215220f80a",
        )
        trade = normalizer.normalize(log)
        assert trade is None

    def test_asset_id_extracted(self, normalizer):
        """Non-USDC asset_id becomes the trade's asset_id."""
        log = _make_log(maker_asset_id=99999, taker_asset_id=0)
        trade = normalizer.normalize(log)
        assert trade.asset_id == "99999"

    def test_condition_id_empty_without_map(self, normalizer):
        """Without a token_map, condition_id defaults to asset_id (best effort)."""
        log = _make_log()
        trade = normalizer.normalize(log)
        # Without token_map, condition_id should be the asset_id (best effort)
        assert trade.condition_id != ""

    def test_with_token_map(self):
        """With token_map, asset_id is resolved to condition_id."""
        from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

        token_map = {"12345": ("cond_abc", "YES")}
        n = PolygonRPCNormalizer(token_market_map=token_map)
        log = _make_log(maker_asset_id=12345, taker_asset_id=0)
        trade = n.normalize(log)
        assert trade.condition_id == "cond_abc"

    def test_non_order_filled_event_skipped(self, normalizer):
        """Events with wrong signature or too few topics return None."""
        log = _make_log()
        # Wrong event signature
        log["topics"][0] = "0x" + "00" * 32
        assert normalizer.normalize(log) is None

    def test_too_few_topics_skipped(self, normalizer):
        """Logs with <4 topics (different event type) return None."""
        log = _make_log()
        log["topics"] = log["topics"][:2]  # only sig + 1 indexed param
        assert normalizer.normalize(log) is None
