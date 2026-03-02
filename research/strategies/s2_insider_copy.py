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


# --------------------------------------------------------------------------- #
# Stage 2, F1: Bayesian hit rate
# --------------------------------------------------------------------------- #

# Direction-aware priors (from population base rates)
YES_PRIOR_ALPHA = 3.81   # 38.1% YES base rate
YES_PRIOR_BETA = 6.19
NO_PRIOR_ALPHA = 6.19    # 61.9% NO base rate
NO_PRIOR_BETA = 3.81


def bayesian_hit_rate(
    wins: int,
    total: int,
    prior_alpha: float = YES_PRIOR_ALPHA,
    prior_beta: float = YES_PRIOR_BETA,
) -> float:
    """Beta-Binomial posterior mean: (alpha + wins) / (alpha + beta + total)."""
    return (prior_alpha + wins) / (prior_alpha + prior_beta + total)


def compute_effective_hr(
    yes_wins: int,
    yes_total: int,
    no_wins: int,
    no_total: int,
) -> tuple[float, Literal["YES", "NO"]]:
    """Return (best_hr, best_direction) using direction-aware priors."""
    yes_hr = bayesian_hit_rate(yes_wins, yes_total, YES_PRIOR_ALPHA, YES_PRIOR_BETA)
    no_hr = bayesian_hit_rate(no_wins, no_total, NO_PRIOR_ALPHA, NO_PRIOR_BETA)
    if yes_hr >= no_hr:
        return yes_hr, "YES"
    return no_hr, "NO"


# --------------------------------------------------------------------------- #
# Stage 2: Composite insider scoring
# --------------------------------------------------------------------------- #

import polars as pl


def _percentile_rank(series: pl.Series) -> pl.Series:
    """Rank values as percentile (0-1). Higher = better."""
    return series.rank() / series.len()


def _z_score(series: pl.Series) -> pl.Series:
    """Standard z-score normalization."""
    mean = series.mean()
    std = series.std()
    if std is None or std == 0:
        return pl.Series([0.0] * series.len())
    return (series - mean) / std


def compute_insider_scores(
    trader_stats: pl.DataFrame,
    *,
    weights: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Compute 6-feature composite insider score for each trader.

    Input DataFrame must have columns:
        trader, yes_wins, yes_total, no_wins, no_total,
        avg_position_usd, markets_per_month, avg_timing_edge, high_market_ratio

    Returns DataFrame with all input columns + f1..f6 features + insider_score.
    """
    w = weights or {
        "f1": 1 / 6, "f2": 1 / 6, "f3": 1 / 6,
        "f4": 1 / 6, "f5": 1 / 6, "f6": 1 / 6,
    }

    # F1: Bayesian hit rate excess (over direction base rate)
    f1_values = []
    f1_directions = []
    for row in trader_stats.iter_rows(named=True):
        hr, direction = compute_effective_hr(
            row["yes_wins"], row["yes_total"],
            row["no_wins"], row["no_total"],
        )
        base = 0.381 if direction == "YES" else 0.619
        f1_values.append(hr - base)
        f1_directions.append(direction)

    result = trader_stats.with_columns(
        pl.Series("f1_bayesian_hr", f1_values),
        pl.Series("best_direction", f1_directions),
    )

    # F2: Conviction (percentile rank of avg bet size)
    result = result.with_columns(
        _percentile_rank(result["avg_position_usd"]).alias("f2_conviction"),
    )

    # F3: Selectivity (inverse of markets_per_month, percentile ranked)
    selectivity_raw = 1.0 / result["markets_per_month"].clip(lower_bound=0.01)
    result = result.with_columns(
        _percentile_rank(selectivity_raw).alias("f3_selectivity"),
    )

    # F4: Anomaly score (Mahalanobis-like: z-score distance in feature space)
    z_markets = _z_score(result["markets_per_month"]) * -1  # fewer = more insider
    z_bet = _z_score(result["avg_position_usd"])  # larger = more insider
    z_hr = _z_score(result["f1_bayesian_hr"])  # higher = more insider
    anomaly_raw = (z_markets + z_bet + z_hr) / 3.0
    result = result.with_columns(
        _percentile_rank(anomaly_raw).alias("f4_anomaly"),
    )

    # F5: Timing edge (percentile rank)
    result = result.with_columns(
        _percentile_rank(result["avg_timing_edge"]).alias("f5_timing"),
    )

    # F6: Susceptibility concentration (already 0-1, use directly)
    result = result.with_columns(
        result["high_market_ratio"].alias("f6_susceptibility"),
    )

    # Composite score (weighted sum of percentile-ranked features)
    result = result.with_columns(
        (
            w["f1"] * _percentile_rank(result["f1_bayesian_hr"])
            + w["f2"] * result["f2_conviction"]
            + w["f3"] * result["f3_selectivity"]
            + w["f4"] * result["f4_anomaly"]
            + w["f5"] * result["f5_timing"]
            + w["f6"] * result["f6_susceptibility"]
        ).alias("insider_score"),
    )

    return result
