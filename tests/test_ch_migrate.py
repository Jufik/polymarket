"""Tests for ClickHouse migration runner."""

from polymarket_pipeline.live.ch_migrate import MIGRATIONS_TABLE, CREATE_MIGRATIONS_TABLE


def test_migrations_table_name():
    assert MIGRATIONS_TABLE == "_ch_migrations"


def test_create_table_sql():
    assert "CREATE TABLE IF NOT EXISTS" in CREATE_MIGRATIONS_TABLE
    assert "_ch_migrations" in CREATE_MIGRATIONS_TABLE
