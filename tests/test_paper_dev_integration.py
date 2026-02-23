"""Integration test: full paper-dev flow with providers, strategies, and LiveRunner."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner
from polymarket_pipeline.strategies.types import ExecutionMode
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
    SkilledTradersProvider,
)
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
    ConsensusCopyStrategy,
)

CID = "0xintegration_market"
SKILLED = ["0xalice", "0xbob", "0xcharlie", "0xdave", "0xeve"]


def _trade(maker: str, ts: int, side: str = "SELL") -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"int:{maker}:{ts}",
        condition_id=CID,
        asset_id="asset_1",
        side=Side(side),
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


@pytest.fixture
def backend() -> PolarsBackend:
    """Backend with enough trades to make all 5 traders 'skilled'."""
    trades = []
    for trader in SKILLED:
        for i in range(10):  # 10 markets each -> above min_trades=5
            trades.append(
                {
                    "condition_id": f"0xhistory_{i}",
                    "maker": trader,
                    "side": "SELL",
                    "published_at": float(i),
                }
            )
    return PolarsBackend(
        trades=pl.DataFrame(trades),
        markets=pl.DataFrame(),
    )


async def test_full_paper_dev_flow(backend: PolarsBackend) -> None:
    """End-to-end: provider computes skilled -> strategy fires on consensus."""
    # Setup
    provider = SkilledTradersProvider(min_trades=5)

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=20,
        cooldown_s=300,
        features=["skilled_traders"],
    )

    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    gateway = ExecutionGateway(executor=executor)

    # First pass: initialize provider to get skilled traders
    cfg_initial = ConsensusCopyConfig(
        min_traders=3,
        agreement_pct=0.80,
        direction="NO",
        base_bet_usd=10.0,
    )
    strategy_initial = ConsensusCopyStrategy(config=cfg_initial)

    runner = LiveRunner(
        strategies=[(strategy_initial, strat_config)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )
    await runner.initialize()

    # Verify provider computed
    features = provider.get_features()
    skilled = features["skilled_traders"]
    assert len(skilled) == 5
    for trader in SKILLED:
        assert trader in skilled

    # Wire strategy with computed skilled traders
    cfg_wired = ConsensusCopyConfig(
        skilled_traders=skilled,
        min_traders=3,
        agreement_pct=0.80,
        direction="NO",
        base_bet_usd=10.0,
    )
    strategy_wired = ConsensusCopyStrategy(config=cfg_wired)

    # Recreate runner with wired strategy
    runner = LiveRunner(
        strategies=[(strategy_wired, strat_config)],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )
    await runner.initialize()

    # Feed 3 SELL trades from skilled traders -> should trigger NO signal
    await runner._handle_trade(_trade("0xalice", 1000, "SELL"))
    await runner._handle_trade(_trade("0xbob", 1001, "SELL"))
    await runner._handle_trade(_trade("0xcharlie", 1002, "SELL"))

    # Verify signal fired
    assert runner._intents_submitted == 1


async def test_provider_features_visible_in_context(backend: PolarsBackend) -> None:
    """Context should expose provider features after handle_trade."""
    provider = SkilledTradersProvider(min_trades=5)
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    gateway = ExecutionGateway(executor=executor)

    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )
    await runner.initialize()

    # Feed a trade to trigger context update
    await runner._handle_trade(_trade("0xalice", 2000))

    # Check context has the feature
    skilled = await ctx.get_features("skilled_traders")
    assert skilled is not None
    assert len(skilled) == 5
