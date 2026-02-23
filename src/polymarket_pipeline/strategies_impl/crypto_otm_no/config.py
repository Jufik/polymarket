"""Configuration for the crypto-otm-no strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CryptoOTMNoConfig:
    """Immutable configuration for the crypto-otm-no strategy.

    Parameters
    ----------
    yes_price_min
        Lower bound of the YES price band (inclusive).
    yes_price_max
        Upper bound of the YES price band (inclusive).
    assets
        Set of supported crypto tickers (e.g. ``{"BTC", "ETH"}``).
    base_bet_usd
        Fixed bet size in USD.
    fee_pct
        Expected fee as a fraction (e.g. 0.02 = 2%).
    min_volume_usd
        Minimum market volume in USD to qualify.
    question_pattern
        Regex pattern to match against market question text.
    """

    yes_price_min: float = 0.05
    yes_price_max: float = 0.25
    assets: frozenset[str] = field(default_factory=frozenset)
    base_bet_usd: float = 100.0
    fee_pct: float = 0.02
    min_volume_usd: float = 50.0
    question_pattern: str = r"(above|below)"

    def __init__(
        self,
        yes_price_min: float = 0.05,
        yes_price_max: float = 0.25,
        assets: set[str] | frozenset[str] | list[str] | None = None,
        base_bet_usd: float = 100.0,
        fee_pct: float = 0.02,
        min_volume_usd: float = 50.0,
        question_pattern: str = r"(above|below)",
    ) -> None:
        # frozen=True requires object.__setattr__
        object.__setattr__(self, "yes_price_min", yes_price_min)
        object.__setattr__(self, "yes_price_max", yes_price_max)
        object.__setattr__(
            self,
            "assets",
            frozenset(assets) if assets is not None else frozenset(),
        )
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(self, "min_volume_usd", min_volume_usd)
        object.__setattr__(self, "question_pattern", question_pattern)
