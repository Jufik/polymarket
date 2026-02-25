"""Tests for derived table DDL in live schema."""

from __future__ import annotations

from polymarket_pipeline.live.schema import (
    MARKETS_RESOLVED_VIEW,
    TRADER_TRADE_AGG_MAKER_MV,
    TRADER_TRADE_AGG_TABLE,
    TRADER_TRADE_AGG_TAKER_MV,
    TRADER_VOLUMES_MAKER_MV,
    TRADER_VOLUMES_TABLE,
    TRADER_VOLUMES_TAKER_MV,
)


def test_ddl_constants_are_nonempty_strings() -> None:
    for name, ddl in [
        ("MARKETS_RESOLVED_VIEW", MARKETS_RESOLVED_VIEW),
        ("TRADER_VOLUMES_TABLE", TRADER_VOLUMES_TABLE),
        ("TRADER_VOLUMES_MAKER_MV", TRADER_VOLUMES_MAKER_MV),
        ("TRADER_VOLUMES_TAKER_MV", TRADER_VOLUMES_TAKER_MV),
        ("TRADER_TRADE_AGG_TABLE", TRADER_TRADE_AGG_TABLE),
        ("TRADER_TRADE_AGG_MAKER_MV", TRADER_TRADE_AGG_MAKER_MV),
        ("TRADER_TRADE_AGG_TAKER_MV", TRADER_TRADE_AGG_TAKER_MV),
    ]:
        assert isinstance(ddl, str), f"{name} should be a string"
        assert len(ddl.strip()) > 50, f"{name} should contain real DDL"


def test_ddl_contains_correct_table_names() -> None:
    assert "markets_resolved" in MARKETS_RESOLVED_VIEW
    assert "trader_volumes" in TRADER_VOLUMES_TABLE
    assert "SummingMergeTree" in TRADER_VOLUMES_TABLE
    assert "trader_trade_agg" in TRADER_TRADE_AGG_TABLE
    assert "SummingMergeTree" in TRADER_TRADE_AGG_TABLE
    assert "TO trader_volumes" in TRADER_VOLUMES_MAKER_MV
    assert "TO trader_trade_agg" in TRADER_TRADE_AGG_MAKER_MV
