"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

router = APIRouter()

# Create a custom registry (avoids default process/platform collectors)
registry = CollectorRegistry()

# ── Gauges ──────────────────────────────────────────────────────────
positions_open = Gauge(
    "polymarket_positions_open",
    "Number of open positions",
    registry=registry,
)
total_exposure_usd = Gauge(
    "polymarket_total_exposure_usd",
    "Total USD exposure across all positions",
    registry=registry,
)
unrealized_pnl_usd = Gauge(
    "polymarket_unrealized_pnl_usd",
    "Total unrealized PnL across all positions",
    registry=registry,
)
pipeline_state = Gauge(
    "polymarket_pipeline_state",
    "Pipeline state (0=checking, 1=ready, 2=degraded, 3=red, 4=closing, 5=safe_stop)",
    registry=registry,
)

# ── Counters ────────────────────────────────────────────────────────
trades_published = Counter(
    "polymarket_trades_published_total",
    "Total trades published to Redpanda",
    ["source"],
    registry=registry,
)
publish_timeouts = Counter(
    "polymarket_publish_timeouts_total",
    "Total publish timeout events",
    ["source"],
    registry=registry,
)
panic_triggered = Counter(
    "polymarket_panic_triggered_total",
    "Total panic close events",
    registry=registry,
)
normalize_drops = Counter(
    "polymarket_normalize_drops_total",
    "Total dropped trades during normalization",
    ["source", "reason"],
    registry=registry,
)


def refresh_gauges(request: Request) -> None:
    """Update gauge values from app state."""
    tracker = getattr(request.app.state, "tracker", None)
    if tracker is not None:
        positions = tracker.get_all_positions()
        positions_open.set(len(positions))
        total_exposure_usd.set(tracker.get_total_exposure())
        total_pnl = sum(p.unrealized_pnl for p in positions)
        unrealized_pnl_usd.set(total_pnl)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus metrics endpoint."""
    refresh_gauges(request)
    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST,
    )
