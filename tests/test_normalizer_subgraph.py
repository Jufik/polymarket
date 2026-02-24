"""Tests for SubgraphNormalizer — Goldsky GraphQL orderFilledEvents."""

import pytest

from polymarket_pipeline.models import Side, Source


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer

    token_map = {"12345": ("cond_abc", "YES")}
    return SubgraphNormalizer(token_market_map=token_map)


def _make_event(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    maker_asset_id: str = "0",
    taker_asset_id: str = "12345",
    maker_amount: str = "500000000",  # 500 USDC
    taker_amount: str = "1000000000",  # 1000 tokens
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


class TestSubgraphNormalizer:
    def test_buy_trade(self, normalizer):
        """BUY: maker provides USDC (makerAssetId=0), taker gets tokens."""
        event = _make_event(
            maker_asset_id="0",
            taker_asset_id="12345",
            maker_amount="500000000",
            taker_amount="1000000000",
        )
        trade = normalizer.normalize(event)
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.asset_id == "12345"
        assert trade.condition_id == "cond_abc"
        assert trade.source == Source.GOLDSKY_SUBGRAPH
        assert trade.version == 2
        assert trade.trade_id.startswith("chain:")

    def test_sell_trade(self, normalizer):
        """SELL: taker provides USDC (takerAssetId=0), maker provides tokens."""
        event = _make_event(
            maker_asset_id="12345",
            taker_asset_id="0",
            maker_amount="1000000000",
            taker_amount="500000000",
        )
        trade = normalizer.normalize(event)
        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.asset_id == "12345"

    def test_taker_duplicate_dropped(self, normalizer):
        """Taker == exchange contract -> None."""
        event = _make_event(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        assert normalizer.normalize(event) is None

    def test_trade_id_matches_sink(self, normalizer):
        """Subgraph trade_id should match GoldskySinkNormalizer for same tx."""
        from polymarket_pipeline.trade_id import make_trade_id_chain

        tx = "0x" + "dd" * 32
        oh = "0x" + "cc" * 32
        event = _make_event(transaction_hash=tx, order_hash=oh)
        trade = normalizer.normalize(event)
        expected_id = make_trade_id_chain(tx_hash=tx, order_hash=oh)
        assert trade.trade_id == expected_id
