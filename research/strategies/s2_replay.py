"""S2 replay helper — wraps ReplayRunner for hit-rate copy strategy."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
from polymarket_pipeline.strategies.ledger.analytics import (
    LedgerSummary,
    compute_summary,
)
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.backtest import BacktestResult
from polymarket_pipeline.strategies.runners.replay import (
    MarketResolution,
    ReplayRunner,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.protocol import Strategy


async def run_s2_replay(
    strategy: Strategy,
    trades: list[NormalizedTrade],
    config: StrategyConfig,
    *,
    resolutions: dict[str, tuple[str, float]] | None = None,
    token_map: dict[str, dict[str, str]] | None = None,
    market_tags: dict[str, str] | None = None,
    fill_config: FillModelConfig | None = None,
    output_dir: Path = Path("research/output"),
) -> tuple[BacktestResult, LedgerSummary]:
    """Run tick-by-tick replay with ReplayRunner and mid-run settlement.

    Parameters
    ----------
    strategy:
        S2HitRateCopy instance (with qualified traders already set).
    trades:
        Historical trades sorted by timestamp.
    config:
        StrategyConfig with capital/risk parameters.
    resolutions:
        condition_id -> (winner_outcome, resolved_at_epoch).
        Used to build MarketResolution objects for mid-run settlement.
    token_map:
        condition_id -> {"YES": asset_id, "NO": asset_id}.
    market_tags:
        condition_id -> primary_tag. Required for tag-aware mode.
        Call strategy.set_market_tags() before replay if provided.
    fill_config:
        RealisticFillSimulator config. None = default realistic fills.
    output_dir:
        Directory for ledger parquet output.
    """
    # Set market tags on strategy if provided (tag-aware mode)
    if market_tags is not None and hasattr(strategy, "set_market_tags"):
        strategy.set_market_tags(market_tags)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / f"ledger_{strategy.name}.parquet"
    ledger = ParquetLedger(ledger_path)

    # Build MarketResolution objects (asset_id-based)
    market_resolutions: dict[str, MarketResolution] = {}
    token_map = token_map or {}
    if resolutions:
        for cid, (winner_outcome, resolved_at) in resolutions.items():
            cid_tokens = token_map.get(cid, {})
            winning_asset = cid_tokens.get(winner_outcome, "")
            winning_ids = frozenset({winning_asset}) if winning_asset else frozenset()
            market_resolutions[cid] = MarketResolution(
                condition_id=cid,
                resolved_at=resolved_at,
                winning_asset_ids=winning_ids,
            )

    # Executor
    fc = fill_config or FillModelConfig()
    if trades:
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
    else:
        market_spreads, market_volumes = {}, {}

    executor = RealisticFillSimulator(
        config=fc,
        market_spreads=market_spreads,
        market_volumes=market_volumes,
    )

    # Gateway + Context
    # For replay: set gateway budget very high.
    # The *real* capital constraint is in check_risk_gate() via cost_basis
    # (which resets on settlement). The gateway's cumulative _strategy_spent
    # counter never resets, so it must be set high enough to never bind.
    gateway = ExecutionGateway(
        executor, strategy_budgets={strategy.name: 1_000_000}
    )
    ctx = InMemoryContext()

    # Runner
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=config,
        resolutions=market_resolutions,
        token_map=token_map,
        ledger=ledger,
    )

    result = await runner.run(trades)

    # Analytics
    records = await ledger.read_all()
    summary = compute_summary(records)

    return result, summary
