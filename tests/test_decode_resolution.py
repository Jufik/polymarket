"""Tests for resolution event decoding."""
from __future__ import annotations

from polymarket_pipeline.live.normalizers.decode.resolution import (
    decode_settled_price,
)
from polymarket_pipeline.live.normalizers.types import ResolutionOutcome


def test_yes_resolution() -> None:
    """settledPrice = 1e18 -> YES, payout 1.0."""
    # 1e18 = 0xDE0B6B3A7640000
    outcome, payout = decode_settled_price("0x0DE0B6B3A7640000")
    assert outcome == ResolutionOutcome.YES
    assert payout == 1.0


def test_no_resolution() -> None:
    """settledPrice = 0 -> NO, payout 0.0."""
    outcome, payout = decode_settled_price("0x0")
    assert outcome == ResolutionOutcome.NO
    assert payout == 0.0


def test_voided_resolution() -> None:
    """settledPrice = 0.5e18 -> VOIDED, payout 0.5."""
    # 0.5e18 = 500000000000000000 = 0x6F05B59D3B20000
    outcome, payout = decode_settled_price("0x06F05B59D3B20000")
    assert outcome == ResolutionOutcome.VOIDED
    assert payout == 0.5


def test_zero_hex() -> None:
    """Plain 0x0 -> NO."""
    outcome, payout = decode_settled_price("0x0")
    assert outcome == ResolutionOutcome.NO
    assert payout == 0.0


def test_unexpected_value_treated_as_voided() -> None:
    """Any unexpected value -> VOIDED with proportional payout."""
    # 0.25e18 = 250000000000000000
    outcome, payout = decode_settled_price(hex(250000000000000000))
    assert outcome == ResolutionOutcome.VOIDED
    assert abs(payout - 0.25) < 1e-9


def test_no_0x_prefix() -> None:
    """Handle hex without 0x prefix."""
    outcome, payout = decode_settled_price("0DE0B6B3A7640000")
    assert outcome == ResolutionOutcome.YES
    assert payout == 1.0
