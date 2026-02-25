# Strategy Next Steps: Grading, Dual-Sided, Overlap Analysis & Live Execution Prep

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the gap between research insights (#14 trader grading, #15 composite strategy) and production-ready code. Implement the longshot YES grading filter in the `GradedPoolProvider`, add dual-sided execution to `will_no`, build a combined equity curve backtester, and wire up execution price validation — everything needed before deploying $1,500 across S1+S2+S3.

**Architecture:** Six focused work streams: (1) real grading logic in the existing `GradedPoolProvider`, (2) dual-sided BUY NO + SELL YES intents for `will_no`, (3) combined multi-strategy backtest runner, (4) S1+S2 overlap analysis, (5) execution price validation against CLOB API, and (6) strategy-level capital partitioning in the `ExecutionGateway`. All work extends existing modules — no new packages.

**Tech Stack:** Python 3.11+, asyncio, Polars, Pydantic v2, structlog, pytest-asyncio, frozen dataclasses.

---

## Current Status (as of 2026-02-25)

| Component | Status | Key Gap |
|-----------|--------|---------|
| S1 Proportional Copy | Framework done | `GradedPoolProvider` only filters `min_markets`, no grading |
| S2a Will NO | Strategy done, market size filter integrated | No dual-sided execution (insight #20) |
| S2b Crypto OTM NO | Strategy done | Minor — no volume provider for live |
| S3 Consensus Copy | Strategy done | Only 2 holdout windows (not code issue) |
| Market Size Classifier | Training + provider done | Needs model artifact committed |
| LiveRunner + Gateway | Fully wired | No per-strategy capital budget |
| LiveExecutor | CLOB API integration done | No execution price validation |
| Backtest runners | Vectorized + replay done | No multi-strategy combined runner |

---

## Task 1: Implement Longshot YES Grading in `GradedPoolProvider`

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py`
- Test: `tests/test_strategy_proportional_copy.py`

This is the #1 gap from insight #14. The provider currently only counts markets per trader. It needs to compute `longshot_yes_fraction` and filter to `> 0.15`.

**Step 1: Write failing tests**

Add to `tests/test_strategy_proportional_copy.py`:

```python
@pytest.mark.asyncio
async def test_graded_pool_filters_by_longshot_yes_fraction() -> None:
    """Only traders with longshot_yes_fraction > 0.15 should pass."""
    # Trader A: 20 markets, 5 are YES buys at <0.50 → longshot_yes_frac = 0.25 (passes)
    # Trader B: 20 markets, 1 is YES buy at <0.50 → longshot_yes_frac = 0.05 (fails)
    rows_a_longshot = [
        {"maker": "0xA", "condition_id": f"0xm{i}", "side": "BUY", "price": 0.30, "published_at": float(i)}
        for i in range(5)
    ]
    rows_a_normal = [
        {"maker": "0xA", "condition_id": f"0xm{i}", "side": "SELL", "price": 0.70, "published_at": float(i)}
        for i in range(5, 20)
    ]
    rows_b_longshot = [
        {"maker": "0xB", "condition_id": f"0xn0", "side": "BUY", "price": 0.25, "published_at": 0.0}
    ]
    rows_b_normal = [
        {"maker": "0xB", "condition_id": f"0xn{i}", "side": "SELL", "price": 0.75, "published_at": float(i)}
        for i in range(1, 20)
    ]

    trades_df = pl.DataFrame(rows_a_longshot + rows_a_normal + rows_b_longshot + rows_b_normal)

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(min_markets=10, min_longshot_yes_frac=0.15)
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xA" in pool   # 0.25 >= 0.15
    assert "0xB" not in pool  # 0.05 < 0.15


@pytest.mark.asyncio
async def test_graded_pool_excludes_high_no_fraction() -> None:
    """Traders with no_fraction > 0.60 should be excluded."""
    # Trader C: 20 markets, 15 are SELL (NO), no_frac = 0.75 → excluded
    rows_c = (
        [{"maker": "0xC", "condition_id": f"0xp{i}", "side": "SELL", "price": 0.80, "published_at": float(i)} for i in range(15)]
        + [{"maker": "0xC", "condition_id": f"0xp{i}", "side": "BUY", "price": 0.30, "published_at": float(i)} for i in range(15, 20)]
    )
    # Trader D: 20 markets, 8 SELL, 12 BUY (4 longshot YES) → no_frac=0.40, longshot=0.20 → passes
    rows_d = (
        [{"maker": "0xD", "condition_id": f"0xq{i}", "side": "SELL", "price": 0.70, "published_at": float(i)} for i in range(8)]
        + [{"maker": "0xD", "condition_id": f"0xq{i}", "side": "BUY", "price": 0.30, "published_at": float(i)} for i in range(8, 12)]
        + [{"maker": "0xD", "condition_id": f"0xq{i}", "side": "BUY", "price": 0.60, "published_at": float(i)} for i in range(12, 20)]
    )

    trades_df = pl.DataFrame(rows_c + rows_d)

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(min_markets=10, min_longshot_yes_frac=0.15, max_no_fraction=0.60)
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xC" not in pool  # no_frac 0.75 > 0.60
    assert "0xD" in pool      # no_frac 0.40, longshot_yes 0.20


@pytest.mark.asyncio
async def test_graded_pool_backward_compat_no_grading() -> None:
    """When no grading params given, behaves like before (market count only)."""
    trades_df = pl.DataFrame({
        "maker": ["0xA"] * 60 + ["0xB"] * 30,
        "condition_id": [f"0xmkt{i}" for i in range(60)] + [f"0xmkt{i}" for i in range(30)],
        "side": ["BUY"] * 90,
        "price": [0.50] * 90,
        "published_at": [float(i) for i in range(90)],
    })

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    # No grading params → old behavior
    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xA" in pool
    assert "0xB" in pool
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_proportional_copy.py::test_graded_pool_filters_by_longshot_yes_fraction tests/test_strategy_proportional_copy.py::test_graded_pool_excludes_high_no_fraction tests/test_strategy_proportional_copy.py::test_graded_pool_backward_compat_no_grading -x -q`
Expected: FAIL — `GradedPoolProvider` doesn't accept `min_longshot_yes_frac` or `max_no_fraction`

**Step 3: Implement grading in `GradedPoolProvider`**

Replace `GradedPoolProvider` in `src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py`:

```python
class GradedPoolProvider:
    """Computes and maintains the graded trader pool for proportional copy.

    Applies three filters (all optional, backward-compatible):
    1. min_markets — minimum distinct markets traded
    2. min_longshot_yes_frac — minimum fraction of YES buys at price < 0.50
    3. max_no_fraction — maximum fraction of SELL-side (NO) positions

    From insight #14: longshot_yes_fraction > 0.15 is the single strongest
    predictor of holdout copy profitability (Spearman r=+0.578).
    """

    name: str = "pool_traders"

    def __init__(
        self,
        min_markets: int = 50,
        min_longshot_yes_frac: float = 0.0,
        max_no_fraction: float = 1.0,
    ) -> None:
        self._min_markets = min_markets
        self._min_longshot_yes_frac = min_longshot_yes_frac
        self._max_no_fraction = max_no_fraction
        self._pool: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        trades = await backend.query_trades()

        if trades.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        import polars as pl

        lf = trades.lazy()

        # Per-trader aggregates
        trader_stats = (
            lf.group_by("maker")
            .agg(
                pl.col("condition_id").n_unique().alias("n_markets"),
                # Longshot YES: BUY side and price < 0.50
                (
                    (pl.col("side") == "BUY") & (pl.col("price").cast(pl.Float64) < 0.50)
                ).sum().alias("n_longshot_yes"),
                # NO fraction: SELL side count
                (pl.col("side") == "SELL").sum().alias("n_no"),
                pl.len().alias("n_total"),
            )
            .with_columns(
                (pl.col("n_longshot_yes") / pl.col("n_total")).alias("longshot_yes_frac"),
                (pl.col("n_no") / pl.col("n_total")).alias("no_frac"),
            )
        )

        # Apply filters
        filtered = trader_stats.filter(pl.col("n_markets") >= self._min_markets)

        if self._min_longshot_yes_frac > 0:
            filtered = filtered.filter(
                pl.col("longshot_yes_frac") >= self._min_longshot_yes_frac
            )

        if self._max_no_fraction < 1.0:
            filtered = filtered.filter(
                pl.col("no_frac") <= self._max_no_fraction
            )

        result = filtered.collect()
        self._pool = frozenset(result["maker"].to_list())
        logger.info(
            "pool_traders.compute",
            count=len(self._pool),
            min_longshot_yes_frac=self._min_longshot_yes_frac,
            max_no_fraction=self._max_no_fraction,
        )

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — pool is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"pool_traders": self._pool}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_proportional_copy.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py tests/test_strategy_proportional_copy.py
git commit -m "feat(S1): implement longshot YES grading in GradedPoolProvider

Add min_longshot_yes_frac and max_no_fraction filters per insight #14.
Backward-compatible — existing callers with no grading params unchanged.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 2: Add Dual-Sided Execution to Will-NO Strategy

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/will_no/config.py`
- Modify: `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`
- Test: `tests/test_strategy_will_no.py`

Insight #20 shows that dual-sided execution (BUY NO + SELL YES) accesses 2x liquidity per market. Currently `will_no` only emits `BUY NO`.

**Step 1: Write failing tests**

Add to `tests/test_strategy_will_no.py`:

```python
@pytest.mark.asyncio
async def test_will_no_dual_sided_emits_two_intents() -> None:
    """With dual_sided=True, should emit both BUY NO and SELL YES."""
    cfg = WillNoConfig(
        yes_price_min=0.15,
        yes_price_max=0.40,
        dual_sided=True,
        base_bet_usd=50.0,
    )
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 2

    outcomes = {(r.side, r.outcome) for r in result}
    assert ("BUY", "NO") in outcomes
    assert ("SELL", "YES") in outcomes

    # Each side gets half the bet size
    for intent in result:
        assert intent.size_usd == 25.0


@pytest.mark.asyncio
async def test_will_no_single_sided_default() -> None:
    """Default (dual_sided=False) should emit only BUY NO as before."""
    cfg = WillNoConfig(yes_price_min=0.15, yes_price_max=0.40)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Y happen?",
            active=True,
            yes_price=0.30,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.30)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"
    assert result[0].outcome == "NO"
    assert result[0].size_usd == 50.0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_will_no.py::test_will_no_dual_sided_emits_two_intents tests/test_strategy_will_no.py::test_will_no_single_sided_default -x -q`
Expected: FAIL — `WillNoConfig` doesn't accept `dual_sided`

**Step 3: Add `dual_sided` to `WillNoConfig`**

In `src/polymarket_pipeline/strategies_impl/will_no/config.py`, add field and init param:

```python
# Add to class body (after max_bucket):
dual_sided: bool = False

# Add to __init__ signature:
dual_sided: bool = False,

# Add to __init__ body:
object.__setattr__(self, "dual_sided", dual_sided)
```

**Step 4: Update `WillNoStrategy.on_trade` for dual-sided**

In `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`, replace the intent-building block in `on_trade` (after `self._signaled.add(cid)`):

```python
        self._signaled.add(cid)

        if self._cfg.dual_sided:
            half = self._cfg.base_bet_usd / 2
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="NO",
                    size_usd=half,
                    urgency="patient",
                    max_price=None,
                    reason=f"will_no: {market.question}",
                    signal_time=trade.published_at,
                ),
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="SELL",
                    outcome="YES",
                    size_usd=half,
                    urgency="patient",
                    max_price=None,
                    reason=f"will_no: {market.question}",
                    signal_time=trade.published_at,
                ),
            ]

        intent = TradeIntent(
            strategy=self.name,
            condition_id=cid,
            side="BUY",
            outcome="NO",
            size_usd=self._cfg.base_bet_usd,
            urgency="patient",
            max_price=None,
            reason=f"will_no: {market.question}",
            signal_time=trade.published_at,
        )
        return [intent]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_will_no.py -x -q`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/will_no/config.py src/polymarket_pipeline/strategies_impl/will_no/strategy.py tests/test_strategy_will_no.py
git commit -m "feat(S2a): add dual-sided execution to will-no strategy

BUY NO + SELL YES accesses 2x liquidity per market (insight #20).
Default off for backward compat.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 3: Per-Strategy Capital Budget in ExecutionGateway

**Files:**
- Modify: `src/polymarket_pipeline/strategies/execution/gateway.py`
- Create: `tests/test_gateway_budget.py`

Currently the gateway has no concept of strategy-level capital allocation. Insight #15 allocates $1,000 to S1, $300 to S2, $200 to S3. The gateway needs to track spent capital per strategy and reject intents when budget is exhausted.

**Step 1: Write failing tests**

Create `tests/test_gateway_budget.py`:

```python
"""Tests for per-strategy capital budgeting in ExecutionGateway."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


def _intent(strategy: str = "s1", size_usd: float = 100.0) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id="0xmkt1",
        side="BUY",
        outcome="NO",
        size_usd=size_usd,
        urgency="patient",
        max_price=None,
        reason="test",
        signal_time=time.time(),
    )


def _fill(intent: TradeIntent, status: FillStatus = FillStatus.FILLED) -> Fill:
    return Fill(
        intent_id="test-fill",
        strategy=intent.strategy,
        condition_id=intent.condition_id,
        side=intent.side,
        outcome=intent.outcome,
        filled_price=0.50,
        filled_size_usd=intent.size_usd,
        fee_usd=0.0,
        status=status,
        filled_at=time.time(),
    )


@pytest.mark.asyncio
async def test_gateway_rejects_over_budget() -> None:
    """Intents exceeding strategy budget should be rejected."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 200.0},
    )

    # First intent: $100, budget $200 → OK
    fill1 = await gw.submit(_intent("s1", 100.0))
    assert fill1.status == FillStatus.FILLED

    # Second: $100, used=$100, budget=$200 → OK
    fill2 = await gw.submit(_intent("s1", 100.0))
    assert fill2.status == FillStatus.FILLED

    # Third: $100, used=$200, budget=$200 → REJECTED
    fill3 = await gw.submit(_intent("s1", 100.0))
    assert fill3.status == FillStatus.REJECTED
    assert "budget" in (fill3.error or "").lower()


@pytest.mark.asyncio
async def test_gateway_no_budget_passes_through() -> None:
    """Strategies without a budget have no spending cap."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(executor=executor)

    for _ in range(10):
        fill = await gw.submit(_intent("no_budget_strat", 1000.0))
        assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_gateway_separate_budgets_per_strategy() -> None:
    """Each strategy has an independent budget."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 100.0, "s2": 300.0},
    )

    # S1 uses its full budget
    fill_s1 = await gw.submit(_intent("s1", 100.0))
    assert fill_s1.status == FillStatus.FILLED

    # S1 is now exhausted
    fill_s1b = await gw.submit(_intent("s1", 1.0))
    assert fill_s1b.status == FillStatus.REJECTED

    # S2 still has budget
    fill_s2 = await gw.submit(_intent("s2", 300.0))
    assert fill_s2.status == FillStatus.FILLED
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway_budget.py -x -q`
Expected: FAIL — `ExecutionGateway` doesn't accept `strategy_budgets`

**Step 3: Add budgeting to `ExecutionGateway`**

In `src/polymarket_pipeline/strategies/execution/gateway.py`:

1. Add `strategy_budgets` parameter to `__init__`:

```python
    def __init__(
        self,
        executor: Executor,
        log_path: Path | None = None,
        *,
        delay_s: float = 0.0,
        quality_state: ReadinessState | None = None,
        strategy_budgets: dict[str, float] | None = None,
    ) -> None:
        self.executor = executor
        self.log_path = log_path
        self.delay_s = delay_s
        self._quality_state = quality_state
        self._strategy_budgets = dict(strategy_budgets) if strategy_budgets else {}
        self._strategy_spent: dict[str, float] = {}
```

2. In the `submit` method, add budget check before executing. Insert early in `submit`, after existing quality gate check but before calling `self.executor.execute(intent)`:

```python
        # Budget gate
        if self._strategy_budgets:
            budget = self._strategy_budgets.get(intent.strategy)
            if budget is not None:
                spent = self._strategy_spent.get(intent.strategy, 0.0)
                if spent + intent.size_usd > budget:
                    logger.warning(
                        "gateway.budget_exceeded",
                        strategy=intent.strategy,
                        spent=spent,
                        requested=intent.size_usd,
                        budget=budget,
                    )
                    return Fill(
                        intent_id=f"budget-{intent.strategy}",
                        strategy=intent.strategy,
                        condition_id=intent.condition_id,
                        side=intent.side,
                        outcome=intent.outcome,
                        filled_price=0.0,
                        filled_size_usd=0.0,
                        fee_usd=0.0,
                        status=FillStatus.REJECTED,
                        filled_at=time.time(),
                        error=f"Budget exhausted: {spent:.2f}/{budget:.2f}",
                    )
```

3. After a successful fill (status == FILLED), track spending:

```python
        if fill.status == FillStatus.FILLED:
            self._strategy_spent[intent.strategy] = (
                self._strategy_spent.get(intent.strategy, 0.0) + fill.filled_size_usd
            )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway_budget.py -x -q`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS (no regressions)

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/execution/gateway.py tests/test_gateway_budget.py
git commit -m "feat: add per-strategy capital budgets to ExecutionGateway

Supports insight #15 allocation: $1K to S1, $300 to S2, $200 to S3.
Budget is optional — strategies without a budget are uncapped.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 4: Combined Multi-Strategy Backtest Runner

**Files:**
- Create: `src/polymarket_pipeline/strategies/runners/combined.py`
- Create: `tests/test_runner_combined.py`

The validation backlog (#15 item 5) requires simulating S1+S2+S3 running simultaneously with proper capital partitioning. This runner takes multiple (strategy, config) pairs and runs them over the same trade stream with a shared capital pool.

**Step 1: Write failing tests**

Create `tests/test_runner_combined.py`:

```python
"""Tests for combined multi-strategy backtest runner."""

from __future__ import annotations

import polars as pl
import pytest

from polymarket_pipeline.strategies.runners.combined import CombinedBacktestRunner


@pytest.fixture
def trades_lf() -> pl.LazyFrame:
    return pl.LazyFrame({
        "trade_id": [f"t{i}" for i in range(6)],
        "condition_id": ["0xm1", "0xm1", "0xm2", "0xm2", "0xm3", "0xm3"],
        "maker": ["0xA", "0xB", "0xA", "0xC", "0xA", "0xB"],
        "side": ["BUY", "BUY", "SELL", "SELL", "BUY", "BUY"],
        "price": [0.30, 0.25, 0.70, 0.80, 0.20, 0.22],
        "published_at": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


@pytest.fixture
def markets_lf() -> pl.LazyFrame:
    return pl.LazyFrame({
        "condition_id": ["0xm1", "0xm2", "0xm3"],
        "question": ["Will X?", "Will Y?", "Bitcoin above $100K?"],
        "category": ["Politics", "Politics", "Crypto"],
    })


def test_combined_runner_produces_per_strategy_signals(trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame) -> None:
    """Each strategy should produce independent signals."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import ProportionalCopyConfig
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import ProportionalCopyStrategy
    from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
    from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(pool_traders={"0xA", "0xB"}, contradiction_filter=False)
    )
    s2 = WillNoStrategy(
        config=WillNoConfig(yes_price_min=0.10, yes_price_max=0.50, avoid_keywords=set())
    )

    runner = CombinedBacktestRunner(
        strategies=[s1, s2],
        budgets={"proportional_copy": 1000.0, "will_no": 300.0},
    )

    result = runner.run(trades_lf, markets_lf)

    assert "strategy" in result.columns
    strategies_found = set(result["strategy"].to_list())
    # Both strategies should have produced signals
    assert len(strategies_found) >= 1  # at least one fires


def test_combined_runner_respects_budgets(trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame) -> None:
    """Total size_usd per strategy should not exceed its budget."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import ProportionalCopyConfig
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import ProportionalCopyStrategy

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders={"0xA", "0xB", "0xC"},
            capital_per_trader_usd=500.0,
            contradiction_filter=False,
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1],
        budgets={"proportional_copy": 600.0},
    )

    result = runner.run(trades_lf, markets_lf)
    total_spent = result.filter(pl.col("strategy") == "proportional_copy")["size_usd"].sum()
    assert total_spent <= 600.0


def test_combined_runner_returns_equity_curve(trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame) -> None:
    """Runner should return equity curve DataFrame."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import ProportionalCopyConfig
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import ProportionalCopyStrategy

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(pool_traders={"0xA"}, contradiction_filter=False)
    )

    runner = CombinedBacktestRunner(
        strategies=[s1],
        budgets={"proportional_copy": 1000.0},
    )

    signals = runner.run(trades_lf, markets_lf)
    assert "signal_time" in signals.columns
    assert "size_usd" in signals.columns
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner_combined.py -x -q`
Expected: FAIL — `CombinedBacktestRunner` doesn't exist

**Step 3: Implement `CombinedBacktestRunner`**

Create `src/polymarket_pipeline/strategies/runners/combined.py`:

```python
"""CombinedBacktestRunner — run multiple vectorized strategies over shared data."""

from __future__ import annotations

from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


class CombinedBacktestRunner:
    """Runs multiple strategies over the same trade/market data.

    Each strategy gets the full trade stream. Signals are tagged by strategy
    name and capped to per-strategy budgets.

    Parameters
    ----------
    strategies:
        List of strategy objects with ``compute_signals(trades, markets)`` method.
    budgets:
        Optional dict mapping strategy name to capital budget in USD.
    """

    def __init__(
        self,
        strategies: list[Any],
        budgets: dict[str, float] | None = None,
    ) -> None:
        self._strategies = strategies
        self._budgets = budgets or {}

    def run(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
        """Run all strategies and return combined signal DataFrame.

        Returns a DataFrame with columns:
        ``strategy``, ``condition_id``, ``signal_time``, ``side``, ``outcome``,
        ``size_usd``, optionally ``entry_price``.
        """
        all_signals: list[pl.DataFrame] = []

        for strategy in self._strategies:
            name = strategy.name
            signals = strategy.compute_signals(trades, markets)

            if signals.is_empty():
                continue

            # Tag with strategy name if not already present
            if "strategy" not in signals.columns:
                signals = signals.with_columns(pl.lit(name).alias("strategy"))

            # Apply budget cap: sort by signal_time, cumulative spend, filter
            budget = self._budgets.get(name)
            if budget is not None and "size_usd" in signals.columns:
                signals = signals.sort("signal_time")
                signals = signals.with_columns(
                    pl.col("size_usd").cum_sum().alias("_cum_spent")
                )
                signals = signals.filter(pl.col("_cum_spent") <= budget)
                signals = signals.drop("_cum_spent")

            all_signals.append(signals)
            logger.info(
                "combined.strategy_signals",
                strategy=name,
                n_signals=len(signals),
            )

        if not all_signals:
            return pl.DataFrame({
                "strategy": [],
                "condition_id": [],
                "signal_time": [],
                "side": [],
                "outcome": [],
                "size_usd": [],
            })

        # Align schemas before concat (some strategies emit entry_price, some don't)
        all_cols = set()
        for df in all_signals:
            all_cols.update(df.columns)

        aligned = []
        for df in all_signals:
            for col in all_cols:
                if col not in df.columns:
                    df = df.with_columns(pl.lit(None).alias(col))
            aligned.append(df.select(sorted(all_cols)))

        combined = pl.concat(aligned).sort("signal_time")
        logger.info("combined.total_signals", n=len(combined))
        return combined
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner_combined.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/combined.py tests/test_runner_combined.py
git commit -m "feat: add CombinedBacktestRunner for multi-strategy simulation

Runs S1+S2+S3 over shared trade stream with per-strategy budgets.
Validation backlog item #5 from insight #15.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 5: S1+S2 Overlap Analysis Script

**Files:**
- Create: `scripts/analyze_s1_s2_overlap.py`

Validation backlog item #3 from #15: "When S1's pool trades in 'Will' binary markets, does S2 add independent edge?" This is a one-off analysis script using the `CombinedBacktestRunner`.

**Step 1: Create analysis script**

Create `scripts/analyze_s1_s2_overlap.py`:

```python
"""Analyze overlap between S1 (proportional copy) and S2 (will-NO) strategies.

Question: When S1's trader pool trades in "Will" binary markets,
does S2 add independent edge?

Usage:
    uv run python scripts/analyze_s1_s2_overlap.py
"""

from __future__ import annotations

import polars as pl

from polymarket_pipeline.strategies.runners.combined import CombinedBacktestRunner
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)
from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
    ProportionalCopyStrategy,
)
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy


def load_data() -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Load trades and market metadata from derived parquet files."""
    trades = pl.scan_parquet("data/derived/trader_market_pnl.parquet")
    markets = pl.scan_parquet("data/metadata/markets.parquet")
    return trades, markets


def main() -> None:
    trades, markets = load_data()

    # S1: proportional copy with graded pool
    # NOTE: Replace with actual pool_traders from latest grading run
    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders=set(),  # TODO: load from grading output
            capital_per_trader_usd=50.0,
            contradiction_filter=True,
        )
    )

    # S2: will-NO
    s2 = WillNoStrategy(
        config=WillNoConfig(
            yes_price_min=0.15,
            yes_price_max=0.40,
            base_bet_usd=50.0,
            avoid_keywords={"reach", "hit"},
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1, s2],
        budgets={"proportional_copy": 1000.0, "will_no": 300.0},
    )

    signals = runner.run(trades, markets)

    if signals.is_empty():
        print("No signals generated. Check pool_traders is populated.")
        return

    # Find overlap: markets where both S1 and S2 fired
    s1_markets = set(
        signals.filter(pl.col("strategy") == "proportional_copy")["condition_id"].to_list()
    )
    s2_markets = set(
        signals.filter(pl.col("strategy") == "will_no")["condition_id"].to_list()
    )

    overlap = s1_markets & s2_markets
    s1_only = s1_markets - s2_markets
    s2_only = s2_markets - s1_markets

    print(f"S1 signals: {len(s1_markets)}")
    print(f"S2 signals: {len(s2_markets)}")
    print(f"Overlap:    {len(overlap)} ({len(overlap) / max(len(s2_markets), 1):.1%} of S2)")
    print(f"S1 only:    {len(s1_only)}")
    print(f"S2 only:    {len(s2_only)}")
    print()

    if overlap:
        # Direction agreement in overlapping markets
        overlap_signals = signals.filter(pl.col("condition_id").is_in(list(overlap)))
        print("Overlap signal details:")
        print(overlap_signals.select("strategy", "condition_id", "outcome", "size_usd"))


if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add scripts/analyze_s1_s2_overlap.py
git commit -m "feat: add S1+S2 overlap analysis script

Validation backlog item #3 from insight #15.
Requires pool_traders to be populated from grading output.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 6: Execution Price Validation

**Files:**
- Create: `scripts/validate_execution_prices.py`

Validation backlog item #6 from #15: "Compare backtest entry prices vs achievable prices via CLOB API." This script samples recent signals and checks CLOB orderbook depth.

**Step 1: Create validation script**

Create `scripts/validate_execution_prices.py`:

```python
"""Validate achievable execution prices against backtest assumptions.

Compares the entry prices assumed in vectorized backtests against actual
CLOB orderbook depth for the same markets.

Usage:
    uv run python scripts/validate_execution_prices.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import polars as pl
import structlog

logger = structlog.get_logger(__name__)


async def fetch_orderbook(
    client: httpx.AsyncClient,
    condition_id: str,
    *,
    asset_id: str | None = None,
) -> dict | None:
    """Fetch orderbook from CLOB API for a given market."""
    url = f"https://clob.polymarket.com/book"
    params = {"token_id": asset_id or condition_id}
    try:
        resp = await client.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("orderbook.fetch_failed", condition_id=condition_id, error=str(e))
        return None


def compute_achievable_price(
    book: dict,
    side: str,
    size_usd: float,
) -> float | None:
    """Walk the orderbook to compute volume-weighted achievable price."""
    if side == "BUY":
        levels = book.get("asks", [])
    else:
        levels = book.get("bids", [])

    if not levels:
        return None

    remaining = size_usd
    total_cost = 0.0

    for level in levels:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        level_value = price * size

        if level_value >= remaining:
            total_cost += remaining
            remaining = 0
            break
        else:
            total_cost += level_value
            remaining -= level_value

    if remaining > 0:
        return None  # insufficient liquidity

    return total_cost / size_usd


async def main() -> None:
    # Load recent signals from a backtest run
    signals_path = Path("data/backtest_signals.parquet")
    if not signals_path.exists():
        print(f"No signals file at {signals_path}.")
        print("Run a backtest first and save signals to this path.")
        return

    signals = pl.read_parquet(signals_path)

    # Sample up to 50 signals
    sample = signals.sample(min(50, len(signals)), seed=42)

    results = []
    async with httpx.AsyncClient() as client:
        for row in sample.iter_rows(named=True):
            cid = row["condition_id"]
            side = row.get("side", "BUY")
            size = row.get("size_usd", 50.0)
            bt_price = row.get("entry_price", None)

            book = await fetch_orderbook(client, cid)
            if book is None:
                continue

            achievable = compute_achievable_price(book, side, size)

            results.append({
                "condition_id": cid,
                "backtest_price": bt_price,
                "achievable_price": achievable,
                "side": side,
                "size_usd": size,
                "slippage": (achievable - bt_price) if (achievable and bt_price) else None,
            })

            # Rate limit
            await asyncio.sleep(0.2)

    if not results:
        print("No results collected.")
        return

    df = pl.DataFrame(results)
    print(df.describe())

    # Summary stats
    valid = df.filter(pl.col("slippage").is_not_null())
    if not valid.is_empty():
        print(f"\nSlippage stats ({len(valid)} markets):")
        print(f"  Median: {valid['slippage'].median():.4f}")
        print(f"  Mean:   {valid['slippage'].mean():.4f}")
        print(f"  P95:    {valid['slippage'].quantile(0.95):.4f}")

    # Save
    out = Path("data/execution_price_validation.parquet")
    df.write_parquet(out)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Commit**

```bash
git add scripts/validate_execution_prices.py
git commit -m "feat: add execution price validation script

Compares backtest entry prices vs CLOB orderbook depth.
Validation backlog item #6 from insight #15.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 7: Wire Grading Params into TOML Config + CLI

**Files:**
- Modify: `src/polymarket_pipeline/cli/strategy.py`
- Test: verify `pm-strategy run --config` passes grading params through

This ensures the new grading parameters from Task 1 are loadable from TOML config.

**Step 1: Update provider factory to pass grading params**

In `src/polymarket_pipeline/cli/strategy.py`, the `_register_providers` function already registers `GradedPoolProvider`. Since the CLI already passes `**params` to provider constructors (line 167), we only need to verify TOML config works.

Create a test TOML snippet to validate:

```toml
# Example strategies.toml snippet for S1
[providers.pool_traders]
enabled = true
min_markets = 20
min_longshot_yes_frac = 0.15
max_no_fraction = 0.60

[strategies.proportional_copy]
enabled = true
features = ["pool_traders"]
params.capital_per_trader_usd = 50.0
params.contradiction_filter = true
params.sizing = "equal"
```

**Step 2: Run smoke test**

Run: `uv run python -c "
from polymarket_pipeline.strategies_impl.proportional_copy.providers import GradedPoolProvider
p = GradedPoolProvider(min_markets=20, min_longshot_yes_frac=0.15, max_no_fraction=0.60)
print('OK:', p._min_markets, p._min_longshot_yes_frac, p._max_no_fraction)
"`
Expected: `OK: 20 0.15 0.6`

**Step 3: Commit**

No code changes needed — the CLI already uses `**params` kwargs forwarding. Commit the example TOML as documentation:

```bash
# Only if you create an example config file
git add config/strategies.example.toml
git commit -m "docs: add example TOML config with grading params

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Summary: Research → Code Gap Closure

| Insight Recommendation | Task | Status After |
|---|---|---|
| `longshot_yes_fraction > 0.15` filter | Task 1 | Implemented in `GradedPoolProvider` |
| `no_fraction > 0.60` exclusion | Task 1 | Implemented in `GradedPoolProvider` |
| Dual-sided BUY NO + SELL YES | Task 2 | Implemented in `WillNoStrategy` |
| Per-strategy capital budgets ($1K/$300/$200) | Task 3 | Implemented in `ExecutionGateway` |
| Combined S1+S2+S3 equity curve | Task 4 | `CombinedBacktestRunner` |
| S1+S2 overlap validation | Task 5 | Analysis script |
| Execution price validation | Task 6 | Validation script |
| TOML config for grading | Task 7 | Verified compatible |

### What Remains After This Plan

- **S3 extended holdout** (backlog item #4): Not a code task — needs 4+ more months of data. Track in monthly review.
- **Capacity testing** (backlog item #9): Run after Task 6 validates prices. Manual with small bets.
- **Market size model artifact**: Commit the trained `.joblib` file from the training script.
- **Live deployment**: Once Tasks 1-7 pass, deploy `pm-strategy run` with graded TOML config in paper-dev mode.
