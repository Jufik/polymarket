"""Tests for the strategy outcome ledger."""

from __future__ import annotations

import math

import pytest

from polymarket_pipeline.strategies.ledger.analytics import (
    compute_summary,
    records_to_dataframe,
)
from polymarket_pipeline.strategies.ledger.base import compute_pnl, make_ledger_record
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.ledger.types import LedgerRecord
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _intent(
    cid: str = "cid_A",
    side: str = "BUY",
    outcome: str = "YES",
    size: float = 100.0,
    max_price: float = 0.60,
    signal_time: float = 1000.0,
) -> TradeIntent:
    return TradeIntent(
        strategy="test_strat",
        condition_id=cid,
        side=side,
        outcome=outcome,
        size_usd=size,
        urgency="patient",
        max_price=max_price,
        reason="test",
        signal_time=signal_time,
        asset_id="asset_1",
    )


def _fill(
    cid: str = "cid_A",
    side: str = "BUY",
    outcome: str = "YES",
    price: float = 0.60,
    size: float = 100.0,
    fee: float = 0.0,
    status: FillStatus = FillStatus.FILLED,
    filled_at: float = 1000.05,
) -> Fill:
    return Fill(
        intent_id="fill_001",
        strategy="test_strat",
        condition_id=cid,
        side=side,
        outcome=outcome,
        filled_price=price,
        filled_size_usd=size,
        fee_usd=fee,
        status=status,
        filled_at=filled_at,
    )


# ---------------------------------------------------------------------------
# make_ledger_record
# ---------------------------------------------------------------------------


class TestMakeLedgerRecord:
    def test_basic_construction(self) -> None:
        intent = _intent()
        fill = _fill()
        record = make_ledger_record(intent, fill)

        assert record.strategy == "test_strat"
        assert record.condition_id == "cid_A"
        assert record.side == "BUY"
        assert record.outcome == "YES"
        assert record.intent_size_usd == 100.0
        assert record.fill_price == 0.60
        assert record.fill_size_usd == 100.0
        assert record.fill_status == FillStatus.FILLED
        assert record.fill_latency_ms == pytest.approx(50.0, abs=1.0)
        assert record.resolution is None
        assert record.pnl_gross is None

    def test_rejected_fill(self) -> None:
        intent = _intent()
        fill = _fill(status=FillStatus.REJECTED, price=0.0, size=0.0)
        record = make_ledger_record(intent, fill)

        assert record.fill_status == FillStatus.REJECTED
        assert record.fill_price == 0.0

    def test_record_id_from_fill(self) -> None:
        intent = _intent()
        fill = _fill()
        record = make_ledger_record(intent, fill)
        assert record.record_id == "fill_001"


# ---------------------------------------------------------------------------
# compute_pnl
# ---------------------------------------------------------------------------


class TestComputePnl:
    def test_buy_yes_wins(self) -> None:
        record = make_ledger_record(_intent(), _fill(price=0.60))
        enriched = compute_pnl(record, "YES", resolved_at=2000.0)

        assert enriched.resolution == "YES"
        assert enriched.resolved_at == 2000.0
        # qty_tokens = 100 / 0.60 = 166.67; pnl_gross = (1.0 - 0.60) * 166.67 = 66.67
        assert enriched.pnl_gross == pytest.approx(66.667, abs=0.01)
        assert enriched.pnl_net == pytest.approx(66.667, abs=0.01)
        assert enriched.hold_duration_s == pytest.approx(1000.0, abs=1.0)

    def test_buy_yes_loses(self) -> None:
        record = make_ledger_record(_intent(), _fill(price=0.60))
        enriched = compute_pnl(record, "NO", resolved_at=2000.0)

        assert enriched.resolution == "NO"
        assert enriched.pnl_gross == pytest.approx(-100.0)
        assert enriched.pnl_net == pytest.approx(-100.0)

    def test_buy_no_wins(self) -> None:
        intent = _intent(outcome="NO")
        fill = _fill(outcome="NO", price=0.40)
        record = make_ledger_record(intent, fill)
        enriched = compute_pnl(record, "NO", resolved_at=2000.0)

        # qty = 100 / 0.40 = 250; pnl = (1.0 - 0.40) * 250 = 150
        assert enriched.pnl_gross == pytest.approx(150.0)

    def test_sell_yes_wins(self) -> None:
        intent = _intent(side="SELL")
        fill = _fill(side="SELL", price=0.60)
        record = make_ledger_record(intent, fill)
        enriched = compute_pnl(record, "YES", resolved_at=2000.0)

        # Sold winner: pnl = (0.60 - 1.0) * 166.67 = -66.67
        assert enriched.pnl_gross == pytest.approx(-66.667, abs=0.01)

    def test_sell_yes_loses(self) -> None:
        intent = _intent(side="SELL")
        fill = _fill(side="SELL", price=0.60)
        record = make_ledger_record(intent, fill)
        enriched = compute_pnl(record, "NO", resolved_at=2000.0)

        # Sold loser: pnl = fill_size_usd = 100
        assert enriched.pnl_gross == pytest.approx(100.0)

    def test_fee_subtracted(self) -> None:
        intent = _intent()
        fill = _fill(price=0.60, fee=5.0)
        record = make_ledger_record(intent, fill)
        enriched = compute_pnl(record, "YES", resolved_at=2000.0)

        assert enriched.pnl_gross == pytest.approx(66.667, abs=0.01)
        assert enriched.pnl_net == pytest.approx(66.667 - 5.0, abs=0.01)

    def test_rejected_fill_no_pnl(self) -> None:
        intent = _intent()
        fill = _fill(status=FillStatus.REJECTED, price=0.0, size=0.0)
        record = make_ledger_record(intent, fill)
        enriched = compute_pnl(record, "YES", resolved_at=2000.0)

        assert enriched.resolution == "YES"
        assert enriched.pnl_gross is None


# ---------------------------------------------------------------------------
# ParquetLedger
# ---------------------------------------------------------------------------


class TestParquetLedger:
    @pytest.mark.asyncio
    async def test_append_and_read(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "ledger.parquet"
        ledger = ParquetLedger(path)

        record = make_ledger_record(_intent(), _fill())
        await ledger.append(record)

        records = await ledger.read_all()
        assert len(records) == 1
        assert records[0].strategy == "test_strat"

    @pytest.mark.asyncio
    async def test_flush_and_read_from_disk(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "ledger.parquet"
        ledger = ParquetLedger(path)

        await ledger.append(make_ledger_record(_intent(), _fill()))
        await ledger.flush()

        # Create new ledger pointing at same file
        ledger2 = ParquetLedger(path)
        records = await ledger2.read_all()
        assert len(records) == 1
        assert records[0].condition_id == "cid_A"

    @pytest.mark.asyncio
    async def test_enrich_resolutions(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "ledger.parquet"
        ledger = ParquetLedger(path)

        await ledger.append(make_ledger_record(_intent(cid="m1"), _fill(cid="m1", price=0.60)))
        await ledger.append(make_ledger_record(_intent(cid="m2"), _fill(cid="m2", price=0.30)))

        resolutions = {"m1": ("YES", 2000.0), "m2": ("NO", 2000.0)}
        count = await ledger.enrich_resolutions(resolutions)

        assert count == 2
        records = await ledger.read_all()
        r1 = [r for r in records if r.condition_id == "m1"][0]
        r2 = [r for r in records if r.condition_id == "m2"][0]

        assert r1.resolution == "YES"
        assert r1.pnl_gross is not None and r1.pnl_gross > 0  # won
        assert r2.resolution == "NO"
        assert r2.pnl_gross is not None and r2.pnl_gross < 0  # lost (bought YES, NO won)

    @pytest.mark.asyncio
    async def test_filter_by_strategy(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "ledger.parquet"
        ledger = ParquetLedger(path)

        i1 = _intent()
        f1 = _fill()
        r1 = make_ledger_record(i1, f1)

        i2 = TradeIntent(
            strategy="other_strat", condition_id="cid_B", side="BUY",
            outcome="NO", size_usd=50.0, urgency="patient",
            max_price=0.40, reason="other", signal_time=1100.0, asset_id="a2",
        )
        f2 = Fill(
            intent_id="fill_002", strategy="other_strat", condition_id="cid_B",
            side="BUY", outcome="NO", filled_price=0.40, filled_size_usd=50.0,
            fee_usd=0.0, status=FillStatus.FILLED, filled_at=1100.05,
        )
        r2 = make_ledger_record(i2, f2)

        await ledger.append_batch([r1, r2])
        filtered = await ledger.read_all(strategy="test_strat")
        assert len(filtered) == 1
        assert filtered[0].strategy == "test_strat"


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    def _make_resolved_records(self) -> list[LedgerRecord]:
        """Create a set of records with known PnL for testing analytics."""
        records: list[LedgerRecord] = []
        # 3 wins, 2 losses
        cases = [
            ("m1", 0.40, "YES", 50.0),   # BUY YES @ 0.40, YES wins → profit
            ("m2", 0.30, "YES", 30.0),   # BUY YES @ 0.30, NO wins → loss
            ("m3", 0.50, "NO", 100.0),   # BUY NO @ 0.50, NO wins → profit
            ("m4", 0.70, "YES", 70.0),   # BUY YES @ 0.70, YES wins → profit
            ("m5", 0.80, "YES", 80.0),   # BUY YES @ 0.80, NO wins → loss
        ]
        winners = {"m1": "YES", "m2": "NO", "m3": "NO", "m4": "YES", "m5": "NO"}

        for i, (cid, price, outcome, size) in enumerate(cases):
            ts = float(1000 + i * 100)
            intent = _intent(
                cid=cid, outcome=outcome, size=size,
                max_price=price, signal_time=ts,
            )
            fill = _fill(
                cid=cid, outcome=outcome, price=price,
                size=size, filled_at=ts + 0.05,
            )
            record = make_ledger_record(intent, fill)
            record = compute_pnl(record, winners[cid], resolved_at=2000.0 + i * 100)
            records.append(record)
        return records

    def test_summary_hit_rate(self) -> None:
        records = self._make_resolved_records()
        summary = compute_summary(records)

        assert summary.total_fills == 5
        assert summary.win_count == 3
        assert summary.loss_count == 2
        assert summary.hit_rate == pytest.approx(0.6)

    def test_summary_pnl_positive(self) -> None:
        records = self._make_resolved_records()
        summary = compute_summary(records)

        # Verify PnL is computed (exact values depend on prices)
        assert summary.total_pnl_net != 0.0

    def test_summary_profit_factor(self) -> None:
        records = self._make_resolved_records()
        summary = compute_summary(records)

        assert summary.profit_factor > 0.0

    def test_summary_max_drawdown(self) -> None:
        records = self._make_resolved_records()
        summary = compute_summary(records)

        assert summary.max_drawdown >= 0.0

    def test_summary_sharpe(self) -> None:
        records = self._make_resolved_records()
        summary = compute_summary(records)

        # With mixed wins/losses, Sharpe should be finite
        assert math.isfinite(summary.sharpe)

    def test_empty_records(self) -> None:
        summary = compute_summary([])
        assert summary.total_fills == 0
        assert summary.hit_rate == 0.0
        assert summary.sharpe == 0.0

    def test_all_pending(self) -> None:
        # Records with no resolution
        records = [make_ledger_record(_intent(), _fill())]
        summary = compute_summary(records)

        assert summary.total_fills == 1
        assert summary.pending_count == 1
        assert summary.win_count == 0
        assert summary.hit_rate == 0.0

    def test_records_to_dataframe(self) -> None:
        records = self._make_resolved_records()
        df = records_to_dataframe(records)

        assert len(df) == 5
        assert "strategy" in df.columns
        assert "pnl_net" in df.columns
