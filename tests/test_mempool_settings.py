"""Tests for mempool settings integration."""

import os


class TestMempoolSettings:
    def test_mempool_disabled_by_default(self):
        """Mempool should be opt-in."""
        from polymarket_pipeline.live.settings import Settings

        os.environ.setdefault("PM_ALCHEMY_WS_URL", "wss://test")
        s = Settings()
        assert s.mempool_enabled is False

    def test_mempool_port_default(self):
        """Default listen port is 30304."""
        from polymarket_pipeline.live.settings import Settings

        os.environ.setdefault("PM_ALCHEMY_WS_URL", "wss://test")
        s = Settings()
        assert s.mempool_listen_port == 30304

    def test_mempool_enabled_via_env(self, monkeypatch):
        """Can enable mempool via PM_MEMPOOL_ENABLED=true."""
        monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test")
        monkeypatch.setenv("PM_MEMPOOL_ENABLED", "true")
        from polymarket_pipeline.live.settings import Settings

        s = Settings()
        assert s.mempool_enabled is True
