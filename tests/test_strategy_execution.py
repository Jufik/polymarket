"""Tests for ExecutionGateway and SimulatedExecutor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.types import FillStatus, TradeIntent


def _make_intent(
    *,
    max_price: float | None = 0.65,
    size_usd: float = 100.0,
    signal_time: float = 1_700_000_000.0,
) -> TradeIntent:
    return TradeIntent(
        strategy="test_strat",
        condition_id="0xabc123",
        side="BUY",
        outcome="YES",
        size_usd=size_usd,
        urgency="immediate",
        max_price=max_price,
        reason="unit test",
        signal_time=signal_time,
    )


# ---------------------------------------------------------------------------
# SimulatedExecutor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulated_fills_at_max_price() -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    intent = _make_intent(max_price=0.65)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.65
    assert fill.filled_size_usd == 100.0
    assert fill.condition_id == "0xabc123"
    assert fill.side == "BUY"
    assert fill.outcome == "YES"
    assert fill.strategy == "test_strat"
    assert fill.filled_at == intent.signal_time
    assert len(fill.intent_id) == 12


@pytest.mark.asyncio
async def test_simulated_fills_at_default_price_when_max_price_none() -> None:
    executor = SimulatedExecutor(fee_pct=0.02, default_price=0.50)
    intent = _make_intent(max_price=None)
    fill = await executor.execute(intent)

    assert fill.filled_price == 0.50
    assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_simulated_fee_calculation() -> None:
    # fee = fee_pct * min(price, 1 - price) * size_usd
    # fee = 0.02 * min(0.65, 0.35) * 100 = 0.02 * 0.35 * 100 = 0.70
    executor = SimulatedExecutor(fee_pct=0.02)
    intent = _make_intent(max_price=0.65, size_usd=100.0)
    fill = await executor.execute(intent)

    expected_fee = 0.02 * min(0.65, 1 - 0.65) * 100.0
    assert fill.fee_usd == pytest.approx(expected_fee)
    assert fill.fee_usd == pytest.approx(0.70)


@pytest.mark.asyncio
async def test_simulated_fee_calculation_low_price() -> None:
    # fee = 0.02 * min(0.30, 0.70) * 200 = 0.02 * 0.30 * 200 = 1.20
    executor = SimulatedExecutor(fee_pct=0.02)
    intent = _make_intent(max_price=0.30, size_usd=200.0)
    fill = await executor.execute(intent)

    assert fill.fee_usd == pytest.approx(0.02 * 0.30 * 200.0)


# ---------------------------------------------------------------------------
# ExecutionGateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_routes_to_executor() -> None:
    executor = SimulatedExecutor(fee_pct=0.02)
    gateway = ExecutionGateway(executor=executor)
    intent = _make_intent(max_price=0.65)
    fill = await gateway.submit(intent)

    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.65


@pytest.mark.asyncio
async def test_gateway_logs_intent_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "intents.jsonl"
    executor = SimulatedExecutor(fee_pct=0.02)
    gateway = ExecutionGateway(executor=executor, log_path=log_file)

    intent = _make_intent(max_price=0.65)
    await gateway.submit(intent)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["strategy"] == "test_strat"
    assert logged["condition_id"] == "0xabc123"
    assert logged["max_price"] == 0.65


@pytest.mark.asyncio
async def test_gateway_appends_multiple_intents(tmp_path: Path) -> None:
    log_file = tmp_path / "intents.jsonl"
    executor = SimulatedExecutor(fee_pct=0.02)
    gateway = ExecutionGateway(executor=executor, log_path=log_file)

    await gateway.submit(_make_intent(signal_time=1.0))
    await gateway.submit(_make_intent(signal_time=2.0))
    await gateway.submit(_make_intent(signal_time=3.0))

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 3
    times = [json.loads(line)["signal_time"] for line in lines]
    assert times == [1.0, 2.0, 3.0]
