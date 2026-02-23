"""Full integration test — backtest runner + vectorized runner + parity gate.

End-to-end validation of the strategy framework working together:
 1. BacktestRunner with real ConsensusCopyStrategy
 2. VectorizedRunner with the same scenario
 3. Parity gate confirming both paths produce identical signals
 4. StrategyRegistry creating ConsensusCopyStrategy from config
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.registry import StrategyRegistry
from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
from polymarket_pipeline.strategies.runners.parity import validate_parity
from polymarket_pipeline.strategies.runners.vectorized import VectorizedRunner
from polymarket_pipeline.strategies.types import ExecutionMode, FillStatus
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import ConsensusCopyStrategy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SKILLED = {"0xt_a", "0xt_b", "0xt_c", "0xt_d", "0xt_e"}


def _make_trade(
    cid: str,
    maker: str,
    price: float,
    ts: float,
    side: Side,
) -> NormalizedTrade:
    """Build a NormalizedTrade for integration test scenarios."""
    return NormalizedTrade(
        trade_id=f"t:{maker}:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=side,
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
        published_at=ts,
    )


def _build_scenario() -> list[NormalizedTrade]:
    """Build the canonical integration test scenario.

    - market1 (0xm1): 3 skilled SELLs -> should trigger NO signal (min_traders=3)
    - market2 (0xm2): 2 skilled BUYs  -> below min_traders=3, no signal
    - noise:          1 non-skilled BUY on 0xm1 -> ignored
    """
    return [
        _make_trade("0xm1", "0xt_a", 0.60, 100.0, Side.SELL),
        _make_trade("0xm1", "0xt_b", 0.58, 200.0, Side.SELL),
        _make_trade("0xm1", "0xt_c", 0.55, 300.0, Side.SELL),
        _make_trade("0xm2", "0xt_d", 0.70, 150.0, Side.BUY),
        _make_trade("0xm2", "0xt_e", 0.72, 250.0, Side.BUY),
        _make_trade("0xm1", "0xrandom", 0.50, 350.0, Side.BUY),
    ]


def _make_config() -> ConsensusCopyConfig:
    """Build the shared ConsensusCopyConfig for integration tests."""
    return ConsensusCopyConfig(
        skilled_traders=SKILLED,
        min_traders=3,
        agreement_pct=0.60,
        direction="NO",
        base_bet_usd=10.0,
    )


_EMPTY_MARKETS = pl.LazyFrame({"condition_id": pl.Series([], dtype=pl.Utf8)})


# ---------------------------------------------------------------------------
# 1. BacktestRunner full pipeline
# ---------------------------------------------------------------------------


async def test_backtest_runner_full_pipeline(tmp_path: Path) -> None:
    """Full pipeline: ConsensusCopyStrategy -> BacktestRunner -> 1 intent, 1 fill."""
    trades = _build_scenario()
    cfg = _make_config()
    strategy = ConsensusCopyStrategy(cfg)

    ctx = InMemoryContext()
    executor = SimulatedExecutor()
    log_path = tmp_path / "intents.jsonl"
    gateway = ExecutionGateway(executor=executor, log_path=log_path)
    runner = BacktestRunner(strategy=strategy, ctx=ctx, gateway=gateway)

    result = await runner.run(trades)

    # 6 trades fed in
    assert result.total_trades == 6

    # Only market1 has 3 skilled SELLs (all NO direction) -> 1 intent
    assert result.total_intents == 1

    # SimulatedExecutor fills every intent
    assert result.total_fills == 1
    assert len(result.fills) == 1

    fill = result.fills[0]
    assert fill.condition_id == "0xm1"
    assert fill.outcome == "NO"
    assert fill.side == "BUY"
    assert fill.status == FillStatus.FILLED
    assert fill.strategy == "consensus_copy"

    # Intent log file should have been written
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# 2. VectorizedRunner same result
# ---------------------------------------------------------------------------


def test_vectorized_runner_same_result() -> None:
    """Same scenario through VectorizedRunner produces matching signal."""
    trades = _build_scenario()
    cfg = _make_config()
    strategy = ConsensusCopyStrategy(cfg)

    trades_lf = pl.DataFrame(
        {
            "condition_id": [t.condition_id for t in trades],
            "published_at": [t.published_at for t in trades],
            "price": [float(t.price) for t in trades],
            "side": [t.side.value for t in trades],
            "maker": [t.maker for t in trades],
            "trade_id": [t.trade_id for t in trades],
        }
    ).lazy()

    runner = VectorizedRunner(strategy)
    result = runner.run(trades=trades_lf, markets=_EMPTY_MARKETS)

    # 1 signal for market1
    assert result.total_signals == 1

    row = result.signals.row(0, named=True)
    assert row["condition_id"] == "0xm1"
    assert row["outcome"] == "NO"
    assert row["side"] == "BUY"


# ---------------------------------------------------------------------------
# 3. Parity gate — consensus_copy
# ---------------------------------------------------------------------------


async def test_parity_gate_consensus_copy() -> None:
    """Both paths (event-driven replay + vectorized) produce the same signal."""
    trades = _build_scenario()
    cfg = _make_config()
    strategy = ConsensusCopyStrategy(cfg)

    report = await validate_parity(strategy, trades, _EMPTY_MARKETS)

    assert report.is_aligned is True
    assert report.vectorized_count == 1
    assert report.replay_count == 1
    assert report.vectorized_markets == {"0xm1"}
    assert report.replay_markets == {"0xm1"}
    assert report.missing_in_replay == set()
    assert report.extra_in_replay == set()


# ---------------------------------------------------------------------------
# 4. Registry creates ConsensusCopyStrategy
# ---------------------------------------------------------------------------


def test_registry_creates_consensus_copy() -> None:
    """StrategyRegistry can instantiate ConsensusCopyStrategy from config params."""
    registry = StrategyRegistry()
    registry.register("consensus_copy", ConsensusCopyStrategy)

    cc_cfg = _make_config()
    strategy_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=1000.0,
        max_position_usd=100.0,
        max_open_positions=20,
        cooldown_s=300,
        params={"config": cc_cfg},
    )

    instance = registry.create("consensus_copy", strategy_config)

    assert isinstance(instance, ConsensusCopyStrategy)
    assert instance.name == "consensus_copy"
