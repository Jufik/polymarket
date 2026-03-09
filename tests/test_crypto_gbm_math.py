"""Tests for GBM math (compute_gbm_p_up, estimate_rolling_sigma)."""

from __future__ import annotations

from polymarket_pipeline.strategies_impl.crypto_gbm.gbm import (
    compute_gbm_p_up,
    estimate_rolling_sigma,
)


class TestComputeGBMPUp:
    """Test P(Up) = Φ(d₂) where d₂ = ln(S_t/S₀) / (σ√(T-t))."""

    def test_no_movement_returns_half(self) -> None:
        """When S_t == S₀, P(Up) should be ~0.5."""
        p = compute_gbm_p_up(100.0, 100.0, 0.001, 5.0)
        assert abs(p - 0.5) < 0.01

    def test_large_up_move(self) -> None:
        """Significant upward move → P(Up) near 1."""
        p = compute_gbm_p_up(100.0, 105.0, 0.001, 2.0)
        assert p > 0.95

    def test_large_down_move(self) -> None:
        """Significant downward move → P(Up) near 0."""
        p = compute_gbm_p_up(100.0, 95.0, 0.001, 2.0)
        assert p < 0.05

    def test_zero_time_remaining_up(self) -> None:
        """At expiry, if S > S₀ → P(Up) = 1.0."""
        p = compute_gbm_p_up(100.0, 101.0, 0.001, 0.0)
        assert p == 1.0

    def test_zero_time_remaining_down(self) -> None:
        """At expiry, if S < S₀ → P(Up) = 0.0."""
        p = compute_gbm_p_up(100.0, 99.0, 0.001, 0.0)
        assert p == 0.0

    def test_zero_time_remaining_equal(self) -> None:
        """At expiry, if S == S₀ → resolves DOWN (must be *above* opening)."""
        p = compute_gbm_p_up(100.0, 100.0, 0.001, 0.0)
        assert p == 0.0

    def test_very_low_vol_up(self) -> None:
        """Very low vol with up move → nearly certain."""
        p = compute_gbm_p_up(100.0, 100.01, 1e-15, 5.0)
        assert p > 0.99

    def test_higher_vol_less_certain(self) -> None:
        """Same move should be less certain with higher volatility."""
        p_low = compute_gbm_p_up(100.0, 101.0, 0.0005, 5.0)
        p_high = compute_gbm_p_up(100.0, 101.0, 0.005, 5.0)
        assert p_low > p_high

    def test_more_time_less_certain(self) -> None:
        """Same move with more time remaining → closer to 0.5."""
        p_short = compute_gbm_p_up(100.0, 102.0, 0.001, 1.0)
        p_long = compute_gbm_p_up(100.0, 102.0, 0.001, 10.0)
        assert abs(p_short - 0.5) > abs(p_long - 0.5)

    def test_output_bounded(self) -> None:
        """Output should always be in [0, 1]."""
        for s0, st, sigma, t in [
            (100, 200, 0.01, 1),
            (100, 50, 0.01, 1),
            (100, 100, 0.0001, 0.1),
            (100, 100.001, 0.1, 15),
        ]:
            p = compute_gbm_p_up(s0, st, sigma, t)
            assert 0.0 <= p <= 1.0, f"Out of bounds: {p}"


class TestEstimateRollingSigma:
    """Test rolling sigma estimation from 1m closes."""

    def test_constant_prices_zero_sigma(self) -> None:
        """Constant prices → zero returns → zero sigma."""
        closes = [100.0] * 200
        sigma = estimate_rolling_sigma(closes, window=180)
        assert sigma is not None
        assert sigma == 0.0

    def test_too_few_samples_returns_none(self) -> None:
        """Less than min_samples should return None."""
        closes = [100.0, 101.0, 100.5]
        sigma = estimate_rolling_sigma(closes, window=60, min_samples=10)
        assert sigma is None

    def test_positive_sigma_with_movement(self) -> None:
        """Price movement should produce positive sigma."""
        # Simple alternating prices
        closes = [100.0 + (i % 2) * 0.5 for i in range(200)]
        sigma = estimate_rolling_sigma(closes, window=100)
        assert sigma is not None
        assert sigma > 0

    def test_window_respected(self) -> None:
        """Only the last `window` closes should be used."""
        # First 500 closes: high volatility
        high_vol = [100.0 + (i % 2) * 5.0 for i in range(500)]
        # Last 100 closes: low volatility
        low_vol = [100.0 + (i % 2) * 0.01 for i in range(100)]
        all_closes = high_vol + low_vol

        sigma_full = estimate_rolling_sigma(all_closes, window=600)
        sigma_recent = estimate_rolling_sigma(all_closes, window=100)

        assert sigma_full is not None
        assert sigma_recent is not None
        assert sigma_recent < sigma_full
