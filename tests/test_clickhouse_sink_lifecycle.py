"""Tests for ClickHouseSink lifecycle methods."""

from polymarket_pipeline.sinks.clickhouse import ClickHouseSink


def test_sink_has_close_method() -> None:
    assert hasattr(ClickHouseSink, "close")


def test_sink_has_ping_method() -> None:
    assert hasattr(ClickHouseSink, "ping")


def test_sink_supports_context_manager() -> None:
    assert hasattr(ClickHouseSink, "__enter__")
    assert hasattr(ClickHouseSink, "__exit__")
