"""Tests for the pending block normalizer (FeeModule matchOrders calldata decoding)."""

from __future__ import annotations

from decimal import Decimal

from eth_abi import encode

from polymarket_pipeline.live.normalizers.pending_block import (
    MATCH_ORDERS_SELECTOR,
    MATCH_ORDERS_TYPES,
    PendingBlockNormalizer,
)
from polymarket_pipeline.models import Side, Source


def _build_order(
    maker: str = "0x" + "aa" * 20,
    token_id: int = 12345,
    maker_amount: int = 500_000,  # 0.50 USDC if BUY; 0.5 tokens if SELL
    taker_amount: int = 500_000,
    side: int = 0,  # 0=BUY, 1=SELL
) -> tuple:
    """Build an Order struct tuple matching the CTFExchange ABI."""
    return (
        1,                          # salt
        maker,                      # maker address
        maker,                      # signer (same as maker for simplicity)
        "0x" + "00" * 20,           # taker (empty = anyone)
        token_id,                   # tokenId
        maker_amount,               # makerAmount
        taker_amount,               # takerAmount
        2**64,                      # expiration (far future)
        0,                          # nonce
        0,                          # feeRateBps
        side,                       # side (0=BUY, 1=SELL)
        0,                          # signatureType
        b"",                        # signature
    )


def _encode_match_orders(
    taker_order: tuple,
    maker_orders: list[tuple],
    taker_fill_amount: int,
    taker_receive_amount: int,
    maker_fill_amounts: list[int],
    taker_fee_amount: int = 0,
    maker_fee_amounts: list[int] | None = None,
) -> str:
    """Encode matchOrders calldata with selector prefix."""
    if maker_fee_amounts is None:
        maker_fee_amounts = [0] * len(maker_orders)

    encoded = encode(
        MATCH_ORDERS_TYPES,
        [
            taker_order,
            maker_orders,
            taker_fill_amount,
            taker_receive_amount,
            maker_fill_amounts,
            taker_fee_amount,
            maker_fee_amounts,
        ],
    )
    return "0x" + MATCH_ORDERS_SELECTOR.hex() + encoded.hex()


def test_decode_single_maker_buy():
    """Taker buys tokens: taker side=BUY, maker side=SELL."""
    taker_addr = "0x" + "11" * 20
    maker_addr = "0x" + "22" * 20
    token_id = 98765

    # Taker BUY: pays 700_000 USDC for 1_000_000 tokens => price = 0.70
    taker_order = _build_order(
        maker=taker_addr, token_id=token_id,
        maker_amount=700_000, taker_amount=1_000_000, side=0,
    )
    # Maker SELL: provides 1_000_000 tokens, wants 700_000 USDC => price = 0.70
    maker_order = _build_order(
        maker=maker_addr, token_id=token_id,
        maker_amount=1_000_000, taker_amount=700_000, side=1,
    )

    calldata = _encode_match_orders(
        taker_order=taker_order,
        maker_orders=[maker_order],
        taker_fill_amount=700_000,
        taker_receive_amount=1_000_000,
        maker_fill_amounts=[1_000_000],  # tokens filled from maker
        taker_fee_amount=0,
        maker_fee_amounts=[1000],  # 0.001 USDC fee
    )

    normalizer = PendingBlockNormalizer()
    tx = {
        "hash": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": calldata,
        "_pending_block": 100,
        "_poll_ts": 1700000000.0,
    }

    trades = normalizer.decode_tx(tx)
    assert len(trades) == 1

    t = trades[0]
    assert t.side == Side.BUY
    assert t.source == Source.PENDING_BLOCK
    assert t.version == 0
    assert t.asset_id == str(token_id)
    assert t.maker == maker_addr
    assert t.taker == taker_addr
    assert t.price == Decimal("0.7000")
    assert t.size == Decimal("1.000000")
    assert t.amount_usd == Decimal("0.700000")
    assert t.fee_usd == Decimal("0.001000")
    assert t.block_number == 100
    assert t.trade_id.startswith("pending:")


def test_decode_single_maker_sell():
    """Taker sells tokens: taker side=SELL, maker side=BUY."""
    taker_addr = "0x" + "33" * 20
    maker_addr = "0x" + "44" * 20
    token_id = 55555

    # Taker SELL: sells 2_000_000 tokens for 1_200_000 USDC => price = 0.60
    taker_order = _build_order(
        maker=taker_addr, token_id=token_id,
        maker_amount=2_000_000, taker_amount=1_200_000, side=1,
    )
    # Maker BUY: pays 1_200_000 USDC for 2_000_000 tokens => price = 0.60
    maker_order = _build_order(
        maker=maker_addr, token_id=token_id,
        maker_amount=1_200_000, taker_amount=2_000_000, side=0,
    )

    calldata = _encode_match_orders(
        taker_order=taker_order,
        maker_orders=[maker_order],
        taker_fill_amount=2_000_000,
        taker_receive_amount=1_200_000,
        maker_fill_amounts=[1_200_000],  # USDC filled from maker
    )

    normalizer = PendingBlockNormalizer()
    tx = {
        "hash": "0x" + "ab" * 32,
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": calldata,
        "_pending_block": 200,
        "_poll_ts": 1700000000.0,
    }

    trades = normalizer.decode_tx(tx)
    assert len(trades) == 1

    t = trades[0]
    assert t.side == Side.SELL
    assert t.price == Decimal("0.6000")
    assert t.size == Decimal("2.000000")
    assert t.amount_usd == Decimal("1.200000")


def test_decode_multiple_makers():
    """One taker order matched against two maker orders."""
    taker_addr = "0x" + "11" * 20
    maker_addr_1 = "0x" + "22" * 20
    maker_addr_2 = "0x" + "33" * 20
    token_id = 77777

    taker_order = _build_order(
        maker=taker_addr, token_id=token_id,
        maker_amount=1_000_000, taker_amount=1_000_000, side=0,
    )
    maker_order_1 = _build_order(
        maker=maker_addr_1, token_id=token_id,
        maker_amount=600_000, taker_amount=600_000, side=1,
    )
    maker_order_2 = _build_order(
        maker=maker_addr_2, token_id=token_id,
        maker_amount=400_000, taker_amount=400_000, side=1,
    )

    calldata = _encode_match_orders(
        taker_order=taker_order,
        maker_orders=[maker_order_1, maker_order_2],
        taker_fill_amount=1_000_000,
        taker_receive_amount=1_000_000,
        maker_fill_amounts=[600_000, 400_000],
    )

    normalizer = PendingBlockNormalizer()
    tx = {
        "hash": "0x" + "cd" * 32,
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": calldata,
        "_pending_block": 300,
        "_poll_ts": 1700000000.0,
    }

    trades = normalizer.decode_tx(tx)
    assert len(trades) == 2
    assert trades[0].maker == maker_addr_1
    assert trades[1].maker == maker_addr_2
    assert trades[0].size == Decimal("0.600000")
    assert trades[1].size == Decimal("0.400000")


def test_wrong_selector_skipped():
    """Transactions with non-matchOrders selector are skipped."""
    normalizer = PendingBlockNormalizer()
    tx = {
        "hash": "0x" + "ee" * 32,
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": "0xdeadbeef0000",
        "_pending_block": 400,
        "_poll_ts": 1700000000.0,
    }
    trades = normalizer.decode_tx(tx)
    assert trades == []


def test_empty_input_skipped():
    """Transactions with no input data are skipped."""
    normalizer = PendingBlockNormalizer()
    tx = {
        "hash": "0x" + "ee" * 32,
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": "0x",
        "_pending_block": 400,
        "_poll_ts": 1700000000.0,
    }
    trades = normalizer.decode_tx(tx)
    assert trades == []


def test_token_market_map_resolves_condition_id():
    """Token market map resolves asset_id -> condition_id."""
    token_id = 12345
    condition_id = "0xcondition123"
    normalizer = PendingBlockNormalizer(
        token_market_map={str(token_id): (condition_id, "YES")}
    )

    taker_order = _build_order(
        maker="0x" + "11" * 20, token_id=token_id,
        maker_amount=500_000, taker_amount=1_000_000, side=0,
    )
    maker_order = _build_order(
        maker="0x" + "22" * 20, token_id=token_id,
        maker_amount=1_000_000, taker_amount=500_000, side=1,
    )

    calldata = _encode_match_orders(
        taker_order=taker_order,
        maker_orders=[maker_order],
        taker_fill_amount=500_000,
        taker_receive_amount=1_000_000,
        maker_fill_amounts=[1_000_000],
    )

    tx = {
        "hash": "0x" + "ff" * 32,
        "from": "0x" + "ff" * 20,
        "to": "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",
        "input": calldata,
        "_pending_block": 500,
        "_poll_ts": 1700000000.0,
    }

    trades = normalizer.decode_tx(tx)
    assert len(trades) == 1
    assert trades[0].condition_id == condition_id
    assert trades[0].asset_id == str(token_id)
