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


@dataclass
class CheckResult:
    """Result of a single health check."""

    ok: bool
    reason: str = ""


class ReadinessState:
    """Tracks pipeline readiness based on health check results."""

    def __init__(self, degraded_grace_s: float = 300.0) -> None:
        self._state = PipelineState.CHECKING
        self._last_results: dict[str, CheckResult] = {}
        self._degraded_since: float | None = None
        self._degraded_grace_s = degraded_grace_s

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

    @property
    def time_until_red(self) -> float | None:
        """Seconds until state transitions to RED, or None if not degraded."""
        if self._degraded_since is None:
            return None
        elapsed = time.monotonic() - self._degraded_since
        remaining = self._degraded_grace_s - elapsed
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
            if elapsed >= self._degraded_grace_s:
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
