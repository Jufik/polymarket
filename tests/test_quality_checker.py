"""Tests for quality checker health checks."""

import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def checker():
    from polymarket_pipeline.live.quality.checker import QualityChecker
    from polymarket_pipeline.live.settings import Settings

    settings = Settings(alchemy_ws_url="wss://test.example.com")
    ch = MagicMock()
    return QualityChecker(settings=settings, clickhouse=ch)


class TestSourceLiveness:
    def test_all_sources_alive(self, checker):
        """Both sources reporting recently -> check passes."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        result = checker.check_source_liveness()
        assert result.ok

    def test_one_source_stale(self, checker):
        """One source stale > threshold -> check fails."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now - 120)  # 2 min ago
        result = checker.check_source_liveness()
        assert not result.ok
        assert "alchemy" in result.reason

    def test_no_heartbeats_yet(self, checker):
        """No heartbeats received -> check fails."""
        result = checker.check_source_liveness()
        assert not result.ok


class TestFullCheck:
    def test_run_all_checks(self, checker):
        """run_all_checks should return results dict and update state."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        # Mock ClickHouse queries to return reasonable data
        checker._ch.query.return_value = [{"cnt": 1000}]
        results = checker.run_all_checks()
        assert isinstance(results, dict)
        assert "source_liveness" in results
