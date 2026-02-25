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
        load_skilled_provider,
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
