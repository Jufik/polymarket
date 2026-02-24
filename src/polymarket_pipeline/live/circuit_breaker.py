"""Circuit breaker for publish operations."""

from __future__ import annotations

import time

import structlog

log = structlog.get_logger()


class CircuitBreaker:
    """Trips after N consecutive failures, auto-resets after cooldown.

    States:
    - CLOSED: normal operation, publishes allowed
    - OPEN: tripped, publishes rejected immediately
    - HALF_OPEN: cooldown elapsed, allow one test publish
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        if (
            self._state == "open"
            and time.monotonic() - self._last_failure_time >= self._cooldown_s
        ):
            return "half_open"
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def record_success(self) -> None:
        """Record a successful publish."""
        if self._state != "closed":
            log.info("circuit_breaker.reset", previous_state=self._state)
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failed publish."""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._consecutive_failures >= self._failure_threshold:
            if self._state != "open":
                log.warning(
                    "circuit_breaker.tripped",
                    failures=self._consecutive_failures,
                    cooldown_s=self._cooldown_s,
                )
            self._state = "open"

    def allow_request(self) -> bool:
        """Return True if a publish should be attempted."""
        return self.state in ("closed", "half_open")
