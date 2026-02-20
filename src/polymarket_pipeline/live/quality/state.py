"""Readiness state machine for the live pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PipelineState(StrEnum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"


@dataclass
class CheckResult:
    """Result of a single health check."""

    ok: bool
    reason: str = ""


class ReadinessState:
    """Tracks pipeline readiness based on health check results."""

    def __init__(self) -> None:
        self._state = PipelineState.CHECKING
        self._last_results: dict[str, CheckResult] = {}

    @property
    def current(self) -> PipelineState:
        return self._state

    @property
    def failures(self) -> list[str]:
        return [
            f"{name}: {r.reason}" for name, r in self._last_results.items() if not r.ok
        ]

    def update(self, results: dict[str, CheckResult]) -> None:
        """Update state based on new check results."""
        self._last_results = results
        all_ok = all(r.ok for r in results.values())
        self._state = PipelineState.READY if all_ok else PipelineState.DEGRADED
