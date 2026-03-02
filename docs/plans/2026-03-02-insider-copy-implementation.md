# Insider Copy Strategy (s2) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a multi-signal insider detection and copy strategy that identifies traders with apparent insider knowledge (high hit rate, large infrequent bets on susceptible markets) and mirrors their trades.

**Architecture:** Two-stage pipeline: (1) market susceptibility classifier filters which markets are analyzed, (2) 6-feature Bayesian scoring model ranks traders. Strategy copies insider BUY trades with configurable consensus threshold and stop-loss.

**Tech Stack:** Python 3.11+, Polars (offline), ClickHouse SQL (data exploration), research harness (ReplayRunner), marimo (notebooks)

---

### Task 1: Market Susceptibility Classifier

**Files:**
- Create: `research/strategies/s2_insider_copy.py`
- Create: `tests/test_s2_insider_copy.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestMarketSusceptibility -x -q`
Expected: FAIL — module not found

**Step 3: Write minimal implementation**

```python
# research/strategies/s2_insider_copy.py
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestMarketSusceptibility -x -q`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "feat(s2): market susceptibility classifier

Two-stage classification: question patterns override category-based
tier assignment. HIGH/MEDIUM markets are insider-susceptible."
```

---

### Task 2: Bayesian Hit Rate Scorer

**Files:**
- Modify: `research/strategies/s2_insider_copy.py`
- Modify: `tests/test_s2_insider_copy.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_s2_insider_copy.py

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
        assert hr > 0.60  # YES posterior (8/10 shrunk toward 0.381) beats NO

    def test_effective_hr_no_direction(self) -> None:
        from research.strategies.s2_insider_copy import compute_effective_hr

        # Trader better at NO
        hr, direction = compute_effective_hr(
            yes_wins=1, yes_total=5,
            no_wins=9, no_total=10,
        )
        assert direction == "NO"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestBayesianHitRate -x -q`
Expected: FAIL — functions not defined

**Step 3: Write minimal implementation**

```python
# Append to research/strategies/s2_insider_copy.py

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
    losses = total - wins
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestBayesianHitRate -x -q`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "feat(s2): Bayesian hit rate scorer with direction-aware priors

Beta-Binomial posterior mean with YES prior=0.381, NO prior=0.619.
compute_effective_hr picks best direction per trader."
```

---

### Task 3: Insider Scorer (composite 6-feature score)

**Files:**
- Modify: `research/strategies/s2_insider_copy.py`
- Modify: `tests/test_s2_insider_copy.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_s2_insider_copy.py
import polars as pl


class TestInsiderScorer:
    """Test composite insider scoring from trader stats DataFrame."""

    def _make_trader_stats(self) -> pl.DataFrame:
        """Create sample trader stats for testing."""
        return pl.DataFrame({
            "trader": ["alice", "bob", "charlie", "dave"],
            # F1 inputs: directional positions
            "yes_wins": [8, 2, 15, 5],
            "yes_total": [10, 5, 20, 10],
            "no_wins": [2, 7, 3, 1],
            "no_total": [3, 10, 5, 2],
            # F2: avg bet size (USD)
            "avg_position_usd": [5000.0, 200.0, 1000.0, 50.0],
            # F3: markets per month
            "markets_per_month": [1.5, 20.0, 5.0, 2.0],
            # F5: timing edge (avg price delta after entry)
            "avg_timing_edge": [0.15, 0.02, 0.08, -0.05],
            # F6: high susceptibility ratio
            "high_market_ratio": [0.8, 0.3, 0.6, 0.1],
        })

    def test_score_returns_all_traders(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        assert len(result) == 4
        assert "insider_score" in result.columns

    def test_alice_scores_highest(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        scores = dict(zip(result["trader"].to_list(), result["insider_score"].to_list()))
        # Alice: high HR, big bets, selective, good timing, high susceptibility
        assert scores["alice"] > scores["bob"]
        assert scores["alice"] > scores["dave"]

    def test_score_has_feature_columns(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        for col in ["f1_bayesian_hr", "f2_conviction", "f3_selectivity",
                     "f4_anomaly", "f5_timing", "f6_susceptibility"]:
            assert col in result.columns, f"Missing column: {col}"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderScorer -x -q`
Expected: FAIL — compute_insider_scores not defined

**Step 3: Write minimal implementation**

```python
# Append to research/strategies/s2_insider_copy.py
import math

import numpy as np
import polars as pl

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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderScorer -x -q`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "feat(s2): composite 6-feature insider scorer

F1=Bayesian HR excess, F2=conviction, F3=selectivity, F4=anomaly,
F5=timing edge, F6=susceptibility concentration. Weighted sum with
configurable weights (default equal 1/6)."
```

---

### Task 4: InsiderProvider (FeatureProvider protocol)

**Files:**
- Modify: `research/strategies/s2_insider_copy.py`
- Modify: `tests/test_s2_insider_copy.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_s2_insider_copy.py
from decimal import Decimal
from datetime import datetime, timezone

from polymarket_pipeline.models import NormalizedTrade


def _make_trade(
    maker: str = "alice",
    condition_id: str = "cid_1",
    asset_id: str = "asset_yes_1",
    side: str = "BUY",
    price: float = 0.65,
    amount_usd: float = 100.0,
    ts: float = 1700000000.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{maker}:{condition_id}:{ts}",
        condition_id=condition_id,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal(str(round(amount_usd / price, 4))),
        amount_usd=Decimal(str(amount_usd)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="taker_1",
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        source="goldsky_subgraph",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


class TestInsiderProvider:
    """Test the InsiderProvider feature provider."""

    def test_provider_tracks_insider_trades(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" in features["insider_signals"]
        signal = features["insider_signals"]["cid_1"]
        assert "alice" in signal["insiders"]
        assert signal["direction"] == "YES"

    def test_provider_ignores_non_insiders(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="bob", condition_id="cid_1", side="BUY")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" not in features["insider_signals"]

    def test_provider_ignores_sell_trades(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="alice", condition_id="cid_1", side="SELL")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" not in features["insider_signals"]

    def test_provider_consensus_count(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {
            "alice": {"score": 0.9, "direction": "YES"},
            "bob": {"score": 0.8, "direction": "YES"},
        }
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        asyncio.run(provider.on_trade(
            _make_trade(maker="alice", condition_id="cid_1", side="BUY")
        ))
        asyncio.run(provider.on_trade(
            _make_trade(maker="bob", condition_id="cid_1", side="BUY")
        ))

        features = provider.get_features()
        signal = features["insider_signals"]["cid_1"]
        assert signal["consensus_count"] == 2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderProvider -x -q`
Expected: FAIL — InsiderProvider not defined

**Step 3: Write minimal implementation**

```python
# Append to research/strategies/s2_insider_copy.py
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderProvider -x -q`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "feat(s2): InsiderProvider tracks insider BUY trades

Implements FeatureProvider protocol. Tracks per-market consensus:
unique insider count, direction, max score. Filters SELL trades
(exit, not signal)."
```

---

### Task 5: InsiderCopyStrategy (Strategy protocol)

**Files:**
- Modify: `research/strategies/s2_insider_copy.py`
- Modify: `tests/test_s2_insider_copy.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_s2_insider_copy.py
from unittest.mock import AsyncMock


class TestInsiderCopyStrategy:
    """Test the InsiderCopyStrategy event-driven strategy."""

    @pytest.fixture
    def ctx(self) -> AsyncMock:
        """Mock StrategyContext."""
        ctx = AsyncMock()
        ctx.get_position.return_value = None  # no existing position
        ctx.get_features.return_value = {
            "insider_signals": {
                "cid_1": {
                    "direction": "YES",
                    "insiders": {"alice"},
                    "consensus_count": 1,
                    "first_signal_time": 1700000000.0,
                    "max_score": 0.9,
                },
            },
        }
        ctx.now.return_value = 1700000100.0
        return ctx

    @pytest.mark.asyncio
    async def test_emits_intent_on_insider_trade(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        intents = await strategy.on_trade(trade, ctx)
        assert intents is not None
        assert len(intents) == 1
        assert intents[0].condition_id == "cid_1"
        assert intents[0].side == "BUY"
        assert intents[0].outcome == "YES"
        assert intents[0].size_usd == 50.0

    @pytest.mark.asyncio
    async def test_skips_if_already_positioned(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy
        from polymarket_pipeline.strategies.types import Position

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy", qty_yes=10.0,
        )
        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_requires_consensus_threshold(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=2, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        # Only 1 insider → below consensus threshold of 2
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_stop_loss_emits_sell(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy
        from polymarket_pipeline.strategies.types import Position

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.65,
        )
        ctx.get_features.return_value = {"insider_signals": {}}
        ctx.get_price.return_value = 0.30  # 54% drop from 0.65 entry → triggers stop

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        # Track that we entered cid_1
        strategy._entries["cid_1"] = {"entry_price": 0.65, "outcome": "YES"}

        trade = _make_trade(maker="random", condition_id="cid_1", side="BUY", price=0.30)
        intents = await strategy.on_trade(trade, ctx)

        assert intents is not None
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].outcome == "YES"

    @pytest.mark.asyncio
    async def test_no_stop_loss_within_threshold(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy
        from polymarket_pipeline.strategies.types import Position

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.65,
        )
        ctx.get_features.return_value = {"insider_signals": {}}
        ctx.get_price.return_value = 0.45  # 31% drop from 0.65 → within 50% stop

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        strategy._entries["cid_1"] = {"entry_price": 0.65, "outcome": "YES"}

        trade = _make_trade(maker="random", condition_id="cid_1", side="BUY", price=0.45)
        intents = await strategy.on_trade(trade, ctx)

        assert intents is None

    @pytest.mark.asyncio
    async def test_ignores_sell_trades(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="SELL")

        intents = await strategy.on_trade(trade, ctx)
        assert intents is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderCopyStrategy -x -q`
Expected: FAIL — InsiderCopyStrategy not defined

**Step 3: Write minimal implementation**

```python
# Append to research/strategies/s2_insider_copy.py
from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.protocol import StrategyContext

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
                            reason=f"stop-loss: {current_price:.3f} < {entry_price:.3f} * {1 - self._stop_loss_pct:.2f}",
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderCopyStrategy -x -q`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "feat(s2): InsiderCopyStrategy with consensus + stop-loss

Implements Strategy protocol. Entry: copy insider BUY with configurable
consensus threshold. Exit: hold to resolution + price-based stop-loss.
Tracks signals inline and from InsiderProvider."
```

---

### Task 6: TOML Config + Run All Tests

**Files:**
- Create: `configs/s2_insider_copy.toml`
- Run: all tests

**Step 1: Create the TOML config**

```toml
# configs/s2_insider_copy.toml
# Insider Copy Strategy — research configuration

[strategy.s2_insider_copy]
enabled = true
mode = "replay"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 20
cooldown_s = 0
features = ["insider_provider"]

[strategy.s2_insider_copy.params]
min_consensus = 1
size_usd = 50
stop_loss_pct = 0.50

[provider.insider_provider]
enabled = true
refresh_interval_s = 0

[provider.insider_provider.params]
min_positions = 3
min_bayesian_hr = 0.55
lookback_months = 6

[promotion]
min_trades = 50
min_sharpe = 0.3
min_fills = 30
max_drawdown = 0.25
```

**Step 2: Run all tests**

Run: `uv run pytest tests/test_s2_insider_copy.py -x -q`
Expected: PASS (all 23 tests)

**Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: PASS

**Step 4: Lint**

Run: `uv run ruff check research/strategies/s2_insider_copy.py tests/test_s2_insider_copy.py`
Expected: clean (fix any issues)

**Step 5: Commit**

```bash
git add configs/s2_insider_copy.toml
git commit --no-gpg-sign -m "config(s2): TOML config for insider copy strategy

Replay mode, $1000 capital, 20 max positions, $50 flat sizing.
Stop-loss at 50%. Insider pool: min 3 positions, 55% Bayesian HR."
```

---

### Task 7: ClickHouse SQL for Insider Pool Discovery

**Files:**
- Create: `research/knowledge/queries/insider_pool.sql`

**Step 1: Write the SQL query**

This query computes the full 6-feature insider score. It will be used in the marimo exploration notebook and can be adapted for the FeatureProvider's `compute()` method.

```sql
-- research/knowledge/queries/insider_pool.sql
-- Compute insider scores for all traders on susceptible markets.
-- Parameters: {lookback_months}, {min_positions}
--
-- Stage 1: Classify markets by susceptibility
-- Stage 2: Compute 6-feature score per trader

WITH susceptible_markets AS (
    SELECT
        m.condition_id,
        multiIf(
            m.question LIKE '%Up or Down%'
                OR m.question LIKE '%up or down%'
                OR m.question LIKE '%coin flip%'
                OR m.question LIKE '%5-min%'
                OR m.question LIKE '%15-min%'
                OR m.question LIKE '%next 5 min%'
                OR m.question LIKE '%next 15 min%',
            'LOW',
            m.question LIKE '%SEC %'
                OR m.question LIKE '%FDA %'
                OR m.question LIKE '%regulat%'
                OR m.question LIKE '%approv%'
                OR m.question LIKE '%election%'
                OR m.question LIKE '%president%'
                OR m.question LIKE '%indict%'
                OR m.question LIKE '%verdict%',
            'HIGH',
            m.category IN ('Politics', 'Government', 'Legal', 'Regulatory'),
            'HIGH',
            m.category IN ('Sports', 'Entertainment', 'Esports'),
            'MEDIUM',
            'MEDIUM'
        ) AS susceptibility
    FROM markets AS m
),
-- Filter to only susceptible resolved markets
resolved_susceptible AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.volume AS market_volume,
        p.trade_count,
        p.avg_yes_price,
        p.resolved_at,
        p.yes_won,
        sm.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback_months:UInt32} MONTH
),
-- Per-trader stats
trader_stats AS (
    SELECT
        trader,
        -- F1 inputs
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        -- F2: bet conviction
        sum(market_volume) / count(*) AS avg_position_usd,
        -- F3: selectivity (markets per month)
        count(*) / greatest(
            dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
        ) AS markets_per_month,
        -- F5: timing edge (avg realized PnL as proxy)
        avg(realized_pnl) AS avg_realized_pnl,
        -- F6: susceptibility concentration
        countIf(susceptibility = 'HIGH') / count(*) AS high_market_ratio,
        -- Extra stats
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
    FROM resolved_susceptible
    GROUP BY trader
    HAVING count(*) >= {min_positions:UInt32}
),
-- F1: Bayesian hit rate
scored AS (
    SELECT
        *,
        -- YES posterior mean: (3.81 + yes_wins) / (10 + yes_total)
        (3.81 + yes_wins) / (10.0 + yes_total) AS bayesian_yes_hr,
        -- NO posterior mean: (6.19 + no_wins) / (10 + no_total)
        (6.19 + no_wins) / (10.0 + no_total) AS bayesian_no_hr,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total) >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        -- F1: excess over base rate
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM trader_stats
)
SELECT
    trader,
    total_positions,
    yes_wins, yes_total,
    no_wins, no_total,
    effective_hr,
    best_direction,
    hr_excess,
    avg_position_usd,
    markets_per_month,
    avg_realized_pnl,
    high_market_ratio,
    total_pnl,
    avg_volume
FROM scored
ORDER BY hr_excess DESC, avg_position_usd DESC
LIMIT 500
```

**Step 2: Commit**

```bash
git add research/knowledge/queries/insider_pool.sql
git commit --no-gpg-sign -m "research: insider pool discovery SQL query

6-feature scoring with market susceptibility filter. Bayesian HR
with direction-aware priors, bet conviction, selectivity, timing,
and susceptibility concentration."
```

---

### Task 8: Marimo Exploration Notebook (Phase 1-2)

**Files:**
- Create: `research/notebooks/S2_insider_exploration.py`

**Step 1: Create the marimo notebook**

This notebook runs the Phase 1 (data exploration) and Phase 2 (vectorized discovery) research. It connects to the remote ClickHouse, computes insider scores, and runs parameter sweeps.

```python
# research/notebooks/S2_insider_exploration.py
"""S2 Insider Copy — Data Exploration & Vectorized Discovery.

Marimo notebook for Phase 1-2 of the insider copy strategy research.
Run: uv run marimo edit research/notebooks/S2_insider_exploration.py
"""
import marimo

__generated_with = "0.10.0"
app = marimo.App(width="full")


@app.cell
def imports():
    import marimo as mo
    import polars as pl
    import clickhouse_connect

    return mo, pl, clickhouse_connect


@app.cell
def connect(clickhouse_connect):
    """Connect to remote ClickHouse."""
    ch = clickhouse_connect.get_client(
        host="192.168.0.148", port=18123, database="polymarket"
    )
    # Verify connection
    row_count = ch.query("SELECT count() FROM trades_raw").result_rows[0][0]
    print(f"Connected. trades_raw: {row_count:,} rows")
    return (ch,)


@app.cell
def market_classification(ch, pl, mo):
    """Stage 1: Classify markets by insider susceptibility."""
    df = pl.from_pandas(ch.query_df("""
        SELECT
            m.condition_id,
            m.question,
            m.category,
            multiIf(
                m.question LIKE '%Up or Down%' OR m.question LIKE '%up or down%'
                    OR m.question LIKE '%coin flip%'
                    OR m.question LIKE '%5-min%' OR m.question LIKE '%15-min%',
                'LOW',
                m.question LIKE '%SEC %' OR m.question LIKE '%FDA %'
                    OR m.question LIKE '%regulat%' OR m.question LIKE '%approv%'
                    OR m.question LIKE '%election%' OR m.question LIKE '%president%'
                    OR m.question LIKE '%indict%' OR m.question LIKE '%verdict%',
                'HIGH',
                m.category IN ('Politics', 'Government', 'Legal', 'Regulatory'),
                'HIGH',
                m.category IN ('Sports', 'Entertainment', 'Esports'),
                'MEDIUM',
                'MEDIUM'
            ) AS susceptibility
        FROM markets AS m
        WHERE m.resolution_value = 1
    """))

    tier_counts = df.group_by("susceptibility").len().sort("len", descending=True)
    mo.md(f"""## Market Susceptibility Distribution
    {tier_counts}

    Total resolved markets: {len(df):,}
    """)
    return (df,)


@app.cell
def insider_pool_query(ch, pl, mo):
    """Stage 2: Compute insider scores from ClickHouse."""
    # Read the SQL query from file
    from pathlib import Path
    sql = Path("research/knowledge/queries/insider_pool.sql").read_text()

    # Replace parameters
    lookback = 6
    min_pos = 3
    sql_exec = sql.replace("{lookback_months:UInt32}", str(lookback))
    sql_exec = sql_exec.replace("{min_positions:UInt32}", str(min_pos))

    pool_df = pl.from_pandas(ch.query_df(sql_exec))
    mo.md(f"""## Insider Pool (lookback={lookback}mo, min_positions={min_pos})
    Traders found: {len(pool_df):,}
    """)
    print(pool_df.head(20))
    return pool_df, lookback, min_pos


@app.cell
def score_distribution(pool_df, pl, mo):
    """Analyze insider score distribution."""
    from research.strategies.s2_insider_copy import compute_insider_scores

    # Prepare input DataFrame
    stats = pool_df.rename({
        "avg_realized_pnl": "avg_timing_edge",
    })
    scored = compute_insider_scores(stats)

    mo.md(f"""## Insider Score Distribution
    - Mean: {scored['insider_score'].mean():.4f}
    - Median: {scored['insider_score'].median():.4f}
    - P95: {scored['insider_score'].quantile(0.95):.4f}
    - P99: {scored['insider_score'].quantile(0.99):.4f}
    """)

    # Top 50 insiders
    top50 = scored.sort("insider_score", descending=True).head(50)
    print(top50.select([
        "trader", "insider_score", "effective_hr", "best_direction",
        "total_positions", "avg_position_usd", "markets_per_month",
        "high_market_ratio", "total_pnl",
    ]))
    return scored, top50


@app.cell
def sanity_check(top50, ch, pl, mo):
    """Sanity check: inspect top insiders' actual trades."""
    top_traders = top50["trader"].head(5).to_list()
    traders_sql = ", ".join(f"'{t}'" for t in top_traders)

    trades_df = pl.from_pandas(ch.query_df(f"""
        SELECT
            lower(p.trader) AS trader,
            p.condition_id,
            p.position,
            p.correct,
            p.realized_pnl,
            p.volume AS market_volume,
            m.question
        FROM (SELECT * FROM trader_positions_resolved) AS p
        INNER JOIN markets AS m ON p.condition_id = m.condition_id
        WHERE lower(p.trader) IN ({traders_sql})
          AND p.position IN ('YES', 'NO')
        ORDER BY p.trader, p.resolved_at
    """))

    for trader in top_traders:
        subset = trades_df.filter(pl.col("trader") == trader)
        mo.md(f"### Trader: `{trader[:10]}...`")
        print(subset.select(["question", "position", "correct", "realized_pnl", "market_volume"]))
    return (trades_df,)


@app.cell
def parameter_sweep_placeholder(scored, mo):
    """Phase 2: Parameter sweep (vectorized upper bound).

    TODO: After manual review of insider pool, implement:
    1. Build insider pool at various score thresholds
    2. Replay trades and count insider copy opportunities
    3. Compute hit rate, edge, trade frequency at each threshold
    4. Compare single vs consensus triggers
    """
    mo.md("""## Phase 2: Vectorized Parameter Sweep
    _Run this cell after reviewing the insider pool above._
    """)
    return ()


if __name__ == "__main__":
    app.run()
```

**Step 2: Commit**

```bash
git add research/notebooks/S2_insider_exploration.py
git commit --no-gpg-sign -m "research: marimo notebook for insider copy exploration

Phase 1: market classification, insider pool computation, sanity check.
Phase 2 placeholder: parameter sweep for score threshold + consensus."
```

---

### Task 9: Update Research Ideas Backlog

**Files:**
- Modify: `research/ideas.md`

**Step 1: Add the insider copy idea**

Add to `research/ideas.md`:

```markdown
### S2: Insider Copy (HIGH priority)

**Hypothesis**: Some traders exhibit "insider knowledge" — infrequent, high-conviction,
high-accuracy bets on susceptible markets. Copy their BUY trades.

**Status**: Design approved, implementation in progress.
**Design doc**: `docs/plans/2026-03-02-insider-copy-strategy-design.md`

**Key features**:
- Two-stage market susceptibility filter (HIGH/MEDIUM/LOW)
- 6-feature Bayesian scoring: HR excess, conviction, selectivity, anomaly, timing, susceptibility
- Configurable: single insider vs consensus trigger
- Stop-loss protection (default 50%)
- Hold to resolution

**Open questions**:
- What's the actual insider pool size at various thresholds?
- Single trigger vs consensus: which has better risk-adjusted returns?
- Optimal stop-loss level vs hold-to-resolution?
- Category-specific insider detection (politics vs sports)?
```

**Step 2: Commit**

```bash
git add research/ideas.md
git commit --no-gpg-sign -m "research: add S2 insider copy to ideas backlog"
```

---

### Task 10: Integration Smoke Test with Research Harness

**Files:**
- Modify: `tests/test_s2_insider_copy.py`

**Step 1: Write integration test**

```python
# Append to tests/test_s2_insider_copy.py

class TestInsiderCopyIntegration:
    """Integration test: run strategy through BacktestRunner."""

    @pytest.mark.asyncio
    async def test_backtest_runs_without_error(self) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy
        from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
        from polymarket_pipeline.strategies.context.memory import InMemoryContext
        from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
        from polymarket_pipeline.strategies.execution.realistic import (
            RealisticFillSimulator,
            FillModelConfig,
        )
        from polymarket_pipeline.strategies.types import ExecutionMode
        from polymarket_pipeline.strategies.config import StrategyConfig

        # Minimal insider pool: one "insider" who happens to be the maker
        pool = {"maker_1": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=10.0, stop_loss_pct=0.50,
        )

        config = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000,
            max_position_usd=100,
            max_open_positions=10,
            cooldown_s=0,
        )

        ctx = InMemoryContext()
        executor = RealisticFillSimulator(config=FillModelConfig())
        gateway = ExecutionGateway(executor)

        runner = BacktestRunner(
            strategy=strategy,
            ctx=ctx,
            gateway=gateway,
            config=config,
        )

        # Create trades where maker_1 is the insider
        trades = [
            _make_trade(maker="maker_1", condition_id="cid_A", side="BUY",
                        price=0.60, amount_usd=500.0, ts=1000.0),
            _make_trade(maker="random", condition_id="cid_A", side="BUY",
                        price=0.62, amount_usd=100.0, ts=1100.0),
            _make_trade(maker="maker_1", condition_id="cid_B", side="BUY",
                        price=0.40, amount_usd=300.0, ts=1200.0),
        ]

        result = await runner.run(trades)

        # Should have attempted to fill at least the first insider trade
        assert result.total_trades == 3
        assert result.total_intents >= 1
        assert result.total_fills >= 1
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_s2_insider_copy.py::TestInsiderCopyIntegration -x -q`
Expected: PASS

**Step 3: Run full test suite**

Run: `uv run pytest tests/test_s2_insider_copy.py -x -v`
Expected: all ~24 tests PASS

**Step 4: Final commit**

```bash
git add tests/test_s2_insider_copy.py
git commit --no-gpg-sign -m "test(s2): integration smoke test with BacktestRunner

Verifies InsiderCopyStrategy runs through the full pipeline:
BacktestRunner → ExecutionGateway → RealisticFillSimulator."
```

---

## Summary

| Task | Component | Tests | Status |
|------|-----------|-------|--------|
| 1 | Market susceptibility classifier | 8 | Pending |
| 2 | Bayesian hit rate scorer | 5 | Pending |
| 3 | Composite insider scorer (6 features) | 3 | Pending |
| 4 | InsiderProvider (FeatureProvider) | 4 | Pending |
| 5 | InsiderCopyStrategy (Strategy protocol) | 6 | Pending |
| 6 | TOML config + full test run | 0 (run existing) | Pending |
| 7 | Insider pool SQL query | 0 (SQL) | Pending |
| 8 | Marimo exploration notebook | 0 (notebook) | Pending |
| 9 | Update research ideas backlog | 0 (docs) | Pending |
| 10 | Integration smoke test | 1 | Pending |

**Total: 10 tasks, ~27 tests, 10 commits**

After completing these tasks, the next step is the **Manual Gate** (Phase 3): run the marimo notebook against real ClickHouse data, inspect the insider pool, and decide which parameter sets to validate with tick-by-tick replay (Phase 4).
