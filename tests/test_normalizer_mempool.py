"""Tests for MempoolNormalizer — decode pending fillOrder calldata dicts."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import Side, Source


def _make_mempool_trade(
    *,
    tx_hash: str = "0x" + "dd" * 32,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    token_id: str = "12345",
    maker_amount: int = 1_000_000_000,  # 1000 tokens (USDC scale)
    taker_amount: int = 500_000_000,    # 500 USDC
    fee_rate_bps: int = 150,
    side: int = 0,  # 0=BUY, 1=SELL
    expiration: int = 1708500000,
    seen_at: float = 1706800000.123,
) -> dict:
    """Build a mock decoded mempool trade dict (as yielded by Rust sidecar)."""
    return {
        "tx_hash": tx_hash,
        "maker": maker,
        "taker": taker,
        "token_id": token_id,
        "maker_amount": maker_amount,
        "taker_amount": taker_amount,
        "fee_rate_bps": fee_rate_bps,
        "side": side,
        "expiration": expiration,
        "seen_at": seen_at,
    }


@pytest.fixture
def normalizer():
    from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

    return MempoolNormalizer()


@pytest.fixture
def normalizer_with_map():
    from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

    token_map = {"12345": ("cond_abc", "YES")}
    return MempoolNormalizer(token_market_map=token_map)


class TestMempoolNormalizer:
    def test_basic_buy_trade(self, normalizer_with_map):
        """BUY: side=0, taker pays USDC, maker provides tokens."""
        raw = _make_mempool_trade(
            maker_amount=1_000_000_000,  # 1000 tokens
            taker_amount=500_000_000,    # 500 USDC
            side=0,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.price == Decimal("0.5000")
        assert trade.size == Decimal("1000")
        assert trade.amount_usd == Decimal("500")
        assert trade.source == Source.MEMPOOL
        assert trade.version == 0
        assert trade.trade_id.startswith("mempool:")
        assert trade.block_number is None
        assert trade.is_backfill is False

    def test_sell_trade(self, normalizer_with_map):
        """SELL: side=1, maker pays USDC, taker provides tokens."""
        raw = _make_mempool_trade(
            maker_amount=300_000_000,    # 300 USDC
            taker_amount=500_000_000,    # 500 tokens
            side=1,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.price == Decimal("0.6000")  # 300/500
        assert trade.size == Decimal("500")
        assert trade.amount_usd == Decimal("300")

    def test_taker_duplicate_dropped(self, normalizer_with_map):
        """Taker == exchange contract returns None."""
        raw = _make_mempool_trade(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_negrisk_taker_dropped(self, normalizer_with_map):
        """NegRisk exchange taker also dropped."""
        raw = _make_mempool_trade(
            taker="0xc5d563a36ae78145c45a50134d48a1215220f80a",
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_unknown_token_id_returns_none(self, normalizer_with_map):
        """Unknown token_id (not in token_map) returns None."""
        raw = _make_mempool_trade(token_id="999999")
        trade = normalizer_with_map.normalize(raw)
        assert trade is None

    def test_without_token_map_returns_none(self, normalizer):
        """Without token_map, all trades return None (can't resolve condition_id)."""
        raw = _make_mempool_trade()
        trade = normalizer.normalize(raw)
        assert trade is None

    def test_with_token_map_resolves_condition_id(self, normalizer_with_map):
        """Token map resolves asset_id to condition_id."""
        raw = _make_mempool_trade(token_id="12345")
        trade = normalizer_with_map.normalize(raw)
        assert trade is not None
        assert trade.condition_id == "cond_abc"
        assert trade.asset_id == "12345"

    def test_fee_usd_is_zero(self, normalizer_with_map):
        """Mempool trades have fee_usd=0 (fee not yet charged)."""
        raw = _make_mempool_trade()
        trade = normalizer_with_map.normalize(raw)
        assert trade.fee_usd == Decimal("0")

    def test_timestamp_from_seen_at(self, normalizer_with_map):
        """Timestamp comes from seen_at (when Rust sidecar saw the tx)."""
        raw = _make_mempool_trade(seen_at=1706800000.0)
        trade = normalizer_with_map.normalize(raw)
        assert trade.timestamp == datetime(2024, 2, 1, 15, 6, 40, tzinfo=UTC)

    def test_maker_taker_lowercased(self, normalizer_with_map):
        """Addresses are lowercased."""
        raw = _make_mempool_trade(
            maker="0xABCDEF" + "a1" * 17,
            taker="0xFEDCBA" + "b2" * 17,
        )
        trade = normalizer_with_map.normalize(raw)
        assert trade.maker == raw["maker"].lower()
        assert trade.taker == raw["taker"].lower()
