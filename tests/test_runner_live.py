"""Tests for LiveRunner — dispatches trades to providers then strategies."""

from __future__ import annotations

import asyncio
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
from polymarket_pipeline.strategies.types import ExecutionMode, MarketInfo, TradeIntent


def _now() -> int:
    import time

    return int(time.time())


def _trade(maker: str = "0xalice", ts: int | None = None) -> NormalizedTrade:
    if ts is None:
        ts = _now()
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


class TimerStrategy:
    """Strategy that emits intents from on_timer."""

    name = "timer_strategy"

    def __init__(self, *, size_usd: float = 3.0) -> None:
        self._size_usd = size_usd

    async def on_trade(self, trade: Any, ctx: Any) -> list[TradeIntent] | None:
        return None

    async def on_market_update(self, update: Any, ctx: Any) -> None:
        return None

    async def on_timer(self, now: float, ctx: Any) -> list[TradeIntent] | None:
        return [
            TradeIntent(
                strategy=self.name,
                condition_id="0xcond",
                side="BUY",
                outcome="YES",
                size_usd=self._size_usd,
                urgency="patient",
                max_price=0.60,
                reason="timer_test",
                signal_time=now,
            )
        ]


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


async def test_position_updated_after_fill(ctx: InMemoryContext) -> None:
    """After a fill, position should be updated in context."""
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

    pos = await ctx.get_position("0xcond")
    assert pos is not None
    assert pos.condition_id == "0xcond"
    assert pos.qty_yes > 0


async def test_risk_gate_blocks_in_live(ctx: InMemoryContext) -> None:
    """Risk gate should block intents exceeding capital."""
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    strategy = RecordingStrategy(emit=True)

    tight_cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=15.0,  # Allows first 10 USD trade but blocks second
        max_position_usd=15.0,
        max_open_positions=10,
        cooldown_s=0,
    )

    runner = LiveRunner(
        strategies=[(strategy, tight_cfg)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    # First trade should succeed (10 USD intent, but cost_basis initially 0)
    now = _now()
    await runner._handle_trade(_trade(ts=now))
    assert runner._intents_submitted == 1

    # Second trade should be blocked by risk gate
    await runner._handle_trade(_trade(ts=now + 1))
    # Still only 1 intent submitted (second was blocked)
    assert runner._intents_submitted == 1


async def test_handle_orderbook_updates_context(ctx: InMemoryContext) -> None:
    """handle_orderbook should store OrderbookSnapshot in context."""
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)

    runner = LiveRunner(
        strategies=[],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )

    runner.handle_orderbook(
        {
            "condition_id": "0xcond",
            "best_bid": 0.55,
            "best_ask": 0.58,
            "timestamp": 1000.0,
        }
    )

    ob = await ctx.get_orderbook("0xcond")
    assert ob is not None
    assert ob.best_bid == 0.55
    assert ob.best_ask == 0.58


async def test_timer_risk_gate_blocks(ctx: InMemoryContext) -> None:
    """Timer loop should apply risk gating and track positions."""
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    strategy = TimerStrategy(size_usd=3.0)

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=5.0,
        max_position_usd=5.0,
        max_open_positions=10,
        cooldown_s=0,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        timer_interval_s=0.01,
    )

    task = asyncio.create_task(runner._timer_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # At least one intent should have been submitted
    assert runner._intents_submitted >= 1

    # Position should have been tracked
    pos = await ctx.get_position("0xcond")
    assert pos is not None
    assert pos.condition_id == "0xcond"
    assert pos.qty_yes > 0


async def test_duplicate_trade_ignored(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Same trade_id dispatched twice — second should be silently dropped."""
    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )
    trade = _trade()
    await runner._handle_trade(trade)
    await runner._handle_trade(trade)  # duplicate
    assert len(strategy.trades_seen) == 1


async def test_stale_trade_ignored(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Trade with published_at older than max_trade_age_s should be dropped."""
    import time

    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        max_trade_age_s=30.0,
    )
    old_trade = _trade(ts=int(time.time()) - 120)  # 2 min old
    await runner._handle_trade(old_trade)
    assert len(strategy.trades_seen) == 0


async def test_fresh_trade_accepted(ctx: InMemoryContext, gateway: ExecutionGateway) -> None:
    """Trade within max_trade_age_s passes through."""
    import time

    strategy = RecordingStrategy()
    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        max_trade_age_s=300.0,
    )
    recent_trade = _trade(ts=int(time.time()) - 5)  # 5s old
    await runner._handle_trade(recent_trade)
    assert len(strategy.trades_seen) == 1


async def test_timer_risk_gate_rejects_over_capital(ctx: InMemoryContext) -> None:
    """Timer loop should reject intents that exceed capital after first fill."""
    executor = SimulatedExecutor()
    gateway = ExecutionGateway(executor=executor)
    # Each intent is 4.0 USD; capital is 5.0 so only first fits
    strategy = TimerStrategy(size_usd=4.0)

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=5.0,
        max_position_usd=5.0,
        max_open_positions=10,
        cooldown_s=0,
    )

    runner = LiveRunner(
        strategies=[(strategy, cfg)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
        timer_interval_s=0.01,
    )

    task = asyncio.create_task(runner._timer_loop())
    # Wait enough for several timer ticks
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Only the first intent should have been submitted; subsequent blocked
    assert runner._intents_submitted == 1


# ---------------------------------------------------------------------------
# Market sync, price update, volume accumulator
# ---------------------------------------------------------------------------


class MarketProvider:
    """Provider that exposes MarketInfo objects via get_features."""

    name = "market_provider"

    def __init__(self, markets: dict[str, MarketInfo]) -> None:
        self._markets = markets

    async def compute(self, backend: Any) -> None:
        pass

    async def on_trade(self, trade: NormalizedTrade) -> None:
        pass

    async def refresh(self, backend: Any) -> None:
        pass

    def get_features(self) -> dict[str, Any]:
        return {"will_markets": self._markets}


async def test_sync_markets_from_features(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    """Initialize should bridge MarketInfo from provider features to ctx.get_market()."""
    market = MarketInfo(
        condition_id="0xcond",
        question="Will X happen?",
        active=True,
        yes_price=0.25,
    )
    provider = MarketProvider({"0xcond": market})

    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )
    await runner.initialize()

    result = await ctx.get_market("0xcond")
    assert result is not None
    assert result.question == "Will X happen?"
    assert result.yes_price == 0.25


async def test_update_market_price_on_trade(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    """_handle_trade should update the market's yes_price from the trade."""
    market = MarketInfo(
        condition_id="0xcond",
        question="Will X happen?",
        active=True,
        yes_price=0.25,
    )
    provider = MarketProvider({"0xcond": market})
    strategy = RecordingStrategy()

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )
    await runner.initialize()

    # Trade has price=0.60 (from _trade helper)
    await runner._handle_trade(_trade())

    result = await ctx.get_market("0xcond")
    assert result is not None
    assert result.yes_price == 0.60  # updated from trade price


async def test_volume_accumulator(
    ctx: InMemoryContext, gateway: ExecutionGateway
) -> None:
    """_handle_trade should accumulate market_volume in features."""
    strategy = RecordingStrategy()

    runner = LiveRunner(
        strategies=[(strategy, _CFG)],
        providers=[],
        gateway=gateway,
        ctx=ctx,
        backend=_BACKEND,
    )
    await runner.initialize()

    # Trade: price=0.60, size=100 → volume=60
    now = _now()
    await runner._handle_trade(_trade(ts=now))

    vol = await ctx.get_features("market_volume")
    assert vol is not None
    assert vol["0xcond"] == pytest.approx(60.0)

    # Second trade accumulates
    await runner._handle_trade(_trade(maker="0xbob", ts=now + 1))
    assert vol["0xcond"] == pytest.approx(120.0)
