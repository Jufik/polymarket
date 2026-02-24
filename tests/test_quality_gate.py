"""Tests for the quality gate readiness state machine."""

from polymarket_pipeline.live.quality.state import CheckResult, PipelineState, ReadinessState


def test_closing_does_not_revert_on_all_ok():
    """CLOSING must be sticky even when all checks pass."""
    state = ReadinessState()
    state.set_closing()
    assert state.current == PipelineState.CLOSING

    # All-ok results must NOT revert CLOSING -> READY
    state.update({"liveness": CheckResult(ok=True), "volume": CheckResult(ok=True)})
    assert state.current == PipelineState.CLOSING


def test_safe_stop_does_not_revert_on_all_ok():
    """SAFE_STOP must be sticky even when all checks pass."""
    state = ReadinessState()
    state.set_safe_stop()
    assert state.current == PipelineState.SAFE_STOP

    state.update({"liveness": CheckResult(ok=True), "volume": CheckResult(ok=True)})
    assert state.current == PipelineState.SAFE_STOP


def test_quality_checker_passes_grace_period():
    """QualityChecker should pass degraded_grace_s from Settings to ReadinessState."""
    from unittest.mock import MagicMock

    from polymarket_pipeline.live.quality.checker import QualityChecker

    settings = MagicMock()
    settings.degraded_grace_s = 42.0
    settings.source_liveness_timeout_s = 30
    checker = QualityChecker(settings=settings, clickhouse=MagicMock())
    assert checker.state._degraded_grace_s == 42.0
