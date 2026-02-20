"""Tests for FastStream app scaffold."""

import pytest


def test_app_importable(monkeypatch: pytest.MonkeyPatch):
    """App module should be importable without side effects."""
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")

    import importlib

    import polymarket_pipeline.live.app as app_mod

    importlib.reload(app_mod)

    assert app_mod.app is not None
    assert app_mod.broker is not None


def test_asgi_app_has_dashboard_route(monkeypatch: pytest.MonkeyPatch):
    """ASGI app should expose /dashboard route."""
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")

    import importlib

    import polymarket_pipeline.live.app as app_mod

    importlib.reload(app_mod)

    assert app_mod.asgi_app is not None
    route_paths = [path for path, _ in app_mod.asgi_app.routes]
    assert "/dashboard" in route_paths
