"""Tests for trade_id generation."""

from polymarket_pipeline.trade_id import (
    make_trade_id_chain,
    make_trade_id_ws,
    make_trade_ids_chain_batch,
)


def test_chain_trade_id_deterministic() -> None:
    """Same tx_hash + order_hash always produce same trade_id."""
    id1 = make_trade_id_chain(
        tx_hash="0xbbcfa118b585eace1e341715",
        order_hash="0xdeadbeef",
    )
    id2 = make_trade_id_chain(
        tx_hash="0xbbcfa118b585eace1e341715",
        order_hash="0xdeadbeef",
    )
    assert id1 == id2
    assert id1.startswith("chain:")
    assert len(id1) == len("chain:") + 16


def test_chain_trade_id_different_order_hash() -> None:
    """Different order_hash produces different trade_id."""
    id1 = make_trade_id_chain(tx_hash="0xabc", order_hash="0x111")
    id2 = make_trade_id_chain(tx_hash="0xabc", order_hash="0x222")
    assert id1 != id2


def test_ws_trade_id_deterministic() -> None:
    """Same composite key always produces same trade_id."""
    id1 = make_trade_id_ws(
        asset_id="46434110155841",
        timestamp_ms=1770537665076,
        price="0.32",
        size="786",
    )
    id2 = make_trade_id_ws(
        asset_id="46434110155841",
        timestamp_ms=1770537665076,
        price="0.32",
        size="786",
    )
    assert id1 == id2
    assert id1.startswith("ws:")


def test_ws_and_chain_never_collide() -> None:
    """WS and chain trade_ids can never collide due to prefix."""
    chain_id = make_trade_id_chain(tx_hash="0xabc", order_hash="0xdef")
    ws_id = make_trade_id_ws(asset_id="123", timestamp_ms=1000, price="0.5", size="10")
    assert chain_id[:6] == "chain:"
    assert ws_id[:3] == "ws:"


def test_batch_matches_individual() -> None:
    """Batch function must produce identical results to individual calls."""
    txs = ["0xabc", "0xdef", "0x123"]
    ohs = ["0x111", "0x222", "0x333"]
    batch = make_trade_ids_chain_batch(txs, ohs)
    individual = [
        make_trade_id_chain(tx_hash=t, order_hash=o) for t, o in zip(txs, ohs, strict=True)
    ]
    assert batch == individual


def test_batch_empty() -> None:
    """Empty input returns empty output."""
    assert make_trade_ids_chain_batch([], []) == []


def test_batch_length_mismatch_raises() -> None:
    """Mismatched lengths should raise ValueError (strict=True)."""
    import pytest

    with pytest.raises(ValueError):
        make_trade_ids_chain_batch(["0xa"], ["0x1", "0x2"])
