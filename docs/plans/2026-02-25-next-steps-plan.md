# Next Steps Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining gaps between current code and production-ready paper trading: test coverage for budget enforcement, wire all config params, add CLOB orderbook query, and create a price validation script.

**Architecture:** Five independent tasks that can be executed in any order. Tasks 1 and 2 are already implemented and just need verification. Tasks 3–5 add new code.

**Tech Stack:** Python 3.11+, pytest, httpx, Polars, Typer, TOML config

**NOTE:** The user will handle all git commits manually. Do NOT commit in any task.

---

### Task 1: Verify Market Size Classifier (already implemented)

All classifier files are modified and tests pass (9/9). This task is verification only.

**Files:**
- Verify: `src/polymarket_pipeline/strategies_impl/market_size/classifier.py`
- Verify: `src/polymarket_pipeline/strategies_impl/market_size/features.py`
- Verify: `src/polymarket_pipeline/strategies_impl/market_size/providers.py`
- Verify: `src/polymarket_pipeline/strategies_impl/market_size/config.py`
- Verify: `scripts/train_market_size_classifier.py`
- Verify: `scripts/validate_market_size_classifier.py`
- Verify: `tests/test_market_size_classifier.py`
- Verify: `models/market_size_xgb.joblib` (12MB, gitignored)

**Step 1: Run classifier tests**

Run: `uv run pytest tests/test_market_size_classifier.py -x -q`
Expected: 9 passed

**Step 2: Run full unit test suite to confirm no regressions**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass (572+)

**Step 3: Verify model artifact exists**

Run: `ls -la models/market_size_xgb.joblib`
Expected: ~12MB file present

No code changes needed. Move to next task.

---

### Task 2: Verify GradedPoolProvider (already implemented)

Exploration confirmed all 3 filters (min_markets, min_longshot_yes_frac, max_no_fraction) are already implemented and tested. This task is verification only.

**Files:**
- Verify: `src/polymarket_pipeline/strategies_impl/proportional_copy/providers.py`
- Verify: `tests/test_strategy_proportional_copy.py`

**Step 1: Run proportional copy tests**

Run: `uv run pytest tests/test_strategy_proportional_copy.py -x -q`
Expected: All pass (includes `test_graded_pool_filters_by_longshot_yes_fraction`, `test_graded_pool_excludes_high_no_fraction`, `test_graded_pool_backward_compat_no_grading`)

No code changes needed. Move to next task.

---

### Task 3: Add ExecutionGateway Budget Tests

Budget enforcement code already exists in `gateway.py` (lines 88–127). This task adds the missing test suite.

**Files:**
- Create: `tests/test_gateway_budget.py`
- Verify: `src/polymarket_pipeline/strategies/execution/gateway.py` (no changes)

**Step 1: Write failing tests**

Create `tests/test_gateway_budget.py`:

```python
"""Tests for ExecutionGateway budget enforcement."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


def _make_intent(strategy: str = "s1", size_usd: float = 100.0) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id="0xabc",
        side="BUY",
        outcome="YES",
        size_usd=size_usd,
        urgency="immediate",
        max_price=0.5,
        reason="test",
        signal_time=1700000000.0,
    )


class _StubExecutor:
    """Executor that always fills at max_price."""

    async def execute(self, intent: TradeIntent) -> Fill:
        return Fill(
            intent_id="stub-1",
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=intent.max_price or 0.5,
            filled_size_usd=intent.size_usd,
            fee_usd=0.0,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )


@pytest.mark.asyncio
async def test_budget_allows_within_limit() -> None:
    """Intent within budget should be filled."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={"s1": 200.0},
    )
    fill = await gw.submit(_make_intent("s1", 100.0))
    assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_budget_rejects_over_limit() -> None:
    """Intent exceeding budget should be rejected."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={"s1": 50.0},
    )
    fill = await gw.submit(_make_intent("s1", 100.0))
    assert fill.status == FillStatus.REJECTED
    assert "Budget exhausted" in (fill.error or "")


@pytest.mark.asyncio
async def test_budget_tracks_cumulative_spending() -> None:
    """Second intent should be rejected after first consumes budget."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={"s1": 150.0},
    )
    fill1 = await gw.submit(_make_intent("s1", 100.0))
    assert fill1.status == FillStatus.FILLED

    fill2 = await gw.submit(_make_intent("s1", 100.0))
    assert fill2.status == FillStatus.REJECTED


@pytest.mark.asyncio
async def test_budget_independent_per_strategy() -> None:
    """Each strategy has its own budget."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={"s1": 50.0, "s2": 200.0},
    )
    fill_s1 = await gw.submit(_make_intent("s1", 100.0))
    assert fill_s1.status == FillStatus.REJECTED

    fill_s2 = await gw.submit(_make_intent("s2", 100.0))
    assert fill_s2.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_no_budget_means_uncapped() -> None:
    """Strategies without a budget entry are uncapped."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={"s1": 50.0},
    )
    fill = await gw.submit(_make_intent("s_uncapped", 99999.0))
    assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_empty_budgets_means_no_enforcement() -> None:
    """Empty strategy_budgets dict means no budget enforcement."""
    gw = ExecutionGateway(
        executor=_StubExecutor(),
        strategy_budgets={},
    )
    fill = await gw.submit(_make_intent("s1", 99999.0))
    assert fill.status == FillStatus.FILLED
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway_budget.py -x -v`
Expected: 6 passed (all tests should pass since the implementation already exists)

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass

---

### Task 4: Wire Final Strategy Configs

Three gaps to close:
1. TOML `pool_traders.params` missing `min_longshot_yes_frac` and `max_no_fraction`
2. TOML missing `consensus_copy` strategy section (S3)
3. CLI doesn't pass `strategy_budgets` to `ExecutionGateway`
4. TOML needs a `[budgets]` section

**Files:**
- Modify: `configs/strategies_example.toml`
- Modify: `src/polymarket_pipeline/cli/strategy.py` (line ~203)
- Modify: `src/polymarket_pipeline/strategies/config.py` (add `load_budgets()`)
- Test: `tests/test_strategy_config.py` (add budget loading test)

**Step 1: Update TOML config**

Add to `configs/strategies_example.toml`:

After the existing `[provider.pool_traders.params]` section (line 96), add the grading params:
```toml
[provider.pool_traders.params]
min_markets = 50
min_longshot_yes_frac = 0.15
max_no_fraction = 0.60
```

Add S3 (consensus copy) strategy section after `[strategy.proportional_copy]`:
```toml
# ---------------------------------------------------------------------------
# S3: Consensus Copy — Copy consistency-filtered skilled traders
# ---------------------------------------------------------------------------
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 200
max_position_usd = 50
max_open_positions = 10
cooldown_s = 60
features = ["skilled_traders"]

[strategy.consensus_copy.params]
direction = "NO"
min_pool_agreement = 0.0
```

Add skilled_traders provider:
```toml
[provider.skilled_traders]
enabled = true
refresh_interval_s = 3600

[provider.skilled_traders.params]
use_consistency = true
```

Add budgets section at the bottom of the file:
```toml
# ---------------------------------------------------------------------------
# Per-Strategy Capital Budgets (cumulative USD, strategies without entry are uncapped)
# ---------------------------------------------------------------------------
[budgets]
proportional_copy = 1000
will_no = 300
crypto_otm_no = 1000
consensus_copy = 200
```

**Step 2: Add `load_budgets()` to config.py**

Read `src/polymarket_pipeline/strategies/config.py` first, then add after `load_provider_configs()`:

```python
def load_budgets(path: Path) -> dict[str, float]:
    """Load per-strategy capital budgets from TOML ``[budgets]`` section.

    Returns empty dict if section is missing (no budget enforcement).
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    budgets_raw = raw.get("budgets", {})
    return {k: float(v) for k, v in budgets_raw.items()}
```

**Step 3: Wire budgets into CLI**

In `src/polymarket_pipeline/cli/strategy.py`, modify `_build_runner()` around line 203:

Before:
```python
    gateway = ExecutionGateway(executor=executor, log_path=log_path, delay_s=delay_s)
```

After:
```python
    from polymarket_pipeline.strategies.config import load_budgets

    budgets = load_budgets(config_path)
    gateway = ExecutionGateway(
        executor=executor,
        log_path=log_path,
        delay_s=delay_s,
        strategy_budgets=budgets or None,
    )
```

**Step 4: Write test for budget loading**

Add to existing `tests/test_strategy_config.py` (or create if needed):

```python
def test_load_budgets(tmp_path: Path) -> None:
    """Budgets section should be loaded as strategy → float dict."""
    from polymarket_pipeline.strategies.config import load_budgets

    toml_path = tmp_path / "test.toml"
    toml_path.write_text(
        '[budgets]\nfoo = 100.0\nbar = 250\n'
    )
    result = load_budgets(toml_path)
    assert result == {"foo": 100.0, "bar": 250.0}


def test_load_budgets_missing_section(tmp_path: Path) -> None:
    """Missing [budgets] section returns empty dict."""
    from polymarket_pipeline.strategies.config import load_budgets

    toml_path = tmp_path / "test.toml"
    toml_path.write_text('[strategy.x]\nenabled = true\n')
    result = load_budgets(toml_path)
    assert result == {}
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_strategy_config.py tests/test_gateway_budget.py -x -v`
Expected: All pass

**Step 6: Dry-run the CLI (confirm no import errors)**

Run: `uv run pm-strategy run --config configs/strategies_example.toml --help`
Expected: Help text displayed (validates imports work)

---

### Task 5: CLOB Orderbook Query + Price Validation Script

Add `get_orderbook()` to ClobClient, then create a script that compares CLOB live prices against paper fills.

**Files:**
- Modify: `src/polymarket_pipeline/execution/clob_client.py` (add `get_orderbook()`)
- Create: `scripts/validate_prices.py`
- Create: `tests/test_clob_orderbook.py`

**Step 1: Write failing test for orderbook query**

Create `tests/test_clob_orderbook.py`:

```python
"""Tests for ClobClient.get_orderbook()."""

from __future__ import annotations

import json

import httpx
import pytest

from polymarket_pipeline.execution.clob_client import ClobClient


@pytest.mark.asyncio
async def test_get_orderbook_parses_response() -> None:
    """Orderbook response should be parsed into best bid/ask."""

    async def _mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "market": "0xabc",
                "asset_id": "tok1",
                "bids": [{"price": "0.45", "size": "100"}],
                "asks": [{"price": "0.55", "size": "200"}],
            },
        )

    transport = httpx.MockTransport(_mock_handler)
    client = ClobClient.__new__(ClobClient)
    client._base_url = "https://clob.polymarket.com"
    client._client = httpx.AsyncClient(transport=transport, base_url=client._base_url)

    result = await client.get_orderbook("tok1")
    assert result is not None
    assert result["best_bid"] == 0.45
    assert result["best_ask"] == 0.55
    assert result["bid_size"] == 100.0
    assert result["ask_size"] == 200.0

    await client.close()


@pytest.mark.asyncio
async def test_get_orderbook_empty_book() -> None:
    """Empty orderbook should return None."""

    async def _mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"market": "0xabc", "asset_id": "tok1", "bids": [], "asks": []},
        )

    transport = httpx.MockTransport(_mock_handler)
    client = ClobClient.__new__(ClobClient)
    client._base_url = "https://clob.polymarket.com"
    client._client = httpx.AsyncClient(transport=transport, base_url=client._base_url)

    result = await client.get_orderbook("tok1")
    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_get_orderbook_handles_error() -> None:
    """HTTP errors should return None."""

    async def _mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(_mock_handler)
    client = ClobClient.__new__(ClobClient)
    client._base_url = "https://clob.polymarket.com"
    client._client = httpx.AsyncClient(transport=transport, base_url=client._base_url)

    result = await client.get_orderbook("tok1")
    assert result is None

    await client.close()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clob_orderbook.py -x -v`
Expected: FAIL — `get_orderbook` not found

**Step 3: Implement `get_orderbook()` in ClobClient**

Add to `src/polymarket_pipeline/execution/clob_client.py` after the `get_balances()` method:

```python
    async def get_orderbook(self, asset_id: str) -> dict[str, float] | None:
        """Fetch the current orderbook for an asset.

        Returns dict with best_bid, best_ask, bid_size, ask_size,
        or None if the book is empty or the request fails.
        """
        try:
            resp = await self._client.get(f"/book", params={"token_id": asset_id})
            resp.raise_for_status()
            data = resp.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if not bids and not asks:
                return None

            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 1.0
            bid_size = float(bids[0]["size"]) if bids else 0.0
            ask_size = float(asks[0]["size"]) if asks else 0.0

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
            }
        except Exception:
            log.exception("clob.get_orderbook_error", asset_id=asset_id)
            return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_clob_orderbook.py -x -v`
Expected: 3 passed

**Step 5: Create price validation script**

Create `scripts/validate_prices.py`:

```python
"""Compare paper fill prices against live CLOB orderbook.

Usage:
    uv run python scripts/validate_prices.py --intents logs/intents.jsonl
    uv run python scripts/validate_prices.py --intents logs/intents.jsonl --top 20

Reads the JSONL intent log produced by ExecutionGateway, fetches the current
CLOB orderbook for each market, and reports bid/ask spreads + slippage.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import polars as pl
import structlog
import typer

from polymarket_pipeline.execution.clob_client import ClobClient
from polymarket_pipeline.live.settings import Settings

logger = structlog.get_logger(__name__)
app = typer.Typer()


async def _fetch_orderbooks(
    client: ClobClient,
    asset_ids: list[str],
    *,
    concurrency: int = 5,
) -> dict[str, dict[str, float]]:
    """Fetch orderbooks for a list of asset_ids with concurrency limit."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, dict[str, float]] = {}

    async def _fetch(aid: str) -> None:
        async with sem:
            ob = await client.get_orderbook(aid)
            if ob is not None:
                results[aid] = ob

    await asyncio.gather(*[_fetch(aid) for aid in asset_ids])
    return results


async def _run(intents_path: Path, top: int) -> None:
    settings = Settings()

    # Load intents
    lines = intents_path.read_text().strip().split("\n")
    intents = [json.loads(line) for line in lines if line.strip()]

    if not intents:
        logger.info("No intents found")
        return

    df = pl.DataFrame(intents)
    logger.info("loaded_intents", count=len(df))

    # Get unique asset_ids
    asset_ids = df["asset_id"].drop_nulls().unique().to_list()
    if not asset_ids:
        # Fall back to condition_id if asset_id not set
        asset_ids = df["condition_id"].unique().to_list()

    logger.info("fetching_orderbooks", count=len(asset_ids))

    async with ClobClient(
        base_url=settings.clob_api_url,
        api_key=settings.clob_api_key,
        api_secret=settings.clob_api_secret,
        api_passphrase=settings.clob_api_passphrase,
    ) as client:
        orderbooks = await _fetch_orderbooks(client, asset_ids[:top])

    # Build comparison
    rows = []
    for intent in intents[:top]:
        aid = intent.get("asset_id") or intent["condition_id"]
        ob = orderbooks.get(aid)
        fill_price = intent.get("max_price", 0.5)
        side = intent["side"]

        if ob:
            live_price = ob["best_ask"] if side == "BUY" else ob["best_bid"]
            spread = ob["best_ask"] - ob["best_bid"]
            slippage = abs(fill_price - live_price) if live_price else None
        else:
            live_price = None
            spread = None
            slippage = None

        rows.append(
            {
                "strategy": intent["strategy"],
                "condition_id": intent["condition_id"],
                "side": side,
                "outcome": intent["outcome"],
                "fill_price": fill_price,
                "live_price": live_price,
                "spread": spread,
                "slippage": slippage,
                "size_usd": intent["size_usd"],
            }
        )

    result = pl.DataFrame(rows)

    # Summary stats
    valid = result.filter(pl.col("slippage").is_not_null())
    if not valid.is_empty():
        print("\n=== Price Validation Summary ===")
        print(f"Markets checked:  {len(valid)}")
        print(f"Median spread:    {valid['spread'].median():.4f}")
        print(f"Median slippage:  {valid['slippage'].median():.4f}")
        print(f"Max slippage:     {valid['slippage'].max():.4f}")
        print(f"Mean slippage:    {valid['slippage'].mean():.4f}")
        print()

        # Per-strategy breakdown
        by_strat = valid.group_by("strategy").agg(
            pl.col("slippage").median().alias("median_slippage"),
            pl.col("slippage").max().alias("max_slippage"),
            pl.len().alias("n"),
        )
        print("Per-strategy:")
        print(by_strat)
    else:
        print("No valid orderbook data to compare")

    # Detail table
    print("\n=== Detail ===")
    print(result.head(top))


@app.command()
def main(
    intents: Path = typer.Option(..., "--intents", "-i", help="Path to intents.jsonl"),
    top: int = typer.Option(50, "--top", "-n", help="Max markets to check"),
) -> None:
    """Validate paper fill prices against live CLOB orderbook."""
    asyncio.run(_run(intents, top))


if __name__ == "__main__":
    app()
```

**Step 6: Run all tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All pass

---

## Summary

| Task | Type | Est. Lines | Status |
|------|------|-----------|--------|
| 1. Market size classifier | Verify only | 0 | Already done |
| 2. GradedPoolProvider | Verify only | 0 | Already done |
| 3. Gateway budget tests | New tests | ~90 | Code exists, tests missing |
| 4. Wire final configs | Config + glue | ~60 | TOML gaps + CLI wiring |
| 5. CLOB orderbook + validation | New feature + script | ~200 | New method + script |
