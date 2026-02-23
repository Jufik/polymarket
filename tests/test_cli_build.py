"""Tests for consolidated pipeline settings and CLI utilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def test_pipeline_settings_defaults() -> None:
    """PipelineSettings should have sensible defaults for local dev."""
    from polymarket_pipeline.settings import PipelineSettings

    s = PipelineSettings()
    assert s.data_dir == "data"
    assert s.raw_parquet_dir == "order_filled"
    assert s.ch_host == "localhost"
    assert s.ch_port == 18123
    assert s.ch_database == "polymarket"
    assert "15432" in s.pg_dsn


def test_pipeline_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PM_DATA_DIR env var should override the default."""
    monkeypatch.setenv("PM_DATA_DIR", "/tmp/test_data")
    monkeypatch.setenv("PM_CH_HOST", "10.0.0.99")

    from polymarket_pipeline.settings import PipelineSettings

    s = PipelineSettings()
    assert s.data_dir == "/tmp/test_data"
    assert s.ch_host == "10.0.0.99"
    assert s.metadata_dir == Path("/tmp/test_data/metadata")
    assert s.compact_dir == Path("/tmp/test_data/compact")
    assert s.derived_dir == Path("/tmp/test_data/derived")
    assert s.token_map_path == Path("/tmp/test_data/metadata/token_map.parquet")
    assert s.markets_path == Path("/tmp/test_data/metadata/markets.parquet")


def test_pipeline_settings_properties() -> None:
    """Derived path properties should compose from data_dir."""
    from polymarket_pipeline.settings import PipelineSettings

    s = PipelineSettings()
    assert s.metadata_dir == Path("data/metadata")
    assert s.compact_dir == Path("data/compact")
    assert s.derived_dir == Path("data/derived")
    assert s.token_map_path == Path("data/metadata/token_map.parquet")
    assert s.markets_path == Path("data/metadata/markets.parquet")


def test_sync_freshness_check_fresh(tmp_path: Path) -> None:
    """Should skip when metadata is fresh (< 24h old)."""
    from polymarket_pipeline.cli.sync import _is_fresh

    meta_dir = tmp_path / "metadata"
    meta_dir.mkdir()
    meta_file = meta_dir / "_fetch_meta.json"
    meta_file.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat()}))
    assert _is_fresh(meta_dir) is True


def test_sync_freshness_check_stale(tmp_path: Path) -> None:
    """Should NOT skip when metadata is stale (> 24h old)."""
    from polymarket_pipeline.cli.sync import _is_fresh

    meta_dir = tmp_path / "metadata"
    meta_dir.mkdir()
    meta_file = meta_dir / "_fetch_meta.json"
    stale = datetime.now(UTC) - timedelta(hours=25)
    meta_file.write_text(json.dumps({"fetched_at": stale.isoformat()}))
    assert _is_fresh(meta_dir) is False


def test_sync_freshness_check_missing(tmp_path: Path) -> None:
    """Should NOT skip when _fetch_meta.json doesn't exist."""
    from polymarket_pipeline.cli.sync import _is_fresh

    meta_dir = tmp_path / "metadata"
    meta_dir.mkdir()
    assert _is_fresh(meta_dir) is False


def test_clob_markets_result_dataclass() -> None:
    """ClobMarketsResult should hold markets, tokens, and resolutions."""
    from polymarket_pipeline.market_sync import (
        ClobMarketRow,
        ClobMarketsResult,
        ClobResolution,
        ClobTokenRow,
    )

    result = ClobMarketsResult(
        markets=[
            ClobMarketRow(
                condition_id="cid1",
                question="Q?",
                market_slug="q",
                closed=True,
                active=False,
                end_date_iso="",
                neg_risk=False,
                resolution_value=1,
                winner_outcome="Yes",
                tags="",
            )
        ],
        tokens=[
            ClobTokenRow(
                asset_id="t1",
                condition_id="cid1",
                outcome="Yes",
                winner=True,
                token_index=0,
            ),
            ClobTokenRow(
                asset_id="t2",
                condition_id="cid1",
                outcome="No",
                winner=False,
                token_index=1,
            ),
        ],
        resolutions={
            "cid1": ClobResolution(
                resolution_value=1,
                winner_outcome="Yes",
                token_winners={"t1": True, "t2": False},
            )
        },
    )
    assert len(result.markets) == 1
    assert len(result.tokens) == 2
    assert result.resolutions["cid1"].resolution_value == 1
    assert result.tokens[0].token_index == 0
    assert result.tokens[1].token_index == 1


def test_build_all_steps_constant() -> None:
    """ALL_STEPS should include the expected pipeline steps."""
    from polymarket_pipeline.cli.build import ALL_STEPS

    assert ALL_STEPS == ("sync", "compact", "load", "derived", "prices")
