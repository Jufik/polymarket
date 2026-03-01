"""Tests for promotion gate checker."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.ledger.base import compute_pnl, make_ledger_record
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.promotion import (
    GateResult,
    PromotionChecker,
    PromotionReport,
    PromotionThresholds,
)
from polymarket_pipeline.strategies.types import (
    ExecutionMode,
    Fill,
    FillStatus,
    TradeIntent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(
    strategy: str = "test_strat",
    cid: str = "m1",
    signal_time: float = 1000.0,
    size: float = 100.0,
    max_price: float = 0.60,
) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id=cid,
        side="BUY",
        outcome="YES",
        size_usd=size,
        urgency="patient",
        max_price=max_price,
        reason="test",
        signal_time=signal_time,
        asset_id="a1",
    )


def _fill(
    strategy: str = "test_strat",
    cid: str = "m1",
    price: float = 0.60,
    size: float = 100.0,
    filled_at: float = 1000.05,
) -> Fill:
    return Fill(
        intent_id=f"f:{cid}:{filled_at}",
        strategy=strategy,
        condition_id=cid,
        side="BUY",
        outcome="YES",
        filled_price=price,
        filled_size_usd=size,
        fee_usd=0.0,
        status=FillStatus.FILLED,
        filled_at=filled_at,
    )


async def _build_ledger(
    n_records: int,
    *,
    tmp_path: object,
    strategy: str = "test_strat",
    time_span_s: float = 86400.0 * 10,  # 10 days
    resolve: bool = True,
    win_ratio: float = 0.6,
) -> ParquetLedger:
    """Build a ParquetLedger with synthetic records."""
    from pathlib import Path

    path = Path(str(tmp_path)) / "ledger.parquet"
    ledger = ParquetLedger(path)

    for i in range(n_records):
        t = 1000.0 + (i * time_span_s / max(n_records - 1, 1))
        cid = f"m{i}"
        price = 0.40 + (i % 5) * 0.05
        intent = _intent(strategy=strategy, cid=cid, signal_time=t, max_price=price)
        fill = _fill(strategy=strategy, cid=cid, price=price, filled_at=t + 0.05)
        record = make_ledger_record(intent, fill)

        if resolve:
            # Determine winner based on win_ratio
            won = (i / n_records) < win_ratio
            winner = "YES" if won else "NO"
            record = compute_pnl(record, winner, resolved_at=t + 86400.0)

        await ledger.append(record)

    return ledger


# ---------------------------------------------------------------------------
# vectorized → paper_dev
# ---------------------------------------------------------------------------


class TestVectorizedToPaperDev:
    @pytest.mark.asyncio
    async def test_passes_all_gates(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            2000, tmp_path=tmp_path, win_ratio=0.7,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_trades=1000, min_sharpe=0.1),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.VECTORIZED, ExecutionMode.PAPER_DEV,
        )

        assert report.all_passed
        assert len(report.gates) == 3
        assert all(g.passed for g in report.gates)

    @pytest.mark.asyncio
    async def test_fails_insufficient_trades(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            50, tmp_path=tmp_path,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_trades=1000),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.VECTORIZED, ExecutionMode.PAPER_DEV,
        )

        assert not report.all_passed
        min_trades_gate = [g for g in report.gates if g.gate_name == "min_trades"][0]
        assert not min_trades_gate.passed

    @pytest.mark.asyncio
    async def test_fails_negative_pnl(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            2000, tmp_path=tmp_path, win_ratio=0.2,  # mostly losses
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_trades=100, min_sharpe=-999.0),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.VECTORIZED, ExecutionMode.PAPER_DEV,
        )

        pnl_gate = [g for g in report.gates if g.gate_name == "positive_pnl"][0]
        assert not pnl_gate.passed

    @pytest.mark.asyncio
    async def test_replay_to_paper_dev_allowed(self, tmp_path: object) -> None:
        """REPLAY → PAPER_DEV should also be allowed."""
        ledger = await _build_ledger(
            2000, tmp_path=tmp_path, win_ratio=0.7,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_trades=100, min_sharpe=0.1),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.REPLAY, ExecutionMode.PAPER_DEV,
        )
        assert report.all_passed


# ---------------------------------------------------------------------------
# paper_dev → paper_prod
# ---------------------------------------------------------------------------


class TestPaperDevToProd:
    @pytest.mark.asyncio
    async def test_passes(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            200, tmp_path=tmp_path, time_span_s=86400.0 * 5,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_paper_fills=50, min_runtime_hours=24.0),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.PAPER_DEV, ExecutionMode.PAPER_PROD,
        )
        assert report.all_passed

    @pytest.mark.asyncio
    async def test_fails_runtime(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            200, tmp_path=tmp_path, time_span_s=3600.0,  # 1 hour
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_paper_fills=50, min_runtime_hours=24.0),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.PAPER_DEV, ExecutionMode.PAPER_PROD,
        )
        assert not report.all_passed
        runtime_gate = [g for g in report.gates if g.gate_name == "min_runtime"][0]
        assert not runtime_gate.passed


# ---------------------------------------------------------------------------
# paper_prod → live
# ---------------------------------------------------------------------------


class TestPaperProdToLive:
    @pytest.mark.asyncio
    async def test_fails_without_force(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            500, tmp_path=tmp_path, time_span_s=86400.0 * 14, win_ratio=0.7,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_live_days=7, max_drawdown=1e9),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.PAPER_PROD, ExecutionMode.LIVE,
        )
        # manual_signoff gate should fail
        assert not report.all_passed
        signoff = [g for g in report.gates if g.gate_name == "manual_signoff"][0]
        assert not signoff.passed

    @pytest.mark.asyncio
    async def test_passes_with_force(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            500, tmp_path=tmp_path, time_span_s=86400.0 * 14, win_ratio=0.7,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_live_days=7, max_drawdown=1e9),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.PAPER_PROD, ExecutionMode.LIVE,
            force=True,
        )
        assert report.all_passed

    @pytest.mark.asyncio
    async def test_fails_drawdown(self, tmp_path: object) -> None:
        ledger = await _build_ledger(
            500, tmp_path=tmp_path, time_span_s=86400.0 * 14, win_ratio=0.3,
        )
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_live_days=7, max_drawdown=0.01, manual_signoff=False),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.PAPER_PROD, ExecutionMode.LIVE,
        )
        dd_gate = [g for g in report.gates if g.gate_name == "max_drawdown"][0]
        assert not dd_gate.passed


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    @pytest.mark.asyncio
    async def test_vectorized_to_live_raises(self, tmp_path: object) -> None:
        ledger = await _build_ledger(10, tmp_path=tmp_path)
        checker = PromotionChecker(ledger)

        with pytest.raises(ValueError, match="Invalid transition"):
            await checker.check(
                "test_strat", ExecutionMode.VECTORIZED, ExecutionMode.LIVE,
            )

    @pytest.mark.asyncio
    async def test_paper_dev_to_live_raises(self, tmp_path: object) -> None:
        ledger = await _build_ledger(10, tmp_path=tmp_path)
        checker = PromotionChecker(ledger)

        with pytest.raises(ValueError, match="Invalid transition"):
            await checker.check(
                "test_strat", ExecutionMode.PAPER_DEV, ExecutionMode.LIVE,
            )


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


class TestReportStructure:
    @pytest.mark.asyncio
    async def test_report_fields(self, tmp_path: object) -> None:
        ledger = await _build_ledger(2000, tmp_path=tmp_path, win_ratio=0.7)
        checker = PromotionChecker(
            ledger,
            PromotionThresholds(min_trades=100, min_sharpe=0.1),
        )
        report = await checker.check(
            "test_strat", ExecutionMode.VECTORIZED, ExecutionMode.PAPER_DEV,
        )

        assert isinstance(report, PromotionReport)
        assert report.strategy == "test_strat"
        assert report.from_mode == ExecutionMode.VECTORIZED
        assert report.to_mode == ExecutionMode.PAPER_DEV
        assert isinstance(report.gates, list)
        assert all(isinstance(g, GateResult) for g in report.gates)
        assert report.summary is not None
        assert report.summary.total_fills > 0
