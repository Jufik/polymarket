"""Tests for live pipeline settings."""

import pytest


def test_settings_defaults():
    """Settings should have sensible defaults for local dev."""
    from polymarket_pipeline.live.settings import Settings

    s = Settings()
    assert s.redpanda_url == "localhost:19092"
    assert s.ch_host == "localhost"
    assert s.ch_port == 18123
    assert s.quality_check_interval_s == 900
    assert s.gap_threshold_s == 600


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch):
    """Settings should read from PM_ prefixed env vars."""
    monkeypatch.setenv("PM_REDPANDA_URL", "redpanda:9092")
    monkeypatch.setenv("PM_RPC_WS_URL", "wss://custom.rpc.com")
    monkeypatch.setenv("PM_CH_HOST", "10.0.0.1")

    from polymarket_pipeline.live.settings import Settings

    s = Settings()
    assert s.redpanda_url == "redpanda:9092"
    assert s.rpc_ws_url == "wss://custom.rpc.com"
    assert s.ch_host == "10.0.0.1"


def test_settings_backward_compat_alchemy_env(monkeypatch: pytest.MonkeyPatch):
    """Legacy PM_ALCHEMY_WS_URL env var should still work via AliasChoices."""
    monkeypatch.delenv("PM_RPC_WS_URL", raising=False)
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://legacy.alchemy.com")

    from polymarket_pipeline.live.settings import Settings

    s = Settings(_env_file=None)
    assert s.rpc_ws_url == "wss://legacy.alchemy.com"


def test_dashboard_settings_defaults(monkeypatch: pytest.MonkeyPatch):
    """Dashboard fields should have sensible defaults."""
    monkeypatch.setenv("PM_RPC_WS_URL", "wss://test.example.com")

    import importlib

    import polymarket_pipeline.live.settings as mod

    importlib.reload(mod)
    s = mod.Settings()
    assert s.dashboard_refresh_s == 5
    assert s.dashboard_port == 8099


def test_settings_rpc_url_has_free_default(monkeypatch: pytest.MonkeyPatch):
    """rpc_ws_url defaults to a free public Polygon RPC endpoint."""
    monkeypatch.delenv("PM_RPC_WS_URL", raising=False)
    monkeypatch.delenv("PM_ALCHEMY_WS_URL", raising=False)

    from polymarket_pipeline.live.settings import Settings

    s = Settings(_env_file=None)
    assert s.rpc_ws_url == "wss://polygon-bor-rpc.publicnode.com"


def test_settings_resolution_rpc_enabled_default():
    """resolution_rpc_enabled should default to True."""
    from polymarket_pipeline.live.settings import Settings

    s = Settings()
    assert s.resolution_rpc_enabled is True
