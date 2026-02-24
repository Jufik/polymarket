"""Tests for execution models and position tracker logic."""

from datetime import UTC, datetime

from polymarket_pipeline.execution.models import FillRecord, Position


def test_position_dataclass() -> None:
    p = Position(
        condition_id="c1",
        asset_id="a1",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=5.0,
        last_price=0.55,
    )
    assert p.condition_id == "c1"
    assert p.size == 100.0
    assert p.unrealized_pnl == 5.0


def test_fill_record_dataclass() -> None:
    f = FillRecord(
        intent_id="i1",
        strategy="consensus_copy",
        condition_id="c1",
        asset_id="a1",
        side="BUY",
        outcome="YES",
        price=0.50,
        size_usd=100.0,
        fee_usd=1.0,
        filled_at=datetime.now(tz=UTC),
    )
    assert f.strategy == "consensus_copy"
    assert f.fee_usd == 1.0


def test_position_is_frozen() -> None:
    p = Position(
        condition_id="c1",
        asset_id="a1",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=0.0,
        last_price=0.50,
    )
    try:
        p.size = 200.0  # type: ignore[misc]
        raise AssertionError("Should be frozen")
    except AttributeError:
        pass


def test_position_tracker_has_required_methods() -> None:
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    assert hasattr(PositionTracker, "record_fill")
    assert hasattr(PositionTracker, "get_position")
    assert hasattr(PositionTracker, "get_all_positions")
    assert hasattr(PositionTracker, "get_total_exposure")
