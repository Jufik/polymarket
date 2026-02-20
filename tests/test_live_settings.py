"""Tests for live pipeline settings."""

import pytest


def test_settings_defaults():
    """Settings should have sensible defaults for local dev."""
    from polymarket_pipeline.live.settings import Settings

    s = Settings(alchemy_ws_url="wss://test.example.com")
    assert s.redpanda_url == "localhost:19092"
    assert s.ch_host == "localhost"
    assert s.ch_port == 18123
    assert s.quality_check_interval_s == 900
    assert s.gap_threshold_s == 600


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch):
    """Settings should read from PM_ prefixed env vars."""
    monkeypatch.setenv("PM_REDPANDA_URL", "redpanda:9092")
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://custom.alchemy.com")
    monkeypatch.setenv("PM_CH_HOST", "10.0.0.1")

    from polymarket_pipeline.live.settings import Settings

    s = Settings()
    assert s.redpanda_url == "redpanda:9092"
    assert s.alchemy_ws_url == "wss://custom.alchemy.com"
    assert s.ch_host == "10.0.0.1"


def test_dashboard_settings_defaults(monkeypatch: pytest.MonkeyPatch):
    """Dashboard fields should have sensible defaults."""
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")

    import importlib

    import polymarket_pipeline.live.settings as mod

    importlib.reload(mod)
    s = mod.Settings()
    assert s.dashboard_refresh_s == 5
    assert s.dashboard_port == 8099


def test_settings_alchemy_url_required(monkeypatch: pytest.MonkeyPatch):
    """alchemy_ws_url has no default and must be provided."""
    # Ensure the env var is NOT set, otherwise pydantic-settings would pick it up
    monkeypatch.delenv("PM_ALCHEMY_WS_URL", raising=False)

    from polymarket_pipeline.live.settings import Settings

    with pytest.raises(Exception):  # ValidationError  # noqa: B017
        Settings(_env_file=None)
