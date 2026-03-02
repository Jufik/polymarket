# tests/test_s2_insider_copy.py
"""Tests for s2_insider_copy strategy components."""

from __future__ import annotations

import pytest


class TestMarketSusceptibility:
    """Test market susceptibility classification."""

    def test_political_is_high(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will Trump win the 2024 election?",
            category="Politics",
        )
        assert tier == "HIGH"

    def test_sports_is_medium(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will the Lakers win tonight?",
            category="Sports",
        )
        assert tier == "MEDIUM"

    def test_crypto_up_or_down_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will Bitcoin go Up or Down in the next 5 minutes?",
            category="Crypto",
        )
        assert tier == "LOW"

    def test_gambling_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Coin flip: heads or tails?",
            category="Gambling",
        )
        assert tier == "LOW"

    def test_regulatory_is_high(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will the SEC approve the Bitcoin ETF?",
            category="Crypto",
        )
        assert tier == "HIGH"

    def test_entertainment_is_medium(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Who will win Best Picture at the Oscars?",
            category="Entertainment",
        )
        assert tier == "MEDIUM"

    def test_weather_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will it snow in NYC tomorrow?",
            category="Weather",
        )
        assert tier == "LOW"

    def test_is_susceptible_helper(self) -> None:
        from research.strategies.s2_insider_copy import is_susceptible

        assert is_susceptible("HIGH") is True
        assert is_susceptible("MEDIUM") is True
        assert is_susceptible("LOW") is False


class TestBayesianHitRate:
    """Test Bayesian-shrunk hit rate computation."""

    def test_uniform_prior_mean(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # No evidence → posterior mean equals prior mean
        hr = bayesian_hit_rate(wins=0, total=0, prior_alpha=3.81, prior_beta=6.19)
        assert abs(hr - 0.381) < 0.001

    def test_strong_evidence_overrides_prior(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # 30 wins out of 35 → posterior near 0.857, barely shrunk
        hr = bayesian_hit_rate(wins=30, total=35, prior_alpha=3.81, prior_beta=6.19)
        assert hr > 0.70  # well above prior
        assert hr < 0.90  # slightly shrunk from 30/35 = 0.857

    def test_weak_evidence_shrunk_to_prior(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # 3 wins out of 3 → still heavily shrunk toward 0.381
        hr = bayesian_hit_rate(wins=3, total=3, prior_alpha=3.81, prior_beta=6.19)
        assert hr < 0.70  # shrunk from 1.0 toward 0.381
        assert hr > 0.381  # but above prior since 3/3

    def test_effective_hr_picks_best_direction(self) -> None:
        from research.strategies.s2_insider_copy import compute_effective_hr

        # Trader with 8 YES wins / 10 YES total, 2 NO wins / 5 NO total
        hr, direction = compute_effective_hr(
            yes_wins=8, yes_total=10,
            no_wins=2, no_total=5,
        )
        assert direction == "YES"
        assert hr > 0.55  # YES posterior (8/10 shrunk toward 0.381) beats NO

    def test_effective_hr_no_direction(self) -> None:
        from research.strategies.s2_insider_copy import compute_effective_hr

        # Trader better at NO
        hr, direction = compute_effective_hr(
            yes_wins=1, yes_total=5,
            no_wins=9, no_total=10,
        )
        assert direction == "NO"
