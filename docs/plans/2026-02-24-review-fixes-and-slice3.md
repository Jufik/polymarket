# Review Fixes + Slice 3 ("Self-Protecting") Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix remaining code review items (C-REC-2 strategy dedup, C-ING-1 pending.signal wiring, C-STR-1 quality stubs), then complete Slice 3 gaps: first-check delay, auto_protect test coverage, and pipeline→API quality bridge.

**Architecture:** Strategy-level dedup lives in `LiveRunner` (cross-cutting, not per-strategy). Pending signal is opt-in per strategy via config. Quality checker gets a PostgreSQL connection for real metadata checks. Quality state publishes to `pipeline.quality` topic for the API.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, structlog, FastStream/Kafka, asyncpg, pytest-asyncio.

---

## Task 1: Strategy-level trade dedup in LiveRunner (C-REC-2)

**Files:**
- Modify: `src/polymarket_pipeline/strategies/runners/live.py`
- Modify: `tests/test_runner_live.py`

**Step 1: Write failing tests**

Add to `tests/test_runner_live.py`:

```python
async def test_duplicate_trade_ignored(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Same trade_id dispatched twice — second should be silently dropped."""
    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )
    trade = _trade(ts=1000)
    await runner._handle_trade(trade)
    await runner._handle_trade(trade)  # duplicate
    assert len(strategy.trades_seen) == 1


async def test_stale_trade_ignored(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Trade with published_at older than max_trade_age_s should be dropped."""
    import time

    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        max_trade_age_s=30.0,
    )
    old_trade = _trade(ts=int(time.time()) - 120)  # 2 min old
    await runner._handle_trade(old_trade)
    assert len(strategy.trades_seen) == 0


async def test_fresh_trade_accepted(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Trade within max_trade_age_s passes through."""
    import time

    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        max_trade_age_s=300.0,
    )
    recent_trade = _trade(ts=int(time.time()) - 5)  # 5s old
    await runner._handle_trade(recent_trade)
    assert len(strategy.trades_seen) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner_live.py::test_duplicate_trade_ignored tests/test_runner_live.py::test_stale_trade_ignored tests/test_runner_live.py::test_fresh_trade_accepted -x -q`
Expected: FAIL (no dedup_ttl_s / max_trade_age_s parameters)

**Step 3: Implement dedup + age filter in LiveRunner**

In `src/polymarket_pipeline/strategies/runners/live.py`, add:

1. Import `TradeDedup` at top:
```python
from polymarket_pipeline.live.dedup import TradeDedup
```

2. Add parameters to `__init__`:
```python
    def __init__(
        self,
        ...
        hot_path_warn_ms: float = 5.0,
        dedup_ttl_s: float = 600.0,       # 10 min max
        max_trade_age_s: float = 120.0,    # ignore trades older than 2 min
    ) -> None:
        ...
        self._dedup = TradeDedup(ttl_s=dedup_ttl_s)
        self._max_trade_age_s = max_trade_age_s
        self._drops_dedup: int = 0
        self._drops_stale: int = 0
```

3. Add guard at top of `_handle_trade`:
```python
    async def _handle_trade(self, trade: NormalizedTrade) -> None:
        # Age filter — drop stale trades (Kafka lag, reconnection replays)
        age = time.time() - trade.published_at
        if self._max_trade_age_s > 0 and age > self._max_trade_age_s:
            self._drops_stale += 1
            return

        # Trade-level dedup — drop duplicate trade_ids within TTL window
        if self._dedup.is_duplicate(trade.trade_id):
            self._drops_dedup += 1
            return

        # ... rest of existing _handle_trade code unchanged
```

4. Include drop counters in `stop()` log:
```python
        logger.info(
            "live_runner.stopped",
            trades_processed=self._trades_processed,
            intents_submitted=self._intents_submitted,
            drops_dedup=self._drops_dedup,
            drops_stale=self._drops_stale,
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner_live.py -x -q`
Expected: ALL PASS

**Step 5: Run full unit test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/live.py tests/test_runner_live.py
git commit -m "feat: add trade-level dedup (10min TTL) and stale trade filter to LiveRunner (C-REC-2)"
```

---

## Task 2: Wire pending.signal to strategy runner (C-ING-1)

**Files:**
- Modify: `src/polymarket_pipeline/strategies/config.py`
- Modify: `src/polymarket_pipeline/cli/strategy.py`
- Modify: `tests/test_strategy_config.py`
- Modify: `tests/test_cli_strategy.py`

**Step 1: Add `subscribe_pending` to StrategyConfig**

In `src/polymarket_pipeline/strategies/config.py`, add field to `StrategyConfig`:

```python
@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a single strategy."""

    enabled: bool
    mode: ExecutionMode
    capital_usd: float
    max_position_usd: float
    max_open_positions: int
    cooldown_s: int
    params: dict[str, Any] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    subscribe_pending: bool = False  # opt-in to pending.signal topic
```

In `load_strategy_configs`, extract the field:

```python
        cfg = StrategyConfig(
            ...
            features=features,
            subscribe_pending=bool(section.get("subscribe_pending", False)),
        )
```

**Step 2: Write a test for the new config field**

Add to `tests/test_strategy_config.py`:

```python
def test_subscribe_pending_defaults_false(tmp_path: Path) -> None:
    toml_content = """
[strategy.basic]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 10
cooldown_s = 300
"""
    p = tmp_path / "cfg.toml"
    p.write_text(toml_content)
    configs = load_strategy_configs(p)
    assert configs["basic"].subscribe_pending is False


def test_subscribe_pending_true(tmp_path: Path) -> None:
    toml_content = """
[strategy.early_bird]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 10
cooldown_s = 300
subscribe_pending = true
"""
    p = tmp_path / "cfg.toml"
    p.write_text(toml_content)
    configs = load_strategy_configs(p)
    assert configs["early_bird"].subscribe_pending is True
```

**Step 3: Run config tests**

Run: `uv run pytest tests/test_strategy_config.py -x -q`
Expected: PASS

**Step 4: Wire pending.signal subscriber in cli/strategy.py**

In `src/polymarket_pipeline/cli/strategy.py`, inside the `_run()` async function, after the `handle_trade` subscriber, add:

```python
        # Check if any strategy opts in to pending.signal
        _pending_strategies = [
            (s, c) for s, c in runner.strategies if c.subscribe_pending
        ]

        if _pending_strategies:

            @broker.subscriber("pending.signal", group_id="strategy-runner")
            async def handle_pending(msg: str) -> None:
                import json

                from polymarket_pipeline.models import NormalizedTrade

                data = json.loads(msg)
                trade = NormalizedTrade(**data)
                # Only dispatch to strategies that opted in
                for strategy, config in _pending_strategies:
                    intents = await strategy.on_trade(trade, runner.ctx)
                    if intents:
                        for intent in intents:
                            await runner.gateway.submit(intent)
```

**Step 5: Run CLI tests**

Run: `uv run pytest tests/test_cli_strategy.py -x -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/config.py src/polymarket_pipeline/cli/strategy.py tests/test_strategy_config.py tests/test_cli_strategy.py
git commit -m "feat: wire pending.signal topic with opt-in per strategy (C-ING-1)"
```

---

## Task 3: Implement real quality checker stubs (C-STR-1)

**Files:**
- Modify: `src/polymarket_pipeline/live/quality/checker.py`
- Modify: `src/polymarket_pipeline/live/settings.py`
- Modify: `tests/test_quality_checker.py`

**Step 1: Write failing tests for real checks**

Add to `tests/test_quality_checker.py`:

```python
def test_metadata_freshness_fails_when_stale(mock_ch: Any) -> None:
    """Metadata check fails if token_map has no entries for recent trades."""
    settings = _settings()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    # Simulate: PG has 0 token_map entries updated in last hour
    checker._pg_pool = _MockPGPool(token_map_fresh_count=0, total_token_map=1000)
    result = checker.check_metadata_freshness()
    assert result.ok is False


def test_metadata_freshness_passes_when_fresh(mock_ch: Any) -> None:
    settings = _settings()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    checker._pg_pool = _MockPGPool(token_map_fresh_count=500, total_token_map=1000)
    result = checker.check_metadata_freshness()
    assert result.ok is True


def test_resolved_completeness_fails_low_coverage(mock_ch: Any) -> None:
    settings = _settings()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    # CH says 50 resolved markets have trades, but PG says 100 are resolved
    mock_ch._resolved_with_trades = 50
    checker._pg_pool = _MockPGPool(resolved_markets=100)
    result = checker.check_resolved_completeness()
    assert result.ok is False


def test_resolved_completeness_passes(mock_ch: Any) -> None:
    settings = _settings()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    mock_ch._resolved_with_trades = 95
    checker._pg_pool = _MockPGPool(resolved_markets=100)
    result = checker.check_resolved_completeness()
    assert result.ok is True
```

Note: These tests need `_MockPGPool` and extended mock_ch. The exact mock shape will depend on how the real methods query PG. We'll define these once the implementation is clear.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quality_checker.py::test_metadata_freshness_fails_when_stale -x -q`
Expected: FAIL (no _pg_pool attribute, stub still returns ok=True)

**Step 3: Add pg_pool to QualityChecker**

In `src/polymarket_pipeline/live/quality/checker.py`:

```python
class QualityChecker:
    def __init__(self, settings: Settings, clickhouse: Any, pg_pool: Any = None) -> None:
        self._settings = settings
        self._ch = clickhouse
        self._pg_pool = pg_pool
        self._state = ReadinessState(degraded_grace_s=settings.degraded_grace_s)
        self._heartbeats: dict[str, float] = {}
```

**Step 4: Implement check_metadata_freshness**

Replace the stub:

```python
    def check_metadata_freshness(self) -> CheckResult:
        """Check that token_market_map was synced recently.

        Verifies the metadata sync pipeline (Gamma API -> PostgreSQL) ran within
        the configured threshold. If no PG pool is available, returns ok (degraded
        monitoring is better than false alarms during bootstrap).
        """
        if self._pg_pool is None:
            return CheckResult(ok=True, reason="no PG pool — skipping metadata check")
        try:
            # Check if any token_map entries were updated in the last 2 hours
            # This is a synchronous helper — called from run_all_checks via to_thread
            import asyncio

            count = asyncio.get_event_loop().run_until_complete(
                self._pg_pool.fetchval(
                    "SELECT count(*) FROM token_market_map "
                    "WHERE updated_at > NOW() - INTERVAL '2 hours'"
                )
            )
            if count == 0:
                return CheckResult(ok=False, reason="token_market_map has no entries updated in 2h")
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Metadata query error: {e}")
```

Wait — `check_metadata_freshness` is called synchronously from `run_all_checks` (it's NOT run via `asyncio.to_thread`). Let me re-read how it's called:

```python
    async def run_all_checks(self) -> dict[str, CheckResult]:
        # Non-CH checks run inline (no I/O blocking)
        source_liveness = self.check_source_liveness()
        metadata_freshness = self.check_metadata_freshness()
        resolved_completeness = self.check_resolved_completeness()
```

It's called synchronously. But PG queries are async. Two options:
1. Make these async and gather them with CH checks
2. Keep them sync and use a sync PG query approach

Best approach: make them async and move them into the gather block. This requires changing `run_all_checks` to await them.

**Revised Step 4: Make metadata checks async**

In `checker.py`, change signatures and `run_all_checks`:

```python
    async def check_metadata_freshness(self) -> CheckResult:
        """Check that token_market_map was synced recently."""
        if self._pg_pool is None:
            return CheckResult(ok=True, reason="no PG pool — skipping metadata check")
        try:
            count = await self._pg_pool.fetchval(
                "SELECT count(*) FROM token_market_map "
                "WHERE updated_at > NOW() - INTERVAL '2 hours'"
            )
            if count == 0:
                return CheckResult(ok=False, reason="token_market_map has no entries updated in 2h")
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Metadata query error: {e}")

    async def check_resolved_completeness(self) -> CheckResult:
        """Check that resolved markets in PG have trade coverage in ClickHouse."""
        if self._pg_pool is None:
            return CheckResult(ok=True, reason="no PG pool — skipping resolved check")
        try:
            pg_count = await self._pg_pool.fetchval(
                "SELECT count(*) FROM markets WHERE status = 'resolved'"
            )
            if pg_count == 0:
                return CheckResult(ok=True, reason="No resolved markets in PG")
            ch_result = self._ch.query(
                "SELECT uniqExact(condition_id) AS cnt FROM trades_raw "
                "WHERE condition_id IN ("
                "  SELECT condition_id FROM postgresql("
                f"    '{self._settings.pg_dsn}', 'markets'"
                "  ) WHERE status = 'resolved'"
                ")"
            )
            ch_count = ch_result[0]["cnt"] if ch_result else 0
            ratio = ch_count / pg_count
            if ratio < 0.90:
                return CheckResult(
                    ok=False,
                    reason=f"Resolved coverage {ratio:.0%} ({ch_count}/{pg_count})",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Resolved query error: {e}")

    async def run_all_checks(self) -> dict[str, CheckResult]:
        """Run all health checks and update readiness state."""
        source_liveness = self.check_source_liveness()

        # I/O checks run concurrently
        (
            volume_reconciliation,
            dedup_sanity,
            metadata_freshness,
            resolved_completeness,
        ) = await asyncio.gather(
            asyncio.to_thread(self.check_volume_reconciliation),
            asyncio.to_thread(self.check_dedup_sanity),
            self.check_metadata_freshness(),
            self.check_resolved_completeness(),
        )

        results = {
            "source_liveness": source_liveness,
            "volume_reconciliation": volume_reconciliation,
            "metadata_freshness": metadata_freshness,
            "dedup_sanity": dedup_sanity,
            "resolved_completeness": resolved_completeness,
        }
        self._state.update(results)
        log.info(
            "quality.check_complete",
            state=self._state.current,
            failures=self._state.failures,
        )
        return results
```

**Step 5: Update tests with async mocks**

Update the test mocks to support the async interface. The `_MockPGPool` needs `async fetchval()`:

```python
class _MockPGPool:
    def __init__(self, *, token_map_fresh_count: int = 500, resolved_markets: int = 0) -> None:
        self._token_fresh = token_map_fresh_count
        self._resolved = resolved_markets

    async def fetchval(self, query: str) -> int:
        if "token_market_map" in query:
            return self._token_fresh
        if "markets" in query and "resolved" in query:
            return self._resolved
        return 0
```

**Step 6: Run tests**

Run: `uv run pytest tests/test_quality_checker.py -x -q`
Expected: ALL PASS

**Step 7: Wire pg_pool into QualityChecker in app.py**

In `src/polymarket_pipeline/live/app.py`, inside `on_startup`, after creating the `QualityChecker`:

```python
    # Connect PG pool for quality checks (metadata freshness, resolved completeness)
    if settings.pg_dsn:
        import asyncpg

        pg_pool = await asyncpg.create_pool(dsn=settings.pg_dsn, min_size=1, max_size=2)
    else:
        pg_pool = None
    _quality_checker = QualityChecker(settings=settings, clickhouse=ch, pg_pool=pg_pool)
```

**Step 8: Run full unit test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 9: Commit**

```bash
git add src/polymarket_pipeline/live/quality/checker.py src/polymarket_pipeline/live/app.py tests/test_quality_checker.py
git commit -m "feat: implement real metadata freshness and resolved completeness checks (C-STR-1)"
```

---

## Task 4: Fix first quality check delay (15min → 60s)

**Files:**
- Modify: `src/polymarket_pipeline/live/orchestrator.py:179`
- Modify: `src/polymarket_pipeline/live/settings.py`
- Modify: `tests/test_quality_checker.py` (or a new test)

The current `periodic_quality_check` has a 15-minute initial delay before the first check runs. The pipeline is blind to quality issues for the entire first 15 minutes.

**Step 1: Add initial_check_delay_s setting**

In `src/polymarket_pipeline/live/settings.py`, add:

```python
    # Quality check timing
    quality_check_interval_s: int = 900        # 15 min between checks
    quality_initial_delay_s: int = 60           # 60s before first check
```

**Step 2: Use initial delay in periodic_quality_check**

In `src/polymarket_pipeline/live/orchestrator.py`, change line 179:

```python
async def periodic_quality_check(
    checker: QualityChecker,
    settings: Settings,
    protect_fn: Any,
) -> None:
    """Run quality checks periodically (independent of caught_up events)."""
    await asyncio.sleep(settings.quality_initial_delay_s)  # short initial delay
    while True:
        await checker.run_all_checks()
        if checker.state.current == PipelineState.RED:
            await protect_fn()
        await asyncio.sleep(settings.quality_check_interval_s)
```

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/orchestrator.py src/polymarket_pipeline/live/settings.py
git commit -m "fix: reduce first quality check delay from 15min to 60s"
```

---

## Task 5: Add test coverage for auto_protect

**Files:**
- Create: `tests/test_protection.py`

**Step 1: Write tests**

```python
"""Tests for auto_protect — the self-protection panic close trigger."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.quality.state import PipelineState
from polymarket_pipeline.live.settings import Settings


def _settings(**overrides: object) -> Settings:
    defaults = {"alchemy_ws_url": "wss://dummy", "pg_dsn": "postgresql://x@localhost/x"}
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_auto_protect_transitions_to_closing() -> None:
    """auto_protect sets state to CLOSING before doing anything else."""
    from polymarket_pipeline.live.protection import auto_protect

    settings = _settings()
    mock_ch = MagicMock()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    # Force state to RED
    checker.state._current = PipelineState.RED

    with patch("polymarket_pipeline.live.protection.asyncpg") as mock_pg:
        mock_pool = AsyncMock()
        mock_pg.create_pool = AsyncMock(return_value=mock_pool)
        mock_pool.close = AsyncMock()
        with patch("polymarket_pipeline.live.protection.ClobClient") as mock_clob_cls:
            mock_clob = AsyncMock()
            mock_clob_cls.return_value = mock_clob
            with patch("polymarket_pipeline.live.protection.PositionTracker") as mock_tracker_cls:
                mock_tracker = AsyncMock()
                mock_tracker.initialize = AsyncMock()
                mock_tracker_cls.return_value = mock_tracker
                with patch("polymarket_pipeline.live.protection.panic_close_all") as mock_panic:
                    mock_panic.return_value = []
                    await auto_protect(checker, settings)

    assert checker.state.current == PipelineState.SAFE_STOP


@pytest.mark.asyncio
async def test_auto_protect_skips_if_already_closing() -> None:
    """auto_protect returns immediately if state is CLOSING or SAFE_STOP."""
    from polymarket_pipeline.live.protection import auto_protect

    settings = _settings()
    mock_ch = MagicMock()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    checker.state.set_safe_stop()

    # Should return without doing anything — no imports, no PG connect
    await auto_protect(checker, settings)
    assert checker.state.current == PipelineState.SAFE_STOP
```

**Step 2: Run tests**

Run: `uv run pytest tests/test_protection.py -x -q`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_protection.py
git commit -m "test: add unit tests for auto_protect protection flow"
```

---

## Task 6: Add ingestor task supervision (C-ORC-1)

**Files:**
- Modify: `src/polymarket_pipeline/live/orchestrator.py`
- Modify: `src/polymarket_pipeline/live/app.py`

Currently, if an ingestor task crashes after exhausting internal retries, `asyncio.create_task` silently swallows the exception. The quality checker will eventually detect stale heartbeats (up to 30s + check interval), but there's no direct crash detection.

**Step 1: Add task supervisor function**

In `src/polymarket_pipeline/live/orchestrator.py`, add:

```python
async def supervise_tasks(
    tasks: list[asyncio.Task[Any]],
    checker: QualityChecker | None,
) -> None:
    """Watch ingestor tasks and log if any crash unexpectedly.

    Does NOT restart them — the quality checker will detect stale heartbeats
    and trigger auto-protect. This just provides immediate crash visibility.
    """
    while tasks:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            tasks.remove(task)
            if task.cancelled():
                log.info("task.cancelled", task_name=task.get_name())
            elif exc := task.exception():
                log.error(
                    "task.crashed",
                    task_name=task.get_name(),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            else:
                log.info("task.completed", task_name=task.get_name())
```

**Step 2: Wire supervisor into app.py on_startup**

In `src/polymarket_pipeline/live/app.py`, after `_ingestor_tasks.extend(create_ingestors(...))`, add:

```python
    from polymarket_pipeline.live.orchestrator import supervise_tasks

    _ingestor_tasks.append(
        asyncio.create_task(supervise_tasks(list(_ingestor_tasks), _quality_checker))
    )
```

Note: pass a copy of the list (`list(_ingestor_tasks)`) since we're about to append the supervisor task itself.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/orchestrator.py src/polymarket_pipeline/live/app.py
git commit -m "feat: add ingestor task supervisor for crash visibility (C-ORC-1)"
```

---

## Task 7: Pipeline → API quality bridge via Redpanda topic

**Files:**
- Modify: `src/polymarket_pipeline/live/quality/checker.py`
- Modify: `src/polymarket_pipeline/live/orchestrator.py`
- Modify: `src/polymarket_pipeline/api/app.py` (if it exists)

The API process needs to know pipeline quality state. Currently there's no bridge. The pipeline publishes state to `pipeline.status`, but only as ad-hoc events. We need a structured `pipeline.quality` topic.

**Step 1: Publish quality state after every check**

In `src/polymarket_pipeline/live/quality/checker.py`, add method:

```python
    def to_quality_message(self) -> dict[str, Any]:
        """Serialize current quality state for the pipeline.quality topic."""
        return {
            "event": "quality_state",
            "state": self._state.current.value,
            "failures": self._state.failures,
            "degraded_since": self._state.degraded_since,
            "time_until_red": self._state.time_until_red,
            "heartbeats": {k: v for k, v in self._heartbeats.items()},
            "ts": time.time(),
        }
```

**Step 2: Publish quality message after run_all_checks**

In `run_all_checks`, after `self._state.update(results)`, add:

```python
        self._last_quality_message = self.to_quality_message()
```

And add a property:
```python
    @property
    def last_quality_message(self) -> dict[str, Any] | None:
        return getattr(self, "_last_quality_message", None)
```

**Step 3: Publish to pipeline.quality in orchestrator**

In `src/polymarket_pipeline/live/orchestrator.py`, modify `periodic_quality_check`:

```python
async def periodic_quality_check(
    checker: QualityChecker,
    settings: Settings,
    protect_fn: Any,
    broker: Any = None,
) -> None:
    """Run quality checks periodically (independent of caught_up events)."""
    await asyncio.sleep(settings.quality_initial_delay_s)
    while True:
        await checker.run_all_checks()

        # Publish quality state for API consumption
        if broker is not None and checker.last_quality_message is not None:
            await safe_publish(
                broker,
                message=json.dumps(checker.last_quality_message),
                topic="pipeline.quality",
                key=b"quality",
                source="quality_checker",
            )

        if checker.state.current == PipelineState.RED:
            await protect_fn()
        await asyncio.sleep(settings.quality_check_interval_s)
```

**Step 4: Pass broker to periodic_quality_check in app.py**

In `src/polymarket_pipeline/live/app.py`, update the call:

```python
    _ingestor_tasks.append(
        asyncio.create_task(periodic_quality_check(_quality_checker, settings, _protect, broker))
    )
```

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/quality/checker.py src/polymarket_pipeline/live/orchestrator.py src/polymarket_pipeline/live/app.py
git commit -m "feat: publish quality state to pipeline.quality topic for API consumption"
```

---

## Task 8: Run full validation

**Step 1: Run all unit tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 2: Run type checker**

Run: `uv run mypy --strict src/`
Expected: PASS (or known existing issues only)

**Step 3: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: PASS

**Step 4: Fix any failures, then commit**

```bash
git add -A && git commit -m "fix: resolve any regressions from review fixes and Slice 3 work"
```

---

## Summary of Changes

| Task | Item | What |
|------|------|------|
| 1 | C-REC-2 | Trade-level dedup (10min TTL) + stale trade filter (>120s) in LiveRunner |
| 2 | C-ING-1 | `subscribe_pending` config flag, wire `pending.signal` subscriber in strategy CLI |
| 3 | C-STR-1 | Real `check_metadata_freshness` + `check_resolved_completeness` with PG queries |
| 4 | Slice 3 | Reduce first quality check delay from 15min to 60s |
| 5 | Slice 3 | Test coverage for `auto_protect` |
| 6 | C-ORC-1 | Ingestor task supervision (crash logging) |
| 7 | Slice 3 | Pipeline→API quality bridge via `pipeline.quality` Redpanda topic |
| 8 | — | Full validation (tests + mypy + ruff) |
