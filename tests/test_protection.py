"""Tests for auto_protect — the self-protection panic close trigger."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.quality.state import PipelineState
from polymarket_pipeline.live.settings import Settings


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "alchemy_ws_url": "wss://dummy",
        "pg_dsn": "postgresql://x@localhost/x",
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_auto_protect_transitions_to_safe_stop() -> None:
    """auto_protect sets state to CLOSING, runs panic, transitions to SAFE_STOP."""
    from polymarket_pipeline.live.protection import auto_protect

    settings = _settings()
    mock_ch = MagicMock()
    checker = QualityChecker(settings=settings, clickhouse=mock_ch)
    # Force state to RED
    checker.state._state = PipelineState.RED

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    mock_clob = AsyncMock()
    mock_clob.__aenter__ = AsyncMock(return_value=mock_clob)
    mock_clob.__aexit__ = AsyncMock(return_value=False)

    mock_tracker = AsyncMock()
    mock_tracker.initialize = AsyncMock()

    with (
        patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "polymarket_pipeline.execution.clob_client.ClobClient", return_value=mock_clob
        ),
        patch(
            "polymarket_pipeline.execution.position_tracker.PositionTracker",
            return_value=mock_tracker,
        ),
        patch(
            "polymarket_pipeline.execution.panic.panic_close_all",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
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
