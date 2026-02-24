"""Tests for quality checker health checks."""

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.settings import Settings


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {"alchemy_ws_url": "wss://test.example.com"}
    defaults.update(overrides)
    return Settings(**defaults)


class _MockPGPool:
    """Async mock for asyncpg pool used by quality checker."""

    def __init__(
        self,
        *,
        token_map_fresh_count: int = 500,
        resolved_markets: int = 0,
    ) -> None:
        self._token_fresh = token_map_fresh_count
        self._resolved = resolved_markets

    async def fetchval(self, query: str) -> int:
        if "token_market_map" in query:
            return self._token_fresh
        if "markets" in query and "resolved" in query:
            return self._resolved
        return 0


@pytest.fixture
def checker() -> QualityChecker:
    settings = _settings()
    ch = MagicMock()
    return QualityChecker(settings=settings, clickhouse=ch)


class TestSourceLiveness:
    def test_all_sources_alive(self, checker: QualityChecker) -> None:
        """Both sources reporting recently -> check passes."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        result = checker.check_source_liveness()
        assert result.ok

    def test_one_source_stale(self, checker: QualityChecker) -> None:
        """One source stale > threshold -> check fails."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now - 120)  # 2 min ago
        result = checker.check_source_liveness()
        assert not result.ok
        assert "alchemy" in result.reason

    def test_no_heartbeats_yet(self, checker: QualityChecker) -> None:
        """No heartbeats received -> check fails."""
        result = checker.check_source_liveness()
        assert not result.ok


class TestFullCheck:
    async def test_run_all_checks(self, checker: QualityChecker) -> None:
        """run_all_checks should return results dict and update state."""
        now = time.time()
        checker.record_heartbeat("rtds", now)
        checker.record_heartbeat("alchemy", now)
        # Mock ClickHouse queries to return reasonable data
        checker._ch.query.return_value = [{"cnt": 1000}]
        results = await checker.run_all_checks()
        assert isinstance(results, dict)
        assert "source_liveness" in results


class TestMetadataFreshness:
    async def test_no_pg_pool_returns_ok(self) -> None:
        """Without a PG pool, metadata check should pass gracefully."""
        settings = _settings()
        ch = MagicMock()
        qc = QualityChecker(settings=settings, clickhouse=ch, pg_pool=None)
        result = await qc.check_metadata_freshness()
        assert result.ok

    async def test_metadata_freshness_fails_when_stale(self) -> None:
        """Metadata check fails if token_map has no entries updated recently."""
        settings = _settings()
        ch = MagicMock()
        qc = QualityChecker(
            settings=settings,
            clickhouse=ch,
            pg_pool=_MockPGPool(token_map_fresh_count=0),
        )
        result = await qc.check_metadata_freshness()
        assert result.ok is False

    async def test_metadata_freshness_passes_when_fresh(self) -> None:
        settings = _settings()
        ch = MagicMock()
        qc = QualityChecker(
            settings=settings,
            clickhouse=ch,
            pg_pool=_MockPGPool(token_map_fresh_count=500),
        )
        result = await qc.check_metadata_freshness()
        assert result.ok is True


class TestResolvedCompleteness:
    async def test_no_pg_pool_returns_ok(self) -> None:
        settings = _settings()
        ch = MagicMock()
        qc = QualityChecker(settings=settings, clickhouse=ch, pg_pool=None)
        result = await qc.check_resolved_completeness()
        assert result.ok

    async def test_resolved_completeness_fails_low_coverage(self) -> None:
        settings = _settings()
        ch = MagicMock()
        ch.query.return_value = [{"cnt": 50}]
        qc = QualityChecker(
            settings=settings,
            clickhouse=ch,
            pg_pool=_MockPGPool(resolved_markets=100),
        )
        result = await qc.check_resolved_completeness()
        assert result.ok is False

    async def test_resolved_completeness_passes(self) -> None:
        settings = _settings()
        ch = MagicMock()
        ch.query.return_value = [{"cnt": 95}]
        qc = QualityChecker(
            settings=settings,
            clickhouse=ch,
            pg_pool=_MockPGPool(resolved_markets=100),
        )
        result = await qc.check_resolved_completeness()
        assert result.ok is True
