"""S2 Insider Copy Strategy — identify and copy high-conviction insiders.

Hypothesis: Some traders bet infrequently, large, on insider-susceptible markets,
and achieve abnormally high hit rates. Copy their directional BUY trades.

Usage:
    from research.strategies.s2_insider_copy import InsiderCopyStrategy
    from research.harness import run_backtest

    result, summary = await run_backtest(
        InsiderCopyStrategy(insider_pool=pool, min_consensus=1),
        trades,
        config,
    )
"""

from __future__ import annotations

import re
from typing import Literal

# --------------------------------------------------------------------------- #
# Stage 1: Market susceptibility classification
# --------------------------------------------------------------------------- #

Susceptibility = Literal["HIGH", "MEDIUM", "LOW"]

# Category-based classification (from Gamma API metadata)
_HIGH_CATEGORIES = frozenset({
    "politics", "political", "regulatory", "legal", "law",
    "geopolitical", "government", "corporate", "company",
})
_MEDIUM_CATEGORIES = frozenset({
    "sports", "entertainment", "awards", "esports",
    "nfl", "nba", "mlb", "nhl", "soccer", "mma",
})
_LOW_CATEGORIES = frozenset({
    "gambling", "weather",
})

# Question-based overrides (take precedence over category)
_LOW_PATTERNS = [
    re.compile(r"up or down", re.IGNORECASE),
    re.compile(r"coin flip", re.IGNORECASE),
    re.compile(r"next \d+ minute", re.IGNORECASE),
    re.compile(r"5-min|15-min|5 min|15 min", re.IGNORECASE),
]
_HIGH_PATTERNS = [
    re.compile(r"SEC |FDA |EPA |FTC ", re.IGNORECASE),
    re.compile(r"regulat|approv|sanction|indict|verdict|ruling", re.IGNORECASE),
    re.compile(r"election|inaugurati|impeach|president|congress", re.IGNORECASE),
    re.compile(r"will .+ announce", re.IGNORECASE),
]


def classify_market_susceptibility(
    question: str,
    category: str | None,
) -> Susceptibility:
    """Classify a market's susceptibility to insider trading.

    Two-stage: question patterns override category-based classification.
    LOW patterns checked first (gambling/noise), then HIGH patterns (insider-prone).
    """
    # Question pattern overrides
    for pat in _LOW_PATTERNS:
        if pat.search(question):
            return "LOW"
    for pat in _HIGH_PATTERNS:
        if pat.search(question):
            return "HIGH"

    # Category-based fallback
    if category:
        cat_lower = category.lower().strip()
        if cat_lower in _LOW_CATEGORIES:
            return "LOW"
        if cat_lower in _HIGH_CATEGORIES:
            return "HIGH"
        if cat_lower in _MEDIUM_CATEGORIES:
            return "MEDIUM"

    # Default: MEDIUM (unknown categories get benefit of the doubt)
    return "MEDIUM"


def is_susceptible(tier: Susceptibility) -> bool:
    """Return True if tier is HIGH or MEDIUM (eligible for insider analysis)."""
    return tier != "LOW"
