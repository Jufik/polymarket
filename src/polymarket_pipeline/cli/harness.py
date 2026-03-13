"""pm-harness — production replay harness for research hypotheses.

Drives ReplayRunner + ExecutionGateway from a single TOML config.
Same execution path used by pm-strategy for paper trading.

Usage:
    uv run pm-harness run --config research/hypotheses/my-hyp/config.toml \\
        --period 2025-01-01:2026-01-01 \\
        --output research/hypotheses/my-hyp/validation/
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import typer

logger = structlog.get_logger(__name__)

app = typer.Typer(name="pm-harness", help="Production replay harness for research hypotheses.")


def _parse_period(period: str) -> tuple[float, float]:
    """Parse 'YYYY-MM-DD:YYYY-MM-DD' into (start_epoch, end_epoch)."""
    parts = period.split(":")
    if len(parts) != 2:
        raise typer.BadParameter(f"Period must be START:END, got {period!r}")
    start = datetime.strptime(parts[0].strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(parts[1].strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    if end <= start:
        raise typer.BadParameter(f"End must be after start: {period!r}")
    return start.timestamp(), end.timestamp()


async def _run_harness(
    config_path: Path,
    period: str,
    output_dir: Path,
    *,
    walk_forward: bool = False,
    verbose: bool = False,
) -> None:
    """Core harness execution — async entry point."""
    from polymarket_pipeline.strategies.config import (
        load_harness_config,
        load_provider_configs,
        load_strategy_configs,
    )
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
    from polymarket_pipeline.strategies.ledger.analytics import compute_summary
    from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
    from polymarket_pipeline.strategies.runners.replay import (
        ReplayRunner,
        load_resolutions_from_rows,
    )

    start_epoch, end_epoch = _parse_period(period)
    harness_cfg = load_harness_config(config_path)
    strategy_cfgs = load_strategy_configs(config_path, enabled_only=True)
    provider_cfgs = load_provider_configs(config_path, enabled_only=True)

    if not strategy_cfgs:
        logger.error("harness.no_strategies", config=str(config_path))
        raise typer.Exit(code=1)

    # Take first strategy (harness runs one at a time)
    strat_name, strat_cfg = next(iter(strategy_cfgs.items()))
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "harness.start",
        strategy=strat_name,
        period=period,
        executor=harness_cfg.executor,
        settlement=harness_cfg.settlement_enabled,
        output=str(output_dir),
    )

    # ── Build strategy + providers (FIRST — needed for pre-filter) ──
    import clickhouse_connect

    from polymarket_pipeline.cli.strategy import (
        _PROVIDER_REGISTRY,
        _STRATEGY_FACTORIES,
        _register_providers,
        _register_strategies,
    )

    _register_strategies()
    _register_providers()

    ctx = InMemoryContext()

    # Instantiate providers
    providers = []
    for pname, pcfg in provider_cfgs.items():
        if pname not in _PROVIDER_REGISTRY:
            logger.warning("harness.unknown_provider", name=pname)
            continue
        provider_cls = _PROVIDER_REGISTRY[pname]
        provider = provider_cls(**pcfg.params)
        providers.append(provider)

    # Instantiate strategy
    if strat_name not in _STRATEGY_FACTORIES:
        logger.error("harness.unknown_strategy", name=strat_name)
        raise typer.Exit(code=1)
    strategy = _STRATEGY_FACTORIES[strat_name](strat_cfg)

    # ── Bootstrap providers ───────────────────────────────────────
    for provider in providers:
        if hasattr(provider, "compute"):
            await provider.compute(ctx)
        if hasattr(provider, "get_features"):
            ctx.update_features(provider.get_features())

    # ── Load trades from ClickHouse (with pre-filter) ─────────────
    ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

    # Load resolutions
    res_rows = ch.query(
        "SELECT condition_id, asset_id, outcome, token_won, "
        "toUnixTimestamp(resolved_at) AS resolved_epoch "
        "FROM polymarket.markets_resolved "
        "WHERE token_won IS NOT NULL AND resolved_at IS NOT NULL"
    )
    res_dicts = [dict(zip(res_rows.column_names, row)) for row in res_rows.result_rows]
    resolutions, token_map = load_resolutions_from_rows(res_dicts)
    logger.info("harness.resolutions_loaded", count=len(resolutions))

    # Build maker filter from qualified traders (pre_filter_makers speedup)
    from polymarket_pipeline.models import NormalizedTrade

    maker_filter_clause = ""
    if harness_cfg.pre_filter_makers:
        all_qualified: set[str] = set()
        for provider in providers:
            feats = provider.get_features() if hasattr(provider, "get_features") else {}
            qt = feats.get("qualified_traders", {})
            for trader_set in qt.values():
                all_qualified.update(trader_set)

        if all_qualified:
            quoted_makers = ", ".join(f"'{m}'" for m in all_qualified)
            # Use a temp table approach for large IN-lists to avoid query size limits
            ch.command(
                f"CREATE OR REPLACE TABLE _tmp_harness_makers ENGINE = Memory AS "
                f"SELECT arrayJoin([{quoted_makers}]) AS maker"
            )
            maker_filter_clause = " AND lower(maker) IN (SELECT maker FROM _tmp_harness_makers)"
            logger.info("harness.pre_filter_makers", n_qualified=len(all_qualified))

    trade_rows = ch.query(
        "SELECT trade_id, condition_id, asset_id, side, price, size, amount_usd, fee_usd, "
        "maker, taker, timestamp, source, tx_hash, order_hash, block_number, is_backfill, "
        "_version AS version, ingested_at, "
        # published_at=0 for backfill; fall back to toUnixTimestamp(timestamp)
        "if(published_at > 0, published_at, toUnixTimestamp(timestamp)) AS published_at "
        "FROM polymarket.trades_raw FINAL "
        f"WHERE timestamp >= %(start)s AND timestamp < %(end)s"
        f"{maker_filter_clause} "
        "ORDER BY timestamp",
        parameters={"start": int(start_epoch), "end": int(end_epoch)},
    )
    trades: list[NormalizedTrade] = []
    col_names = trade_rows.column_names
    for row in trade_rows.result_rows:
        row_dict = dict(zip(col_names, row))
        try:
            t = NormalizedTrade.model_validate(row_dict)
            trades.append(t)
        except Exception:
            continue

    logger.info("harness.trades_loaded", count=len(trades), period=period)

    if not trades:
        logger.warning("harness.no_trades", period=period)
        raise typer.Exit(code=1)

    # ── Build executor ────────────────────────────────────────────
    executor: RealisticFillSimulator | SimulatedExecutor
    if harness_cfg.executor == "realistic":
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
        executor = RealisticFillSimulator(
            config=FillModelConfig(),
            market_spreads=market_spreads,
            market_volumes=market_volumes,
        )
        logger.info("harness.realistic_executor", markets=len(market_spreads))
    else:
        executor = SimulatedExecutor(fee_pct=0.0)
        logger.info("harness.simulated_executor")

    # ── Build gateway + context ───────────────────────────────────
    log_path = output_dir / "replay_log.jsonl"
    # For replay: use no gateway budget (risk gate in ReplayRunner handles capital recycling).
    # The gateway cumulative budget would block after capital_usd/max_position_usd fills,
    # but positions settle mid-replay and capital recycles — gateway budget can't model this.
    gateway = ExecutionGateway(
        executor,
        log_path=log_path,
        strategy_budgets=None,
    )

    # ── Build runner ──────────────────────────────────────────────
    ledger = ParquetLedger(output_dir / "ledger.parquet")
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=strat_cfg,
        providers=providers,
        resolutions=resolutions if harness_cfg.settlement_enabled else None,
        token_map=token_map if harness_cfg.settlement_enabled else None,
        ledger=ledger,
    )

    # ── Run ───────────────────────────────────────────────────────
    result = await runner.run(trades)

    # ── Post-processing ───────────────────────────────────────────
    records = await ledger.read_all()
    summary = compute_summary(records)

    # Write summary.json
    summary_path = output_dir / "summary.json"
    summary_dict: dict[str, Any] = {
        "strategy": strat_name,
        "period": period,
        "executor": harness_cfg.executor,
        "settlement": harness_cfg.settlement_enabled,
        "total_trades": result.total_trades,
        "total_intents": result.total_intents,
        "total_fills": result.total_fills,
        "settled": runner.n_settled,
        "rejected": len(result.rejected_intents),
        "hit_rate": round(summary.hit_rate, 4),
        "sharpe": round(summary.sharpe, 4),
        "total_pnl_net": round(summary.total_pnl_net, 2),
        "avg_edge": round(summary.avg_edge, 4),
        "max_drawdown": round(summary.max_drawdown, 2),
        "profit_factor": round(summary.profit_factor, 4),
        "avg_hold_hours": round(summary.avg_hold_duration_s / 3600, 1),
    }
    summary_path.write_text(json.dumps(summary_dict, indent=2))
    await ledger.flush()

    # Print summary
    logger.info("harness.complete", **summary_dict)
    print(f"\n{'='*60}")
    print(f"  Strategy: {strat_name}")
    print(f"  Period:   {period}")
    print(f"  Fills:    {result.total_fills} ({runner.n_settled} settled)")
    print(f"  HR:       {summary.hit_rate:.1%}")
    print(f"  PnL:      ${summary.total_pnl_net:,.2f}")
    print(f"  Sharpe:   {summary.sharpe:.2f}")
    print(f"  Drawdown: ${summary.max_drawdown:,.2f}")
    print(f"  Avg Hold: {summary.avg_hold_duration_s / 3600:.1f}h")
    print(f"{'='*60}")
    print(f"  Output:   {output_dir}")
    print(f"  Ledger:   {output_dir / 'ledger.parquet'}")
    print(f"  Summary:  {summary_path}")
    print()


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="TOML config file"),
    period: str = typer.Option(
        ..., "--period", "-p", help="Replay period START:END (YYYY-MM-DD:YYYY-MM-DD)"
    ),
    output: Path = typer.Option(
        Path("research/output"), "--output", "-o", help="Output directory"
    ),
    walk_forward: bool = typer.Option(False, "--walk-forward", help="Walk-forward windowing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """Run the production replay harness for a hypothesis."""
    if not config.exists():
        typer.echo(f"Error: config file not found: {config}", err=True)
        raise typer.Exit(code=1)

    asyncio.run(_run_harness(config, period, output, walk_forward=walk_forward, verbose=verbose))


if __name__ == "__main__":
    app()
