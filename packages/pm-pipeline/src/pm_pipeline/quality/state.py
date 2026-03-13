"""Readiness state machine for the live pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class PipelineState(StrEnum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"
    RED = "red"  # Critical failure — positions being closed
    CLOSING = "closing"  # Panic close in progress
    SAFE_STOP = "safe_stop"  # All positions closed, pipeline can stop


# Per-check grace periods (seconds).  Source liveness is the most urgent
# because an ingestor crash means we're flying blind.
DEFAULT_CHECK_GRACE_S: dict[str, float] = {
    "source_liveness": 120.0,  # 2 min  — ingestor crash
    "volume_reconciliation": 600.0,  # 10 min — could be quiet market
    "dedup_sanity": 300.0,  # 5 min  — enrichment lag
    "metadata_freshness": 900.0,  # 15 min — slow Gamma sync
    "resolved_completeness": 600.0,  # 10 min — CH backlog
}


@dataclass
class CheckResult:
    """Result of a single health check."""

    ok: bool
    reason: str = ""


class ReadinessState:
    """Tracks pipeline readiness based on health check results.

    Supports per-check grace periods: the shortest grace period among
    currently failing checks determines when the state transitions to RED.
    """

    def __init__(
        self,
        degraded_grace_s: float = 300.0,
        check_grace_s: dict[str, float] | None = None,
    ) -> None:
        self._state = PipelineState.CHECKING
        self._last_results: dict[str, CheckResult] = {}
        self._degraded_since: float | None = None
        self._degraded_grace_s = degraded_grace_s
        self._check_grace_s = dict(check_grace_s) if check_grace_s else {}

    @property
    def current(self) -> PipelineState:
        return self._state

    @property
    def last_results(self) -> dict[str, CheckResult]:
        return dict(self._last_results)

    @property
    def failures(self) -> list[str]:
        return [f"{name}: {r.reason}" for name, r in self._last_results.items() if not r.ok]

    @property
    def degraded_since(self) -> float | None:
        return self._degraded_since

    def _effective_grace(self) -> float:
        """Return the shortest grace period among currently failing checks."""
        failing_checks = [name for name, r in self._last_results.items() if not r.ok]
        if not failing_checks:
            return self._degraded_grace_s

        graces = [self._check_grace_s.get(name, self._degraded_grace_s) for name in failing_checks]
        return min(graces)

    @property
    def time_until_red(self) -> float | None:
        """Seconds until state transitions to RED, or None if not degraded."""
        if self._degraded_since is None:
            return None
        elapsed = time.monotonic() - self._degraded_since
        remaining = self._effective_grace() - elapsed
        return max(0.0, remaining)

    def update(self, results: dict[str, CheckResult]) -> None:
        """Update state based on new check results."""
        self._last_results = results

        # Terminal states are sticky -- never transition back
        if self._state in (PipelineState.CLOSING, PipelineState.SAFE_STOP):
            return

        all_ok = all(r.ok for r in results.values())

        if all_ok:
            self._state = PipelineState.READY
            self._degraded_since = None
        elif self._state == PipelineState.RED:
            # Stay in RED until manually reset or CLOSING
            pass
        else:
            # CHECKING or DEGRADED with failures
            if self._degraded_since is None:
                self._degraded_since = time.monotonic()

            elapsed = time.monotonic() - self._degraded_since
            if elapsed >= self._effective_grace():
                self._state = PipelineState.RED
            else:
                self._state = PipelineState.DEGRADED

    def set_closing(self) -> None:
        """Transition to CLOSING (panic close in progress)."""
        self._state = PipelineState.CLOSING

    def set_safe_stop(self) -> None:
        """Transition to SAFE_STOP (all positions closed)."""
        self._state = PipelineState.SAFE_STOP

    def reset(self) -> None:
        """Reset to CHECKING state (e.g. after manual intervention)."""
        self._state = PipelineState.CHECKING
        self._degraded_since = None
