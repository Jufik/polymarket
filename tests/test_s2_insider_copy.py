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
