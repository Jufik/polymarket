"""Tests for strategy framework core types."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.types import (
    ExecutionMode,
    Fill,
    FillStatus,
    MarketInfo,
    OrderbookSnapshot,
    Position,
    TradeIntent,
)

# ---------------------------------------------------------------------------
# ExecutionMode enum
# ---------------------------------------------------------------------------


class TestExecutionMode:
    def test_values(self) -> None:
        assert ExecutionMode.VECTORIZED == "vectorized"
        assert ExecutionMode.REPLAY == "replay"
        assert ExecutionMode.PAPER_DEV == "paper_dev"
        assert ExecutionMode.PAPER_PROD == "paper_prod"
        assert ExecutionMode.LIVE == "live"

    def test_all_members(self) -> None:
        names = {m.name for m in ExecutionMode}
        assert names == {"VECTORIZED", "REPLAY", "PAPER_DEV", "PAPER_PROD", "LIVE"}

    def test_is_str(self) -> None:
        assert isinstance(ExecutionMode.LIVE, str)


# ---------------------------------------------------------------------------
# FillStatus enum
# ---------------------------------------------------------------------------


class TestFillStatus:
    def test_values(self) -> None:
        assert FillStatus.FILLED == "filled"
        assert FillStatus.PARTIAL == "partial"
        assert FillStatus.REJECTED == "rejected"

    def test_all_members(self) -> None:
        names = {m.name for m in FillStatus}
        assert names == {"FILLED", "PARTIAL", "REJECTED"}


# ---------------------------------------------------------------------------
# TradeIntent
# ---------------------------------------------------------------------------


class TestTradeIntent:
    @pytest.fixture()
    def intent(self) -> TradeIntent:
        return TradeIntent(
            strategy="consensus_copy",
            condition_id="0xabc123",
            side="BUY",
            outcome="YES",
            size_usd=100.0,
            urgency="immediate",
            max_price=0.65,
            reason="signal detected",
            signal_time=1700000000.0,
        )

    def test_fields(self, intent: TradeIntent) -> None:
        assert intent.strategy == "consensus_copy"
        assert intent.condition_id == "0xabc123"
        assert intent.side == "BUY"
        assert intent.outcome == "YES"
        assert intent.size_usd == 100.0
        assert intent.urgency == "immediate"
        assert intent.max_price == 0.65
        assert intent.reason == "signal detected"
        assert intent.signal_time == 1700000000.0

    def test_frozen(self, intent: TradeIntent) -> None:
        with pytest.raises(AttributeError):
            intent.side = "SELL"  # type: ignore[misc]

    def test_equality(self) -> None:
        kwargs = dict(
            strategy="s",
            condition_id="c",
            side="BUY",
            outcome="NO",
            size_usd=50.0,
            urgency="patient",
            max_price=None,
            reason="r",
            signal_time=1.0,
        )
        a = TradeIntent(**kwargs)  # type: ignore[arg-type]
        b = TradeIntent(**kwargs)  # type: ignore[arg-type]
        assert a == b

    def test_max_price_none(self) -> None:
        intent = TradeIntent(
            strategy="s",
            condition_id="c",
            side="SELL",
            outcome="NO",
            size_usd=10.0,
            urgency="patient",
            max_price=None,
            reason="r",
            signal_time=0.0,
        )
        assert intent.max_price is None


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class TestPosition:
    def test_defaults(self) -> None:
        pos = Position(condition_id="0xabc", strategy="consensus_copy")
        assert pos.qty_yes == 0.0
        assert pos.qty_no == 0.0
        assert pos.avg_entry_price == 0.0
        assert pos.cost_basis == 0.0
        assert pos.realized_pnl == 0.0

    def test_explicit_values(self) -> None:
        pos = Position(
            condition_id="0xdef",
            strategy="arb",
            qty_yes=10.5,
            qty_no=3.2,
            avg_entry_price=0.55,
            cost_basis=100.0,
            realized_pnl=-5.0,
        )
        assert pos.qty_yes == 10.5
        assert pos.qty_no == 3.2
        assert pos.avg_entry_price == 0.55
        assert pos.cost_basis == 100.0
        assert pos.realized_pnl == -5.0

    def test_frozen(self) -> None:
        pos = Position(condition_id="0x1", strategy="s")
        with pytest.raises(AttributeError):
            pos.qty_yes = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MarketInfo
# ---------------------------------------------------------------------------


class TestMarketInfo:
    def test_defaults(self) -> None:
        mi = MarketInfo(
            condition_id="0x1",
            question="Will X happen?",
            active=True,
        )
        assert mi.yes_price is None
        assert mi.no_price is None
        assert mi.event_id is None
        assert mi.category is None

    def test_full(self) -> None:
        mi = MarketInfo(
            condition_id="0x1",
            question="Will X happen?",
            active=True,
            yes_price=0.65,
            no_price=0.35,
            event_id="evt_123",
            category="politics",
        )
        assert mi.yes_price == 0.65
        assert mi.no_price == 0.35
        assert mi.event_id == "evt_123"
        assert mi.category == "politics"

    def test_frozen(self) -> None:
        mi = MarketInfo(condition_id="0x1", question="q", active=False)
        with pytest.raises(AttributeError):
            mi.active = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# OrderbookSnapshot
# ---------------------------------------------------------------------------


class TestOrderbookSnapshot:
    @pytest.fixture()
    def snap(self) -> OrderbookSnapshot:
        return OrderbookSnapshot(
            condition_id="0xabc",
            best_bid=0.60,
            best_ask=0.65,
            bid_depth=1000.0,
            ask_depth=800.0,
            timestamp=1700000000.0,
        )

    def test_fields(self, snap: OrderbookSnapshot) -> None:
        assert snap.condition_id == "0xabc"
        assert snap.best_bid == 0.60
        assert snap.best_ask == 0.65
        assert snap.bid_depth == 1000.0
        assert snap.ask_depth == 800.0
        assert snap.timestamp == 1700000000.0

    def test_spread_property(self, snap: OrderbookSnapshot) -> None:
        assert snap.spread == pytest.approx(0.05)

    def test_spread_zero(self) -> None:
        snap = OrderbookSnapshot(
            condition_id="0x1",
            best_bid=0.50,
            best_ask=0.50,
            bid_depth=100.0,
            ask_depth=100.0,
            timestamp=0.0,
        )
        assert snap.spread == 0.0

    def test_frozen(self, snap: OrderbookSnapshot) -> None:
        with pytest.raises(AttributeError):
            snap.best_bid = 0.70  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


class TestFill:
    @pytest.fixture()
    def fill(self) -> Fill:
        return Fill(
            intent_id="int_001",
            strategy="consensus_copy",
            condition_id="0xabc",
            side="BUY",
            outcome="YES",
            filled_price=0.63,
            filled_size_usd=99.5,
            fee_usd=0.50,
            status=FillStatus.FILLED,
            filled_at=1700000010.0,
        )

    def test_fields(self, fill: Fill) -> None:
        assert fill.intent_id == "int_001"
        assert fill.strategy == "consensus_copy"
        assert fill.condition_id == "0xabc"
        assert fill.side == "BUY"
        assert fill.outcome == "YES"
        assert fill.filled_price == 0.63
        assert fill.filled_size_usd == 99.5
        assert fill.fee_usd == 0.50
        assert fill.status == FillStatus.FILLED
        assert fill.filled_at == 1700000010.0
        assert fill.error is None

    def test_error_field(self) -> None:
        fill = Fill(
            intent_id="int_002",
            strategy="s",
            condition_id="0x1",
            side="SELL",
            outcome="NO",
            filled_price=0.0,
            filled_size_usd=0.0,
            fee_usd=0.0,
            status=FillStatus.REJECTED,
            filled_at=1700000020.0,
            error="insufficient liquidity",
        )
        assert fill.status == FillStatus.REJECTED
        assert fill.error == "insufficient liquidity"

    def test_partial_fill(self) -> None:
        fill = Fill(
            intent_id="int_003",
            strategy="s",
            condition_id="0x1",
            side="BUY",
            outcome="YES",
            filled_price=0.55,
            filled_size_usd=25.0,
            fee_usd=0.10,
            status=FillStatus.PARTIAL,
            filled_at=1700000030.0,
        )
        assert fill.status == FillStatus.PARTIAL

    def test_frozen(self, fill: Fill) -> None:
        with pytest.raises(AttributeError):
            fill.filled_price = 0.99  # type: ignore[misc]
