"""Tests for TTL-based trade dedup."""

from __future__ import annotations

import time

from polymarket_pipeline.live.dedup import TradeDedup


def test_first_seen_is_not_duplicate() -> None:
    d = TradeDedup()
    assert d.is_duplicate("t1") is False


def test_second_seen_is_duplicate() -> None:
    d = TradeDedup()
    d.is_duplicate("t1")
    assert d.is_duplicate("t1") is True


def test_eviction_after_ttl() -> None:
    d = TradeDedup(ttl_s=1.0)
    d.is_duplicate("t1")
    # Manually set the timestamp to be old
    d._seen["t1"] = time.monotonic() - 2.0
    assert d.is_duplicate("t1") is False  # Evicted, so not a dup


def test_len() -> None:
    d = TradeDedup()
    d.is_duplicate("t1")
    d.is_duplicate("t2")
    assert len(d) == 2
