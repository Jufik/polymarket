"""Tests for BacktestRunner — event-driven replay of NormalizedTrades."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.runners.backtest import BacktestResult, BacktestRunner
from polymarket_pipeline.strategies.types import ExecutionMode, TradeIntent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade(cid: str, ts: int, price: float = 0.50) -> NormalizedTrade:
    """Build a minimal NormalizedTrade for testing."""
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=Side.BUY,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=float(ts),
    )


class CountingStrategy:
    """Test helper that counts on_trade calls and emits an intent every *n* trades."""

    name: str = "counting"

    def __init__(self, emit_every: int = 1) -> None:
        self.emit_every = emit_every
        self.call_count = 0
        self.seen_trade_ids: list[str] = []

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: object,
    ) -> list[TradeIntent] | None:
        self.call_count += 1
        self.seen_trade_ids.append(trade.trade_id)
        if self.call_count % self.emit_every == 0:
            return [
                TradeIntent(
                    strategy="counting",
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="immediate",
                    max_price=float(trade.price),
                    reason="test",
                    signal_time=trade.published_at,
                )
            ]
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_empty_trades_returns_empty_result() -> None:
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy()
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw)

    result = await runner.run(trades=[])

    assert isinstance(result, BacktestResult)
    assert result.total_trades == 0
    assert result.total_intents == 0
    assert result.total_fills == 0
    assert result.fills == []


async def test_feeds_trades_in_timestamp_order() -> None:
    """Trades given out of order should be sorted by published_at before replay."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=999)  # never emit, just track order

    trades = [
        _make_trade("cid_A", 300),
        _make_trade("cid_B", 100),
        _make_trade("cid_C", 200),
    ]

    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw)
    await runner.run(trades=trades)

    # Strategy should see trades sorted by published_at: 100, 200, 300
    assert strategy.seen_trade_ids == [
        "test:cid_B:100",
        "test:cid_C:200",
        "test:cid_A:300",
    ]


async def test_collects_intents_and_fills() -> None:
    """Intents emitted by the strategy should be submitted and fills collected."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=2)  # emit on trade 2 and 4

    trades = [
        _make_trade("cid_A", 100, price=0.60),
        _make_trade("cid_B", 200, price=0.70),
        _make_trade("cid_C", 300, price=0.80),
        _make_trade("cid_D", 400, price=0.40),
    ]

    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw)
    result = await runner.run(trades=trades)

    assert result.total_trades == 4
    assert result.total_intents == 2
    assert result.total_fills == 2
    assert len(result.fills) == 2

    # Verify fill details correspond to the intents
    assert result.fills[0].condition_id == "cid_B"
    assert result.fills[0].filled_price == 0.70
    assert result.fills[1].condition_id == "cid_D"
    assert result.fills[1].filled_price == 0.40


async def test_updates_context_time() -> None:
    """Context time should be updated to each trade's published_at."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())

    observed_times: list[float] = []

    class TimeTrackingStrategy:
        name: str = "time_tracker"

        async def on_trade(
            self,
            trade: NormalizedTrade,
            ctx: InMemoryContext,
        ) -> list[TradeIntent] | None:
            t = await ctx.now()
            observed_times.append(t)
            return None

    trades = [
        _make_trade("cid_A", 500),
        _make_trade("cid_B", 100),
        _make_trade("cid_C", 300),
    ]

    runner = BacktestRunner(strategy=TimeTrackingStrategy(), ctx=ctx, gateway=gw)
    await runner.run(trades=trades)

    # After sorting: 100, 300, 500 — context time should match each trade
    assert observed_times == [100.0, 300.0, 500.0]
    # Final context time should be the last trade's published_at
    assert await ctx.now() == 500.0


async def test_multiple_intents_per_trade() -> None:
    """A strategy can return multiple intents from a single on_trade call."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())

    class MultiIntentStrategy:
        name: str = "multi_intent"

        async def on_trade(
            self,
            trade: NormalizedTrade,
            ctx: object,
        ) -> list[TradeIntent] | None:
            return [
                TradeIntent(
                    strategy="multi_intent",
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="immediate",
                    max_price=0.50,
                    reason="intent_1",
                    signal_time=trade.published_at,
                ),
                TradeIntent(
                    strategy="multi_intent",
                    condition_id=trade.condition_id,
                    side="SELL",
                    outcome="NO",
                    size_usd=5.0,
                    urgency="patient",
                    max_price=0.60,
                    reason="intent_2",
                    signal_time=trade.published_at,
                ),
            ]

    runner = BacktestRunner(strategy=MultiIntentStrategy(), ctx=ctx, gateway=gw)
    result = await runner.run(trades=[_make_trade("cid_X", 100)])

    assert result.total_trades == 1
    assert result.total_intents == 2
    assert result.total_fills == 2
    assert len(result.fills) == 2


async def test_strategy_returning_none_produces_no_intents() -> None:
    """When strategy returns None, no intents or fills should be recorded."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())

    class NoOpStrategy:
        name: str = "noop"

        async def on_trade(
            self,
            trade: NormalizedTrade,
            ctx: object,
        ) -> list[TradeIntent] | None:
            return None

    runner = BacktestRunner(strategy=NoOpStrategy(), ctx=ctx, gateway=gw)
    result = await runner.run(trades=[_make_trade("cid_A", 100), _make_trade("cid_B", 200)])

    assert result.total_trades == 2
    assert result.total_intents == 0
    assert result.total_fills == 0
    assert result.fills == []


# ---------------------------------------------------------------------------
# Position tracking & risk gate tests
# ---------------------------------------------------------------------------

_PERMISSIVE = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=1e9,
    max_position_usd=1e9,
    max_open_positions=100000,
    cooldown_s=0,
)


async def test_position_updated_after_fill() -> None:
    """After a fill, position should be updated in context."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=1)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE)

    trades = [_make_trade("cid_A", 100, price=0.60)]
    await runner.run(trades=trades)

    pos = await ctx.get_position("cid_A")
    assert pos is not None
    assert pos.condition_id == "cid_A"
    assert pos.qty_yes > 0  # BUY YES should have positive qty


async def test_risk_gate_blocks_over_capital() -> None:
    """Risk gate should block when capital is exceeded."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=1)

    tight_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=15.0,  # Allows first 10 USD fill, blocks second
        max_position_usd=1e9,
        max_open_positions=100000,
        cooldown_s=0,
    )
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=tight_config)

    # Each intent is 10 USD (from CountingStrategy). First fill uses ~10 cost_basis.
    # Second should be blocked by capital limit.
    trades = [
        _make_trade("cid_A", 100, price=0.60),
        _make_trade("cid_B", 200, price=0.50),
    ]
    result = await runner.run(trades=trades)

    assert result.total_intents == 2
    assert result.total_fills == 1  # Only first fill succeeds
    assert len(result.rejected_intents) == 1
    assert result.rejected_intents[0][1] == "capital_exceeded"


async def test_cooldown_blocks_rapid_trades() -> None:
    """Cooldown should block trades that come too quickly."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=1)

    cooldown_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=1e9,
        max_position_usd=1e9,
        max_open_positions=100000,
        cooldown_s=100,  # 100 second cooldown
    )
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=cooldown_config)

    # Trades 10 seconds apart, cooldown is 100s
    trades = [
        _make_trade("cid_A", 100, price=0.60),
        _make_trade("cid_B", 110, price=0.50),  # 10s later, within cooldown
    ]
    result = await runner.run(trades=trades)

    assert result.total_intents == 2
    assert result.total_fills == 1
    assert len(result.rejected_intents) == 1
    assert result.rejected_intents[0][1] == "cooldown"


async def test_delay_queue_holds_intents() -> None:
    """With delay_s, intents should be held until delay elapses."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(executor=SimulatedExecutor())
    strategy = CountingStrategy(emit_every=1)
    runner = BacktestRunner(
        strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE, delay_s=50.0
    )

    # Trade at t=100 generates intent, but delay_s=50 means it's ready at t=150.
    # Trade at t=120 comes before intent is ready. Trade at t=200 should drain it.
    trades = [
        _make_trade("cid_A", 100, price=0.60),
        _make_trade("cid_B", 120, price=0.50),  # intent for cid_A not yet ready
        _make_trade("cid_C", 200, price=0.70),  # now cid_A intent is ready (100+50=150 < 200)
    ]
    result = await runner.run(trades=trades)

    # All 3 trades seen, all emit intents (emit_every=1)
    assert result.total_trades == 3
    assert result.total_intents == 3
    # All fills should eventually happen (delayed intents drain at end)
    assert result.total_fills == 3
