# Pool Refresh Automation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically refresh the skilled trader pool when markets resolve, via CLOB WS resolution events piped through Kafka, with timer fallback.

**Architecture:** Extend `CLOBOrderbookIngestor` to forward `market_resolved`/`new_market` events to a `markets.events` Kafka topic. A FastStream subscriber updates PG and signals `LiveRunner.request_refresh()`, which triggers an out-of-cycle provider refresh. `SkilledTradersProvider.refresh()` re-queries CH derived views (not static DataFrames) and re-runs `filter_consistent_traders()`.

**Tech Stack:** FastStream/Kafka, ClickHouse (derived views), asyncpg (PG upserts), asyncio (debounce + event signaling), Polars, websockets.

**Design doc:** `docs/plans/2026-02-25-pool-refresh-automation-design.md`

---

### Task 1: Extend ClickHouseBackend with consistency-filter-compatible queries

The CH `trader_pnl_query()` doesn't produce `net_yes_tokens` or `wavg_yes_entry_price` — columns that `filter_consistent_traders()` requires. We need a new query that JOINs with `token_market_map` to compute YES-side breakdown, plus a simple resolved-markets query.

**Files:**
- Modify: `src/polymarket_pipeline/strategies/features/backend_clickhouse.py`
- Test: `tests/test_backend_clickhouse_queries.py` (create)

**Step 1: Write failing tests for the two new query builders**

```python
"""Tests for ClickHouseBackend extended query builders."""
from __future__ import annotations

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend


def test_consistency_pnl_query_no_filters() -> None:
    """Extended PnL query should include net_yes_tokens and wavg_yes_entry_price."""
    sql = ClickHouseBackend.consistency_pnl_query()
    assert "net_yes_tokens" in sql
    assert "wavg_yes_entry_price" in sql
    assert "token_market_map" in sql
    assert "trader_trade_agg FINAL" in sql
    assert "markets_resolved" in sql
    assert "WHERE" not in sql


def test_consistency_pnl_query_with_traders() -> None:
    """Extended PnL query with trader filter."""
    sql = ClickHouseBackend.consistency_pnl_query(traders=["0xabc", "0xdef"])
    assert "'0xabc'" in sql
    assert "'0xdef'" in sql
    assert "a.trader IN" in sql


def test_resolved_markets_query() -> None:
    """Resolved markets query should select condition_id + resolved_at."""
    sql = ClickHouseBackend.resolved_markets_query()
    assert "condition_id" in sql
    assert "resolved_at" in sql
    assert "markets_resolved" in sql
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backend_clickhouse_queries.py -x -q`
Expected: FAIL — `consistency_pnl_query` and `resolved_markets_query` don't exist yet.

**Step 3: Implement the two new static query builders**

Add to `src/polymarket_pipeline/strategies/features/backend_clickhouse.py` after the existing `trader_pnl_query` method (after line 141):

```python
    @staticmethod
    def consistency_pnl_query(traders: list[str] | None = None) -> str:
        """Build SQL for trader-market PnL with YES-side breakdown.

        Returns columns compatible with ``filter_consistent_traders()``:
        trader, condition_id, market_pnl, first_trade, net_yes_tokens,
        wavg_yes_entry_price.
        """
        where = ""
        if traders:
            ids = ", ".join(f"'{t}'" for t in traders)
            where = f"WHERE a.trader IN ({ids})"
        return f"""
            SELECT
                a.trader,
                a.condition_id,
                sum(a.net_tokens * if(mr.token_won, 1.0, 0.0)
                    + a.net_usd - a.total_fees) AS market_pnl,
                min(a.first_trade) AS first_trade,
                sum(if(tm.token_index = 0, a.net_tokens, 0)) AS net_yes_tokens,
                sum(if(tm.token_index = 0, a.price_x_vol, a.volume - a.price_x_vol))
                    / nullIf(sum(a.volume), 0) AS wavg_yes_entry_price
            FROM (SELECT * FROM trader_trade_agg FINAL) AS a
            INNER JOIN markets_resolved AS mr
                ON a.condition_id = mr.condition_id AND a.asset_id = mr.asset_id
            INNER JOIN token_market_map AS tm
                ON a.asset_id = tm.asset_id
            {where}
            GROUP BY a.trader, a.condition_id
        """

    @staticmethod
    def resolved_markets_query() -> str:
        """Build SQL for resolved markets (condition_id + resolved_at)."""
        return """
            SELECT DISTINCT condition_id, resolved_at
            FROM markets_resolved
        """
```

Also add convenience async methods after `query_trader_pnl` (after line 155):

```python
    async def query_consistency_pnl(
        self, traders: list[str] | None = None
    ) -> pl.DataFrame:
        """Query trader-market PnL with YES-side breakdown for consistency filter."""
        return await self._execute(self.consistency_pnl_query(traders=traders))

    async def query_resolved_markets(self) -> pl.DataFrame:
        """Query resolved markets (condition_id + resolved_at)."""
        return await self._execute(self.resolved_markets_query())
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backend_clickhouse_queries.py -x -q`
Expected: 3 passed.

**Step 5: Commit**

```bash
git add tests/test_backend_clickhouse_queries.py src/polymarket_pipeline/strategies/features/backend_clickhouse.py
git commit -m "feat: add consistency-filter-compatible CH query builders"
```

---

### Task 2: Add CH-backed refresh to SkilledTradersProvider

Currently `refresh()` just re-calls `compute()`, which uses static DataFrames in consistency mode. For live, we need `refresh()` to re-query CH derived views and re-run the filter.

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`
- Test: `tests/test_consistency_filter.py` (add tests)

**Step 1: Write failing test for CH-backed refresh**

Add to `tests/test_consistency_filter.py`:

```python
@pytest.mark.asyncio
async def test_provider_refresh_queries_ch_backend(
    resolved: pl.DataFrame, mvf: pl.DataFrame
) -> None:
    """refresh() should re-query CH backend and update skilled set."""
    from unittest.mock import AsyncMock

    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )

    # Start with consistency mode (static DataFrames)
    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 12,
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.30] * 12,
    })

    provider = SkilledTradersProvider(
        pnl_df=pnl,
        resolved_df=resolved,
        mvf_df=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
    )

    # Initial compute
    backend = AsyncMock()
    await provider.compute(backend)
    assert "0xGood" in provider.get_features()["skilled_traders"]

    # Mock a CH backend that has query_consistency_pnl, query_resolved_markets, query_mvf
    ch_backend = AsyncMock()
    # Return new data where 0xGood has a bad month → no longer qualifies
    bad_pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 10 + [-5.0, -5.0],
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.30] * 12,
    })
    ch_backend.query_consistency_pnl = AsyncMock(return_value=bad_pnl)
    ch_backend.query_resolved_markets = AsyncMock(return_value=resolved)
    ch_backend.query_mvf = AsyncMock(return_value=mvf)

    await provider.refresh(ch_backend)
    assert "0xGood" not in provider.get_features()["skilled_traders"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consistency_filter.py::test_provider_refresh_queries_ch_backend -x -q`
Expected: FAIL — `refresh()` doesn't use backend's CH-specific methods.

**Step 3: Implement CH-backed refresh**

Modify `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`. Replace the `refresh` method (line 150-152):

```python
    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the skilled set.

        If the backend has CH-specific query methods (``query_consistency_pnl``,
        ``query_resolved_markets``, ``query_mvf``), use them to fetch fresh data
        from ClickHouse derived views. Otherwise fall back to ``compute()``.
        """
        if (
            self._use_consistency
            and hasattr(backend, "query_consistency_pnl")
            and hasattr(backend, "query_resolved_markets")
            and hasattr(backend, "query_mvf")
        ):
            await self._refresh_from_ch(backend)
        else:
            await self.compute(backend)

    async def _refresh_from_ch(self, backend: Any) -> None:
        """Refresh using ClickHouse derived views."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        pnl = await backend.query_consistency_pnl()
        resolved = await backend.query_resolved_markets()
        mvf = await backend.query_mvf()

        self._skilled = filter_consistent_traders(
            pnl=pnl,
            resolved=resolved,
            mvf=mvf,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )
        logger.info("skilled_traders.ch_refresh", count=len(self._skilled))
```

Add `Any` to the imports at the top of the file (it's already in `TYPE_CHECKING` block — add it to the runtime imports too by changing line 7):

```python
from typing import TYPE_CHECKING, Any
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consistency_filter.py -x -q`
Expected: All 9 tests pass (8 existing + 1 new).

**Step 5: Run full regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py tests/test_consistency_filter.py
git commit -m "feat: add CH-backed refresh to SkilledTradersProvider"
```

---

### Task 3: Add `request_refresh()` to LiveRunner

The existing `_refresh_loop` sleeps for `refresh_interval_s` then refreshes all providers. We need a way to trigger an immediate out-of-cycle refresh.

**Files:**
- Modify: `src/polymarket_pipeline/strategies/runners/live.py`
- Test: `tests/test_live_runner_refresh.py` (create)

**Step 1: Write failing tests**

```python
"""Tests for LiveRunner.request_refresh() out-of-cycle refresh."""
from __future__ import annotations

import asyncio

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.runners.live import LiveRunner


class FakeProvider:
    """Minimal provider that tracks refresh calls."""

    name = "fake"

    def __init__(self) -> None:
        self.refresh_count = 0
        self.features: dict[str, object] = {"test_key": "v0"}

    async def compute(self, backend: object) -> None:
        pass

    async def on_trade(self, trade: object) -> None:
        pass

    async def refresh(self, backend: object) -> None:
        self.refresh_count += 1
        self.features = {"test_key": f"v{self.refresh_count}"}

    def get_features(self) -> dict[str, object]:
        return self.features


class FakeGateway:
    async def submit(self, intent: object) -> object:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_request_refresh_triggers_immediate_refresh() -> None:
    """request_refresh() should cause _refresh_loop to run immediately."""
    provider = FakeProvider()
    ctx = InMemoryContext()
    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=FakeGateway(),  # type: ignore[arg-type]
        ctx=ctx,
        backend=object(),  # type: ignore[arg-type]
        refresh_interval_s=3600.0,  # very long — would never fire naturally
    )

    await runner.start_background_loops()

    # Request an immediate refresh
    runner.request_refresh()

    # Give the refresh loop time to process the event
    await asyncio.sleep(0.1)

    assert provider.refresh_count >= 1
    assert ctx._features.get("test_key") == "v1"

    await runner.stop()


@pytest.mark.asyncio
async def test_refresh_loop_still_works_on_timer() -> None:
    """Normal timer-based refresh should still function."""
    provider = FakeProvider()
    ctx = InMemoryContext()
    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=FakeGateway(),  # type: ignore[arg-type]
        ctx=ctx,
        backend=object(),  # type: ignore[arg-type]
        refresh_interval_s=0.05,  # very short for test
    )

    await runner.start_background_loops()
    await asyncio.sleep(0.15)

    assert provider.refresh_count >= 1

    await runner.stop()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_live_runner_refresh.py -x -q`
Expected: FAIL — `request_refresh` doesn't exist.

**Step 3: Implement `request_refresh()` and modify `_refresh_loop`**

Modify `src/polymarket_pipeline/strategies/runners/live.py`:

In `__init__` (after line 84, `self._last_trade_times`), add:

```python
        self._refresh_event = asyncio.Event()
```

Add the `request_refresh` method (after `handle_orderbook`, around line 189):

```python
    def request_refresh(self) -> None:
        """Signal the refresh loop to run immediately (non-blocking)."""
        self._refresh_event.set()
```

Replace `_refresh_loop` (lines 222-230) with:

```python
    async def _refresh_loop(self) -> None:
        """Periodic provider refresh, with support for on-demand triggers."""
        while True:
            # Wait for either the timer or an explicit refresh request
            try:
                async with asyncio.timeout(self.refresh_interval_s):
                    await self._refresh_event.wait()
            except TimeoutError:
                pass  # timer expired — normal periodic refresh
            self._refresh_event.clear()

            for provider in self.providers:
                logger.info("provider.refresh_start", provider=provider.name)
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
                logger.info("provider.refresh_done", provider=provider.name)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_live_runner_refresh.py -x -q`
Expected: 2 passed.

**Step 5: Run full regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/runners/live.py tests/test_live_runner_refresh.py
git commit -m "feat: add request_refresh() to LiveRunner for on-demand pool refresh"
```

---

### Task 4: Extend CLOBOrderbookIngestor to forward market events

The ingestor currently drops `market_resolved` and `new_market` events. Extend it to:
1. Set `custom_feature_enabled: true` in the WS subscription.
2. Route these events to a `markets.events` Kafka topic.

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/clob_orderbook.py`
- Test: `tests/test_clob_orderbook_market_events.py` (create)

**Step 1: Write failing tests**

```python
"""Tests for CLOBOrderbookIngestor market event forwarding."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, call

import pytest

from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor


@pytest.fixture
def broker() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def ingestor(broker: AsyncMock) -> CLOBOrderbookIngestor:
    return CLOBOrderbookIngestor(
        broker=broker,
        topic="orderbooks.raw",
        markets_events_topic="markets.events",
    )


@pytest.mark.asyncio
async def test_market_resolved_published_to_events_topic(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """market_resolved events should be published to markets.events topic."""
    msg = json.dumps({
        "event_type": "market_resolved",
        "condition_id": "0xabc123",
        "resolution": "YES",
        "timestamp": 1700000000.0,
    })
    await ingestor._handle_message(msg)

    # Should have published to markets.events
    broker.publish.assert_called_once()
    published = broker.publish.call_args
    payload = json.loads(published.kwargs["message"])
    assert payload["type"] == "market_resolved"
    assert payload["condition_id"] == "0xabc123"
    assert published.kwargs["topic"] == "markets.events"


@pytest.mark.asyncio
async def test_new_market_published_to_events_topic(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """new_market events should be published to markets.events topic."""
    msg = json.dumps({
        "event_type": "new_market",
        "condition_id": "0xdef456",
        "question": "Will BTC hit 100k?",
        "timestamp": 1700000000.0,
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    payload = json.loads(broker.publish.call_args.kwargs["message"])
    assert payload["type"] == "new_market"
    assert payload["condition_id"] == "0xdef456"


@pytest.mark.asyncio
async def test_price_change_still_works(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """price_change events should still go to orderbooks.raw."""
    msg = json.dumps({
        "event_type": "price_change",
        "asset_id": "tok123",
        "best_bid": 0.55,
        "best_ask": 0.57,
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    assert broker.publish.call_args.kwargs["topic"] == "orderbooks.raw"


@pytest.mark.asyncio
async def test_subscription_includes_custom_feature(
    ingestor: CLOBOrderbookIngestor,
) -> None:
    """WS subscription payload should include custom_feature_enabled."""
    payload = ingestor._subscription_payload()
    assert payload["custom_feature_enabled"] is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clob_orderbook_market_events.py -x -q`
Expected: FAIL — `markets_events_topic` param and `_subscription_payload` don't exist.

**Step 3: Implement the changes**

Modify `src/polymarket_pipeline/live/ingestors/clob_orderbook.py`:

Update `__init__` (lines 31-42) to accept `markets_events_topic`:

```python
    def __init__(
        self,
        broker: Any,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        topic: str = "orderbooks.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        markets_events_topic: str = "markets.events",
    ) -> None:
        super().__init__(broker=broker, topic=topic, status_topic=status_topic)
        self._ws_url = ws_url
        self._token_map = token_market_map or {}
        self._markets_events_topic = markets_events_topic
        self._update_count: int = 0
        self._market_event_count: int = 0
```

Add `_subscription_payload` method (before `_handle_message`):

```python
    def _subscription_payload(self) -> dict[str, Any]:
        """Build the WS subscription message."""
        return {
            "type": "market",
            "markets": [],
            "assets_ids": [],
            "custom_feature_enabled": True,
        }
```

Update `_handle_message` (lines 44-67) to route market events:

```python
    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WS message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("clob_orderbook.invalid_json", raw=raw[:100])
            return

        events: list[dict[str, Any]]
        if isinstance(msg, list):
            events = msg
        elif isinstance(msg, dict):
            events = [msg]
        else:
            return

        for event in events:
            event_type = event.get("event_type")
            if event_type == "price_change":
                await self._process_price_change(event)
            elif event_type in ("market_resolved", "new_market"):
                await self._process_market_event(event)
```

Add `_process_market_event` method (after `_process_price_change`):

```python
    async def _process_market_event(self, event: dict[str, Any]) -> None:
        """Forward market_resolved / new_market events to the events topic."""
        condition_id = event.get("condition_id", "")
        payload = {
            "type": event["event_type"],
            "condition_id": condition_id,
            "payload": event,
            "timestamp": event.get("timestamp", time.time()),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode() if condition_id else b"unknown",
            source="clob_orderbook",
        )
        self._market_event_count += 1
```

Update `_heartbeat_fields` to include market event count:

```python
    def _heartbeat_fields(self) -> dict[str, Any]:
        """CLOB-specific heartbeat fields."""
        return {
            "update_count": self._update_count,
            "market_event_count": self._market_event_count,
        }
```

Update the `run` method's subscribe block (lines 131-138) to use the new method:

```python
                    subscribe = json.dumps(self._subscription_payload())
                    await ws.send(subscribe)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_clob_orderbook_market_events.py -x -q`
Expected: 4 passed.

**Step 5: Run full regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/clob_orderbook.py tests/test_clob_orderbook_market_events.py
git commit -m "feat: forward market_resolved/new_market events from CLOB WS to Kafka"
```

---

### Task 5: Add `markets_events_topic` to Settings

**Files:**
- Modify: `src/polymarket_pipeline/live/settings.py`

**Step 1: Add the setting**

Add after the `clob_orderbook_ws_url` line (line 73) in `src/polymarket_pipeline/live/settings.py`:

```python
    clob_markets_events_topic: str = "markets.events"
```

**Step 2: Run full regression to verify no breakage**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/settings.py
git commit -m "feat: add clob_markets_events_topic setting"
```

---

### Task 6: Add `markets.events` subscriber with debounce + PG upsert

A FastStream subscriber that processes market events, updates PG, and signals a debounced refresh.

**Files:**
- Create: `src/polymarket_pipeline/live/consumers/market_events.py`
- Test: `tests/test_market_events_consumer.py` (create)

**Step 1: Write failing tests**

```python
"""Tests for market events consumer (debounce + PG upsert)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer


@pytest.fixture
def consumer() -> MarketEventsConsumer:
    runner = MagicMock()
    runner.request_refresh = MagicMock()
    return MarketEventsConsumer(
        pg_pool=None,  # no PG in unit tests
        runner=runner,
        debounce_s=0.05,  # fast debounce for tests
    )


@pytest.mark.asyncio
async def test_market_resolved_triggers_refresh(consumer: MarketEventsConsumer) -> None:
    """A market_resolved event should eventually trigger request_refresh."""
    event = {
        "type": "market_resolved",
        "condition_id": "0xabc",
        "payload": {"resolution": "YES"},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))

    # Wait for debounce
    await asyncio.sleep(0.1)
    consumer._runner.request_refresh.assert_called()


@pytest.mark.asyncio
async def test_debounce_batches_events(consumer: MarketEventsConsumer) -> None:
    """Multiple rapid events should produce a single refresh call."""
    for i in range(5):
        event = {
            "type": "market_resolved",
            "condition_id": f"0x{i:04x}",
            "payload": {},
            "timestamp": 1700000000.0 + i,
        }
        await consumer.handle(json.dumps(event))

    await asyncio.sleep(0.1)
    # Should have called request_refresh only once (debounced)
    assert consumer._runner.request_refresh.call_count == 1


@pytest.mark.asyncio
async def test_new_market_does_not_trigger_refresh(consumer: MarketEventsConsumer) -> None:
    """new_market events should not trigger pool refresh (no PnL impact)."""
    event = {
        "type": "new_market",
        "condition_id": "0xnew",
        "payload": {},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)
    consumer._runner.request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json_ignored(consumer: MarketEventsConsumer) -> None:
    """Invalid JSON should be silently ignored."""
    await consumer.handle("not json")
    # No exception raised
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_market_events_consumer.py -x -q`
Expected: FAIL — `MarketEventsConsumer` doesn't exist.

**Step 3: Implement the consumer**

First check that the consumers directory exists:

```bash
ls src/polymarket_pipeline/live/consumers/
```

Create `src/polymarket_pipeline/live/consumers/market_events.py`:

```python
"""Consumer for markets.events Kafka topic — resolution tracking + pool refresh."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class MarketEventsConsumer:
    """Processes market events and triggers debounced pool refresh.

    Parameters
    ----------
    pg_pool:
        asyncpg connection pool for PG upserts. ``None`` skips PG updates.
    runner:
        LiveRunner instance — ``request_refresh()`` is called after debounce.
    debounce_s:
        Seconds to wait after last event before triggering refresh.
    """

    def __init__(
        self,
        pg_pool: Any | None,
        runner: Any,
        debounce_s: float = 5.0,
    ) -> None:
        self._pg_pool = pg_pool
        self._runner = runner
        self._debounce_s = debounce_s
        self._debounce_task: asyncio.Task[None] | None = None
        self._pending_resolutions: int = 0

    async def handle(self, raw: str) -> None:
        """Process a single market event message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("market_events.invalid_json", raw=raw[:100])
            return

        event_type = data.get("type")
        condition_id = data.get("condition_id", "")

        if event_type == "market_resolved":
            await self._handle_resolved(condition_id, data.get("payload", {}))
        elif event_type == "new_market":
            await self._handle_new_market(condition_id, data.get("payload", {}))

    async def _handle_resolved(self, condition_id: str, payload: dict[str, Any]) -> None:
        """Handle a market resolution — update PG + schedule refresh."""
        log.info("market_events.resolved", condition_id=condition_id)

        # Update PG if pool is available
        if self._pg_pool is not None:
            await self._upsert_resolution(condition_id, payload)

        # Schedule debounced refresh
        self._pending_resolutions += 1
        self._schedule_refresh()

    async def _handle_new_market(self, condition_id: str, payload: dict[str, Any]) -> None:
        """Handle a new market — log only (no PnL impact, no refresh needed)."""
        log.info("market_events.new_market", condition_id=condition_id)

    async def _upsert_resolution(
        self, condition_id: str, payload: dict[str, Any]
    ) -> None:
        """Update the markets table in PostgreSQL with resolution data."""
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE markets
                    SET resolution_value = 1,
                        winner_outcome = $1,
                        resolved_at = NOW()
                    WHERE condition_id = $2
                    """,
                    payload.get("resolution", ""),
                    condition_id,
                )
        except Exception:
            log.exception("market_events.pg_upsert_error", condition_id=condition_id)

    def _schedule_refresh(self) -> None:
        """Schedule a debounced refresh — resets timer on each call."""
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounced_refresh())

    async def _debounced_refresh(self) -> None:
        """Wait for debounce period, then trigger refresh."""
        await asyncio.sleep(self._debounce_s)
        count = self._pending_resolutions
        self._pending_resolutions = 0
        log.info("market_events.triggering_refresh", batched_resolutions=count)
        self._runner.request_refresh()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_market_events_consumer.py -x -q`
Expected: 4 passed.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/consumers/market_events.py tests/test_market_events_consumer.py
git commit -m "feat: add MarketEventsConsumer with debounced pool refresh"
```

---

### Task 7: Wire everything into `app.py`

Connect the `markets.events` subscriber and pass the `LiveRunner` + `MarketEventsConsumer` into the live pipeline app.

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py`
- Modify: `src/polymarket_pipeline/live/orchestrator.py` (pass `markets_events_topic` to ingestor)

**Step 1: Add the subscriber to app.py**

Add import near top of `app.py` (after line 14):

```python
from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer
```

Add a module-level variable (after `_ingestor_tasks` on line 71):

```python
_market_events_consumer: MarketEventsConsumer | None = None
```

Add subscriber after the `handle_status` function (after line 191):

```python
@broker.subscriber(settings.clob_markets_events_topic, group_id="market-events")
async def handle_market_event(msg: str) -> None:
    """Process market resolution and new market events."""
    if _market_events_consumer is not None:
        await _market_events_consumer.handle(msg)
```

**Step 2: Wire `MarketEventsConsumer` creation in `on_startup`**

The consumer needs a `LiveRunner` reference. Since the strategy runner is typically started separately (via `pm-strategy`), the consumer should be initialized with a `runner=None` fallback that just logs. For now, initialize it with the PG pool and a no-op runner. The strategy CLI will set the real runner.

In `on_startup` (after line 100 where `_quality_checker` is set), add:

```python
    global _market_events_consumer
    _market_events_consumer = MarketEventsConsumer(
        pg_pool=pg_pool,
        runner=_NullRunner(),
        debounce_s=5.0,
    )
```

Add a simple null runner class near the top of `app.py` (after the imports):

```python
class _NullRunner:
    """Stub runner for when no strategy runner is attached."""

    def request_refresh(self) -> None:
        log.debug("market_events.no_runner_attached")
```

**Step 3: Pass `markets_events_topic` to CLOBOrderbookIngestor in orchestrator**

Read the orchestrator's `create_ingestors` to find where `CLOBOrderbookIngestor` is instantiated, then add the new param.

In `src/polymarket_pipeline/live/orchestrator.py`, find the `CLOBOrderbookIngestor(...)` call and add `markets_events_topic=settings.clob_markets_events_topic`.

**Step 4: Run full regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/app.py src/polymarket_pipeline/live/orchestrator.py
git commit -m "feat: wire markets.events subscriber into live pipeline"
```

---

### Task 8: Wire `LiveRunner` to `MarketEventsConsumer` in strategy CLI

When `pm-strategy run` starts, it creates a `LiveRunner`. After initializing it, attach the runner to the `MarketEventsConsumer` so resolution events trigger pool refresh.

**Files:**
- Modify: `src/polymarket_pipeline/cli/strategy.py`

**Step 1: Update the `_run()` function in the `run` command**

In `src/polymarket_pipeline/cli/strategy.py`, inside the `_run()` async function (around line 231-295), after `await runner.initialize()` and `await runner.start_background_loops()`, add a subscriber for `markets.events`:

```python
        # Subscribe to market events for pool refresh
        from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer

        market_consumer = MarketEventsConsumer(
            pg_pool=None,  # PG updates handled by main pipeline
            runner=runner,
            debounce_s=5.0,
        )

        @broker.subscriber(
            settings.clob_markets_events_topic
            if hasattr(settings, "clob_markets_events_topic")
            else "markets.events",
            group_id="strategy-market-events",
        )
        async def handle_market_event(msg: str) -> None:
            await market_consumer.handle(msg)
```

Note: This uses a separate consumer group (`strategy-market-events`) from the main pipeline's `market-events` group, so both independently process the events.

**Step 2: Run full regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_market_size_classifier.py --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass.

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/cli/strategy.py
git commit -m "feat: wire market events subscriber into strategy CLI for pool refresh"
```
