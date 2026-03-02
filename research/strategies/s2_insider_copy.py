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
from typing import TYPE_CHECKING, Any, Literal

import polars as pl

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend, StrategyContext

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


# --------------------------------------------------------------------------- #
# InsiderProvider: tracks insider trades per market (FeatureProvider protocol)
# --------------------------------------------------------------------------- #

InsiderPool = dict[str, dict[str, Any]]  # trader -> {"score": float, "direction": "YES"|"NO"}


class InsiderProvider:
    """Tracks insider BUY trades and builds per-market consensus signals.

    Implements FeatureProvider protocol for use with ReplayRunner.
    """

    name: str = "insider_provider"

    def __init__(self, insider_pool: InsiderPool) -> None:
        self._pool = {k.lower(): v for k, v in insider_pool.items()}
        # condition_id -> {direction, insiders: set, consensus_count, first_signal_time}
        self._signals: dict[str, dict[str, Any]] = {}

    async def compute(self, backend: FeatureBackend) -> None:
        """No batch compute needed — pool is pre-loaded."""

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """Track BUY trades from insider pool members."""
        # CRITICAL: SELL is exit, not directional signal
        if trade.side != "BUY":
            return
        maker = (trade.maker or "").lower()
        if maker not in self._pool:
            return

        cid = trade.condition_id
        insider_info = self._pool[maker]
        direction = insider_info["direction"]

        if cid not in self._signals:
            self._signals[cid] = {
                "direction": direction,
                "insiders": set(),
                "consensus_count": 0,
                "first_signal_time": trade.published_at,
                "max_score": insider_info["score"],
            }

        signal = self._signals[cid]
        if maker not in signal["insiders"]:
            signal["insiders"].add(maker)
            signal["consensus_count"] = len(signal["insiders"])
            signal["max_score"] = max(signal["max_score"], insider_info["score"])

    async def refresh(self, backend: FeatureBackend) -> None:
        """No periodic refresh needed for offline backtesting."""

    def get_features(self) -> dict[str, Any]:
        return {"insider_signals": self._signals}


# --------------------------------------------------------------------------- #
# InsiderCopyStrategy (Strategy protocol)
# --------------------------------------------------------------------------- #

class InsiderCopyStrategy:
    """Copy trades from identified insiders.

    Entry: When insider pool member(s) BUY into a susceptible market
    (single or consensus mode). Exit: Hold to resolution + stop-loss.

    Params (from TOML config.params):
        insider_pool: dict[trader -> {score, direction}]
        min_consensus: int (1 = single trigger, 2+ = consensus)
        size_usd: float (flat position size)
        stop_loss_pct: float (exit if price drops this fraction from entry)
    """

    name: str = "s2_insider_copy"

    def __init__(
        self,
        insider_pool: InsiderPool,
        min_consensus: int = 1,
        size_usd: float = 50.0,
        stop_loss_pct: float = 0.50,
    ) -> None:
        self._pool = {k.lower(): v for k, v in insider_pool.items()}
        self._min_consensus = min_consensus
        self._size_usd = size_usd
        self._stop_loss_pct = stop_loss_pct
        # Track our entries for stop-loss: condition_id -> {entry_price, outcome}
        self._entries: dict[str, dict[str, Any]] = {}
        # Track insider signals inline (no separate provider needed for backtest)
        self._signals: dict[str, dict[str, Any]] = {}

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id
        price = float(trade.price)

        # --- Stop-loss check on existing positions ---
        if cid in self._entries:
            pos = await ctx.get_position(cid)
            if pos and (pos.qty_yes > 0 or pos.qty_no > 0):
                entry = self._entries[cid]
                entry_price = entry["entry_price"]
                outcome = entry["outcome"]

                # Get current market price for our outcome
                current_price = await ctx.get_price(cid, outcome)
                if current_price is None:
                    current_price = price  # fallback to trade price

                if current_price < entry_price * (1 - self._stop_loss_pct):
                    qty = pos.qty_yes if outcome == "YES" else pos.qty_no
                    del self._entries[cid]
                    return [
                        TradeIntent(
                            strategy=self.name,
                            condition_id=cid,
                            side="SELL",
                            outcome=outcome,
                            size_usd=qty * current_price,
                            urgency="immediate",
                            max_price=current_price,
                            reason=f"stop-loss: {current_price:.3f} < "
                                   f"{entry_price:.3f} * {1 - self._stop_loss_pct:.2f}",
                            signal_time=trade.published_at,
                            asset_id=trade.asset_id,
                        ),
                    ]
            return None

        # --- Entry logic: only process BUY trades from insiders ---
        if trade.side != "BUY":
            return None

        maker = (trade.maker or "").lower()
        if maker not in self._pool:
            return None

        # Update signals (inline, same logic as InsiderProvider)
        insider_info = self._pool[maker]
        direction = insider_info["direction"]

        if cid not in self._signals:
            self._signals[cid] = {
                "direction": direction,
                "insiders": set(),
                "consensus_count": 0,
            }
        signal = self._signals[cid]
        signal["insiders"].add(maker)
        signal["consensus_count"] = len(signal["insiders"])

        # Also check features from provider (if running with ReplayRunner)
        provider_signals = {}
        try:
            feats = await ctx.get_features("insider_provider")
            if isinstance(feats, dict) and "insider_signals" in feats:
                provider_signals = feats["insider_signals"]
        except Exception:
            pass

        # Merge: use provider signals if available, else inline
        effective_signal = provider_signals.get(cid, signal)
        consensus = effective_signal.get("consensus_count", 0)

        # Check consensus threshold
        if consensus < self._min_consensus:
            return None

        # Check: no existing position
        pos = await ctx.get_position(cid)
        if pos and (pos.qty_yes > 0 or pos.qty_no > 0):
            return None

        # Determine outcome from insider direction
        outcome = effective_signal.get("direction", direction)

        # Record entry for stop-loss tracking
        self._entries[cid] = {"entry_price": price, "outcome": outcome}

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome=outcome,
                size_usd=self._size_usd,
                urgency="patient",
                max_price=price + 0.02,
                reason=f"insider copy: {consensus} insiders, direction={outcome}",
                signal_time=trade.published_at,
                asset_id=trade.asset_id,
            ),
        ]

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None
