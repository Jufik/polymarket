"""Tests for Market WebSocket normalizer."""

from datetime import UTC
from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.market_ws import MarketWSNormalizer

TOKEN_MAP = {
    "57625936606489185661652559589880983710918172021553907271126623944716577292773": (
        "0x204d24f3a0f5dd5fca",
        "NO",
    ),
}

LAST_TRADE_MSG: dict = {
    "market": "0x204d24f3a0f5dd5fca825292bdeab6a97af3978b2caa2b21bb37e610eddfff5d",
    "asset_id": "57625936606489185661652559589880983710918172021553907271126623944716577292773",
    "price": "0.32",
    "size": "786",
    "fee_rate_bps": "0",
    "side": "BUY",
    "timestamp": "1770537665076",
    "event_type": "last_trade_price",
    "transaction_hash": "0x27837a1de09654241b0483089feb1dc08729d6864ec407c7c48c689263098343",
}


def test_last_trade_price_normalization() -> None:
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.32")
    assert trade.size == Decimal("786")
    assert trade.source == Source.WEBSOCKET
    assert trade.maker is None
    assert trade.taker is None
    assert trade.version == 1
    assert trade.is_backfill is False


def test_tx_hash_preserved() -> None:
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.tx_hash == "0x27837a1de09654241b0483089feb1dc08729d6864ec407c7c48c689263098343"


def test_non_trade_events_return_none() -> None:
    """price_change and book events are not trades — skip them."""
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    assert norm.normalize({"event_type": "price_change", "price_changes": []}) is None
    assert norm.normalize({"event_type": "book", "bids": [], "asks": []}) is None


def test_timestamp_is_milliseconds() -> None:
    """Market WS timestamps are millisecond strings."""

    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.timestamp.tzinfo == UTC
    assert trade.timestamp.year == 2026


def test_fee_rate_bps_converted_to_usd() -> None:
    msg = {**LAST_TRADE_MSG, "fee_rate_bps": "200"}  # 2%
    norm = MarketWSNormalizer(token_market_map=TOKEN_MAP)
    trade = norm.normalize(msg)
    assert trade is not None
    # fee = price * size * bps / 10000 = 0.32 * 786 * 200 / 10000 = 5.03
    assert trade.fee_usd == Decimal("5.03")


def test_unknown_token_gets_unknown_condition_id() -> None:
    norm = MarketWSNormalizer(token_market_map={})
    trade = norm.normalize(LAST_TRADE_MSG)
    assert trade is not None
    assert trade.condition_id == "unknown"
