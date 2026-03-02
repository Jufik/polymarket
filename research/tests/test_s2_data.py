# research/tests/test_s2_data.py
"""Test S2 data loading utilities."""
from research.strategies.s2_data import parse_period_range


def test_parse_period_range():
    start, end = parse_period_range("2025-07")
    assert start == "2025-07-01"
    assert end == "2025-08-01"


def test_parse_period_range_december():
    start, end = parse_period_range("2025-12")
    assert start == "2025-12-01"
    assert end == "2026-01-01"


def test_qualified_trades_query():
    from research.strategies.s2_data import qualified_trades_query

    sql = qualified_trades_query(
        traders={"0xabc", "0xdef"},
        start_date="2025-07-01",
        end_date="2025-08-01",
    )
    assert "0xabc" in sql
    assert "maker" in sql
    assert "2025-07-01" in sql
    assert "side = 'BUY'" in sql
