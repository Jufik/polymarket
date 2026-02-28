"""Tests for P2/P3 fixes: realized PnL, MTM limits, reconciliation,
variable grace, auto-close on resolution."""

from __future__ import annotations

import time

import pytest

from polymarket_pipeline.execution.models import Position as ExecPosition
from polymarket_pipeline.quality.state import (
    DEFAULT_CHECK_GRACE_S,
    CheckResult,
    PipelineState,
    ReadinessState,
)


# ============================================================================
# Realized PnL: Position model + DDL
# ============================================================================


def test_position_has_realized_pnl_field() -> None:
    """Position model should expose realized_pnl."""
    p = ExecPosition(
        condition_id="c1",
        asset_id="a1",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=5.0,
        last_price=0.55,
        realized_pnl=12.5,
    )
    assert p.realized_pnl == 12.5


def test_position_realized_pnl_defaults_to_zero() -> None:
    """realized_pnl should default to 0.0 for backward compat."""
    p = ExecPosition(
        condition_id="c1",
        asset_id="a1",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=0.0,
        last_price=0.50,
    )
    assert p.realized_pnl == 0.0


def test_positions_ddl_has_realized_pnl() -> None:
    """DDL should include realized_pnl column."""
    from polymarket_pipeline.execution.position_tracker import POSITIONS_DDL

    assert "realized_pnl" in POSITIONS_DDL


def test_migration_for_realized_pnl_exists() -> None:
    """Migration SQL for adding realized_pnl column should exist."""
    from polymarket_pipeline.execution.position_tracker import (
        _MIGRATE_POSITIONS_REALIZED_PNL,
    )

    assert "realized_pnl" in _MIGRATE_POSITIONS_REALIZED_PNL
    assert "ALTER TABLE positions" in _MIGRATE_POSITIONS_REALIZED_PNL


# ============================================================================
# Mark-to-market position limits
# ============================================================================


def test_live_executor_has_mark_to_market() -> None:
    """LiveExecutor should have _mark_to_market method."""
    from polymarket_pipeline.strategies.execution.live import LiveExecutor

    assert hasattr(LiveExecutor, "_mark_to_market")


# ============================================================================
# Position reconciliation
# ============================================================================


def test_position_tracker_has_reconcile() -> None:
    """PositionTracker should expose an async reconcile method."""
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    assert hasattr(PositionTracker, "reconcile")


@pytest.mark.asyncio
async def test_reconcile_detects_drift() -> None:
    """reconcile() should detect when tracked != actual."""
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    tracker = PositionTracker(pool=None)
    # Inject a tracked position manually
    tracker._positions["c1"] = {
        "condition_id": "c1",
        "asset_id": "asset-1",
        "side": "BUY",
        "size": 100.0,
        "avg_entry": 0.50,
        "cost_basis": 50.0,
        "last_price": 0.50,
        "realized_pnl": 0.0,
    }

    # API says we have 80 tokens, but tracker says 100
    api_balances = {"asset-1": 80.0}
    drifts = await tracker.reconcile(api_balances)
    assert len(drifts) == 1
    assert drifts[0]["asset_id"] == "asset-1"
    assert drifts[0]["tracked"] == 100.0
    assert drifts[0]["actual"] == 80.0


@pytest.mark.asyncio
async def test_reconcile_detects_phantom_positions() -> None:
    """reconcile() should detect API balances not in tracker."""
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    tracker = PositionTracker(pool=None)
    # No tracked positions, but API says we have tokens
    api_balances = {"unknown-asset": 50.0}
    drifts = await tracker.reconcile(api_balances)
    assert len(drifts) == 1
    assert drifts[0].get("phantom") is True


@pytest.mark.asyncio
async def test_reconcile_passes_when_matching() -> None:
    """reconcile() returns empty list when everything matches."""
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    tracker = PositionTracker(pool=None)
    tracker._positions["c1"] = {
        "condition_id": "c1",
        "asset_id": "asset-1",
        "side": "BUY",
        "size": 100.0,
        "avg_entry": 0.50,
        "cost_basis": 50.0,
        "last_price": 0.50,
        "realized_pnl": 0.0,
    }
    api_balances = {"asset-1": 100.0}
    drifts = await tracker.reconcile(api_balances)
    assert len(drifts) == 0


# ============================================================================
# Variable grace periods
# ============================================================================


def test_default_check_grace_constants() -> None:
    """Verify default per-check grace periods are exported."""
    assert "source_liveness" in DEFAULT_CHECK_GRACE_S
    assert DEFAULT_CHECK_GRACE_S["source_liveness"] == 120.0
    assert DEFAULT_CHECK_GRACE_S["metadata_freshness"] == 900.0


def test_variable_grace_uses_shortest_failing_check() -> None:
    """When source_liveness fails, grace should be 120s (not default 300s)."""
    state = ReadinessState(
        degraded_grace_s=300.0,
        check_grace_s={"source_liveness": 120.0, "metadata_freshness": 900.0},
    )

    # Only source_liveness fails
    results = {
        "source_liveness": CheckResult(ok=False, reason="alchemy stale"),
        "metadata_freshness": CheckResult(ok=True),
    }
    state.update(results)
    assert state.current == PipelineState.DEGRADED

    # Effective grace should be 120s (source_liveness), not 300s (default)
    assert state.time_until_red is not None
    assert state.time_until_red <= 120.0


def test_variable_grace_metadata_only_failure() -> None:
    """When only metadata_freshness fails, grace should be 900s."""
    state = ReadinessState(
        degraded_grace_s=300.0,
        check_grace_s={"source_liveness": 120.0, "metadata_freshness": 900.0},
    )

    results = {
        "source_liveness": CheckResult(ok=True),
        "metadata_freshness": CheckResult(ok=False, reason="stale 3h"),
    }
    state.update(results)
    assert state.current == PipelineState.DEGRADED
    assert state.time_until_red is not None
    assert state.time_until_red <= 900.0
    assert state.time_until_red > 120.0  # NOT the short grace


def test_variable_grace_all_pass_clears() -> None:
    """When all checks pass, state returns to READY."""
    state = ReadinessState(
        degraded_grace_s=300.0,
        check_grace_s={"source_liveness": 120.0},
    )

    # First: fail
    state.update({"source_liveness": CheckResult(ok=False, reason="stale")})
    assert state.current == PipelineState.DEGRADED

    # Then: pass
    state.update({"source_liveness": CheckResult(ok=True)})
    assert state.current == PipelineState.READY
    assert state.time_until_red is None


# ============================================================================
# Auto-close on resolution (settle_resolved_market)
# ============================================================================


def test_live_runner_has_settle_method() -> None:
    """LiveRunner should expose settle_resolved_market."""
    from polymarket_pipeline.strategies.runners.live import LiveRunner

    assert hasattr(LiveRunner, "settle_resolved_market")


def test_settle_resolved_market_logic() -> None:
    """Test PnL math: YES winner with YES position → profit."""
    from dataclasses import replace
    from unittest.mock import MagicMock

    from polymarket_pipeline.strategies.runners.live import LiveRunner
    from polymarket_pipeline.strategies.types import Position

    # Create a minimal runner with mocked context
    runner = object.__new__(LiveRunner)
    runner.ctx = MagicMock()

    # Position: bought 100 YES tokens at 0.40 each (cost = $40)
    pos = Position(
        condition_id="0xabc",
        strategy="test",
        qty_yes=100.0,
        avg_entry_yes=0.40,
        cost_basis=40.0,
    )
    runner.ctx.get_all_positions.return_value = {"0xabc": pos}

    runner.settle_resolved_market("0xabc", "YES")

    # Should have called set_position with zeroed qty and positive PnL
    call_args = runner.ctx.set_position.call_args
    assert call_args is not None
    settled_pos = call_args[0][1]
    assert settled_pos.qty_yes == 0.0
    assert settled_pos.qty_no == 0.0
    assert settled_pos.cost_basis == 0.0
    # PnL = (1.0 - 0.40) * 100 = 60.0
    assert abs(settled_pos.realized_pnl - 60.0) < 0.01


def test_settle_resolved_market_loser() -> None:
    """YES position but NO wins → loss."""
    from unittest.mock import MagicMock

    from polymarket_pipeline.strategies.runners.live import LiveRunner
    from polymarket_pipeline.strategies.types import Position

    runner = object.__new__(LiveRunner)
    runner.ctx = MagicMock()

    pos = Position(
        condition_id="0xabc",
        strategy="test",
        qty_yes=100.0,
        avg_entry_yes=0.40,
        cost_basis=40.0,
    )
    runner.ctx.get_all_positions.return_value = {"0xabc": pos}

    runner.settle_resolved_market("0xabc", "NO")

    settled_pos = runner.ctx.set_position.call_args[0][1]
    assert settled_pos.qty_yes == 0.0
    # Loss = -0.40 * 100 = -40.0
    assert abs(settled_pos.realized_pnl - (-40.0)) < 0.01


def test_settle_no_position_is_noop() -> None:
    """Settling a market with no position should be a no-op."""
    from unittest.mock import MagicMock

    from polymarket_pipeline.strategies.runners.live import LiveRunner

    runner = object.__new__(LiveRunner)
    runner.ctx = MagicMock()
    runner.ctx.get_all_positions.return_value = {}

    runner.settle_resolved_market("0xnonexistent", "YES")

    # set_position should NOT have been called
    runner.ctx.set_position.assert_not_called()
