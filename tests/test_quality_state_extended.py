"""Tests for the extended ReadinessState machine."""

from __future__ import annotations

import time
from unittest.mock import patch

from polymarket_pipeline.live.quality.state import CheckResult, PipelineState, ReadinessState


def test_initial_state_is_checking() -> None:
    state = ReadinessState()
    assert state.current == PipelineState.CHECKING


def test_all_ok_transitions_to_ready() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=True), "pg": CheckResult(ok=True)})
    assert state.current == PipelineState.READY


def test_failure_transitions_to_degraded() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=False, reason="timeout")})
    assert state.current == PipelineState.DEGRADED


def test_degraded_transitions_to_red_after_grace() -> None:
    state = ReadinessState(degraded_grace_s=1.0)
    state.update({"ch": CheckResult(ok=False, reason="down")})
    assert state.current == PipelineState.DEGRADED

    # Simulate time passing beyond grace period
    with patch("polymarket_pipeline.quality.state.time") as mock_time:
        start = time.monotonic()
        mock_time.monotonic.return_value = start + 2.0
        state._degraded_since = start
        state.update({"ch": CheckResult(ok=False, reason="still down")})
        assert state.current == PipelineState.RED


def test_recovery_from_degraded_to_ready() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=False, reason="timeout")})
    assert state.current == PipelineState.DEGRADED
    state.update({"ch": CheckResult(ok=True)})
    assert state.current == PipelineState.READY
    assert state.degraded_since is None


def test_closing_state() -> None:
    state = ReadinessState()
    state.set_closing()
    assert state.current == PipelineState.CLOSING


def test_safe_stop_state() -> None:
    state = ReadinessState()
    state.set_safe_stop()
    assert state.current == PipelineState.SAFE_STOP


def test_closing_does_not_revert_on_update() -> None:
    state = ReadinessState()
    state.set_closing()
    state.update({"ch": CheckResult(ok=False)})
    assert state.current == PipelineState.CLOSING


def test_safe_stop_does_not_revert_on_update() -> None:
    state = ReadinessState()
    state.set_safe_stop()
    state.update({"ch": CheckResult(ok=False)})
    assert state.current == PipelineState.SAFE_STOP


def test_red_does_not_revert_on_degraded_update() -> None:
    state = ReadinessState(degraded_grace_s=0.0)
    state.update({"ch": CheckResult(ok=False, reason="down")})
    assert state.current == PipelineState.RED
    # Another failure update should stay RED
    state.update({"ch": CheckResult(ok=False, reason="still down")})
    assert state.current == PipelineState.RED


def test_red_recovers_on_all_ok() -> None:
    state = ReadinessState(degraded_grace_s=0.0)
    state.update({"ch": CheckResult(ok=False, reason="down")})
    assert state.current == PipelineState.RED
    state.update({"ch": CheckResult(ok=True)})
    assert state.current == PipelineState.READY


def test_reset() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=False)})
    state.reset()
    assert state.current == PipelineState.CHECKING
    assert state.degraded_since is None


def test_time_until_red_none_when_not_degraded() -> None:
    state = ReadinessState(degraded_grace_s=300.0)
    assert state.time_until_red is None


def test_time_until_red_positive_when_degraded() -> None:
    state = ReadinessState(degraded_grace_s=300.0)
    state.update({"ch": CheckResult(ok=False)})
    assert state.time_until_red is not None
    assert state.time_until_red > 0


def test_time_until_red_zero_when_expired() -> None:
    state = ReadinessState(degraded_grace_s=1.0)
    state._degraded_since = time.monotonic() - 10.0
    assert state.time_until_red == 0.0


def test_last_results_empty_by_default() -> None:
    state = ReadinessState()
    assert state.last_results == {}


def test_failures_list() -> None:
    state = ReadinessState()
    state.update(
        {
            "a": CheckResult(ok=True),
            "b": CheckResult(ok=False, reason="broken"),
        }
    )
    assert state.failures == ["b: broken"]
