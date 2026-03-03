# research/tests/test_s2_tags.py
"""Tests for the S2 tag data module."""
from __future__ import annotations

from research.strategies.s2_tags import (
    TAG_BASE_RATES,
    TAG_PRIORITY,
    GLOBAL_FALLBACK,
    get_base_rate,
    materialize_tag_markets_sql,
    materialize_tag_base_rates_sql,
)


def test_tag_base_rates_has_16_tags():
    assert len(TAG_BASE_RATES) == 16


def test_tag_base_rates_yes_no_sum_to_one():
    for tag, rates in TAG_BASE_RATES.items():
        assert "YES" in rates and "NO" in rates, f"{tag} missing YES/NO"
        assert abs(rates["YES"] + rates["NO"] - 1.0) < 0.01, f"{tag} doesn't sum to 1.0"


def test_tag_base_rates_known_values():
    assert TAG_BASE_RATES["Elections"]["YES"] == 0.090
    assert TAG_BASE_RATES["Earnings"]["YES"] == 0.729
    assert TAG_BASE_RATES["Politics"]["YES"] == 0.232


def test_global_fallback():
    assert GLOBAL_FALLBACK["YES"] == 0.381
    assert GLOBAL_FALLBACK["NO"] == 0.619


def test_get_base_rate_known_tag():
    assert get_base_rate("Politics", "YES") == 0.232
    assert get_base_rate("Politics", "NO") == 0.768


def test_get_base_rate_unknown_tag():
    assert get_base_rate("UnknownTag", "YES") == 0.381
    assert get_base_rate("UnknownTag", "NO") == 0.619


def test_tag_priority_has_16_entries():
    assert len(TAG_PRIORITY) == 16
    assert set(TAG_PRIORITY) == set(TAG_BASE_RATES.keys())


def test_tag_priority_niche_first():
    """Most specific tags should come before general ones."""
    assert TAG_PRIORITY.index("Tariffs") < TAG_PRIORITY.index("Politics")
    assert TAG_PRIORITY.index("Courts") < TAG_PRIORITY.index("Culture")
    assert TAG_PRIORITY.index("AI") < TAG_PRIORITY.index("Tech")


def test_materialize_tag_markets_sql():
    sql = materialize_tag_markets_sql("_tmp_test_markets")
    assert "_tmp_test_markets" in sql
    assert "Memory" in sql
    assert "event_tags" in sql
    assert "condition_id" in sql
    assert "tag" in sql


def test_materialize_tag_base_rates_sql():
    sql = materialize_tag_base_rates_sql("_tmp_test_rates")
    assert "_tmp_test_rates" in sql
    assert "Memory" in sql
    assert "Politics" in sql
    assert "0.232" in sql
    # Should NOT contain PG engine joins
    assert "event_tags" not in sql
