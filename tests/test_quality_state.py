"""Tests for ReadinessState and CheckResult."""

import pytest

from polymarket_pipeline.live.quality.state import CheckResult, PipelineState, ReadinessState


def test_last_results_empty_by_default():
    state = ReadinessState()
    assert state.last_results == {}


def test_last_results_populated_after_update():
    state = ReadinessState()
    results = {
        "liveness": CheckResult(ok=True),
        "volume": CheckResult(ok=False, reason="low"),
    }
    state.update(results)
    assert state.last_results == results
    assert state.current == PipelineState.DEGRADED


class TestReadinessState:
    def test_initial_state_is_checking(self):
        from polymarket_pipeline.live.quality.state import PipelineState, ReadinessState

        state = ReadinessState()
        assert state.current == PipelineState.CHECKING

    def test_all_checks_pass_transitions_to_ready(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        results = {
            "resolved_completeness": CheckResult(ok=True),
            "volume_reconciliation": CheckResult(ok=True),
            "source_liveness": CheckResult(ok=True),
            "metadata_freshness": CheckResult(ok=True),
            "dedup_sanity": CheckResult(ok=True),
        }
        state.update(results)
        assert state.current == PipelineState.READY

    def test_any_check_fails_transitions_to_degraded(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        results = {
            "resolved_completeness": CheckResult(ok=True),
            "volume_reconciliation": CheckResult(ok=False, reason="Volume < 10% of average"),
            "source_liveness": CheckResult(ok=True),
            "metadata_freshness": CheckResult(ok=True),
            "dedup_sanity": CheckResult(ok=True),
        }
        state.update(results)
        assert state.current == PipelineState.DEGRADED

    def test_recovery_from_degraded_to_ready(self):
        from polymarket_pipeline.live.quality.state import (
            CheckResult,
            PipelineState,
            ReadinessState,
        )

        state = ReadinessState()
        # First: degraded
        state.update({"check1": CheckResult(ok=False, reason="bad")})
        assert state.current == PipelineState.DEGRADED
        # Then: all good
        state.update({"check1": CheckResult(ok=True)})
        assert state.current == PipelineState.READY

    def test_failures_list(self):
        from polymarket_pipeline.live.quality.state import CheckResult, ReadinessState

        state = ReadinessState()
        state.update({
            "a": CheckResult(ok=True),
            "b": CheckResult(ok=False, reason="broken"),
        })
        assert state.failures == ["b: broken"]
