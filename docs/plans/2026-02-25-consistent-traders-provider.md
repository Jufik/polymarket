# Align SkilledTradersProvider with Research Consistency Filtering

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the production `SkilledTradersProvider`'s single filter (market count) with the research-validated 5-filter pipeline: monthly profitability consistency, MVF band, and median entry price — matching `get_consistent_traders()` from the backtester.

**Architecture:** Extend `SkilledTradersProvider` with the same filtering logic the research backtester uses, backed by the derived parquet tables (`trader_market_pnl.parquet`, `maker_volume_fractions.parquet`, `markets_resolved.parquet`). The `FeatureBackend` protocol already supports `query_trades()` and `query_custom()`, but the consistency computation needs resolution dates and MVF data. Rather than stretching the backend protocol, the provider will accept pre-loaded DataFrames (for backtest) or query ClickHouse directly (for live). All filter thresholds are constructor params with defaults matching the research top config.

**Tech Stack:** Python 3.11+, Polars, Pydantic v2, structlog, pytest-asyncio.

---

## Current State

| Component | Production (`SkilledTradersProvider`) | Research (`get_consistent_traders`) |
|-----------|--------------------------------------|-------------------------------------|
| Min markets | `n_unique(condition_id) >= 50` | `total_markets >= min_markets` (10/20) |
| Monthly profitability | **missing** | `market_pnl > 0` every active month |
| Min active months | **missing** | `total_months >= n_periods` (6/9) |
| MVF band | **missing** | `mvf < 0.10` (pure taker) |
| Median entry price | **missing** | `median_directional_entry <= 0.90` |

**Research top config:** `consistency_months=6, min_markets=10, mvf_band=pure_taker, max_median_entry=0.90`

---

## Data Dependencies

The provider needs three DataFrames:

1. **`trader_market_pnl`** — columns: `trader`, `condition_id`, `market_pnl`, `first_trade`, `net_yes_tokens`, `wavg_yes_entry_price`
2. **`maker_volume_fractions`** — columns: `trader`, `mvf`
3. **`markets_resolved`** — columns: `condition_id`, `resolved_at`

For backtest: loaded from `data/derived/*.parquet`.
For live: queried from ClickHouse (same tables replicated there after `pm-build`).

---

## Task 1: Write the Consistency Filter as a Pure Function

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/consensus_copy/consistency.py`
- Create: `tests/test_consistency_filter.py`

This is a standalone pure function — no async, no backend. Takes DataFrames in, returns a `frozenset[str]` of qualifying traders. Directly mirrors `get_consistent_traders()` + MVF + median entry from research.

**Step 1: Write failing tests**

Create `tests/test_consistency_filter.py`:

```python
"""Tests for the consistency-based trader filter."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
    filter_consistent_traders,
)


def _ts(y: int, m: int, d: int = 1) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.fixture
def resolved() -> pl.DataFrame:
    """Markets resolved in Jan-Jun 2025."""
    return pl.DataFrame({
        "condition_id": [f"0xm{i}" for i in range(12)],
        "resolved_at": [
            _ts(2025, 1), _ts(2025, 1),   # Jan
            _ts(2025, 2), _ts(2025, 2),   # Feb
            _ts(2025, 3), _ts(2025, 3),   # Mar
            _ts(2025, 4), _ts(2025, 4),   # Apr
            _ts(2025, 5), _ts(2025, 5),   # May
            _ts(2025, 6), _ts(2025, 6),   # Jun
        ],
    })


@pytest.fixture
def mvf() -> pl.DataFrame:
    return pl.DataFrame({
        "trader": ["0xGood", "0xMaker", "0xNoMVF"],
        "mvf": [0.05, 0.60, 0.02],
    })


def test_consistent_trader_passes_all_filters(resolved: pl.DataFrame, mvf: pl.DataFrame) -> None:
    """Trader profitable every month for 6 months, pure taker, low entry → passes."""
    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 12,  # positive every market
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.30] * 12,
    })

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
    )

    assert "0xGood" in result


def test_one_negative_month_excluded(resolved: pl.DataFrame, mvf: pl.DataFrame) -> None:
    """Trader with one negative month is excluded (must be profitable EVERY month)."""
    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 10 + [-5.0, -5.0],  # Jun negative
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.30] * 12,
    })

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
    )

    assert "0xGood" not in result


def test_too_few_months_excluded(resolved: pl.DataFrame, mvf: pl.DataFrame) -> None:
    """Trader active only 3 months is excluded when min_periods=6."""
    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 6,
        "condition_id": [f"0xm{i}" for i in range(6)],
        "market_pnl": [10.0] * 6,
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2,
        "net_yes_tokens": [1.0] * 6,
        "wavg_yes_entry_price": [0.30] * 6,
    })

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
        min_periods=6,
    )

    assert "0xGood" not in result


def test_maker_dominant_excluded(resolved: pl.DataFrame) -> None:
    """Trader with mvf > 0.10 excluded from pure_taker band."""
    pnl = pl.DataFrame({
        "trader": ["0xMaker"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 12,
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.30] * 12,
    })

    mvf = pl.DataFrame({"trader": ["0xMaker"], "mvf": [0.60]})

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
        max_mvf=0.10,
    )

    assert "0xMaker" not in result


def test_high_median_entry_excluded(resolved: pl.DataFrame, mvf: pl.DataFrame) -> None:
    """Trader with high median directional entry excluded."""
    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 12,
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.95] * 12,  # very high entry
    })

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
        max_median_entry=0.90,
    )

    assert "0xGood" not in result


def test_backward_compat_no_extra_filters(resolved: pl.DataFrame) -> None:
    """With relaxed params, any profitable trader passes (like old provider)."""
    pnl = pl.DataFrame({
        "trader": ["0xAnyone"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)],
        "market_pnl": [10.0] * 12,
        "first_trade": [_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2,
        "net_yes_tokens": [1.0] * 12,
        "wavg_yes_entry_price": [0.50] * 12,
    })

    result = filter_consistent_traders(
        pnl=pnl,
        resolved=resolved,
        mvf=pl.DataFrame({"trader": ["0xAnyone"], "mvf": [0.99]}),
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
        min_periods=1,
        min_markets=1,
        max_mvf=1.0,
        max_median_entry=1.0,
    )

    assert "0xAnyone" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_consistency_filter.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_pipeline.strategies_impl.consensus_copy.consistency'`

**Step 3: Implement `filter_consistent_traders`**

Create `src/polymarket_pipeline/strategies_impl/consensus_copy/consistency.py`:

```python
"""Consistency-based trader filtering — production mirror of research logic.

Applies the same 5-filter pipeline as the research backtester's
``get_consistent_traders()`` + MVF band + median entry price.

All filters have relaxed defaults so callers can opt into strictness
incrementally.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


def filter_consistent_traders(
    *,
    pnl: pl.DataFrame,
    resolved: pl.DataFrame,
    mvf: pl.DataFrame,
    train_start: datetime,
    train_end: datetime,
    min_periods: int = 6,
    min_markets: int = 10,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.90,
) -> frozenset[str]:
    """Return traders passing all five consistency filters.

    Parameters
    ----------
    pnl:
        ``trader_market_pnl`` table. Needs: ``trader``, ``condition_id``,
        ``market_pnl``, ``net_yes_tokens``, ``wavg_yes_entry_price``.
    resolved:
        ``markets_resolved`` table. Needs: ``condition_id``, ``resolved_at``.
    mvf:
        ``maker_volume_fractions`` table. Needs: ``trader``, ``mvf``.
    train_start:
        Training window start (inclusive).
    train_end:
        Training window end (exclusive).
    min_periods:
        Minimum number of distinct profitable months.
    min_markets:
        Minimum total distinct markets across training.
    max_mvf:
        Maximum maker volume fraction (pure_taker = 0.10).
    max_median_entry:
        Maximum median directional entry price.

    Returns
    -------
    frozenset[str]
        Set of qualifying trader addresses.
    """
    # --- Step 1: Join PnL with resolution dates, filter to training window ---
    df = pnl.lazy().join(
        resolved.lazy().select("condition_id", "resolved_at"),
        on="condition_id",
        how="inner",
    ).filter(
        (pl.col("resolved_at") >= train_start)
        & (pl.col("resolved_at") < train_end)
    )

    # --- Step 2: Monthly aggregation ---
    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y%m").cast(pl.UInt32).alias("month")
    )

    monthly = df.group_by(["trader", "month"]).agg(
        pl.col("market_pnl").sum().alias("monthly_pnl"),
        pl.col("condition_id").n_unique().alias("markets_traded"),
    )

    # --- Step 3: Trader-level consistency stats ---
    trader_stats = monthly.group_by("trader").agg(
        (pl.col("monthly_pnl") > 0).sum().alias("positive_months"),
        pl.len().alias("total_months"),
        pl.col("markets_traded").sum().alias("total_markets"),
    )

    # --- Filter 1+2+3: profitable every month, enough months, enough markets ---
    consistent = trader_stats.filter(
        (pl.col("positive_months") == pl.col("total_months"))
        & (pl.col("total_months") >= min_periods)
        & (pl.col("total_markets") >= min_markets)
    ).collect()

    traders = set(consistent["trader"].to_list())
    logger.info(
        "consistency.base_filter",
        n_consistent=len(traders),
        min_periods=min_periods,
        min_markets=min_markets,
    )

    if not traders:
        return frozenset()

    # --- Filter 4: MVF band ---
    if max_mvf < 1.0:
        mvf_pass = set(
            mvf.filter(pl.col("mvf") <= max_mvf)["trader"].to_list()
        )
        traders &= mvf_pass
        logger.info("consistency.mvf_filter", remaining=len(traders), max_mvf=max_mvf)

    if not traders:
        return frozenset()

    # --- Filter 5: Median directional entry price ---
    if max_median_entry < 1.0:
        # Directional entry: if net long YES, use wavg_yes_entry; else 1 - wavg_yes_entry
        trader_entries = (
            df.filter(pl.col("trader").is_in(list(traders)))
            .with_columns(
                pl.when(pl.col("net_yes_tokens") > 0)
                .then(pl.col("wavg_yes_entry_price"))
                .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
                .alias("directional_entry")
            )
            .group_by("trader")
            .agg(pl.col("directional_entry").median().alias("median_entry"))
            .filter(pl.col("median_entry") <= max_median_entry)
            .collect()
        )
        entry_pass = set(trader_entries["trader"].to_list())
        traders &= entry_pass
        logger.info(
            "consistency.entry_filter",
            remaining=len(traders),
            max_median_entry=max_median_entry,
        )

    return frozenset(traders)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consistency_filter.py -x -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/consensus_copy/consistency.py tests/test_consistency_filter.py
git commit -m "feat(S3): add consistency-based trader filter matching research pipeline

Five filters: monthly profitability, min periods, min markets, MVF band,
median entry price. Mirrors research get_consistent_traders() exactly.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 2: Upgrade `SkilledTradersProvider` to Use Consistency Filter

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`
- Modify: `tests/test_consistency_filter.py` (add provider integration test)

The provider needs to load the three DataFrames and call `filter_consistent_traders()`. For the `PolarsBackend`, it loads from parquet. For `ClickHouseBackend`, it queries SQL.

**Step 1: Write failing tests**

Add to `tests/test_consistency_filter.py`:

```python
@pytest.mark.asyncio
async def test_provider_uses_consistency_filter(
    resolved: pl.DataFrame, mvf: pl.DataFrame
) -> None:
    """SkilledTradersProvider should apply full consistency filtering."""
    from unittest.mock import AsyncMock

    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )

    pnl = pl.DataFrame({
        "trader": ["0xGood"] * 12 + ["0xWeak"] * 12,
        "condition_id": [f"0xm{i}" for i in range(12)] * 2,
        "market_pnl": [10.0] * 12 + [10.0] * 10 + [-5.0, -5.0],
        "first_trade": ([_ts(2025, 1)] * 2 + [_ts(2025, 2)] * 2 + [_ts(2025, 3)] * 2
            + [_ts(2025, 4)] * 2 + [_ts(2025, 5)] * 2 + [_ts(2025, 6)] * 2) * 2,
        "net_yes_tokens": [1.0] * 24,
        "wavg_yes_entry_price": [0.30] * 24,
    })

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=pnl)

    provider = SkilledTradersProvider(
        pnl_df=pnl,
        resolved_df=resolved,
        mvf_df=mvf,
        train_start=_ts(2025, 1),
        train_end=_ts(2025, 7),
        min_periods=6,
        min_markets=10,
        max_mvf=0.10,
        max_median_entry=0.90,
    )
    await provider.compute(backend)

    pool = provider.get_features()["skilled_traders"]
    assert "0xGood" in pool   # passes all filters
    assert "0xWeak" not in pool  # one negative month


@pytest.mark.asyncio
async def test_provider_legacy_mode_no_dataframes() -> None:
    """Without DataFrames, falls back to simple market-count filter."""
    from unittest.mock import AsyncMock

    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )

    trades_df = pl.DataFrame({
        "maker": ["0xA"] * 60 + ["0xB"] * 10,
        "condition_id": [f"0xm{i}" for i in range(60)] + [f"0xm{i}" for i in range(10)],
        "side": ["BUY"] * 70,
        "price": [0.50] * 70,
        "published_at": [float(i) for i in range(70)],
    })

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = SkilledTradersProvider(min_trades=20)
    await provider.compute(backend)

    pool = provider.get_features()["skilled_traders"]
    assert "0xA" in pool
    assert "0xB" not in pool
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_consistency_filter.py::test_provider_uses_consistency_filter tests/test_consistency_filter.py::test_provider_legacy_mode_no_dataframes -x -q`
Expected: FAIL — `SkilledTradersProvider` doesn't accept `pnl_df` etc.

**Step 3: Upgrade `SkilledTradersProvider`**

Replace `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py`:

```python
"""Feature providers for the consensus-copy strategy."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class SkilledTradersProvider:
    """Computes and maintains the set of skilled trader addresses.

    Two modes of operation:

    **Consistency mode** (when ``pnl_df``, ``resolved_df``, ``mvf_df`` are provided):
    Applies the full 5-filter research pipeline via
    ``filter_consistent_traders()``. This matches the research backtester's
    ``get_consistent_traders()`` + MVF + median entry.

    **Legacy mode** (when DataFrames not provided):
    Falls back to the simple market-count filter (``n_unique(condition_id) >= min_trades``).
    This preserves backward compatibility with existing tests and configs.

    Parameters
    ----------
    min_trades:
        Legacy mode: minimum distinct markets to qualify.
    pnl_df:
        Pre-loaded ``trader_market_pnl`` DataFrame. Enables consistency mode.
    resolved_df:
        Pre-loaded ``markets_resolved`` DataFrame.
    mvf_df:
        Pre-loaded ``maker_volume_fractions`` DataFrame.
    train_start:
        Training window start (consistency mode).
    train_end:
        Training window end (consistency mode).
    min_periods:
        Minimum profitable months (default: 6, from research top config).
    min_markets:
        Minimum total markets (default: 10, from research top config).
    max_mvf:
        Maximum MVF (default: 0.10 = pure_taker band).
    max_median_entry:
        Maximum median directional entry (default: 0.90).
    """

    name: str = "skilled_traders"

    def __init__(
        self,
        min_trades: int = 50,
        *,
        pnl_df: pl.DataFrame | None = None,
        resolved_df: pl.DataFrame | None = None,
        mvf_df: pl.DataFrame | None = None,
        train_start: datetime | None = None,
        train_end: datetime | None = None,
        min_periods: int = 6,
        min_markets: int = 10,
        max_mvf: float = 0.10,
        max_median_entry: float = 0.90,
    ) -> None:
        self._min_trades = min_trades
        self._pnl_df = pnl_df
        self._resolved_df = resolved_df
        self._mvf_df = mvf_df
        self._train_start = train_start
        self._train_end = train_end
        self._min_periods = min_periods
        self._min_markets = min_markets
        self._max_mvf = max_mvf
        self._max_median_entry = max_median_entry
        self._skilled: frozenset[str] = frozenset()

    @property
    def _use_consistency(self) -> bool:
        return (
            self._pnl_df is not None
            and self._resolved_df is not None
            and self._mvf_df is not None
            and self._train_start is not None
            and self._train_end is not None
        )

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute the skilled traders set."""
        if self._use_consistency:
            self._compute_consistent()
        else:
            await self._compute_legacy(backend)

    def _compute_consistent(self) -> None:
        """Full 5-filter consistency pipeline."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._pnl_df is not None  # noqa: S101
        assert self._resolved_df is not None  # noqa: S101
        assert self._mvf_df is not None  # noqa: S101
        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        self._skilled = filter_consistent_traders(
            pnl=self._pnl_df,
            resolved=self._resolved_df,
            mvf=self._mvf_df,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )
        logger.info("skilled_traders.consistency_mode", count=len(self._skilled))

    async def _compute_legacy(self, backend: FeatureBackend) -> None:
        """Simple market-count filter (backward compat)."""
        trades = await backend.query_trades()

        if trades.is_empty():
            self._skilled = frozenset()
            logger.info("skilled_traders.compute", count=0)
            return

        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_trades)
            .collect()
        )

        self._skilled = frozenset(trader_counts["maker"].to_list())
        logger.info("skilled_traders.legacy_mode", count=len(self._skilled))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — skilled set is refreshed periodically, not per-trade."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the skilled set."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Return ``{"skilled_traders": frozenset[str]}``."""
        return {"skilled_traders": self._skilled}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consistency_filter.py -x -q`
Expected: ALL PASS

**Step 5: Run existing consensus copy tests for regression**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py -k "consensus"`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py tests/test_consistency_filter.py
git commit -m "feat(S3): upgrade SkilledTradersProvider with consistency filtering

Consistency mode (pnl_df + resolved_df + mvf_df provided) applies all 5
research filters. Legacy mode (no DataFrames) falls back to market count.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 3: Wire Provider into CLI with Parquet Auto-Loading

**Files:**
- Modify: `src/polymarket_pipeline/cli/strategy.py`
- Create: `tests/test_cli_strategy_provider.py`

The CLI needs to load the derived parquet files and pass them to `SkilledTradersProvider` when consistency params are in the TOML config. Add a TOML config section for the provider:

```toml
[providers.skilled_traders]
enabled = true
min_periods = 6
min_markets = 10
max_mvf = 0.10
max_median_entry = 0.90
train_start = "2023-01-01"
train_end = "2026-02-01"
data_dir = "data/derived"
```

**Step 1: Write failing test**

Create `tests/test_cli_strategy_provider.py`:

```python
"""Tests for CLI provider wiring with consistency data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest


def test_load_skilled_provider_with_data_dir(tmp_path: Path) -> None:
    """Provider factory should load parquet files from data_dir."""
    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )

    # Create minimal parquet files
    pnl = pl.DataFrame({
        "trader": ["0xA"] * 6,
        "condition_id": [f"0xm{i}" for i in range(6)],
        "market_pnl": [10.0] * 6,
        "first_trade": [datetime(2025, m, 1, tzinfo=timezone.utc) for m in range(1, 7)],
        "net_yes_tokens": [1.0] * 6,
        "wavg_yes_entry_price": [0.30] * 6,
    })
    resolved = pl.DataFrame({
        "condition_id": [f"0xm{i}" for i in range(6)],
        "resolved_at": [datetime(2025, m, 15, tzinfo=timezone.utc) for m in range(1, 7)],
    })
    mvf = pl.DataFrame({"trader": ["0xA"], "mvf": [0.05]})

    pnl.write_parquet(tmp_path / "trader_market_pnl.parquet")
    resolved.write_parquet(tmp_path / "markets_resolved.parquet")
    mvf.write_parquet(tmp_path / "maker_volume_fractions.parquet")

    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        load_skilled_provider,
    )

    provider = load_skilled_provider(
        data_dir=tmp_path,
        train_start="2025-01-01",
        train_end="2025-07-01",
        min_periods=6,
        min_markets=5,
        max_mvf=0.10,
        max_median_entry=0.90,
    )

    assert isinstance(provider, SkilledTradersProvider)
    assert provider._use_consistency is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_strategy_provider.py -x -q`
Expected: FAIL — `load_skilled_provider` doesn't exist

**Step 3: Add `load_skilled_provider` factory**

Add to `src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py` (after the class):

```python
def load_skilled_provider(
    *,
    data_dir: str | Path,
    train_start: str,
    train_end: str,
    min_periods: int = 6,
    min_markets: int = 10,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.90,
) -> SkilledTradersProvider:
    """Factory that loads derived parquet files and creates a consistency-mode provider.

    Parameters
    ----------
    data_dir:
        Directory containing ``trader_market_pnl.parquet``,
        ``markets_resolved.parquet``, and ``maker_volume_fractions.parquet``.
    train_start:
        ISO date string for training window start (e.g. ``"2023-01-01"``).
    train_end:
        ISO date string for training window end (e.g. ``"2026-02-01"``).
    """
    from pathlib import Path as P

    d = P(data_dir)

    pnl_df = pl.read_parquet(d / "trader_market_pnl.parquet")
    resolved_df = pl.read_parquet(d / "markets_resolved.parquet")
    mvf_df = pl.read_parquet(d / "maker_volume_fractions.parquet")

    ts = datetime.fromisoformat(train_start).replace(tzinfo=timezone.utc)
    te = datetime.fromisoformat(train_end).replace(tzinfo=timezone.utc)

    return SkilledTradersProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=ts,
        train_end=te,
        min_periods=min_periods,
        min_markets=min_markets,
        max_mvf=max_mvf,
        max_median_entry=max_median_entry,
    )
```

Add missing imports at top of file:

```python
from datetime import datetime, timezone
from pathlib import Path
```

**Step 4: Update CLI provider registry**

In `src/polymarket_pipeline/cli/strategy.py`, update the provider instantiation block (around line 163-167) to detect consistency params and use the factory:

```python
    for pname in needed_providers:
        if pname in _PROVIDER_REGISTRY:
            pcfg = provider_configs.get(pname)
            params = pcfg.params if pcfg else {}

            # Special case: skilled_traders with data_dir uses consistency mode
            if pname == "skilled_traders" and "data_dir" in params:
                from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
                    load_skilled_provider,
                )

                provider = load_skilled_provider(**params)
            else:
                provider = _PROVIDER_REGISTRY[pname](**params)

            providers.append(provider)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_strategy_provider.py tests/test_consistency_filter.py -x -q`
Expected: ALL PASS

**Step 6: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/consensus_copy/providers.py src/polymarket_pipeline/cli/strategy.py tests/test_cli_strategy_provider.py
git commit -m "feat(S3): wire consistency provider into CLI with parquet auto-loading

load_skilled_provider() factory loads derived parquet files.
CLI detects data_dir in TOML config and uses consistency mode.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Task 4: Add S3 to `run_vectorized_strategies.py`

**Files:**
- Modify: `scripts/run_vectorized_strategies.py`

Currently this script runs S1/S2a/S2b but skips S3. Wire it in.

**Step 1: Read current script to understand structure**

Read: `scripts/run_vectorized_strategies.py`

**Step 2: Add S3 (ConsensusCopy) to the strategy list**

Add after the existing strategy definitions:

```python
# S3: Consensus Copy (requires derived data for skilled pool)
from polymarket_pipeline.strategies_impl.consensus_copy.providers import load_skilled_provider
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import ConsensusCopyStrategy

provider = load_skilled_provider(
    data_dir="data/derived",
    train_start="2023-01-01",
    train_end="2026-02-01",
    min_periods=6,
    min_markets=10,
    max_mvf=0.10,
    max_median_entry=0.90,
)

# Compute pool (need a backend for the provider)
import asyncio
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend

backend = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())
asyncio.run(provider.compute(backend))
skilled_pool = provider.get_features()["skilled_traders"]

s3 = ConsensusCopyStrategy(config=ConsensusCopyConfig(
    skilled_traders=skilled_pool,
    min_traders=5,
    agreement_pct=0.80,
    direction="NO",
    base_bet_usd=10.0,
))
```

Add `s3` to the strategies list passed to the runner.

**Step 3: Commit**

```bash
git add scripts/run_vectorized_strategies.py
git commit -m "feat: add S3 consensus copy to vectorized strategies script

Uses consistency-mode SkilledTradersProvider for pool computation.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

## Summary: Production ↔ Research Alignment

| Filter | Before | After |
|--------|--------|-------|
| Market count | `n_markets >= 50` | `total_markets >= 10` (configurable) |
| Monthly profitability | none | Every active month must be positive |
| Active months | none | `>= 6` months (configurable) |
| MVF band | none | `mvf <= 0.10` pure taker (configurable) |
| Median entry price | none | `<= 0.90` (configurable) |
| Backward compat | — | Legacy mode when no DataFrames provided |

### What Remains After This Plan

- **Run the consistency filter on real data** — execute `run_vectorized_strategies.py` with S3 to see pool size and signal count.
- **Extend sweep to Feb 2026** — update `sweep_config.toml` `last_holdout` to `2026-02-01`, rebuild derived tables, re-run.
- **Automate pool refresh** — monthly cron or pm-build step that re-computes the skilled pool from latest data.
