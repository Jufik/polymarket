"""Tests for the shared enrich stage (token map lookup + taker dedup)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.constants import EXCHANGE_ADDRS
from polymarket_pipeline.live.normalizers.enrich import enrich
from polymarket_pipeline.live.normalizers.token_map import TokenMap
from polymarket_pipeline.live.normalizers.types import DecodedTrade, Rejection
from polymarket_pipeline.models import NormalizedTrade, Side, Source

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
_ASSET_ID = "464341101558"
_CONDITION_ID = "0xabc123"
_OUTCOME = "Yes"

# Deterministic exchange addr for taker dedup tests
_EXCHANGE_ADDR = next(iter(EXCHANGE_ADDRS))


def _decoded(**overrides: object) -> DecodedTrade:
    """Build a DecodedTrade with sensible defaults; override any field."""
    defaults: dict[str, object] = dict(
        asset_id=_ASSET_ID,
        maker_asset_id="0",
        taker_asset_id=_ASSET_ID,
        price=None,
        size=None,
        maker_amount=Decimal("68000000"),  # 68 USDC (1e6 scaled)
        taker_amount=Decimal("100000000"),  # 100 tokens (1e6 scaled)
        fee_raw=Decimal("500000"),  # 0.5 USDC
        maker="0xaaaa",
        taker="0xbbbb",
        tx_hash="0xdeadbeef",
        order_hash="0xcafebabe",
        block_number=12345,
        timestamp=_TS,
        source=Source.ALCHEMY,
        is_backfill=False,
    )
    defaults.update(overrides)
    return DecodedTrade(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def token_map() -> TokenMap:
    return TokenMap({_ASSET_ID: (_CONDITION_ID, _OUTCOME)})


# ---------------------------------------------------------------------------
# On-chain BUY (maker_asset_id="0")
# ---------------------------------------------------------------------------


class TestOnChainBuy:
    def test_side_is_buy(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.side == Side.BUY

    def test_condition_id_from_token_map(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.condition_id == _CONDITION_ID

    def test_asset_id_preserved(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.asset_id == _ASSET_ID

    def test_price_computation(self, token_map: TokenMap) -> None:
        """price = amount_usd / size = 68 / 100 = 0.68"""
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.6800")

    def test_size_computation(self, token_map: TokenMap) -> None:
        """size = taker_amount / USDC_SCALE = 100_000_000 / 1e6 = 100"""
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.size == Decimal("100.0000")

    def test_amount_usd(self, token_map: TokenMap) -> None:
        """amount_usd = maker_amount / USDC_SCALE = 68_000_000 / 1e6 = 68"""
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.amount_usd == Decimal("68.0000")

    def test_fee_usd(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.fee_usd == Decimal("0.5000")

    def test_chain_trade_id(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.trade_id.startswith("chain:")

    def test_version_on_chain(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 2

    def test_provenance_fields(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.tx_hash == "0xdeadbeef"
        assert result.order_hash == "0xcafebabe"
        assert result.block_number == 12345
        assert result.source == Source.ALCHEMY
        assert result.is_backfill is False

    def test_participants(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.maker == "0xaaaa"
        assert result.taker == "0xbbbb"

    def test_timestamp(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.timestamp == _TS


# ---------------------------------------------------------------------------
# On-chain SELL (maker_asset_id is a token, not "0")
# ---------------------------------------------------------------------------


class TestOnChainSell:
    def test_side_is_sell(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            maker_asset_id=_ASSET_ID,
            taker_asset_id="0",
            maker_amount=Decimal("100000000"),  # 100 tokens
            taker_amount=Decimal("68000000"),  # 68 USDC
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.side == Side.SELL

    def test_sell_price_computation(self, token_map: TokenMap) -> None:
        """SELL: usdc from taker, tokens from maker."""
        decoded = _decoded(
            maker_asset_id=_ASSET_ID,
            taker_asset_id="0",
            maker_amount=Decimal("100000000"),  # 100 tokens
            taker_amount=Decimal("68000000"),  # 68 USDC
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.6800")
        assert result.size == Decimal("100.0000")
        assert result.amount_usd == Decimal("68.0000")


# ---------------------------------------------------------------------------
# Taker dedup
# ---------------------------------------------------------------------------


class TestTakerDedup:
    def test_taker_is_exchange_addr(self, token_map: TokenMap) -> None:
        decoded = _decoded(taker=_EXCHANGE_ADDR)
        result = enrich(decoded, token_map)
        assert isinstance(result, Rejection)
        assert result.reason == "taker_dedup"
        assert result.asset_id == _ASSET_ID
        assert result.source == Source.ALCHEMY

    def test_taker_exchange_case_insensitive(self, token_map: TokenMap) -> None:
        """Exchange addr check must be case-insensitive."""
        decoded = _decoded(taker=_EXCHANGE_ADDR.upper())
        result = enrich(decoded, token_map)
        assert isinstance(result, Rejection)
        assert result.reason == "taker_dedup"

    def test_taker_none_passes(self, token_map: TokenMap) -> None:
        """If taker is None (e.g. WS), skip dedup check."""
        decoded = _decoded(
            taker=None,
            price=Decimal("0.68"),
            size=Decimal("100"),
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.RTDS,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)


# ---------------------------------------------------------------------------
# Unknown asset
# ---------------------------------------------------------------------------


class TestUnknownAsset:
    def test_unknown_asset_rejected(self, token_map: TokenMap) -> None:
        decoded = _decoded(asset_id="unknown_999")
        result = enrich(decoded, token_map)
        assert isinstance(result, Rejection)
        assert result.reason == "unknown_asset"
        assert result.details == "not in token_map"
        assert result.asset_id == "unknown_999"

    def test_unknown_before_price_computation(self, token_map: TokenMap) -> None:
        """Token map lookup happens before any price arithmetic."""
        decoded = _decoded(
            asset_id="missing",
            maker_amount=None,
            taker_amount=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, Rejection)
        assert result.reason == "unknown_asset"


# ---------------------------------------------------------------------------
# WS source (pre-computed price/size)
# ---------------------------------------------------------------------------


class TestWSSource:
    def test_ws_preserves_price_size(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.4567"),
            size=Decimal("250"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.RTDS,
            taker=None,
            maker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.4567")
        assert result.size == Decimal("250.0000")

    def test_ws_trade_id(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.4567"),
            size=Decimal("250"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.WEBSOCKET,
            taker=None,
            maker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.trade_id.startswith("ws:")

    def test_ws_amount_usd_from_price_size(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.4567"),
            size=Decimal("250"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.RTDS,
            taker=None,
            maker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        # amount_usd = price * size = 0.4567 * 250 = 114.175
        assert result.amount_usd == Decimal("114.1750")

    def test_ws_side_defaults_buy(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.50"),
            size=Decimal("100"),
            maker_asset_id=None,
            taker_asset_id=None,
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.WEBSOCKET,
            taker=None,
            maker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.side == Side.BUY

    def test_ws_price_quantized(self, token_map: TokenMap) -> None:
        """Price from WS should be quantized to 4dp ROUND_HALF_UP."""
        decoded = _decoded(
            price=Decimal("0.12345"),
            size=Decimal("100"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.RTDS,
            taker=None,
            maker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.1235")


# ---------------------------------------------------------------------------
# Version by source
# ---------------------------------------------------------------------------


class TestVersionBySource:
    def test_alchemy_version_2(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(source=Source.ALCHEMY), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 2

    def test_goldsky_sink_version_2(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(source=Source.GOLDSKY_SINK), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 2

    def test_goldsky_subgraph_version_2(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(source=Source.GOLDSKY_SUBGRAPH), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 2

    def test_rtds_version_1(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.50"),
            size=Decimal("100"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.RTDS,
            taker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 1

    def test_websocket_version_1(self, token_map: TokenMap) -> None:
        decoded = _decoded(
            price=Decimal("0.50"),
            size=Decimal("100"),
            maker_amount=None,
            taker_amount=None,
            tx_hash=None,
            order_hash=None,
            block_number=None,
            source=Source.WEBSOCKET,
            taker=None,
        )
        result = enrich(decoded, token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 1

    def test_mempool_version_0(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(source=Source.MEMPOOL), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 0

    def test_pending_block_version_0(self, token_map: TokenMap) -> None:
        result = enrich(_decoded(source=Source.PENDING_BLOCK), token_map)
        assert isinstance(result, NormalizedTrade)
        assert result.version == 0
