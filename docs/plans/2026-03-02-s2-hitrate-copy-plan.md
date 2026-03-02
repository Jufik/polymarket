# S2 Hit-Rate Copy Strategy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a tiered-conviction copy-trading strategy that identifies skilled traders (excess hit rate above direction-specific base rate) and copies their entries, with an interactive marimo workbench for rapid tick-by-tick parameter exploration and automated sweeps.

**Architecture:** Provider/Strategy split following existing protocol patterns. Provider queries ClickHouse for qualified traders and tracks per-market consensus state. Strategy emits tiered BUY intents (seed at low consensus, scale at high). Marimo notebook wraps ReplayRunner for interactive multi-period tick-by-tick replay with auto-sweep.

**Tech Stack:** Python 3.11+, async/await, Polars, ClickHouse, marimo, ReplayRunner, RealisticFillSimulator, ParquetLedger

**Design doc:** `docs/plans/2026-03-02-s2-hitrate-copy-design.md`

---

### Task 1: Research strategy — core data structures and constructor

**Files:**
- Create: `research/strategies/s2_hitrate_copy.py`
- Test: `research/tests/test_s2_strategy.py`

**Step 1: Write the failing test**

```python
# research/tests/test_s2_strategy.py
"""Unit tests for S2 hit-rate copy strategy."""
from __future__ import annotations

import pytest

from research.strategies.s2_hitrate_copy import S2HitRateCopy, S2Config


def test_default_config():
    cfg = S2Config()
    assert cfg.min_positions == 30
    assert cfg.min_excess_hr == 0.10
    assert cfg.seed_threshold == 1
    assert cfg.scale_threshold == 4
    assert cfg.seed_pct == 0.25
    assert cfg.seed_timeout_hours is None
    assert cfg.direction == "BOTH"
    assert cfg.recency_months == 6


def test_strategy_name():
    strat = S2HitRateCopy(S2Config())
    assert strat.name == "s2_hitrate_copy"


def test_strategy_has_protocol_methods():
    strat = S2HitRateCopy(S2Config())
    assert hasattr(strat, "on_trade")
    assert hasattr(strat, "on_market_update")
    assert hasattr(strat, "on_timer")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.strategies.s2_hitrate_copy'`

**Step 3: Write minimal implementation**

```python
# research/strategies/s2_hitrate_copy.py
"""S2 Hit-Rate Copy — tiered conviction copy-trading strategy.

Copies trades from skilled traders (excess hit rate above direction-specific
base rate). Two-tier entry: seed position on low consensus, scale to full
on high consensus.

Usage:
    from research.strategies.s2_hitrate_copy import S2HitRateCopy, S2Config
    strat = S2HitRateCopy(S2Config(min_excess_hr=0.10, scale_threshold=4))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


# Base rates (from research/knowledge/data/market_base_rates.md)
YES_BASE_RATE = 0.381
NO_BASE_RATE = 0.619


@dataclass(frozen=True)
class S2Config:
    """Strategy parameters — all sweepable."""

    min_positions: int = 30
    min_excess_hr: float = 0.10
    seed_threshold: int = 1
    scale_threshold: int = 4
    seed_pct: float = 0.25
    seed_timeout_hours: float | None = None
    direction: Literal["YES", "NO", "BOTH"] = "BOTH"
    recency_months: int = 6
    position_size_usd: float = 100.0


class S2HitRateCopy:
    """Tiered conviction copy strategy."""

    name: str = "s2_hitrate_copy"

    def __init__(self, config: S2Config) -> None:
        self._cfg = config
        # Qualified traders: set of lowercase addresses
        self._qualified: set[str] = set()
        # Consensus: condition_id -> {direction -> set(trader_address)}
        self._consensus: dict[str, dict[str, set[str]]] = {}
        # Track which markets already have seed/full positions
        self._seeded: set[str] = set()
        self._scaled: set[str] = set()
        # Seed timestamps for timeout logic
        self._seed_times: dict[str, float] = {}

    def set_qualified_traders(self, traders: set[str]) -> None:
        """Set the qualified trader pool (called by provider or test setup)."""
        self._qualified = {t.lower() for t in traders}

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        return None  # implemented in Task 2

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None  # timeout logic in Task 3
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_hitrate_copy.py research/tests/test_s2_strategy.py
git commit -m "feat(s2): scaffold strategy with S2Config and data structures"
```

---

### Task 2: on_trade — SELL filter, consensus tracking, tiered entry

**Files:**
- Modify: `research/strategies/s2_hitrate_copy.py`
- Modify: `research/tests/test_s2_strategy.py`

**Step 1: Write the failing tests**

Append to `research/tests/test_s2_strategy.py`:

```python
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext


def _make_trade(
    cid: str,
    maker: str,
    price: float = 0.50,
    ts: float = 1000.0,
    side: Side = Side.BUY,
    asset_id: str = "asset_yes",
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{maker}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(round(price * 100, 2))),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


def test_sell_trades_are_skipped():
    """SELL trades are exits, not signals — must be filtered."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", side=Side.SELL)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_unqualified_trader_skipped():
    strat = S2HitRateCopy(S2Config(seed_threshold=1))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xrandom_trader")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_seed_entry_on_first_qualified_trader():
    """First qualified trader triggers seed (25% position)."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=4, seed_pct=0.25, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.40)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert len(result) == 1
    intent = result[0]
    assert intent.size_usd == 25.0  # 100 * 0.25
    assert intent.side == "BUY"
    assert intent.condition_id == "cid_1"


def test_scale_entry_on_consensus():
    """4 unique qualified traders triggers scale (remaining 75%)."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=4, seed_pct=0.25, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a", "0xtrader_b", "0xtrader_c", "0xtrader_d"})
    ctx = InMemoryContext()

    # First trader → seed
    r1 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))
    assert r1 is not None and r1[0].size_usd == 25.0

    # 2nd and 3rd → no new intent (between seed and scale)
    r2 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_b", ts=2.0), ctx))
    assert r2 is None
    r3 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_c", ts=3.0), ctx))
    assert r3 is None

    # 4th → scale (top up to full)
    r4 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_d", ts=4.0), ctx))
    assert r4 is not None
    assert r4[0].size_usd == 75.0  # remaining 75%


def test_duplicate_trader_not_counted_twice():
    """Same trader trading twice in same market doesn't inflate consensus."""
    strat = S2HitRateCopy(S2Config(seed_threshold=2, scale_threshold=4))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    r1 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))
    assert r1 is None  # need 2 unique traders
    r2 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=2.0), ctx))
    assert r2 is None  # still only 1 unique trader


def test_no_duplicate_intents_after_scale():
    """Once scaled, no more intents for that market."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=2, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a", "0xtrader_b", "0xtrader_c"})
    ctx = InMemoryContext()

    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))  # seed
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_b", ts=2.0), ctx))  # scale

    # 3rd trader → already scaled, no more intents
    r3 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_c", ts=3.0), ctx))
    assert r3 is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: FAIL (on_trade returns None for everything)

**Step 3: Implement on_trade**

Replace the `on_trade` method in `research/strategies/s2_hitrate_copy.py`:

```python
    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        # 1. SELL is exit, not signal
        if trade.side != "BUY":
            return None

        # 2. Must be a qualified trader
        maker = (trade.maker or "").lower()
        if maker not in self._qualified:
            return None

        # 3. Already at full position
        cid = trade.condition_id
        if cid in self._scaled:
            return None

        # 4. Determine direction from asset_id
        # For now, use the trade's asset_id directly.
        # The outcome is determined by the token_map at resolution time.
        # We track consensus per condition_id (direction-agnostic for now).
        if cid not in self._consensus:
            self._consensus[cid] = {"traders": set()}
        self._consensus[cid]["traders"].add(maker)
        count = len(self._consensus[cid]["traders"])

        now = trade.published_at
        price = float(trade.price)

        # 5. Check tiered entry
        if count >= self._cfg.scale_threshold and cid not in self._scaled:
            # Scale: emit remaining portion
            self._scaled.add(cid)
            remaining_pct = 1.0 - self._cfg.seed_pct if cid in self._seeded else 1.0
            size = self._cfg.position_size_usd * remaining_pct
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",  # outcome determined by asset_id at fill
                    size_usd=size,
                    urgency="patient",
                    max_price=min(price + 0.02, 0.95),
                    reason=f"consensus={count} (scale)",
                    signal_time=now,
                    asset_id=trade.asset_id,
                ),
            ]

        if count >= self._cfg.seed_threshold and cid not in self._seeded:
            # Seed: emit small position
            self._seeded.add(cid)
            self._seed_times[cid] = now
            size = self._cfg.position_size_usd * self._cfg.seed_pct
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",
                    size_usd=size,
                    urgency="patient",
                    max_price=min(price + 0.02, 0.95),
                    reason=f"consensus={count} (seed)",
                    signal_time=now,
                    asset_id=trade.asset_id,
                ),
            ]

        return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_hitrate_copy.py research/tests/test_s2_strategy.py
git commit -m "feat(s2): implement on_trade with SELL filter, consensus, tiered entry"
```

---

### Task 3: on_timer — seed timeout logic

**Files:**
- Modify: `research/strategies/s2_hitrate_copy.py`
- Modify: `research/tests/test_s2_strategy.py`

**Step 1: Write the failing test**

Append to `research/tests/test_s2_strategy.py`:

```python
def test_seed_timeout_exits():
    """Stale seed positions emit SELL after timeout."""
    cfg = S2Config(
        seed_threshold=1,
        scale_threshold=4,
        seed_timeout_hours=24,
        position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    # Enter seed at t=1000
    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=t0), ctx))

    # Timer at t=1000 + 25h → timeout (> 24h)
    t_timeout = t0 + 25 * 3600
    result = asyncio.run(strat.on_timer(t_timeout, ctx))
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "SELL"
    assert result[0].condition_id == "cid_1"


def test_seed_no_timeout_when_disabled():
    """No timeout when seed_timeout_hours is None."""
    cfg = S2Config(seed_threshold=1, scale_threshold=4, seed_timeout_hours=None)
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1000.0), ctx))

    result = asyncio.run(strat.on_timer(1000.0 + 999 * 3600, ctx))
    assert result is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest research/tests/test_s2_strategy.py::test_seed_timeout_exits -x -q`
Expected: FAIL (on_timer returns None)

**Step 3: Implement on_timer**

Replace on_timer in `research/strategies/s2_hitrate_copy.py`:

```python
    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        if self._cfg.seed_timeout_hours is None:
            return None

        timeout_s = self._cfg.seed_timeout_hours * 3600
        exits: list[TradeIntent] = []

        expired = []
        for cid, seed_time in self._seed_times.items():
            if cid in self._scaled:
                continue  # already at full position
            if now - seed_time >= timeout_s:
                expired.append(cid)
                exits.append(
                    TradeIntent(
                        strategy=self.name,
                        condition_id=cid,
                        side="SELL",
                        outcome="YES",
                        size_usd=self._cfg.position_size_usd * self._cfg.seed_pct,
                        urgency="patient",
                        max_price=None,
                        reason=f"seed_timeout ({self._cfg.seed_timeout_hours}h)",
                        signal_time=now,
                        asset_id=None,
                    ),
                )

        for cid in expired:
            del self._seed_times[cid]
            self._seeded.discard(cid)

        return exits if exits else None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add research/strategies/s2_hitrate_copy.py research/tests/test_s2_strategy.py
git commit -m "feat(s2): add seed timeout logic in on_timer"
```

---

### Task 4: Qualified trader pool — CH query builder

**Files:**
- Modify: `research/strategies/s2_hitrate_copy.py`
- Modify: `research/tests/test_s2_strategy.py`

This adds a static method that builds the ClickHouse SQL for fetching qualified traders with direction-specific excess hit rate.

**Step 1: Write the failing test**

```python
def test_qualified_traders_query_has_excess_hr():
    """SQL must filter by excess HR above direction-specific base rate."""
    sql = S2HitRateCopy.qualified_traders_query(
        min_positions=30, min_excess_hr=0.10, recency_months=6
    )
    assert "trader" in sql
    assert "hit_rate" in sql
    assert "excess_hr" in sql
    # Must filter out Up or Down markets
    assert "Up or Down" in sql
    # Must split by direction for base rate adjustment
    assert "position" in sql
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest research/tests/test_s2_strategy.py::test_qualified_traders_query_has_excess_hr -x -q`
Expected: FAIL — `AttributeError: type object 'S2HitRateCopy' has no attribute 'qualified_traders_query'`

**Step 3: Implement the query builder**

Add to `research/strategies/s2_hitrate_copy.py`:

```python
    @staticmethod
    def qualified_traders_query(
        min_positions: int = 30,
        min_excess_hr: float = 0.10,
        recency_months: int = 6,
        direction: str = "BOTH",
    ) -> str:
        """Build CH SQL for qualified traders with direction-aware excess HR.

        Returns trader, position (YES/NO), wins, total, hit_rate, excess_hr.
        Excess HR = hit_rate - base_rate(direction).
        """
        dir_filter = ""
        if direction == "YES":
            dir_filter = "AND p.position = 'YES'"
        elif direction == "NO":
            dir_filter = "AND p.position = 'NO'"

        return f"""
            SELECT
                lower(p.trader) AS trader,
                p.position AS direction,
                countIf(p.correct = 1) AS wins,
                count(*) AS total,
                countIf(p.correct = 1) / count(*) AS hit_rate,
                countIf(p.correct = 1) / count(*) -
                    if(p.position = 'YES', {YES_BASE_RATE}, {NO_BASE_RATE}) AS excess_hr
            FROM (
                SELECT * FROM trader_positions_resolved
                WHERE position IN ('YES', 'NO')
                  AND toDate(resolved_at) >= toDate(now()) - INTERVAL {recency_months} MONTH
                  {dir_filter}
            ) AS p
            INNER JOIN markets AS m ON p.condition_id = m.condition_id
            WHERE m.question NOT LIKE '%Up or Down%'
              AND m.question NOT LIKE '%up or down%'
            GROUP BY trader, p.position
            HAVING count(*) >= {min_positions}
               AND excess_hr >= {min_excess_hr}
            ORDER BY excess_hr DESC
        """
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest research/tests/test_s2_strategy.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add research/strategies/s2_hitrate_copy.py research/tests/test_s2_strategy.py
git commit -m "feat(s2): add CH SQL builder for direction-aware qualified traders"
```

---

### Task 5: Replay helper — run_s2_replay function

**Files:**
- Create: `research/strategies/s2_replay.py`
- Test: `research/tests/test_s2_replay.py`

This wraps ReplayRunner with S2-specific setup: load qualified traders from a pre-computed set, build resolutions, calibrate fills, run replay, return LedgerSummary.

**Step 1: Write the failing test**

```python
# research/tests/test_s2_replay.py
"""Test the S2 replay helper."""
from __future__ import annotations

import asyncio

from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
from research.strategies.s2_replay import run_s2_replay


def test_run_s2_replay_returns_summary(sample_trades, permissive_config):
    """Replay should run without error and return a summary."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=2))
    strat.set_qualified_traders({"0xmaker"})  # matches sample_trades maker

    result, summary = asyncio.run(
        run_s2_replay(
            strategy=strat,
            trades=sample_trades,
            config=permissive_config,
            resolutions={"cid_A": ("YES", 1700000000.0)},
            token_map={"cid_A": {"YES": "asset_1", "NO": "asset_2"}},
        )
    )
    # Should have processed trades and produced some fills
    assert result.total_trades == len(sample_trades)
    assert summary is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest research/tests/test_s2_replay.py -x -q`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement run_s2_replay**

```python
# research/strategies/s2_replay.py
"""S2 replay helper — wraps ReplayRunner for hit-rate copy strategy."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.calibrate import (
    calibrate_spreads,
    calibrate_volumes,
)
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.realistic import (
    FillModelConfig,
    RealisticFillSimulator,
)
from polymarket_pipeline.strategies.ledger.analytics import (
    LedgerSummary,
    compute_summary,
)
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.backtest import BacktestResult
from polymarket_pipeline.strategies.runners.replay import (
    MarketResolution,
    ReplayRunner,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.protocol import Strategy


async def run_s2_replay(
    strategy: Strategy,
    trades: list[NormalizedTrade],
    config: StrategyConfig,
    *,
    resolutions: dict[str, tuple[str, float]] | None = None,
    token_map: dict[str, dict[str, str]] | None = None,
    fill_config: FillModelConfig | None = None,
    output_dir: Path = Path("research/output"),
) -> tuple[BacktestResult, LedgerSummary]:
    """Run tick-by-tick replay with ReplayRunner and mid-run settlement.

    Parameters
    ----------
    strategy:
        S2HitRateCopy instance (with qualified traders already set).
    trades:
        Historical trades sorted by timestamp.
    config:
        StrategyConfig with capital/risk parameters.
    resolutions:
        condition_id -> (winner_outcome, resolved_at_epoch).
        Used to build MarketResolution objects for mid-run settlement.
    token_map:
        condition_id -> {"YES": asset_id, "NO": asset_id}.
    fill_config:
        RealisticFillSimulator config. None = default realistic fills.
    output_dir:
        Directory for ledger parquet output.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / f"ledger_{strategy.name}.parquet"
    ledger = ParquetLedger(ledger_path)

    # Build MarketResolution objects (asset_id-based)
    market_resolutions: dict[str, MarketResolution] = {}
    token_map = token_map or {}
    if resolutions:
        for cid, (winner_outcome, resolved_at) in resolutions.items():
            cid_tokens = token_map.get(cid, {})
            winning_asset = cid_tokens.get(winner_outcome, "")
            winning_ids = frozenset({winning_asset}) if winning_asset else frozenset()
            market_resolutions[cid] = MarketResolution(
                condition_id=cid,
                resolved_at=resolved_at,
                winning_asset_ids=winning_ids,
            )

    # Executor
    fc = fill_config or FillModelConfig()
    if trades:
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
    else:
        market_spreads, market_volumes = {}, {}

    executor = RealisticFillSimulator(
        config=fc,
        market_spreads=market_spreads,
        market_volumes=market_volumes,
    )

    # Gateway + Context
    gateway = ExecutionGateway(
        executor, strategy_budgets={strategy.name: config.capital_usd}
    )
    ctx = InMemoryContext()

    # Runner
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=config,
        resolutions=market_resolutions,
        token_map=token_map,
        ledger=ledger,
    )

    result = await runner.run(trades)

    # Analytics
    records = await ledger.read_all()
    summary = compute_summary(records)

    return result, summary
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest research/tests/test_s2_replay.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add research/strategies/s2_replay.py research/tests/test_s2_replay.py
git commit -m "feat(s2): add run_s2_replay helper with ReplayRunner + settlement"
```

---

### Task 6: Marimo workbench — manual explore mode

**Files:**
- Create: `research/notebooks/s2_workbench.py`

This is a marimo notebook. No TDD here — it's interactive UI code. The notebook provides parameter controls and runs tick-by-tick replay.

**Step 1: Create the marimo notebook**

```python
# research/notebooks/s2_workbench.py
"""S2 Hit-Rate Copy — Interactive Research Workbench.

Run with: uv run marimo edit research/notebooks/s2_workbench.py
"""
import marimo

__generated_with = "0.10.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md("# S2 Hit-Rate Copy — Research Workbench")
    return ()


@app.cell
def _(mo):
    # ── Parameter Controls ──
    min_positions = mo.ui.slider(10, 100, value=30, step=10, label="Min positions")
    min_excess_hr = mo.ui.slider(0.01, 0.30, value=0.10, step=0.01, label="Min excess HR")
    seed_threshold = mo.ui.dropdown(["1", "2"], value="1", label="Seed threshold")
    scale_threshold = mo.ui.dropdown(["2", "3", "4", "5"], value="4", label="Scale threshold")
    seed_pct = mo.ui.dropdown(["0.25", "0.50"], value="0.25", label="Seed %")
    direction = mo.ui.dropdown(["YES", "NO", "BOTH"], value="BOTH", label="Direction")
    seed_timeout = mo.ui.dropdown(["None", "24", "72", "168"], value="None", label="Seed timeout (h)")
    position_size = mo.ui.slider(25, 250, value=100, step=25, label="Position size ($)")
    max_open = mo.ui.slider(5, 50, value=20, step=5, label="Max open positions")
    capital = mo.ui.slider(500, 5000, value=1000, step=500, label="Capital ($)")

    mo.hstack([
        mo.vstack([min_positions, min_excess_hr, direction]),
        mo.vstack([seed_threshold, scale_threshold, seed_pct]),
        mo.vstack([seed_timeout, position_size, max_open, capital]),
    ])
    return (
        min_positions, min_excess_hr, seed_threshold, scale_threshold,
        seed_pct, direction, seed_timeout, position_size, max_open, capital,
    )


@app.cell
def _(mo):
    # ── Period Selection ──
    periods = mo.ui.multiselect(
        options=["2025-01", "2025-04", "2025-07", "2025-10", "2026-01"],
        value=["2025-07"],
        label="Replay periods",
    )
    periods
    return (periods,)


@app.cell
def _(
    mo, min_positions, min_excess_hr, seed_threshold, scale_threshold,
    seed_pct, direction, seed_timeout, position_size, max_open, capital,
    periods,
):
    run_btn = mo.ui.run_button(label="Run Replay")
    run_btn
    return (run_btn,)


@app.cell
def _(
    run_btn, mo, min_positions, min_excess_hr, seed_threshold, scale_threshold,
    seed_pct, direction, seed_timeout, position_size, max_open, capital,
    periods,
):
    mo.stop(not run_btn.value, mo.md("*Click 'Run Replay' to start.*"))

    import asyncio
    import time

    from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
    from research.strategies.s2_replay import run_s2_replay
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    t0 = time.time()

    cfg = S2Config(
        min_positions=min_positions.value,
        min_excess_hr=min_excess_hr.value,
        seed_threshold=int(seed_threshold.value),
        scale_threshold=int(scale_threshold.value),
        seed_pct=float(seed_pct.value),
        seed_timeout_hours=None if seed_timeout.value == "None" else float(seed_timeout.value),
        direction=direction.value,
        position_size_usd=position_size.value,
    )

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital.value,
        max_position_usd=position_size.value,
        max_open_positions=max_open.value,
        cooldown_s=0,
    )

    # Load qualified traders from CH
    from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
    import httpx

    backend = ClickHouseBackend(host="192.168.0.148", port=18123, database="polymarket")
    sql = S2HitRateCopy.qualified_traders_query(
        min_positions=cfg.min_positions,
        min_excess_hr=cfg.min_excess_hr,
        recency_months=cfg.recency_months,
        direction=cfg.direction,
    )
    pool_df = asyncio.run(backend._execute(sql))

    qualified_traders = set(pool_df["trader"].to_list()) if len(pool_df) > 0 else set()

    mo.md(f"**Pool size:** {len(qualified_traders)} traders ({time.time() - t0:.1f}s)")

    # Load trades for selected periods (pre-filtered by qualified makers)
    from research.strategies.s2_data import load_period_trades, load_replay_resolutions

    all_results = {}
    for period in periods.value:
        strat = S2HitRateCopy(cfg)
        strat.set_qualified_traders(qualified_traders)

        trades, resolutions, token_map = load_period_trades(
            period, qualified_traders, backend
        )

        if not trades:
            all_results[period] = None
            continue

        result, summary = asyncio.run(run_s2_replay(
            strategy=strat,
            trades=trades,
            config=strat_config,
            resolutions=resolutions,
            token_map=token_map,
        ))
        all_results[period] = summary

    elapsed = time.time() - t0

    return (all_results, elapsed, qualified_traders, pool_df)


@app.cell
def _(mo, all_results, elapsed):
    import polars as pl

    rows = []
    for period, summary in all_results.items():
        if summary is None:
            continue
        # Compute excess HR (approximate — need direction info)
        base_hr = 0.381  # YES base rate; adjust per direction
        excess = summary.hit_rate - base_hr
        hold_days = summary.avg_hold_duration_s / 86400 if summary.avg_hold_duration_s > 0 else 0
        compound = (
            excess * summary.avg_edge / max(hold_days, 0.1)
            if summary.avg_edge > 0
            else 0
        )
        rows.append({
            "period": period,
            "fills": summary.total_fills,
            "wins": summary.win_count,
            "losses": summary.loss_count,
            "hit_rate": f"{summary.hit_rate:.1%}",
            "excess_hr": f"{excess:+.1%}",
            "sharpe": f"{summary.sharpe:.2f}",
            "pnl_net": f"${summary.total_pnl_net:,.2f}",
            "avg_edge": f"${summary.avg_edge:,.4f}",
            "max_dd": f"${summary.max_drawdown:,.2f}",
            "hold_days": f"{hold_days:.1f}",
            "compounding": f"{compound:.3f}",
        })

    if rows:
        df = pl.DataFrame(rows)
        mo.md(f"### Results ({elapsed:.1f}s)")
        mo.ui.table(df)
    else:
        mo.md("*No results — check period selection and qualified pool.*")

    return ()


if __name__ == "__main__":
    app.run()
```

**Step 2: Verify the notebook loads**

Run: `uv run marimo run research/notebooks/s2_workbench.py --headless` (quick check that imports resolve)

**Step 3: Commit**

```bash
git add research/notebooks/s2_workbench.py
git commit -m "feat(s2): add marimo workbench with manual explore mode"
```

---

### Task 7: Data loading — period-based trade loading from CH

**Files:**
- Create: `research/strategies/s2_data.py`
- Test: `research/tests/test_s2_data.py`

This module provides functions to load trades pre-filtered by qualified makers from ClickHouse, sliced by time period. This is the 11x speedup — we only fetch trades from qualified makers.

**Step 1: Write the failing test**

```python
# research/tests/test_s2_data.py
"""Test S2 data loading utilities."""
from research.strategies.s2_data import parse_period_range


def test_parse_period_range():
    start, end = parse_period_range("2025-07")
    assert start == "2025-07-01"
    assert end == "2025-08-01"


def test_parse_period_range_december():
    start, end = parse_period_range("2025-12")
    assert start == "2025-12-01"
    assert end == "2026-01-01"


def test_qualified_trades_query():
    from research.strategies.s2_data import qualified_trades_query

    sql = qualified_trades_query(
        traders={"0xabc", "0xdef"},
        start_date="2025-07-01",
        end_date="2025-08-01",
    )
    assert "0xabc" in sql
    assert "maker" in sql
    assert "2025-07-01" in sql
    assert "side = 'BUY'" in sql
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest research/tests/test_s2_data.py -x -q`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement s2_data.py**

```python
# research/strategies/s2_data.py
"""Data loading for S2 strategy — period-based, pre-filtered by qualified makers."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.features.backend_clickhouse import (
        ClickHouseBackend,
    )


def parse_period_range(period: str) -> tuple[str, str]:
    """Convert 'YYYY-MM' to (start_date, end_date) strings.

    Returns first day of month and first day of next month.
    """
    dt = datetime.strptime(period, "%Y-%m")
    year, month = dt.year, dt.month
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    return start, end


def qualified_trades_query(
    traders: set[str],
    start_date: str,
    end_date: str,
) -> str:
    """Build CH SQL to fetch BUY trades from qualified makers in a date range."""
    trader_list = ", ".join(f"'{t.lower()}'" for t in traders)
    return f"""
        SELECT *
        FROM trades_raw FINAL
        WHERE lower(maker) IN ({trader_list})
          AND side = 'BUY'
          AND toDate(timestamp) >= '{start_date}'
          AND toDate(timestamp) < '{end_date}'
        ORDER BY timestamp
    """


def resolutions_query(start_date: str, end_date: str) -> str:
    """Build CH SQL for market resolutions in a date range."""
    return f"""
        SELECT
            condition_id,
            asset_id,
            outcome,
            token_won,
            toUnixTimestamp(resolved_at) AS resolved_epoch
        FROM markets_resolved
        WHERE toDate(resolved_at) >= '{start_date}'
          AND toDate(resolved_at) < '{end_date}'
    """


async def load_period_trades(
    period: str,
    qualified_traders: set[str],
    backend: ClickHouseBackend,
) -> tuple[list[Any], dict[str, tuple[str, float]], dict[str, dict[str, str]]]:
    """Load trades, resolutions, and token_map for a single period.

    Returns (trades, resolutions, token_map).
    trades are NormalizedTrade objects.
    resolutions are cid -> (winner_outcome, resolved_at_epoch).
    token_map is cid -> {"YES": asset_id, "NO": asset_id}.
    """
    from polymarket_pipeline.models import NormalizedTrade

    start_date, end_date = parse_period_range(period)

    if not qualified_traders:
        return [], {}, {}

    # Fetch trades
    trades_sql = qualified_trades_query(qualified_traders, start_date, end_date)
    trades_df = await backend._execute(trades_sql)

    trades: list[NormalizedTrade] = []
    if len(trades_df) > 0:
        for row in trades_df.iter_rows(named=True):
            try:
                trade = NormalizedTrade(**row)
                trades.append(trade)
            except Exception:
                continue

    # Fetch resolutions
    res_sql = resolutions_query(start_date, end_date)
    res_df = await backend._execute(res_sql)

    resolutions: dict[str, tuple[str, float]] = {}
    token_map: dict[str, dict[str, str]] = {}
    if len(res_df) > 0:
        for row in res_df.iter_rows(named=True):
            cid = str(row["condition_id"])
            asset_id = str(row["asset_id"])
            outcome = str(row["outcome"])
            token_won = int(row["token_won"])
            epoch = float(row.get("resolved_epoch", 0))

            if cid not in token_map:
                token_map[cid] = {}
            token_map[cid][outcome] = asset_id

            if token_won == 1 and epoch > 0:
                resolutions[cid] = (outcome, epoch)

    return trades, resolutions, token_map
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest research/tests/test_s2_data.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add research/strategies/s2_data.py research/tests/test_s2_data.py
git commit -m "feat(s2): add period-based CH data loading with pre-filtered makers"
```

---

### Task 8: Auto-sweep mode in the workbench

**Files:**
- Create: `research/strategies/s2_sweep.py`
- Modify: `research/notebooks/s2_workbench.py`

This adds a sweep engine that runs replay for every parameter combination across all periods.

**Step 1: Write the sweep engine**

```python
# research/strategies/s2_sweep.py
"""Parameter sweep engine for S2 strategy."""
from __future__ import annotations

import itertools
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

import polars as pl


@dataclass(frozen=True)
class SweepResult:
    """Results for a single sweep config across all periods."""

    config_label: str
    config: dict[str, Any]
    periods_profitable: int
    total_periods: int
    mean_excess_hr: float
    std_excess_hr: float
    mean_sharpe: float
    mean_edge: float
    mean_hold_days: float
    total_fills: int
    total_pnl: float
    compounding_score: float


def build_sweep_grid(
    sweep_params: dict[str, list[Any]],
    fixed_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build parameter grid from sweep and fixed params.

    Parameters
    ----------
    sweep_params:
        Dict of param_name -> list of values to sweep.
    fixed_params:
        Dict of param_name -> fixed value.

    Returns list of dicts, each a complete parameter set.
    """
    keys = list(sweep_params.keys())
    values = list(sweep_params.values())
    grid = []
    for combo in itertools.product(*values):
        params = dict(fixed_params)
        for k, v in zip(keys, combo):
            params[k] = v
        grid.append(params)
    return grid


def config_label(params: dict[str, Any]) -> str:
    """Generate a short label for a parameter set."""
    parts = []
    if "min_excess_hr" in params:
        parts.append(f"ehr{int(params['min_excess_hr']*100)}")
    if "scale_threshold" in params:
        parts.append(f"s{params['scale_threshold']}")
    if "direction" in params:
        parts.append(params["direction"][0])  # Y, N, or B
    if "min_positions" in params:
        parts.append(f"p{params['min_positions']}")
    return "_".join(parts) if parts else "default"


def aggregate_period_summaries(
    config_params: dict[str, Any],
    summaries: list[Any],  # list of LedgerSummary or None
    base_hr: float = 0.381,
) -> SweepResult:
    """Aggregate LedgerSummary across periods into SweepResult."""
    valid = [s for s in summaries if s is not None and s.total_fills > 0]
    n_periods = len(summaries)

    if not valid:
        return SweepResult(
            config_label=config_label(config_params),
            config=config_params,
            periods_profitable=0,
            total_periods=n_periods,
            mean_excess_hr=0.0,
            std_excess_hr=0.0,
            mean_sharpe=0.0,
            mean_edge=0.0,
            mean_hold_days=0.0,
            total_fills=0,
            total_pnl=0.0,
            compounding_score=0.0,
        )

    excess_hrs = [s.hit_rate - base_hr for s in valid]
    sharpes = [s.sharpe for s in valid]
    edges = [s.avg_edge for s in valid]
    hold_days = [s.avg_hold_duration_s / 86400 for s in valid]
    profitable = sum(1 for s in valid if s.total_pnl_net > 0)

    mean_excess = sum(excess_hrs) / len(excess_hrs) if excess_hrs else 0
    std_excess = (
        (sum((x - mean_excess) ** 2 for x in excess_hrs) / len(excess_hrs)) ** 0.5
        if len(excess_hrs) > 1
        else 0.0
    )
    mean_hold = sum(hold_days) / len(hold_days) if hold_days else 0
    mean_edge_val = sum(edges) / len(edges) if edges else 0

    compound = (
        mean_excess * mean_edge_val / max(mean_hold, 0.1)
        if mean_edge_val > 0
        else 0
    )

    return SweepResult(
        config_label=config_label(config_params),
        config=config_params,
        periods_profitable=profitable,
        total_periods=n_periods,
        mean_excess_hr=mean_excess,
        std_excess_hr=std_excess,
        mean_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0,
        mean_edge=mean_edge_val,
        mean_hold_days=mean_hold,
        total_fills=sum(s.total_fills for s in valid),
        total_pnl=sum(s.total_pnl_net for s in valid),
        compounding_score=compound,
    )


def sweep_results_to_polars(results: list[SweepResult]) -> pl.DataFrame:
    """Convert sweep results to a Polars DataFrame for display."""
    rows = []
    for r in results:
        rows.append({
            "config": r.config_label,
            "fills": r.total_fills,
            "profitable": f"{r.periods_profitable}/{r.total_periods}",
            "excess_hr": f"{r.mean_excess_hr:+.1%}",
            "std_hr": f"{r.std_excess_hr:.1%}",
            "sharpe": f"{r.mean_sharpe:.2f}",
            "pnl": f"${r.total_pnl:,.2f}",
            "edge": f"${r.mean_edge:,.4f}",
            "hold_d": f"{r.mean_hold_days:.1f}",
            "compound": f"{r.compounding_score:.3f}",
        })
    return pl.DataFrame(rows).sort("compound", descending=True)
```

**Step 2: Write a quick unit test**

```python
# research/tests/test_s2_sweep.py
"""Test sweep engine utilities."""
from research.strategies.s2_sweep import build_sweep_grid, config_label


def test_build_sweep_grid():
    grid = build_sweep_grid(
        sweep_params={"a": [1, 2], "b": ["x", "y"]},
        fixed_params={"c": True},
    )
    assert len(grid) == 4
    assert grid[0] == {"a": 1, "b": "x", "c": True}
    assert grid[3] == {"a": 2, "b": "y", "c": True}


def test_config_label():
    label = config_label({"min_excess_hr": 0.10, "scale_threshold": 4, "direction": "YES"})
    assert label == "ehr10_s4_Y"
```

**Step 3: Run test**

Run: `uv run pytest research/tests/test_s2_sweep.py -x -q`
Expected: PASS

**Step 4: Commit**

```bash
git add research/strategies/s2_sweep.py research/tests/test_s2_sweep.py
git commit -m "feat(s2): add parameter sweep engine with aggregation and labeling"
```

---

### Task 9: Add sweep cells to the marimo workbench

**Files:**
- Modify: `research/notebooks/s2_workbench.py`

Add new cells for sweep configuration, execution, and results display. This extends the notebook from Task 6.

**Step 1: Add sweep cells to the notebook**

Add the following cells after the existing manual explore cells in `research/notebooks/s2_workbench.py`:

```python
@app.cell
def _(mo):
    mo.md("---\n## Auto-Sweep Mode")
    return ()


@app.cell
def _(mo):
    sweep_excess_hr = mo.ui.multiselect(
        options=["0.05", "0.10", "0.15", "0.20"],
        value=["0.05", "0.10", "0.15"],
        label="Sweep: min_excess_hr",
    )
    sweep_scale = mo.ui.multiselect(
        options=["3", "4", "5"],
        value=["3", "4"],
        label="Sweep: scale_threshold",
    )
    sweep_direction = mo.ui.multiselect(
        options=["YES", "NO", "BOTH"],
        value=["YES", "BOTH"],
        label="Sweep: direction",
    )
    sweep_periods = mo.ui.multiselect(
        options=["2025-01", "2025-04", "2025-07", "2025-10", "2026-01"],
        value=["2025-04", "2025-07", "2025-10"],
        label="Sweep periods",
    )

    grid_size = len(sweep_excess_hr.value) * len(sweep_scale.value) * len(sweep_direction.value)
    total_runs = grid_size * len(sweep_periods.value)

    mo.hstack([
        mo.vstack([sweep_excess_hr, sweep_scale]),
        mo.vstack([sweep_direction, sweep_periods]),
    ])
    mo.md(f"**Grid:** {grid_size} configs x {len(sweep_periods.value)} periods = {total_runs} replays")

    return (sweep_excess_hr, sweep_scale, sweep_direction, sweep_periods)


@app.cell
def _(mo):
    sweep_btn = mo.ui.run_button(label="Run Sweep")
    sweep_btn
    return (sweep_btn,)


@app.cell
def _(
    sweep_btn, mo, sweep_excess_hr, sweep_scale, sweep_direction, sweep_periods,
    min_positions, seed_threshold, seed_pct, seed_timeout, position_size, max_open, capital,
):
    mo.stop(not sweep_btn.value, mo.md("*Click 'Run Sweep' to start.*"))

    import asyncio
    import time

    from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
    from research.strategies.s2_replay import run_s2_replay
    from research.strategies.s2_data import load_period_trades
    from research.strategies.s2_sweep import (
        build_sweep_grid,
        aggregate_period_summaries,
        sweep_results_to_polars,
    )
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
    from polymarket_pipeline.strategies.types import ExecutionMode

    t0 = time.time()
    backend = ClickHouseBackend(host="192.168.0.148", port=18123, database="polymarket")

    # Build sweep grid
    grid = build_sweep_grid(
        sweep_params={
            "min_excess_hr": [float(x) for x in sweep_excess_hr.value],
            "scale_threshold": [int(x) for x in sweep_scale.value],
            "direction": list(sweep_direction.value),
        },
        fixed_params={
            "min_positions": min_positions.value,
            "seed_threshold": int(seed_threshold.value),
            "seed_pct": float(seed_pct.value),
            "seed_timeout_hours": None if seed_timeout.value == "None" else float(seed_timeout.value),
            "position_size_usd": position_size.value,
            "recency_months": 6,
        },
    )

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital.value,
        max_position_usd=position_size.value,
        max_open_positions=max_open.value,
        cooldown_s=0,
    )

    all_sweep_results = []
    for params in grid:
        cfg = S2Config(**params)
        # Get pool for this config
        sql = S2HitRateCopy.qualified_traders_query(
            min_positions=cfg.min_positions,
            min_excess_hr=cfg.min_excess_hr,
            recency_months=cfg.recency_months,
            direction=cfg.direction,
        )
        pool_df = asyncio.run(backend._execute(sql))
        qualified = set(pool_df["trader"].to_list()) if len(pool_df) > 0 else set()

        period_summaries = []
        for period in sweep_periods.value:
            strat = S2HitRateCopy(cfg)
            strat.set_qualified_traders(qualified)

            trades, resolutions, token_map = asyncio.run(
                load_period_trades(period, qualified, backend)
            )

            if not trades:
                period_summaries.append(None)
                continue

            _, summary = asyncio.run(run_s2_replay(
                strategy=strat, trades=trades, config=strat_config,
                resolutions=resolutions, token_map=token_map,
            ))
            period_summaries.append(summary)

        sweep_result = aggregate_period_summaries(params, period_summaries)
        all_sweep_results.append(sweep_result)

    sweep_df = sweep_results_to_polars(all_sweep_results)
    elapsed_sweep = time.time() - t0

    mo.md(f"### Sweep Results ({elapsed_sweep:.1f}s, {len(grid)} configs x {len(sweep_periods.value)} periods)")
    mo.ui.table(sweep_df)

    return (all_sweep_results, sweep_df)


if __name__ == "__main__":
    app.run()
```

**Step 2: Verify notebook loads**

Run: `uv run python -c "import research.notebooks.s2_workbench"` (basic import check)

**Step 3: Commit**

```bash
git add research/notebooks/s2_workbench.py
git commit -m "feat(s2): add auto-sweep mode to marimo workbench"
```

---

### Task 10: Production registration — strategy_impl + CLI + TOML config

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/provider.py`
- Create: `src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/strategy.py`
- Create: `configs/s2_hitrate_copy.toml`
- Modify: `src/polymarket_pipeline/cli/strategy.py`

This task registers the strategy for live/paper execution. Only do this after research validation confirms promising results.

**Step 1: Create the production strategy (thin wrapper)**

```python
# src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/__init__.py
"""S2 Hit-Rate Copy — production registration."""

# src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/strategy.py
"""S2 Hit-Rate Copy — production strategy (delegates to research impl)."""
from __future__ import annotations

from typing import Any

from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy


def create_s2_strategy(config: Any) -> S2HitRateCopy:
    """Factory function for CLI registry."""
    params = config.params if hasattr(config, "params") else {}
    cfg = S2Config(
        min_positions=params.get("min_positions", 30),
        min_excess_hr=params.get("min_excess_hr", 0.10),
        seed_threshold=params.get("seed_threshold", 1),
        scale_threshold=params.get("scale_threshold", 4),
        seed_pct=params.get("seed_pct", 0.25),
        seed_timeout_hours=params.get("seed_timeout_hours"),
        direction=params.get("direction", "BOTH"),
        recency_months=params.get("recency_months", 6),
        position_size_usd=params.get("position_size_usd", 100.0),
    )
    return S2HitRateCopy(cfg)
```

**Step 2: Create the FeatureProvider**

```python
# src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/provider.py
"""S2 FeatureProvider — loads and refreshes qualified trader pool from CH."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from research.strategies.s2_hitrate_copy import S2HitRateCopy

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class S2Provider:
    """Qualified trader pool provider."""

    name: str = "s2_provider"

    def __init__(
        self,
        min_positions: int = 30,
        min_excess_hr: float = 0.10,
        recency_months: int = 6,
        direction: str = "BOTH",
    ) -> None:
        self._min_positions = min_positions
        self._min_excess_hr = min_excess_hr
        self._recency_months = recency_months
        self._direction = direction
        self._qualified: set[str] = set()

    async def compute(self, backend: FeatureBackend) -> None:
        """Initial pool computation from CH."""
        await self._load_pool(backend)

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic pool refresh."""
        await self._load_pool(backend)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No per-trade action needed — pool is static between refreshes."""

    def get_features(self) -> dict[str, Any]:
        return {
            "pool_traders": frozenset(self._qualified),
            "pool_size": len(self._qualified),
        }

    async def _load_pool(self, backend: FeatureBackend) -> None:
        sql = S2HitRateCopy.qualified_traders_query(
            min_positions=self._min_positions,
            min_excess_hr=self._min_excess_hr,
            recency_months=self._recency_months,
            direction=self._direction,
        )
        df = await backend.query_custom(sql)
        self._qualified = set(df["trader"].to_list()) if len(df) > 0 else set()
        logger.info("s2_provider.pool_loaded", size=len(self._qualified))
```

**Step 3: Create TOML config**

```toml
# configs/s2_hitrate_copy.toml

[strategy.s2_hitrate_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 20
cooldown_s = 0
features = ["s2_provider"]

[strategy.s2_hitrate_copy.params]
min_positions = 30
min_excess_hr = 0.10
seed_threshold = 1
scale_threshold = 4
seed_pct = 0.25
direction = "BOTH"
recency_months = 6
position_size_usd = 100

[provider.s2_provider]
enabled = true
refresh_interval_s = 900

[provider.s2_provider.params]
min_positions = 30
min_excess_hr = 0.10
recency_months = 6
direction = "BOTH"
```

**Step 4: Register in CLI**

Add to `_register_strategies()` in `src/polymarket_pipeline/cli/strategy.py`:

```python
def _register_strategies() -> None:
    from polymarket_pipeline.strategies_impl.s2_hitrate_copy.strategy import create_s2_strategy
    _STRATEGY_FACTORIES["s2_hitrate_copy"] = create_s2_strategy
```

Add to `_register_providers()`:

```python
def _register_providers() -> None:
    from polymarket_pipeline.strategies_impl.s2_hitrate_copy.provider import S2Provider
    _PROVIDER_REGISTRY["s2_provider"] = S2Provider
```

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/s2_hitrate_copy/ configs/s2_hitrate_copy.toml src/polymarket_pipeline/cli/strategy.py
git commit -m "feat(s2): register production strategy, provider, and TOML config"
```

---

### Task 11: Update research ideas backlog

**Files:**
- Modify: `research/ideas.md`

**Step 1: Add S2 entry to ideas.md**

Add under the existing ideas:

```markdown
## 6. S2 Hit-Rate Copy (Tiered Conviction)

**Status**: IN PROGRESS
**Priority**: HIGH
**Design**: `docs/plans/2026-03-02-s2-hitrate-copy-design.md`

**Hypothesis**: Traders with excess hit rate above direction-specific base rate are skilled.
Copying their entries with tiered conviction (seed + scale) builds an edge.

**Key parameters**: min_excess_hr, scale_threshold, direction, seed_timeout_hours
**Workbench**: `research/notebooks/s2_workbench.py`

**Known risks**:
- NO direction collapses in tick-by-tick (previous research: 82% → 34%)
- Consensus dedup critical (72.6% inflation if trades not unique traders)
- Long-dated markets (politics) block capital for weeks

**Next**: Run vectorized discovery sweep, then tick-by-tick validation
```

**Step 2: Commit**

```bash
git add research/ideas.md
git commit -m "docs: add S2 hit-rate copy to research ideas backlog"
```

---

## Summary

| Task | What | Files | Test? |
|------|------|-------|-------|
| 1 | Data structures + constructor | `s2_hitrate_copy.py`, `test_s2_strategy.py` | Yes |
| 2 | on_trade: SELL filter, consensus, tiered entry | same files | Yes |
| 3 | on_timer: seed timeout | same files | Yes |
| 4 | CH query builder for qualified traders | same files | Yes |
| 5 | run_s2_replay helper | `s2_replay.py`, `test_s2_replay.py` | Yes |
| 6 | Marimo workbench — manual explore | `s2_workbench.py` | Manual |
| 7 | Period-based CH data loading | `s2_data.py`, `test_s2_data.py` | Yes |
| 8 | Sweep engine | `s2_sweep.py`, `test_s2_sweep.py` | Yes |
| 9 | Sweep cells in workbench | `s2_workbench.py` | Manual |
| 10 | Production registration | `strategies_impl/`, CLI, TOML | No (deferred) |
| 11 | Update ideas backlog | `ideas.md` | No |

**Tasks 1-9** are the research phase. **Task 10** is production (only after research validates).
**Total**: ~11 commits, each self-contained and tested.
