"""Tests for the monitoring dashboard."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from polymarket_pipeline.live.quality.checker import QualityChecker


@pytest.fixture
def mock_checker(monkeypatch: pytest.MonkeyPatch) -> QualityChecker:
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test")
    from polymarket_pipeline.live.settings import Settings

    ch = MagicMock()
    ch.query.return_value = []
    checker = QualityChecker(settings=Settings(), clickhouse=ch)
    checker.record_heartbeat("rtds", time.time())
    checker.record_heartbeat("alchemy", time.time() - 5)
    checker.run_all_checks()
    return checker


def test_build_html_contains_pipeline_state(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert '<meta http-equiv="refresh"' in html
    assert "DEGRADED" in html or "READY" in html or "CHECKING" in html


def test_build_html_contains_producer_table(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "rtds" in html
    assert "alchemy" in html


def test_build_html_contains_check_results(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "source_liveness" in html
    assert "volume_reconciliation" in html
    assert "dedup_sanity" in html


def test_build_html_contains_gap_section(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "Source Race" in html
    assert "Coverage Gaps" in html


def test_make_asgi_app_callable(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import make_dashboard_route

    asgi_app = make_dashboard_route(mock_checker, refresh_s=5)
    assert callable(asgi_app)
