"""Tests for ClickHouseBackend derived-view query builders."""

from __future__ import annotations

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend


def test_mvf_query_is_valid_sql() -> None:
    """MVF query should use trader_volumes FINAL."""
    sql = ClickHouseBackend.mvf_query()
    assert "trader_volumes" in sql
    assert "FINAL" in sql
    assert "maker_vol" in sql
    assert "taker_vol" in sql


def test_mvf_query_with_traders_filter() -> None:
    """MVF query should accept trader filter."""
    sql = ClickHouseBackend.mvf_query(traders=["0xA", "0xB"])
    assert "WHERE" in sql
    assert "'0xA'" in sql
    assert "'0xB'" in sql


def test_trader_pnl_query_is_valid_sql() -> None:
    """PnL query should join trader_trade_agg with markets_resolved."""
    sql = ClickHouseBackend.trader_pnl_query()
    assert "trader_trade_agg" in sql
    assert "FINAL" in sql
    assert "markets_resolved" in sql
    assert "market_pnl" in sql


def test_trader_pnl_query_with_traders_filter() -> None:
    """PnL query should accept trader filter."""
    sql = ClickHouseBackend.trader_pnl_query(traders=["0xC"])
    assert "'0xC'" in sql


def test_trader_pnl_query_with_condition_filter() -> None:
    """PnL query should accept condition_id filter."""
    sql = ClickHouseBackend.trader_pnl_query(condition_ids=["0xm1"])
    assert "'0xm1'" in sql


def test_consistency_pnl_query_no_filters() -> None:
    """Extended PnL query should include net_yes_tokens and wavg_yes_entry_price."""
    sql = ClickHouseBackend.consistency_pnl_query()
    assert "net_yes_tokens" in sql
    assert "wavg_yes_entry_price" in sql
    assert "token_market_map" in sql
    assert "trader_trade_agg FINAL" in sql
    assert "markets_resolved" in sql
    assert "WHERE" not in sql


def test_consistency_pnl_query_with_traders() -> None:
    """Extended PnL query with trader filter."""
    sql = ClickHouseBackend.consistency_pnl_query(traders=["0xabc", "0xdef"])
    assert "'0xabc'" in sql
    assert "'0xdef'" in sql
    assert "a.trader IN" in sql


def test_resolved_markets_query() -> None:
    """Resolved markets query should select condition_id + resolved_at."""
    sql = ClickHouseBackend.resolved_markets_query()
    assert "condition_id" in sql
    assert "resolved_at" in sql
    assert "markets_resolved" in sql
