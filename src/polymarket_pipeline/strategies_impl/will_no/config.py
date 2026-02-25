"""Configuration for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WillNoConfig:
    """Immutable configuration for the will-no strategy.

    Parameters
    ----------
    yes_price_min
        Lower bound of the YES price band (inclusive).
    yes_price_max
        Upper bound of the YES price band (inclusive).
    base_bet_usd
        Fixed bet size in USD.
    fee_pct
        Expected fee as a fraction (e.g. 0.02 = 2%).
    prefer_keywords
        Keywords that indicate fast-resolving markets (boost priority).
    avoid_keywords
        Keywords that indicate negative edge or long lockup (skip).
    max_volume_usd
        Maximum market volume — thin markets have higher edge.
        0 = no filter.
    question_pattern
        Regex pattern the question must match (case-insensitive).
        Default matches questions starting with "Will".
    """

    yes_price_min: float = 0.15
    yes_price_max: float = 0.40
    base_bet_usd: float = 50.0
    fee_pct: float = 0.0
    prefer_keywords: frozenset[str] = field(default_factory=frozenset)
    avoid_keywords: frozenset[str] = field(default_factory=frozenset)
    max_volume_usd: float = 0.0
    question_pattern: str = r"^Will\b"
    max_bucket: str | None = None

    def __init__(
        self,
        yes_price_min: float = 0.15,
        yes_price_max: float = 0.40,
        base_bet_usd: float = 50.0,
        fee_pct: float = 0.0,
        prefer_keywords: set[str] | frozenset[str] | list[str] | None = None,
        avoid_keywords: set[str] | frozenset[str] | list[str] | None = None,
        max_volume_usd: float = 0.0,
        question_pattern: str = r"^Will\b",
        max_bucket: str | None = None,
    ) -> None:
        object.__setattr__(self, "yes_price_min", yes_price_min)
        object.__setattr__(self, "yes_price_max", yes_price_max)
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(
            self,
            "prefer_keywords",
            frozenset(prefer_keywords) if prefer_keywords is not None else frozenset(),
        )
        object.__setattr__(
            self,
            "avoid_keywords",
            frozenset(avoid_keywords)
            if avoid_keywords is not None
            else frozenset({"reach", "hit"}),
        )
        object.__setattr__(self, "max_volume_usd", max_volume_usd)
        object.__setattr__(self, "question_pattern", question_pattern)
        object.__setattr__(self, "max_bucket", max_bucket)
