"""Tests for RTDS WebSocket normalizer."""

from datetime import UTC
from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

RTDS_MSG: dict = {
    "connection_id": "Yc9dWdUJLPECJPg=",
    "timestamp": 1770537659939,
    "topic": "activity",
    "type": "trades",
    "payload": {
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
        "name": "MtKanin",
        "pseudonym": "Forsaken-Moth",
        "bio": "",
        "profileImage": "",
        "icon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/BTC+fullsize.png",
        "title": "Bitcoin Up or Down",
        "eventSlug": "btc-updown-15m-1770537600",
        "slug": "btc-updown-15m-1770537600",
    },
}


def test_rtds_basic_normalization() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.36")
    assert trade.size == Decimal("164.67")
    assert trade.amount_usd == Decimal("59.28")  # 0.36 * 164.67 rounded
    assert trade.source == Source.RTDS
    assert trade.version == 1
    assert trade.is_backfill is False


def test_rtds_proxy_wallet_becomes_maker() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.maker == "0xBBF93c3d184f8A6c558C93Be38eAD60cf53e2584"
    assert trade.taker is None


def test_rtds_condition_id_from_payload() -> None:
    """RTDS provides conditionId directly — no token_market_map needed."""
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    expected = "0xaa6f622e00c696078424494dbcd331b8435275ef97d8dde2a0f66696db53a75d"
    assert trade.condition_id == expected


def test_rtds_tx_hash_preserved() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.tx_hash == "0x2d5a647433c051d18ca7d855737f42d51f1301090b711e774d34da1f06fd9ffb"


def test_rtds_float_imprecision_rounded() -> None:
    """RTDS sometimes sends prices like 0.3996666666666667."""
    msg = {**RTDS_MSG, "payload": {**RTDS_MSG["payload"], "price": 0.3996666666666667}}
    norm = RTDSNormalizer()
    trade = norm.normalize(msg)
    assert trade.price == Decimal("0.40")


def test_rtds_trade_id_uses_ws_format() -> None:
    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.trade_id.startswith("ws:")


def test_rtds_timestamp_uses_payload_seconds() -> None:
    """Use payload.timestamp (trade time), not top-level timestamp (delivery time)."""

    norm = RTDSNormalizer()
    trade = norm.normalize(RTDS_MSG)
    assert trade.timestamp.tzinfo == UTC
    assert trade.timestamp.year == 2026
