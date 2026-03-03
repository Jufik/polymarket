"""Tests for shared runner helpers -- position math and risk gate."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.runners.helpers import (
    apply_fill_to_position,
    check_risk_gate,
)
from polymarket_pipeline.strategies.types import (
    ExecutionMode,
    Fill,
    FillStatus,
    Position,
    TradeIntent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONDITION = "0xabc123"
STRATEGY = "consensus_copy"


def _make_fill(
    *,
    side: str = "BUY",
    outcome: str = "YES",
    price: float = 0.60,
    size_usd: float = 60.0,
    fee_usd: float = 0.50,
    condition_id: str = CONDITION,
    strategy: str = STRATEGY,
) -> Fill:
    return Fill(
        intent_id="int_001",
        strategy=strategy,
        condition_id=condition_id,
        side=side,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        filled_price=price,
        filled_size_usd=size_usd,
        fee_usd=fee_usd,
        status=FillStatus.FILLED,
        filled_at=1_700_000_010.0,
    )


def _make_config(
    *,
    capital_usd: float = 1000.0,
    max_position_usd: float = 200.0,
    max_open_positions: int = 5,
    cooldown_s: int = 60,
) -> StrategyConfig:
    return StrategyConfig(
        name="test",
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=capital_usd,
        max_position_usd=max_position_usd,
        max_open_positions=max_open_positions,
        cooldown_s=cooldown_s,
    )


def _make_intent(
    *,
    size_usd: float = 50.0,
    condition_id: str = CONDITION,
    strategy: str = STRATEGY,
) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id=condition_id,
        side="BUY",
        outcome="YES",
        size_usd=size_usd,
        urgency="immediate",
        max_price=0.65,
        reason="test signal",
        signal_time=1_700_000_000.0,
    )


# ---------------------------------------------------------------------------
# apply_fill_to_position
# ---------------------------------------------------------------------------


class TestApplyFillBuyFromNone:
    """BUY on a brand-new (None) position."""

    def test_buy_yes_from_none(self) -> None:
        fill = _make_fill(side="BUY", outcome="YES", price=0.50, size_usd=50.0, fee_usd=1.0)
        pos = apply_fill_to_position(None, fill)

        expected_qty = 50.0 / 0.50  # 100.0
        assert pos.condition_id == CONDITION
        assert pos.strategy == STRATEGY
        assert pos.qty_yes == pytest.approx(expected_qty)
        assert pos.qty_no == 0.0
        assert pos.avg_entry_price == pytest.approx(0.50)
        assert pos.cost_basis == pytest.approx(50.0 + 1.0)
        assert pos.realized_pnl == 0.0

    def test_buy_no_from_none(self) -> None:
        fill = _make_fill(side="BUY", outcome="NO", price=0.40, size_usd=40.0, fee_usd=0.5)
        pos = apply_fill_to_position(None, fill)

        expected_qty = 40.0 / 0.40  # 100.0
        assert pos.qty_yes == 0.0
        assert pos.qty_no == pytest.approx(expected_qty)
        assert pos.avg_entry_price == pytest.approx(0.40)
        assert pos.cost_basis == pytest.approx(40.0 + 0.5)


class TestApplyFillBuyExisting:
    """BUY on an existing position -- weighted avg_entry_yes/no."""

    def test_buy_yes_weighted_avg(self) -> None:
        old = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_yes=100.0,  # bought 100 shares at 0.50
            avg_entry_yes=0.50,
            cost_basis=50.0,
        )
        fill = _make_fill(side="BUY", outcome="YES", price=0.70, size_usd=70.0, fee_usd=1.0)
        pos = apply_fill_to_position(old, fill)

        added = 70.0 / 0.70  # 100.0
        new_qty = 100.0 + added  # 200.0
        expected_avg = (0.50 * 100.0 + 0.70 * added) / new_qty  # 0.60
        assert pos.qty_yes == pytest.approx(new_qty)
        assert pos.avg_entry_price == pytest.approx(expected_avg)
        assert pos.cost_basis == pytest.approx(50.0 + 70.0 + 1.0)

    def test_buy_no_existing(self) -> None:
        old = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_no=50.0,
            avg_entry_no=0.30,
            cost_basis=15.0,
        )
        fill = _make_fill(side="BUY", outcome="NO", price=0.40, size_usd=20.0, fee_usd=0.0)
        pos = apply_fill_to_position(old, fill)

        added = 20.0 / 0.40  # 50.0
        new_qty = 50.0 + added  # 100.0
        expected_avg = (0.30 * 50.0 + 0.40 * 50.0) / 100.0  # 0.35
        assert pos.qty_no == pytest.approx(new_qty)
        assert pos.avg_entry_price == pytest.approx(expected_avg)
        assert pos.cost_basis == pytest.approx(15.0 + 20.0)


class TestApplyFillSell:
    """SELL -- realized PnL calculation."""

    def test_sell_yes_profit(self) -> None:
        old = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_yes=100.0,
            avg_entry_yes=0.50,
            cost_basis=50.0,
            realized_pnl=0.0,
        )
        # Sell at 0.70 -> profit
        fill = _make_fill(side="SELL", outcome="YES", price=0.70, size_usd=35.0, fee_usd=0.5)
        pos = apply_fill_to_position(old, fill)

        sold_qty = 35.0 / 0.70  # 50.0
        pnl_delta = (0.70 - 0.50) * sold_qty - 0.5  # 10.0 - 0.5 = 9.5
        assert pos.qty_yes == pytest.approx(100.0 - sold_qty)
        assert pos.realized_pnl == pytest.approx(pnl_delta)
        # cost_basis unchanged on sell
        assert pos.cost_basis == pytest.approx(50.0)

    def test_sell_no_loss(self) -> None:
        old = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_no=200.0,
            avg_entry_no=0.60,
            cost_basis=120.0,
            realized_pnl=5.0,
        )
        # Sell at 0.40 -> loss
        fill = _make_fill(side="SELL", outcome="NO", price=0.40, size_usd=20.0, fee_usd=0.0)
        pos = apply_fill_to_position(old, fill)

        sold_qty = 20.0 / 0.40  # 50.0
        pnl_delta = (0.40 - 0.60) * sold_qty  # -10.0
        assert pos.qty_no == pytest.approx(200.0 - sold_qty)
        assert pos.realized_pnl == pytest.approx(5.0 + pnl_delta)  # -5.0


# ---------------------------------------------------------------------------
# check_risk_gate
# ---------------------------------------------------------------------------


class TestCheckRiskGatePass:
    """All checks pass."""

    def test_all_pass(self) -> None:
        config = _make_config(capital_usd=1000.0, max_position_usd=200.0)
        intent = _make_intent(size_usd=50.0)
        positions: dict[str, Position] = {}
        last_trade_times: dict[str, float] = {}

        allowed, reason = check_risk_gate(intent, config, positions, last_trade_times, now=100.0)
        assert allowed is True
        assert reason == ""


class TestCheckRiskGateCapital:
    """Capital limit exceeded."""

    def test_capital_exceeded(self) -> None:
        config = _make_config(capital_usd=100.0)
        # Existing position uses 80 of 100 capital
        positions = {
            "0xother": Position(
                condition_id="0xother",
                strategy=STRATEGY,
                qty_yes=100.0,
                cost_basis=80.0,
            ),
        }
        intent = _make_intent(size_usd=25.0)  # 80 + 25 = 105 > 100

        allowed, reason = check_risk_gate(intent, config, positions, {}, now=1000.0)
        assert allowed is False
        assert reason == "capital_exceeded"


class TestCheckRiskGatePositionLimit:
    """Per-market position limit exceeded."""

    def test_position_limit(self) -> None:
        config = _make_config(max_position_usd=50.0)
        positions = {
            CONDITION: Position(
                condition_id=CONDITION,
                strategy=STRATEGY,
                qty_yes=50.0,
                cost_basis=40.0,
            ),
        }
        intent = _make_intent(size_usd=15.0)  # 40 + 15 = 55 > 50

        allowed, reason = check_risk_gate(intent, config, positions, {}, now=1000.0)
        assert allowed is False
        assert reason == "position_limit"


class TestCheckRiskGateMaxOpen:
    """Max open positions limit."""

    def test_max_open_positions(self) -> None:
        config = _make_config(max_open_positions=2)
        # Two markets already open, intent targets a third
        positions = {
            "0xmarket_a": Position(condition_id="0xmarket_a", strategy=STRATEGY, qty_yes=10.0),
            "0xmarket_b": Position(condition_id="0xmarket_b", strategy=STRATEGY, qty_no=5.0),
        }
        intent = _make_intent(size_usd=10.0, condition_id="0xmarket_c")

        allowed, reason = check_risk_gate(intent, config, positions, {}, now=1000.0)
        assert allowed is False
        assert reason == "max_open_positions"

    def test_existing_open_position_does_not_count_as_new(self) -> None:
        """Adding to an already-open position should NOT trip the open-position limit."""
        config = _make_config(max_open_positions=2)
        positions = {
            CONDITION: Position(
                condition_id=CONDITION, strategy=STRATEGY, qty_yes=10.0, cost_basis=5.0
            ),
            "0xother": Position(
                condition_id="0xother", strategy=STRATEGY, qty_no=5.0, cost_basis=3.0
            ),
        }
        intent = _make_intent(size_usd=10.0, condition_id=CONDITION)

        allowed, reason = check_risk_gate(intent, config, positions, {}, now=1000.0)
        assert allowed is True
        assert reason == ""


class TestCheckRiskGateCooldown:
    """Cooldown between trades."""

    def test_cooldown_blocks(self) -> None:
        config = _make_config(cooldown_s=60)
        intent = _make_intent()
        last_trade_times = {STRATEGY: 950.0}

        allowed, reason = check_risk_gate(intent, config, {}, last_trade_times, now=1000.0)
        assert allowed is False
        assert reason == "cooldown"

    def test_cooldown_passes_after_elapsed(self) -> None:
        config = _make_config(cooldown_s=60)
        intent = _make_intent()
        last_trade_times = {STRATEGY: 930.0}

        allowed, reason = check_risk_gate(intent, config, {}, last_trade_times, now=1000.0)
        assert allowed is True
        assert reason == ""


# ---------------------------------------------------------------------------
# Cross-side avg_entry tracking
# ---------------------------------------------------------------------------


class TestApplyFillCrossSide:
    """Verify per-side avg_entry fields stay independent when both sides are held."""

    def test_buy_yes_then_buy_no_tracks_separate_avg(self) -> None:
        """Build a YES position, then add NO -- verify both avgs are independent."""
        # Start with a YES position
        fill_yes = _make_fill(side="BUY", outcome="YES", price=0.60, size_usd=60.0, fee_usd=0.0)
        pos = apply_fill_to_position(None, fill_yes)

        assert pos.avg_entry_yes == pytest.approx(0.60)
        assert pos.avg_entry_no == 0.0

        # Now buy NO at a different price
        fill_no = _make_fill(side="BUY", outcome="NO", price=0.35, size_usd=35.0, fee_usd=0.0)
        pos = apply_fill_to_position(pos, fill_no)

        # YES avg should be unchanged
        assert pos.avg_entry_yes == pytest.approx(0.60)
        # NO avg should reflect the NO fill only
        assert pos.avg_entry_no == pytest.approx(0.35)
        # Both sides should have quantity
        assert pos.qty_yes == pytest.approx(100.0)  # 60 / 0.60
        assert pos.qty_no == pytest.approx(100.0)  # 35 / 0.35

    def test_sell_yes_uses_yes_avg(self) -> None:
        """Mixed position: selling YES uses avg_entry_yes for PnL, not NO avg."""
        pos = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_yes=100.0,
            qty_no=100.0,
            avg_entry_yes=0.40,
            avg_entry_no=0.70,
            cost_basis=110.0,
            realized_pnl=0.0,
        )
        # Sell YES at 0.60 -- PnL should use avg_entry_yes=0.40, NOT avg_entry_no=0.70
        fill = _make_fill(side="SELL", outcome="YES", price=0.60, size_usd=30.0, fee_usd=0.0)
        pos = apply_fill_to_position(pos, fill)

        sold_qty = 30.0 / 0.60  # 50.0
        expected_pnl = (0.60 - 0.40) * sold_qty  # 10.0
        assert pos.realized_pnl == pytest.approx(expected_pnl)
        assert pos.qty_yes == pytest.approx(50.0)
        # NO side should be unchanged
        assert pos.qty_no == pytest.approx(100.0)
        assert pos.avg_entry_no == pytest.approx(0.70)

    def test_sell_no_uses_no_avg(self) -> None:
        """Mixed position: selling NO uses avg_entry_no for PnL, not YES avg."""
        pos = Position(
            condition_id=CONDITION,
            strategy=STRATEGY,
            qty_yes=100.0,
            qty_no=200.0,
            avg_entry_yes=0.50,
            avg_entry_no=0.30,
            cost_basis=85.0,
            realized_pnl=0.0,
        )
        # Sell NO at 0.40 -- PnL should use avg_entry_no=0.30, NOT avg_entry_yes=0.50
        fill = _make_fill(side="SELL", outcome="NO", price=0.40, size_usd=20.0, fee_usd=1.0)
        pos = apply_fill_to_position(pos, fill)

        sold_qty = 20.0 / 0.40  # 50.0
        expected_pnl = (0.40 - 0.30) * sold_qty - 1.0  # 5.0 - 1.0 = 4.0
        assert pos.realized_pnl == pytest.approx(expected_pnl)
        assert pos.qty_no == pytest.approx(150.0)
        # YES side should be unchanged
        assert pos.qty_yes == pytest.approx(100.0)
        assert pos.avg_entry_yes == pytest.approx(0.50)
