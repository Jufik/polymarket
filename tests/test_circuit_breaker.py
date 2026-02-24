"""Tests for the circuit breaker."""

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker


def test_circuit_starts_closed() -> None:
    cb = CircuitBreaker()
    assert cb.state == "closed"
    assert cb.allow_request() is True


def test_circuit_trips_after_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=3, cooldown_s=1000.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "open"
    assert cb.allow_request() is False


def test_circuit_resets_on_success() -> None:
    cb = CircuitBreaker(failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    # Simulate half_open by setting cooldown to 0
    cb._cooldown_s = 0.0
    assert cb.state == "half_open"
    cb.record_success()
    assert cb.state == "closed"


def test_circuit_half_open_after_cooldown() -> None:
    cb = CircuitBreaker(failure_threshold=1, cooldown_s=0.0)
    cb.record_failure()
    # Cooldown is 0, should be half_open
    assert cb.state == "half_open"
    assert cb.allow_request() is True


def test_failures_below_threshold_stay_closed() -> None:
    cb = CircuitBreaker(failure_threshold=5)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"
    assert cb.allow_request() is True


def test_success_resets_failure_count() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == "closed"
