"""Tests for Prometheus metrics endpoint."""

from polymarket_pipeline.api.routes.metrics import (
    registry,
    positions_open,
    total_exposure_usd,
    trades_published,
    publish_timeouts,
    normalize_drops,
)
from prometheus_client import generate_latest


def test_metrics_registry_has_gauges():
    """Verify metrics are registered."""
    output = generate_latest(registry).decode()
    assert "polymarket_positions_open" in output
    assert "polymarket_total_exposure_usd" in output


def test_positions_gauge_updates():
    positions_open.set(5)
    output = generate_latest(registry).decode()
    assert "polymarket_positions_open 5.0" in output
    positions_open.set(0)


def test_counter_increments():
    trades_published.labels(source="rtds").inc()
    output = generate_latest(registry).decode()
    assert "polymarket_trades_published_total" in output
