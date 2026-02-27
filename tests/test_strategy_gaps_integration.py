"""Integration test — all 5 architecture gaps exercised.

Validates:
1. Context features (not config) drive skilled trader filtering
2. Orderbook data feeds PaperExecutor pricing (fill at best_ask, not 0.50)
3. Position updated after fill
4. Risk gate blocks over-capital
5. Delay queues intent in backtest
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
from polymarket_pipeline.strategies.types import (
    ExecutionMode,
    OrderbookSnapshot,
)
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import ConsensusCopyStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKILLED_CONTEXT = frozenset({"0xeve", "0xfrank", "0xgrace"})
SKILLED_CONFIG = frozenset({"0xalice", "0xbob", "0xcharlie", "0xdave"})

_PERMISSIVE = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=1e9,
    max_position_usd=1e9,
    max_open_positions=100000,
    cooldown_s=0,
)


def _trade(
    maker: str,
    side: str,
    ts: int,
    cid: str = "0xm1",
    price: float = 0.60,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"t:{maker}:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=Side(side),
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexch",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


def _make_strategy(
    skilled: frozenset[str] = SKILLED_CONFIG,
) -> ConsensusCopyStrategy:
    cfg = ConsensusCopyConfig(
        skilled_traders=skilled,
        min_traders=3,
        agreement_pct=0.80,
        direction="NO",
        base_bet_usd=10.0,
    )
    return ConsensusCopyStrategy(cfg)


# ---------------------------------------------------------------------------
# 1. Context features drive skilled trader filtering
# ---------------------------------------------------------------------------


async def test_context_features_override_config() -> None:
    """Strategy uses skilled_traders from context, NOT config."""
    strategy = _make_strategy(skilled=SKILLED_CONFIG)
    ctx = InMemoryContext()

    # Inject different skilled set into context (like SkilledTradersProvider would)
    ctx.update_features({"skilled_traders": SKILLED_CONTEXT})

    gw = ExecutionGateway(executor=SimulatedExecutor())
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE)

    # Trades from context-skilled traders (NOT in config)
    trades = [
        _trade("0xeve", "SELL", 100),
        _trade("0xfrank", "SELL", 200),
        _trade("0xgrace", "SELL", 300),
    ]
    result = await runner.run(trades)

    # Should fire because context's skilled set is used
    assert result.total_fills == 1
    assert result.fills[0].condition_id == "0xm1"
    assert result.fills[0].outcome == "NO"


async def test_config_skilled_ignored_when_context_set() -> None:
    """Trades from config-only skilled traders should NOT fire when context overrides."""
    strategy = _make_strategy(skilled=SKILLED_CONFIG)
    ctx = InMemoryContext()

    # Context has different set — config traders are not skilled
    ctx.update_features({"skilled_traders": SKILLED_CONTEXT})

    gw = ExecutionGateway(executor=SimulatedExecutor())
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE)

    trades = [
        _trade("0xalice", "SELL", 100),
        _trade("0xbob", "SELL", 200),
        _trade("0xcharlie", "SELL", 300),
    ]
    result = await runner.run(trades)

    # No signal — alice/bob/charlie are in config but NOT in context
    assert result.total_fills == 0


# ---------------------------------------------------------------------------
# 2. Orderbook data feeds PaperExecutor pricing
# ---------------------------------------------------------------------------


async def test_orderbook_feeds_paper_executor() -> None:
    """PaperExecutor should fill at NO price from YES-side orderbook."""
    strategy = _make_strategy(skilled=SKILLED_CONFIG)
    ctx = InMemoryContext()

    # Orderbook: YES bid=0.58, ask=0.62 (consistent with trade price 0.60)
    # Consensus signal is BUY NO → NO ask = 1 - YES_bid = 0.42
    ob = OrderbookSnapshot(
        condition_id="0xm1",
        best_bid=0.58,
        best_ask=0.62,
        bid_depth=1000.0,
        ask_depth=500.0,
        timestamp=50.0,
    )
    ctx.set_orderbook("0xm1", ob)

    executor = PaperExecutor(ctx=ctx)
    gw = ExecutionGateway(executor=executor)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE)

    trades = [
        _trade("0xalice", "SELL", 100),
        _trade("0xbob", "SELL", 200),
        _trade("0xcharlie", "SELL", 300),
    ]
    result = await runner.run(trades)

    assert result.total_fills == 1
    # BUY NO → fill at NO ask = 1 - YES_bid = 1 - 0.58 = 0.42
    assert result.fills[0].filled_price == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# 3. Position updated after fill
# ---------------------------------------------------------------------------


async def test_position_tracked_after_fill() -> None:
    """After fill, context has updated position."""
    strategy = _make_strategy(skilled=SKILLED_CONFIG)
    ctx = InMemoryContext()

    gw = ExecutionGateway(executor=SimulatedExecutor())
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE)

    trades = [
        _trade("0xalice", "SELL", 100),
        _trade("0xbob", "SELL", 200),
        _trade("0xcharlie", "SELL", 300),
    ]
    result = await runner.run(trades)

    assert result.total_fills == 1
    pos = await ctx.get_position("0xm1")
    assert pos is not None
    assert pos.condition_id == "0xm1"
    # BUY NO -> qty_no > 0
    assert pos.qty_no > 0
    assert pos.cost_basis > 0


# ---------------------------------------------------------------------------
# 4. Risk gate blocks over-capital
# ---------------------------------------------------------------------------


async def test_risk_gate_blocks_second_market() -> None:
    """Risk gate blocks intent when capital is exceeded."""
    ctx = InMemoryContext()

    tight_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=12.0,  # Just enough for one 10 USD fill
        max_position_usd=1e9,
        max_open_positions=100000,
        cooldown_s=0,
    )

    gw = ExecutionGateway(executor=SimulatedExecutor())

    # We need TWO different market signals to hit capital limit.
    # Use two separate strategy instances to avoid signal_fired guard.
    # Actually, let's just use a simple strategy that always emits.
    from polymarket_pipeline.strategies.types import TradeIntent

    class AlwaysEmitStrategy:
        name = "always_emit"

        async def on_trade(self, trade: NormalizedTrade, ctx: object) -> list[TradeIntent] | None:
            return [
                TradeIntent(
                    strategy="always_emit",
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

    runner = BacktestRunner(strategy=AlwaysEmitStrategy(), ctx=ctx, gateway=gw, config=tight_config)

    trades = [
        _trade("0xalice", "SELL", 100, cid="0xm1"),
        _trade("0xbob", "SELL", 200, cid="0xm2"),
    ]
    result = await runner.run(trades)

    assert result.total_intents == 2
    assert result.total_fills == 1
    assert len(result.rejected_intents) == 1
    assert result.rejected_intents[0][1] == "capital_exceeded"


# ---------------------------------------------------------------------------
# 5. Delay queues intent in backtest
# ---------------------------------------------------------------------------


async def test_delay_queues_intent() -> None:
    """With delay_s, intent is held until delay elapses."""
    strategy = _make_strategy(skilled=SKILLED_CONFIG)
    ctx = InMemoryContext()

    gw = ExecutionGateway(executor=SimulatedExecutor())
    runner = BacktestRunner(
        strategy=strategy, ctx=ctx, gateway=gw, config=_PERMISSIVE, delay_s=250.0
    )

    # Signal fires at t=300 (3rd skilled SELL). Delay=250 means ready at t=550.
    # No more trades after t=300, so fill happens in end-of-run drain.
    trades = [
        _trade("0xalice", "SELL", 100),
        _trade("0xbob", "SELL", 200),
        _trade("0xcharlie", "SELL", 300),
    ]
    result = await runner.run(trades)

    # Signal detected at trade 3 (t=300), queued with delay
    assert result.total_intents == 1
    # Still filled (drained at end)
    assert result.total_fills == 1
    assert result.fills[0].condition_id == "0xm1"
