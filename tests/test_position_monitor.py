"""Tests for PositionMonitor trailing stop loss."""

from polymarket_pipeline.strategies.execution.monitor import PositionMonitor, TrailingStop
from polymarket_pipeline.strategies.types import Fill, FillStatus


def _make_fill(
    *,
    condition_id: str = "cid_abc",
    strategy: str = "test_strat",
    side: str = "BUY",
    outcome: str = "YES",
    price: float = 0.40,
    size_usd: float = 10.0,
) -> Fill:
    return Fill(
        intent_id="int_1",
        strategy=strategy,
        condition_id=condition_id,
        side=side,
        outcome=outcome,
        filled_price=price,
        filled_size_usd=size_usd,
        fee_usd=0.0,
        status=FillStatus.FILLED,
        filled_at=1000.0,
    )


class TestTrailingStop:
    def test_stop_level(self):
        ts = TrailingStop(
            condition_id="cid",
            strategy="s",
            outcome="YES",
            asset_id="aid",
            entry_price=0.40,
            high_watermark=0.50,
            trail_delta=0.08,
        )
        assert ts.stop_level == 0.50 - 0.08  # 0.42


class TestPositionMonitor:
    def test_register_and_check_no_trigger(self):
        mon = PositionMonitor(default_trail_delta=0.10)
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 1

        # Price above stop level (0.40 - 0.10 = 0.30) — no trigger
        intent = mon.check("aid_yes", 0.35, signal_time=1001.0)
        assert intent is None

    def test_trigger_on_breach(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")

        # Price drops to stop level (0.30)
        intent = mon.check("aid_yes", 0.30, signal_time=1001.0)
        assert intent is not None
        assert intent.side == "SELL"
        assert intent.outcome == "YES"
        assert intent.condition_id == "cid_abc"
        assert intent.strategy == "test_strat"
        assert "trailing_sl" in intent.reason

        # One-shot: stop is removed after trigger
        assert mon.active_count == 0

    def test_trigger_below_stop(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")

        # Price drops well below stop level
        intent = mon.check("aid_yes", 0.20, signal_time=1001.0)
        assert intent is not None
        assert intent.side == "SELL"

    def test_ratchet_up(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")

        # Price rises to 0.60 — hwm ratchets, SL now at 0.50
        intent = mon.check("aid_yes", 0.60, signal_time=1001.0)
        assert intent is None

        # Price drops to 0.51 — still above new SL (0.50)
        intent = mon.check("aid_yes", 0.51, signal_time=1002.0)
        assert intent is None

        # Price drops to 0.50 — triggers
        intent = mon.check("aid_yes", 0.50, signal_time=1003.0)
        assert intent is not None
        assert intent.side == "SELL"
        assert "hwm=0.600" in intent.reason

    def test_no_trigger_wrong_asset(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")

        # Trade on a different asset_id — no check
        intent = mon.check("aid_no", 0.10, signal_time=1001.0)
        assert intent is None
        assert mon.active_count == 1

    def test_unregister_by_condition_id(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 1

        mon.unregister("cid_abc")
        assert mon.active_count == 0

        # Check after unregister — no trigger
        intent = mon.check("aid_yes", 0.10, signal_time=1001.0)
        assert intent is None

    def test_clear(self):
        mon = PositionMonitor()
        for i in range(5):
            fill = _make_fill(condition_id=f"cid_{i}", price=0.40)
            mon.register(fill, trail_delta=0.10, asset_id=f"aid_{i}")
        assert mon.active_count == 5

        mon.clear()
        assert mon.active_count == 0

    def test_sell_fill_ignored(self):
        mon = PositionMonitor()
        fill = _make_fill(side="SELL", price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        # SELL fills should not register a trailing stop
        assert mon.active_count == 0

    def test_no_asset_id_no_register(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="")
        assert mon.active_count == 0

    def test_default_trail_delta(self):
        mon = PositionMonitor(default_trail_delta=0.05)
        fill = _make_fill(price=0.40)
        mon.register(fill, asset_id="aid_yes")  # no trail_delta — uses default
        assert mon.active_count == 1

        # SL = 0.40 - 0.05 = 0.35 — price at 0.35 triggers
        intent = mon.check("aid_yes", 0.35, signal_time=1001.0)
        assert intent is not None

    def test_get_status(self):
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.08, asset_id="aid_yes")

        status = mon.get_status()
        assert len(status) == 1
        key = list(status.keys())[0]
        assert status[key]["entry"] == 0.40
        assert status[key]["hwm"] == 0.40
        assert status[key]["stop_level"] == 0.32
        assert status[key]["trail_delta"] == 0.08

    def test_size_usd_sentinel(self):
        """Monitor emits size_usd=0 as sentinel for runner to resolve."""
        mon = PositionMonitor()
        fill = _make_fill(price=0.40)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")

        intent = mon.check("aid_yes", 0.20, signal_time=1001.0)
        assert intent is not None
        assert intent.size_usd == 0.0  # sentinel — runner resolves actual size

    def test_skip_longshot_entry(self):
        """Entries below min_entry_price are not registered."""
        mon = PositionMonitor(min_entry_price=0.30)
        fill = _make_fill(price=0.20)  # longshot
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 0

    def test_skip_high_entry(self):
        """Entries at or above max_entry_price are not registered."""
        mon = PositionMonitor(max_entry_price=0.70)
        fill = _make_fill(price=0.75)  # high entry
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 0

    def test_entry_in_band_registers(self):
        """Entries within [min, max) band are registered."""
        mon = PositionMonitor(min_entry_price=0.30, max_entry_price=0.70)
        fill = _make_fill(price=0.50)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 1

    def test_custom_price_bounds(self):
        """Custom min/max bounds are respected."""
        mon = PositionMonitor(min_entry_price=0.10, max_entry_price=0.90)
        # 0.20 would be skipped with default bounds, but passes with 0.10
        fill = _make_fill(price=0.20)
        mon.register(fill, trail_delta=0.10, asset_id="aid_yes")
        assert mon.active_count == 1
