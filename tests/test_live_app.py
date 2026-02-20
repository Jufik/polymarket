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
