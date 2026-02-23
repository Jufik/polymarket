"""Tests for LiveRunner — dispatches trades to providers then strategies."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner
from polymarket_pipeline.strategies.types import ExecutionMode, TradeIntent


def _trade(maker: str = "0xalice", ts: int = 1000) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{maker}:{ts}",
        condition_id="0xcond",
        asset_id="asset_1",
        side=Side.BUY,
        price=Decimal("0.60"),
        size=Decimal("100"),
        amount_usd=Decimal("60"),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


class RecordingProvider:
    """FeatureProvider that records calls."""

    name = "recorder"

    def __init__(self) -> None:
        self.compute_calls: int = 0
        self.on_trade_calls: list[str] = []
        self.refresh_calls: int = 0
        self._value: int = 0

    async def compute(self, backend: Any) -> None:
        self.compute_calls += 1
        self._value = 42

    async def on_trade(self, trade: NormalizedTrade) -> None:
        self.on_trade_calls.append(trade.trade_id)

    async def refresh(self, backend: Any) -> None:
        self.refresh_calls += 1

    def get_features(self) -> dict[str, Any]:
        return {"recorder_value": self._value}


class RecordingStrategy:
    """Strategy that records on_trade calls and optionally emits intents."""

    name = "recorder_strategy"

    def __init__(self, *, emit: bool = False) -> None:
        self.trades_seen: list[str] = []
        self._emit = emit

    async def on_trade(self, trade: NormalizedTrade, ctx: Any) -> list[TradeIntent] | None:
        self.trades_seen.append(trade.trade_id)
        if self._emit:
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="patient",
                    max_price=0.60,
                    reason="test",
                    signal_time=trade.published_at,
                )
            ]
        return None

    async def on_market_update(self, update: Any, ctx: Any) -> None:
        return None

    async def on_timer(self, now: float, ctx: Any) -> list[TradeIntent] | None:
        return None


_CFG = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.PAPER_DEV,
    capital_usd=1000.0,
    max_position_usd=100.0,
    max_open_positions=10,
    cooldown_s=300,
)

_BACKEND = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())


@pytest.fixture
def ctx() -> InMemoryContext:
    return InMemoryContext()


@pytest.fixture
def gateway() -> ExecutionGateway:
    return ExecutionGateway(executor=SimulatedExecutor())


async def test_handle_trade_dispatches_to_provider(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    strategy = RecordingStrategy()

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    await runner._handle_trade(_trade())
    assert len(provider.on_trade_calls) == 1


async def test_handle_trade_dispatches_to_strategy(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    strategy = RecordingStrategy()

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    await runner._handle_trade(_trade())
    assert len(strategy.trades_seen) == 1


async def test_providers_run_before_strategies(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    """After handle_trade, context should have provider features."""
    provider = RecordingProvider()
    provider._value = 42

    class FeatureCheckStrategy:
        name = "checker"

        def __init__(self) -> None:
            self.feature_value: Any = None

        async def on_trade(
            self, trade: NormalizedTrade, ctx: InMemoryContext
        ) -> list[TradeIntent] | None:
            self.feature_value = await ctx.get_features("recorder_value")
            return None

        async def on_market_update(self, update: Any, ctx: Any) -> None:
            return None

        async def on_timer(self, now: float, ctx: Any) -> None:
            return None

    strategy = FeatureCheckStrategy()

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],  # type: ignore[list-item]
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    await runner._handle_trade(_trade())
    assert strategy.feature_value == 42


async def test_intents_submitted_to_gateway(ctx: InMemoryContext) -> None:
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    strategy = RecordingStrategy(emit=True)

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    await runner._handle_trade(_trade())
    assert len(strategy.trades_seen) == 1
    assert runner._intents_submitted == 1


async def test_initialize_calls_provider_compute(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    provider = RecordingProvider()
    backend = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())

    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )

    await runner.initialize()
    assert provider.compute_calls == 1
