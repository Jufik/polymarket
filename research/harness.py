"""Backtesting harness — load data, run strategy, dump ledger, print analytics.

Two modes:
  1. Compact files (legacy): load_compact_trades → run_backtest (async BacktestRunner)
  2. Fast Parquet snapshot: run_fast_backtest (SyncReplayRunner, ~10x faster)

Usage (fast path — preferred):
    from research.harness import run_fast_backtest, print_summary
    from research.strategies.example import ExampleStrategy
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    config = StrategyConfig(
        name="test", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=1000, max_position_usd=100,
        max_open_positions=20, cooldown_s=0,
    )
    result, summary = run_fast_backtest(
        ExampleStrategy(), config,
        universe={"0xabc...", "0xdef..."},
    )
    print_summary(summary, "example")

Usage (legacy):
    import asyncio
    from research.harness import load_compact_trades, run_backtest, print_summary

    trades = load_compact_trades(max_files=10)
    result, summary = asyncio.run(run_backtest(ExampleStrategy(), trades, config))
    print_summary(summary, "example")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import structlog

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.calibrate import (
    calibrate_spreads,
    calibrate_volumes,
)
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.realistic import (
    FillModelConfig,
    RealisticFillSimulator,
)
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.ledger.analytics import (
    LedgerSummary,
    compute_summary,
)
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.backtest import BacktestRunner, BacktestResult

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.protocol import Strategy

logger = structlog.get_logger(__name__)


def load_compact_trades(
    compact_dir: Path = Path("data/compact"),
    max_files: int | None = None,
) -> list[NormalizedTrade]:
    """Load compact parquet files and convert to NormalizedTrade objects.

    Uses fastparquet (the only reader that handles DECIMAL(100,18) fields).
    Trades are returned sorted by timestamp.

    Parameters
    ----------
    compact_dir:
        Directory containing compact_NNNN.parquet files.
    max_files:
        Limit number of files for quick iteration.
    """
    from polymarket_pipeline.normalizers.sink import normalize_goldsky_row

    files = sorted(compact_dir.glob("compact_*.parquet"))
    if max_files is not None:
        files = files[:max_files]

    if not files:
        logger.warning("harness.no_compact_files", dir=str(compact_dir))
        return []

    trades: list[NormalizedTrade] = []
    for fpath in files:
        try:
            import fastparquet

            pf = fastparquet.ParquetFile(str(fpath))
            df = pf.to_pandas()
            for _, row in df.iterrows():
                try:
                    t = normalize_goldsky_row(row)
                    if t is not None:
                        trades.append(t)
                except Exception:
                    continue
        except Exception:
            logger.warning("harness.skip_file", path=str(fpath), exc_info=True)
            continue

    trades.sort(key=lambda t: t.published_at)
    logger.info("harness.loaded_trades", count=len(trades), files=len(files))
    return trades


def load_token_map(
    path: Path = Path("data/metadata/token_map.parquet"),
) -> dict[str, dict[str, str]]:
    """Load token_map: condition_id -> {YES: asset_id, NO: asset_id}."""
    df = pl.read_parquet(path)
    result: dict[str, dict[str, str]] = {}
    for row in df.iter_rows(named=True):
        cid = row["condition_id"]
        outcome = row.get("outcome", "YES")
        asset_id = row.get("asset_id", "")
        if cid not in result:
            result[cid] = {}
        result[cid][outcome] = asset_id
    return result


def load_resolutions(
    path: Path = Path("data/metadata/markets.parquet"),
) -> dict[str, tuple[str, float]]:
    """Load resolutions: condition_id -> (winner_outcome, resolved_at_epoch).

    Reads from the markets metadata parquet (CLOB API source of truth).
    """
    df = pl.read_parquet(path)
    result: dict[str, tuple[str, float]] = {}
    for row in df.iter_rows(named=True):
        cid = row.get("condition_id", "")
        winner = row.get("winner_outcome")
        resolved_at = row.get("resolved_at")
        if winner and resolved_at and cid:
            result[cid] = (str(winner), float(resolved_at))
    return result


async def run_backtest(
    strategy: Strategy,
    trades: list[NormalizedTrade],
    config: StrategyConfig,
    *,
    fill_config: FillModelConfig | None = None,
    output_dir: Path = Path("research/output"),
    resolutions: dict[str, tuple[str, float]] | None = None,
) -> tuple[BacktestResult, LedgerSummary]:
    """Run a full backtest pipeline.

    Steps:
    1. Calibrate spreads from trades (if fill_config provided)
    2. Create RealisticFillSimulator (or SimulatedExecutor if no fill_config)
    3. Create ParquetLedger
    4. Run BacktestRunner
    5. Enrich resolutions (if provided)
    6. Flush ledger to parquet
    7. Compute and return analytics summary
    """
    # Ledger
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / f"ledger_{strategy.name}.parquet"
    ledger = ParquetLedger(ledger_path)

    # Executor
    if fill_config is not None:
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
        executor = RealisticFillSimulator(
            config=fill_config,
            market_spreads=market_spreads,
            market_volumes=market_volumes,
        )
        logger.info(
            "harness.realistic_executor",
            calibrated_markets=len(market_spreads),
        )
    else:
        executor = SimulatedExecutor(fee_pct=0.0)

    # Gateway + Context
    gateway = ExecutionGateway(executor, strategy_budgets={strategy.name: config.capital_usd})
    ctx = InMemoryContext()

    # Runner
    runner = BacktestRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=config,
        ledger=ledger,
    )

    # Run
    result = await runner.run(trades)

    # Enrich resolutions
    if resolutions:
        enriched = await ledger.enrich_resolutions(resolutions)
        logger.info("harness.enriched_resolutions", count=enriched)

    # Flush
    await ledger.flush()

    # Analytics
    records = await ledger.read_all()
    summary = compute_summary(records)

    return result, summary


def run_fast_backtest(
    strategy: Strategy,
    config: StrategyConfig,
    *,
    universe: set[str] | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
    output_dir: Path = Path("research/output"),
) -> tuple[BacktestResult, LedgerSummary | None]:
    """Run a fast backtest using Parquet snapshot + SyncReplayRunner.

    Loads trades via Polars predicate pushdown and resolutions from Parquet.
    No asyncio required — fully synchronous.

    Returns (result, summary) where summary is None if no settled positions.
    """
    from research.fast_replay import load_replay_resolutions, load_replay_trades
    from research.sync_replay import SyncReplayRunner

    # Load data
    ticks = load_replay_trades(
        universe=universe, start_month=start_month, end_month=end_month,
    )
    resolutions, token_map = load_replay_resolutions()
    if universe:
        resolutions = {k: v for k, v in resolutions.items() if k in universe}

    # Setup
    executor = SimulatedExecutor(fee_pct=0.0)
    # Pass strategy_budgets=None: the gateway's cumulative spend tracker is not
    # suitable for replay — it blocks new fills once total spent >= capital_usd,
    # even after settled positions have freed that capital.  Capital constraints
    # are enforced correctly by check_risk_gate() inside SyncReplayRunner via the
    # per-strategy StrategyConfig (capital_usd, max_position_usd, max_open_positions).
    gateway = ExecutionGateway(executor, strategy_budgets=None)
    ctx = InMemoryContext()

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / f"ledger_{strategy.name}.parquet"
    ledger = ParquetLedger(ledger_path)

    runner = SyncReplayRunner(
        strategy=strategy, ctx=ctx, gateway=gateway, config=config,
        resolutions=resolutions, token_map=token_map, ledger=ledger,
    )

    # Run
    result = runner.run(ticks)

    # Flush ledger (sync via coro.send)
    from research.sync_replay import _run_coro
    _run_coro(ledger.flush())
    records = _run_coro(ledger.read_all())

    summary = compute_summary(records) if records else None

    logger.info(
        "harness.fast_backtest_complete",
        trades=result.total_trades,
        fills=result.total_fills,
        settled=runner.n_settled,
    )
    return result, summary


def print_summary(summary: LedgerSummary, name: str = "") -> None:
    """Pretty-print analytics summary to console."""
    header = f"=== {name} ===" if name else "=== Backtest Summary ==="
    print(header)
    print(f"  Fills:           {summary.total_fills}")
    print(f"  Wins/Losses:     {summary.win_count}/{summary.loss_count} (pending: {summary.pending_count})")
    print(f"  Hit Rate:        {summary.hit_rate:.1%}")
    print(f"  Total PnL (net): ${summary.total_pnl_net:,.2f}")
    print(f"  Total Fees:      ${summary.total_fees:,.2f}")
    print(f"  Avg Edge:        ${summary.avg_edge:,.4f}")
    print(f"  Sharpe:          {summary.sharpe:.2f}")
    print(f"  Max Drawdown:    ${summary.max_drawdown:,.2f}")
    print(f"  Profit Factor:   {summary.profit_factor:.2f}")
    print(f"  Avg Hold:        {summary.avg_hold_duration_s / 3600:.1f}h")
    print()
