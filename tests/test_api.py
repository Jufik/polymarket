"""Tests for the FastAPI service."""

from __future__ import annotations

import os

# Settings() is instantiated at module level in app.py and requires PM_ALCHEMY_WS_URL.
# Set it before importing the module.
os.environ.setdefault("PM_ALCHEMY_WS_URL", "wss://fake.alchemy.test/ws")

from fastapi.testclient import TestClient

from polymarket_pipeline.api.app import create_app
from polymarket_pipeline.execution.clob_client import OrderResult
from polymarket_pipeline.execution.models import Position


class MockConnection:
    """Mock asyncpg connection."""

    async def execute(self, sql, *args):
        return "SELECT 1"

    async def fetch(self, sql, *args):
        return []

    async def fetchval(self, sql, *args):
        return 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


class _AcquireContext:
    """Mimics asyncpg pool.acquire() which is both awaitable and an async context manager."""

    async def __aenter__(self):
        return MockConnection()

    async def __aexit__(self, *exc):
        pass


class MockPool:
    """Mock asyncpg pool."""

    def acquire(self):
        return _AcquireContext()

    async def close(self):
        pass


class MockTracker:
    def __init__(self, positions=None):
        self._positions = positions or []

    def get_all_positions(self):
        return self._positions

    def get_position(self, cid):
        return next((p for p in self._positions if p.condition_id == cid), None)

    def get_total_exposure(self):
        return sum(p.size * p.last_price for p in self._positions)


class MockClob:
    async def get_open_orders(self, condition_id=None):
        return []

    async def cancel_order(self, order_id):
        return True

    async def submit_order(self, **kwargs):
        return OrderResult(order_id="c1", success=True)

    async def close(self):
        pass


def _make_test_app(positions=None):
    """Create a test app with mock dependencies."""
    test_app = create_app()
    test_app.state.pool = MockPool()
    test_app.state.tracker = MockTracker(positions)
    test_app.state.clob = MockClob()
    test_app.state.settings = None
    return test_app


def test_health_endpoint():
    """NOTE: This is a sync test - TestClient handles async internally."""
    # Health check requires a real pool.acquire(), so we test the route exists
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_list_positions_empty():
    app = _make_test_app()
    client = TestClient(app)
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["positions"] == []


def test_list_positions_with_data():
    pos = Position(
        condition_id="cond-1",
        asset_id="asset-1",
        side="BUY",
        size=10.0,
        avg_entry=0.55,
        cost_basis=5.5,
        unrealized_pnl=0.5,
        last_price=0.60,
    )
    app = _make_test_app(positions=[pos])
    client = TestClient(app)
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["positions"][0]["condition_id"] == "cond-1"


def test_get_position_not_found():
    app = _make_test_app()
    client = TestClient(app)
    resp = client.get("/api/positions/missing")
    assert resp.status_code == 200
    assert resp.json()["position"] is None


def test_panic_endpoint():
    app = _make_test_app()
    client = TestClient(app)
    resp = client.post("/api/panic")
    assert resp.status_code == 200
    data = resp.json()
    assert data["triggered"] is True


def test_fills_endpoint():
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/fills")
    assert resp.status_code == 200
