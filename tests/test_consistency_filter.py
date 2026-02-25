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
