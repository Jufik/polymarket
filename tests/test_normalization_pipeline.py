"""End-to-end normalization pipeline integration tests.

Verify that raw messages from each source flow through decode -> enrich -> validate
to produce correct NormalizedTrade objects.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from eth_abi import encode

from polymarket_pipeline.constants import EXCHANGE_ADDRS
from polymarket_pipeline.live.normalizers.decode.resolution import decode_settled_price
from polymarket_pipeline.live.normalizers.decode.rpc import decode_rpc_log
from polymarket_pipeline.live.normalizers.decode.rtds import decode_rtds_payload
from polymarket_pipeline.live.normalizers.decode.subgraph import decode_subgraph_event
from polymarket_pipeline.live.normalizers.enrich import enrich
from polymarket_pipeline.live.normalizers.token_map import TokenMap
from polymarket_pipeline.live.normalizers.types import Rejection, ResolutionOutcome
from polymarket_pipeline.live.normalizers.validate import validate
from polymarket_pipeline.models import NormalizedTrade, Source

# ── Shared fixtures ──────────────────────────────────────────────────────

ASSET_ID = "48712387461298376"
CONDITION_ID = "0xcondition_abc123"
OUTCOME = "YES"

TOKEN_MAP = TokenMap({ASSET_ID: (CONDITION_ID, OUTCOME)})


def _make_rpc_log(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = int(ASSET_ID),
    taker_asset_id: int = 0,
    maker_amount: int = 1_000_000_000,  # 1000 tokens
    taker_amount: int = 500_000_000,  # 500 USDC
    fee: int = 5_000_000,  # 5 USDC
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
    block_timestamp: float = 1700000000.0,
) -> tuple[dict, float]:
    """Build a realistic RPC log dict and its block timestamp.

    Default values produce a valid BUY trade:
      - taker_asset_id=0 means taker pays USDC
      - usdc_raw = taker_amount = 500M  (500 USDC)
      - token_amount = maker_amount = 1000M  (1000 tokens)
      - price = 500 / 1000 = 0.5  (valid: 0 <= price <= 1)
    """
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )
    log_entry = {
        "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        "topics": [
            # OrderFilled event signature
            "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6",
            order_hash,
            "0x" + "00" * 12 + maker[2:],
            "0x" + "00" * 12 + taker[2:],
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
    }
    return log_entry, block_timestamp


def _make_rtds_payload(
    *,
    asset_id: str = ASSET_ID,
    price: float = 0.55,
    size: float = 100.0,
    timestamp: int = 1700000000,
    proxy_wallet: str = "0x" + "f1" * 20,
    tx_hash: str = "0x" + "dd" * 32,
) -> dict:
    """Build a realistic RTDS activity/trades payload."""
    return {
        "asset": asset_id,
        "price": price,
        "size": size,
        "timestamp": timestamp,
        "proxyWallet": proxy_wallet,
        "transactionHash": tx_hash,
    }


def _make_subgraph_event(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    maker_asset_id: str = "0",
    taker_asset_id: str = ASSET_ID,
    maker_amount_filled: int = 500_000_000,  # 500 USDC
    taker_amount_filled: int = 1_000_000_000,  # 1000 tokens
    fee: int = 5_000_000,
    tx_hash: str = "0x" + "dd" * 32,
    order_hash: str = "0x" + "cc" * 32,
    timestamp: int = 1700000000,
) -> dict:
    """Build a realistic Subgraph orderFilledEvent dict."""
    return {
        "maker": maker,
        "taker": taker,
        "makerAssetId": maker_asset_id,
        "takerAssetId": taker_asset_id,
        "makerAmountFilled": str(maker_amount_filled),
        "takerAmountFilled": str(taker_amount_filled),
        "fee": str(fee),
        "transactionHash": tx_hash,
        "orderHash": order_hash,
        "timestamp": str(timestamp),
    }


# ── Full pipeline tests (decode -> enrich -> validate) ────────────────


class TestRPCFullPipeline:
    """Raw RPC log -> decode -> enrich -> validate -> NormalizedTrade."""

    def test_rpc_log_full_pipeline(self):
        """Happy path: valid RPC log produces a correct NormalizedTrade."""
        log_entry, block_ts = _make_rpc_log()

        decoded = decode_rpc_log(log_entry, block_timestamp=block_ts)
        assert decoded is not None, "decode_rpc_log should not return None for valid log"

        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade), f"Expected NormalizedTrade, got {enriched}"

        result = validate(enriched)
        assert isinstance(result, NormalizedTrade), f"Expected NormalizedTrade, got {result}"

        # Verify key fields
        assert result.condition_id == CONDITION_ID
        assert result.asset_id == ASSET_ID
        assert result.trade_id.startswith("chain:"), f"Expected chain: prefix, got {result.trade_id}"
        assert result.version == 2
        assert result.source == Source.ALCHEMY
        assert Decimal("0") <= result.price <= Decimal("1")
        assert result.size > 0
        assert result.maker == "0x" + "a1" * 20
        assert result.taker == "0x" + "b2" * 20
        assert result.tx_hash == "0x" + "dd" * 32
        assert result.block_number == 50_000_000

    def test_rpc_sell_side(self):
        """SELL trade: maker provides tokens (maker_asset_id != 0)."""
        log_entry, block_ts = _make_rpc_log(
            maker_asset_id=0,  # maker pays USDC
            taker_asset_id=int(ASSET_ID),  # taker pays tokens
            maker_amount=500_000_000,  # 500 USDC
            taker_amount=1_000_000_000,  # 1000 tokens
        )
        decoded = decode_rpc_log(log_entry, block_timestamp=block_ts)
        assert decoded is not None
        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade)
        result = validate(enriched)
        assert isinstance(result, NormalizedTrade)
        assert Decimal("0") <= result.price <= Decimal("1")


class TestRTDSFullPipeline:
    """Raw RTDS payload -> decode -> enrich -> validate -> NormalizedTrade."""

    def test_rtds_payload_full_pipeline(self):
        """Happy path: valid RTDS payload produces correct NormalizedTrade."""
        payload = _make_rtds_payload()

        decoded = decode_rtds_payload(payload)
        assert decoded is not None, "decode_rtds_payload should not return None"

        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade), f"Expected NormalizedTrade, got {enriched}"

        result = validate(enriched)
        assert isinstance(result, NormalizedTrade), f"Expected NormalizedTrade, got {result}"

        # Verify key fields
        assert result.condition_id == CONDITION_ID
        assert result.asset_id == ASSET_ID
        assert result.trade_id.startswith("ws:"), f"Expected ws: prefix, got {result.trade_id}"
        assert result.version == 1
        assert result.source == Source.RTDS
        assert result.price == Decimal("0.55").quantize(Decimal("0.0001"))
        assert result.maker == "0x" + "f1" * 20
        assert result.taker is None  # RTDS never has taker

    def test_rtds_dust_trade_rejected(self):
        """RTDS payload with size that rounds to zero returns None from decode."""
        payload = _make_rtds_payload(size=0.001)  # rounds to 0.00

        decoded = decode_rtds_payload(payload)
        assert decoded is None, "Dust trade should be rejected at decode stage"


class TestSubgraphFullPipeline:
    """Raw Subgraph event -> decode -> enrich -> validate -> NormalizedTrade."""

    def test_subgraph_event_full_pipeline(self):
        """Happy path: valid Subgraph event produces correct NormalizedTrade."""
        event = _make_subgraph_event()

        decoded = decode_subgraph_event(event)
        assert decoded is not None, "decode_subgraph_event should not return None"

        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade), f"Expected NormalizedTrade, got {enriched}"

        result = validate(enriched)
        assert isinstance(result, NormalizedTrade), f"Expected NormalizedTrade, got {result}"

        # Verify key fields
        assert result.condition_id == CONDITION_ID
        assert result.asset_id == ASSET_ID
        assert result.trade_id.startswith("chain:"), f"Expected chain: prefix, got {result.trade_id}"
        assert result.version == 2
        assert result.source == Source.GOLDSKY_SUBGRAPH
        assert Decimal("0") <= result.price <= Decimal("1")
        assert result.maker == "0x" + "a1" * 20
        assert result.taker == "0x" + "b2" * 20

    def test_subgraph_sell_trade(self):
        """SELL: maker provides tokens (makerAssetId != 0), taker pays USDC."""
        event = _make_subgraph_event(
            maker_asset_id=ASSET_ID,
            taker_asset_id="0",
            maker_amount_filled=1_000_000_000,  # 1000 tokens
            taker_amount_filled=500_000_000,  # 500 USDC
        )
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade)
        result = validate(enriched)
        assert isinstance(result, NormalizedTrade)
        assert Decimal("0") <= result.price <= Decimal("1")


# ── Cross-source rejection tests ─────────────────────────────────────


class TestUnknownAssetRejection:
    """All sources produce Rejection('unknown_asset') for unmapped asset_ids."""

    EMPTY_MAP = TokenMap({})

    def test_rpc_unknown_asset(self):
        log_entry, block_ts = _make_rpc_log()
        decoded = decode_rpc_log(log_entry, block_timestamp=block_ts)
        assert decoded is not None
        result = enrich(decoded, self.EMPTY_MAP)
        assert isinstance(result, Rejection)
        assert result.reason == "unknown_asset"

    def test_rtds_unknown_asset(self):
        payload = _make_rtds_payload(asset_id="99999_nonexistent")
        decoded = decode_rtds_payload(payload)
        assert decoded is not None
        result = enrich(decoded, self.EMPTY_MAP)
        assert isinstance(result, Rejection)
        assert result.reason == "unknown_asset"

    def test_subgraph_unknown_asset(self):
        event = _make_subgraph_event(taker_asset_id="99999_nonexistent")
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        result = enrich(decoded, self.EMPTY_MAP)
        assert isinstance(result, Rejection)
        assert result.reason == "unknown_asset"


class TestTakerDedupRejection:
    """On-chain sources with exchange-address taker produce Rejection('taker_dedup')."""

    # Pick the first exchange address for testing
    EXCHANGE_ADDR = next(iter(EXCHANGE_ADDRS))

    def test_rpc_taker_dedup(self):
        log_entry, block_ts = _make_rpc_log(taker=self.EXCHANGE_ADDR)
        decoded = decode_rpc_log(log_entry, block_timestamp=block_ts)
        assert decoded is not None
        result = enrich(decoded, TOKEN_MAP)
        assert isinstance(result, Rejection)
        assert result.reason == "taker_dedup"

    def test_subgraph_taker_dedup(self):
        event = _make_subgraph_event(taker=self.EXCHANGE_ADDR)
        decoded = decode_subgraph_event(event)
        assert decoded is not None
        result = enrich(decoded, TOKEN_MAP)
        assert isinstance(result, Rejection)
        assert result.reason == "taker_dedup"

    def test_rtds_no_taker_dedup(self):
        """RTDS never has a taker, so taker_dedup should never trigger."""
        payload = _make_rtds_payload()
        decoded = decode_rtds_payload(payload)
        assert decoded is not None
        # RTDS taker is always None, so should pass taker_dedup
        result = enrich(decoded, TOKEN_MAP)
        assert isinstance(result, NormalizedTrade)


# ── Price clamping tests ─────────────────────────────────────────────


class TestPriceClamping:
    """Out-of-range prices are clamped to [0,1] by validate stage.

    The NormalizedTrade Pydantic model has ``price: Field(ge=0, le=1)`` which
    rejects out-of-range values at construction time.  The ``validate()`` stage
    handles clamping for trades that arrive with bad prices via ``model_copy()``
    (which bypasses Pydantic field validators when ``update`` is used with
    ``model_config = {"validate_assignment": False}``).

    To test clamping, we construct a valid trade first, then use
    ``model_construct()`` (no validation) to inject an out-of-range price.
    """

    @staticmethod
    def _make_trade_with_price(price: Decimal) -> NormalizedTrade:
        """Build a NormalizedTrade with an arbitrary price, bypassing validation."""
        payload = _make_rtds_payload(price=0.50, size=10.0)
        decoded = decode_rtds_payload(payload)
        assert decoded is not None
        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade)
        # Use model_construct to bypass Pydantic validators
        fields = enriched.model_dump()
        fields["price"] = price
        fields["amount_usd"] = price * enriched.size
        return NormalizedTrade.model_construct(**fields)

    def test_price_above_one_clamped(self):
        """Price > 1 should be clamped to 1.0000."""
        trade = self._make_trade_with_price(Decimal("1.5000"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("1.0000")
        assert result.amount_usd == Decimal("1.0000") * trade.size

    def test_price_below_zero_clamped(self):
        """Price < 0 should be clamped to 0.0000."""
        trade = self._make_trade_with_price(Decimal("-0.1000"))
        result = validate(trade)
        assert isinstance(result, NormalizedTrade)
        assert result.price == Decimal("0.0000")

    def test_valid_price_passes_through(self):
        """Price in [0,1] should pass through unchanged."""
        payload = _make_rtds_payload(price=0.55, size=10.0)
        decoded = decode_rtds_payload(payload)
        assert decoded is not None
        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade)
        result = validate(enriched)
        assert isinstance(result, NormalizedTrade)
        assert result.price == enriched.price


# ── Resolution decoder tests ─────────────────────────────────────────


class TestResolutionDecoder:
    """settled_price hex -> ResolutionOutcome + payout."""

    def test_yes_resolution(self):
        """1e18 -> YES with payout 1.0."""
        outcome, payout = decode_settled_price(hex(10**18))
        assert outcome == ResolutionOutcome.YES
        assert payout == 1.0

    def test_no_resolution(self):
        """0 -> NO with payout 0.0."""
        outcome, payout = decode_settled_price("0x0")
        assert outcome == ResolutionOutcome.NO
        assert payout == 0.0

    def test_voided_resolution(self):
        """0.5e18 -> VOIDED with payout 0.5."""
        outcome, payout = decode_settled_price(hex(5 * 10**17))
        assert outcome == ResolutionOutcome.VOIDED
        assert payout == 0.5

    def test_zero_prefixed_hex(self):
        """Hex with 0x prefix should work."""
        outcome, payout = decode_settled_price("0x0de0b6b3a7640000")  # 1e18
        assert outcome == ResolutionOutcome.YES
        assert payout == 1.0

    def test_empty_hex_is_no(self):
        """Empty hex string should decode as NO."""
        outcome, payout = decode_settled_price("0x")
        assert outcome == ResolutionOutcome.NO
        assert payout == 0.0

    def test_unusual_settled_price_is_voided(self):
        """Non-standard settled price treated as VOIDED with proportional payout."""
        # 0.25e18 -> VOIDED with payout 0.25
        outcome, payout = decode_settled_price(hex(25 * 10**16))
        assert outcome == ResolutionOutcome.VOIDED
        assert pytest.approx(payout, abs=1e-6) == 0.25


# ── Size validation tests ────────────────────────────────────────────


class TestSizeValidation:
    """Zero or negative size should be rejected by validate stage."""

    def test_zero_size_rejected(self):
        """Trade with size=0 should be rejected."""
        payload = _make_rtds_payload(price=0.50, size=10.0)
        decoded = decode_rtds_payload(payload)
        assert decoded is not None
        enriched = enrich(decoded, TOKEN_MAP)
        assert isinstance(enriched, NormalizedTrade)
        # Use model_construct to bypass Pydantic size>0 validator
        fields = enriched.model_dump()
        fields["size"] = Decimal("0")
        fields["amount_usd"] = Decimal("0")
        zero_size_trade = NormalizedTrade.model_construct(**fields)
        result = validate(zero_size_trade)
        assert isinstance(result, Rejection)
        assert result.reason == "zero_size"


# ── Trade ID consistency tests ────────────────────────────────────────


class TestTradeIDConsistency:
    """RPC and Subgraph for the same on-chain event produce identical trade_ids."""

    def test_rpc_subgraph_same_trade_id(self):
        """Same tx_hash + order_hash -> identical chain: trade_id."""
        tx_hash = "0x" + "dd" * 32
        order_hash = "0x" + "cc" * 32

        # RPC path
        log_entry, block_ts = _make_rpc_log(tx_hash=tx_hash, order_hash=order_hash)
        rpc_decoded = decode_rpc_log(log_entry, block_timestamp=block_ts)
        assert rpc_decoded is not None
        rpc_enriched = enrich(rpc_decoded, TOKEN_MAP)
        assert isinstance(rpc_enriched, NormalizedTrade)

        # Subgraph path
        event = _make_subgraph_event(tx_hash=tx_hash, order_hash=order_hash)
        sub_decoded = decode_subgraph_event(event)
        assert sub_decoded is not None
        sub_enriched = enrich(sub_decoded, TOKEN_MAP)
        assert isinstance(sub_enriched, NormalizedTrade)

        assert rpc_enriched.trade_id == sub_enriched.trade_id
        assert rpc_enriched.trade_id.startswith("chain:")
