"""Tests for Goldsky Sink normalizer."""

from datetime import UTC
from decimal import Decimal

from polymarket_pipeline.models import Side, Source
from polymarket_pipeline.normalizers.sink import GoldskySinkNormalizer
from tests.fixtures.sink_rows import (
    SINK_ROW_BUY,
    SINK_ROW_DUP_CTF,
    SINK_ROW_DUP_NEGRISK,
    SINK_ROW_SELL,
)

TOKEN_MAP = {
    "46434110155841033529384949983718980438706543876953886750286883506638610790525": (
        "0x204d24f3a0f5dd5fca",
        "YES",
    ),
}


def _make_normalizer() -> GoldskySinkNormalizer:
    return GoldskySinkNormalizer(token_market_map=TOKEN_MAP)


def test_buy_trade_normalization() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.price == Decimal("0.55")  # 110/200
    assert trade.size == Decimal("200")  # 200_000_000 / 1e6
    assert trade.amount_usd == Decimal("110")
    assert trade.fee_usd == Decimal("0")
    assert trade.maker == "0xa4a6fcb5df72529d4a"
    assert trade.taker == "0x1e057fb222bf2fdcb8"
    assert trade.source == Source.GOLDSKY_SINK
    assert trade.is_backfill is True
    assert trade.version == 2
    assert trade.tx_hash is not None
    assert trade.tx_hash.startswith("0x")
    assert trade.order_hash is not None
    assert trade.trade_id.startswith("chain:")
    assert trade.condition_id == "0x204d24f3a0f5dd5fca"
    expected_asset = "46434110155841033529384949983718980438706543876953886750286883506638610790525"
    assert trade.asset_id == expected_asset


def test_sell_trade_normalization() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_SELL)
    assert trade is not None
    assert trade.side == Side.SELL
    # price = 91.56 / 117.44 ~ 0.7797
    assert abs(trade.price - Decimal("0.7797")) < Decimal("0.001")
    assert trade.size == Decimal("117.44")
    assert trade.amount_usd == Decimal("91.56")


def test_duplicate_ctf_exchange_dropped() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_DUP_CTF)
    assert trade is None


def test_duplicate_negrisk_exchange_dropped() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_DUP_NEGRISK)
    assert trade is None


def test_unknown_token_gets_unknown_condition_id() -> None:
    norm = GoldskySinkNormalizer(token_market_map={})
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.condition_id == "unknown"


def test_trade_id_uses_hex_hashes() -> None:
    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    tx = "0x" + SINK_ROW_BUY["transaction_hash"].hex()
    oh = "0x" + SINK_ROW_BUY["order_hash"].hex()
    assert trade.tx_hash == tx
    assert trade.order_hash == oh


def test_timestamp_is_utc() -> None:

    norm = _make_normalizer()
    trade = norm.normalize(SINK_ROW_BUY)
    assert trade is not None
    assert trade.timestamp.tzinfo == UTC
    assert trade.timestamp.year == 2023
    assert trade.timestamp.month == 4
