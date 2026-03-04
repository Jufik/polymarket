"""CLI entry point for running strategies against live Kafka feed.

Usage:
    uv run pm-strategy run --config strategies.toml
    uv run pm-strategy run --config strategies.toml --only consensus_copy
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
import typer

from polymarket_pipeline.strategies.config import (
    load_execution_config,
    load_provider_configs,
    load_strategy_configs,
)
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner

logger = structlog.get_logger(__name__)

app = typer.Typer(name="pm-strategy", help="Strategy execution CLI.")


# ---------------------------------------------------------------------------
# Provider registry (manual for now — only SkilledTradersProvider)
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, type[Any]] = {}


def _register_providers() -> None:
    """Register known provider classes.

    Add new provider registrations here when implementing strategies.
    """
    try:
        from polymarket_pipeline.strategies_impl.s2_hitrate_copy.provider import S2Provider

        _PROVIDER_REGISTRY["s2_provider"] = S2Provider
    except ImportError:
        logger.warning("provider.skip", name="s2_provider", reason="import failed")

    from polymarket_pipeline.strategies_impl.s2_insider_copy.provider import InsiderCopyProvider

    _PROVIDER_REGISTRY["insider_copy_provider"] = InsiderCopyProvider

    from polymarket_pipeline.strategies_impl.s3_no_sniper.provider import S3DataProvider

    _PROVIDER_REGISTRY["s3_data_provider"] = S3DataProvider


# ---------------------------------------------------------------------------
# Strategy factory registry
# ---------------------------------------------------------------------------

_STRATEGY_FACTORIES: dict[str, Any] = {}


def _register_strategies() -> None:
    """Register known strategy factories.

    Add new strategy factories here when implementing strategies.
    """
    try:
        from polymarket_pipeline.strategies_impl.s2_hitrate_copy.strategy import (
            create_s2_strategy,
        )

        _STRATEGY_FACTORIES["s2_hitrate_copy"] = create_s2_strategy
    except ImportError:
        logger.warning("strategy.skip", name="s2_hitrate_copy", reason="import failed")

    from polymarket_pipeline.strategies_impl.s2_insider_copy.strategy import (
        create_insider_copy_strategy,
    )

    _STRATEGY_FACTORIES["s2_insider_sports"] = create_insider_copy_strategy
    _STRATEGY_FACTORIES["s2_insider_politics"] = create_insider_copy_strategy
    _STRATEGY_FACTORIES["s2_insider_misc"] = create_insider_copy_strategy

    from polymarket_pipeline.strategies_impl.s3_no_sniper.strategy import (
        create_s3_no_sniper_strategy,
    )

    _STRATEGY_FACTORIES["s3_no_sniper"] = create_s3_no_sniper_strategy


# ---------------------------------------------------------------------------
# Runner assembly
# ---------------------------------------------------------------------------


def _build_runner(
    config_path: Path,
    *,
    only: str | None = None,
    log_dir: Path | None = None,
) -> LiveRunner:
    """Assemble a LiveRunner from TOML config.

    Parameters
    ----------
    config_path:
        Path to the TOML config file.
    only:
        If set, only run this strategy (by name).
    log_dir:
        Directory for intent logs. Defaults to no file logging.
    """
    _register_strategies()
    _register_providers()

    # Load configs
    strategy_configs = load_strategy_configs(config_path, enabled_only=True)
    provider_configs = load_provider_configs(config_path, enabled_only=True)

    # Filter if --only
    if only:
        strategy_configs = {k: v for k, v in strategy_configs.items() if k == only}

    # Validate feature dependencies
    for name, cfg in strategy_configs.items():
        for feat in cfg.features:
            if feat not in provider_configs and feat not in _PROVIDER_REGISTRY:
                msg = (
                    f"Strategy {name!r} requires feature provider {feat!r} but it is not configured"
                )
                raise ValueError(msg)

    # Create providers
    providers = []
    needed_providers: set[str] = set()
    for cfg in strategy_configs.values():
        needed_providers.update(cfg.features)

    for pname in needed_providers:
        if pname in _PROVIDER_REGISTRY:
            pcfg = provider_configs.get(pname)
            params = pcfg.params if pcfg else {}
            provider = _PROVIDER_REGISTRY[pname](**params)
            providers.append(provider)

    # Create strategies
    strategies = []
    for sname, scfg in strategy_configs.items():
        if sname in _STRATEGY_FACTORIES:
            factory = _STRATEGY_FACTORIES[sname]
            strategy = factory(scfg)
            strategies.append((strategy, scfg))
        else:
            logger.warning("strategy.unknown", name=sname)

    # Load execution config (OrderConfig)
    from polymarket_pipeline.strategies.execution.live import OrderConfig

    exec_raw = load_execution_config(config_path)
    order_config = OrderConfig(**exec_raw) if exec_raw else OrderConfig()

    # Assemble — optionally use Redis-backed context for orderbook reads
    from polymarket_pipeline.live.settings import Settings as _LiveSettings

    _live_settings = _LiveSettings()
    if _live_settings.redis_orderbook_enabled:
        import redis.asyncio as aioredis

        from polymarket_pipeline.strategies.context.redis import RedisContext

        _redis_client = aioredis.from_url(
            _live_settings.redis_url, decode_responses=False
        )
        ctx = RedisContext(redis=_redis_client, inner=InMemoryContext())
    else:
        ctx = InMemoryContext()
    # ClobClient for CLOB REST API price verification (public, no auth needed)
    from polymarket_pipeline.execution.clob_client import ClobClient

    clob_client = ClobClient()  # default base_url, no auth for /book
    # token_map loaded async at startup — placeholder here, filled in _run()
    executor = PaperExecutor(ctx=ctx, clob_client=clob_client, order_config=order_config)
    log_path = (log_dir / "intents.jsonl") if log_dir else None
    # Use delay_s from first strategy's params (if any)
    delay_s = 0.0
    if strategies:
        first_params = strategies[0][1].params
        delay_s = float(first_params.get("delay_s", 0.0))

    # Wire per-strategy budget caps from capital_usd
    strategy_budgets = {name: cfg.capital_usd for name, cfg in strategy_configs.items()}
    gateway = ExecutionGateway(
        executor=executor,
        log_path=log_path,
        delay_s=delay_s,
        strategy_budgets=strategy_budgets,
    )

    # Use ClickHouse backend so providers get real market data at startup
    from polymarket_pipeline.live.settings import Settings

    settings = Settings()
    backend = ClickHouseBackend(
        host=settings.ch_host,
        port=settings.ch_port,
        database=settings.ch_database,
    )

    return LiveRunner(
        strategies=strategies,
        providers=providers,
        gateway=gateway,
        ctx=ctx,
        backend=backend,
        intent_cb=None,  # wired at runtime when broker is available
    )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="Path to strategies TOML"),
    only: str | None = typer.Option(None, "--only", help="Run only this strategy"),
    log_dir: Path | None = typer.Option(
        "logs/paper", "--log-dir", help="Intent/fill log directory"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all logs on console"),
) -> None:
    """Start strategies in paper-dev mode against live Kafka."""
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)

    # Configure logging: quiet console (WARN+) + full JSON file
    from polymarket_pipeline.logging_config import configure_paper_logging

    if log_dir is not None:
        configure_paper_logging(log_dir)
        if verbose:
            # Override console handler to INFO
            import logging

            for h in logging.getLogger().handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(
                    h, logging.handlers.RotatingFileHandler
                ):
                    h.setLevel(logging.INFO)

    logger.warning("strategy_cli.starting", config=str(config), only=only, log_dir=str(log_dir))

    runner = _build_runner(config, only=only, log_dir=log_dir)

    # Unique group per process — avoids partition starvation when multiple
    # strategy processes share a single-partition topic.
    group_suffix = config.stem  # e.g. "will_no" from configs/will_no.toml
    group_id = f"strategy-{group_suffix}"

    async def _run() -> None:
        import json
        import signal
        from datetime import datetime, timezone

        import asyncpg
        from faststream.kafka import KafkaBroker

        from polymarket_pipeline.live.ingestors._publish import safe_publish
        from polymarket_pipeline.live.settings import Settings

        settings = Settings()
        broker = KafkaBroker(settings.redpanda_url, compression_type="lz4", linger_ms=50)

        # SIGUSR1 → reset all paper state (positions, budgets, counters)
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            signal.SIGUSR1,
            lambda: runner.reset(),
        )

        # PG pool for intent persistence
        pg_pool = await asyncpg.create_pool(dsn=settings.pg_dsn, min_size=1, max_size=2)

        # Load token_map from PG for CLOB REST API price verification
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT condition_id, outcome, asset_id FROM token_market_map"
            )
        token_map: dict[str, dict[str, str]] = {}
        for r in rows:
            cid = r["condition_id"]
            if cid not in token_map:
                token_map[cid] = {}
            token_map[cid][r["outcome"]] = r["asset_id"]
        logger.warning("token_map.loaded", count=len(token_map))

        # Wire token_map into executor
        runner.gateway.executor._token_map = token_map  # type: ignore[attr-defined]

        # Wire intent capture → Kafka topic + PostgreSQL
        async def _publish_intent(record: dict[str, Any]) -> None:
            key = f"{record['strategy']}:{record['condition_id']}".encode()
            await safe_publish(
                broker,
                message=json.dumps(record, default=str),
                topic="strategy.intents",
                key=key,
                source="strategy_runner",
            )
            # Persist to PG (best-effort, don't block hot path)
            # Skip risk_rejected — these flood PG (~252 per 30s from proportional_copy)
            if record["disposition"] == "risk_rejected":
                return

            fill = record.get("fill")
            metadata = record.get("metadata") or {}
            try:
                async with pg_pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO strategy_intents
                           (strategy, condition_id, side, outcome, size_usd,
                            urgency, max_price, reason, signal_time, asset_id,
                            disposition, rejection_reason,
                            filled_price, filled_size_usd, fee_usd, metadata,
                            captured_at)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
                        record["strategy"],
                        record["condition_id"],
                        record["side"],
                        record["outcome"],
                        record["size_usd"],
                        record.get("urgency", "patient"),
                        record.get("max_price"),
                        record.get("reason", ""),
                        datetime.fromtimestamp(record["signal_time"], tz=timezone.utc),
                        record.get("asset_id"),
                        record["disposition"],
                        record.get("rejection_reason", ""),
                        fill["filled_price"] if fill else None,
                        fill["filled_size_usd"] if fill else None,
                        fill["fee_usd"] if fill else None,
                        json.dumps(metadata, default=str),
                        datetime.fromtimestamp(record["captured_at"], tz=timezone.utc),
                    )
            except Exception:
                logger.exception("intent_pg.write_error")

        runner._intent_cb = _publish_intent

        # Wire pool refresh callback → PG strategy_pool table
        async def _publish_pool(provider_name: str, features: dict[str, Any]) -> None:
            # Only publish for providers that expose a pool (frozenset of addresses)
            pool = features.get("pool_traders")
            if pool is None or not isinstance(pool, (set, frozenset)):
                return
            strategy_name = provider_name  # provider name matches strategy key
            try:
                async with pg_pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM strategy_pool WHERE strategy = $1",
                            strategy_name,
                        )
                        if pool:
                            await conn.executemany(
                                """INSERT INTO strategy_pool (strategy, trader_address)
                                   VALUES ($1, $2)""",
                                [(strategy_name, addr) for addr in pool],
                            )
                logger.info(
                    "pool_pg.published",
                    strategy=strategy_name,
                    count=len(pool),
                )
            except Exception:
                logger.exception("pool_pg.write_error", strategy=strategy_name)

        runner._pool_refresh_cb = _publish_pool

        await runner.initialize()
        await runner.start_background_loops()

        # Startup summary
        for strategy, cfg in runner.strategies:
            budget = runner.gateway._strategy_budgets.get(strategy.name, float("inf"))
            logger.warning(
                "strategy.ready",
                name=strategy.name,
                mode=cfg.mode,
                capital_usd=cfg.capital_usd,
                budget_cap=budget,
                max_position_usd=cfg.max_position_usd,
                max_open=cfg.max_open_positions,
                features=cfg.features,
            )

        # Subscribe to market events for pool refresh
        from polymarket_pipeline.live.consumers.market_events import (
            MarketEventsConsumer,
            ResolutionPoller,
        )

        market_consumer = MarketEventsConsumer(
            pg_pool=None,  # PG updates handled by main pipeline
            runner=runner,
            debounce_s=5.0,
        )

        # Periodic CLOB API resolution checker (WS firehose doesn't emit
        # market_resolved events, so we poll for our filled markets)
        resolution_poller = ResolutionPoller(
            pg_pool=pg_pool,
            runner=runner,
            interval_s=60.0,
        )
        resolution_poller.start()

        @broker.subscriber("markets.events", group_id=f"{group_id}-events")
        async def handle_market_event(msg: str) -> None:
            await market_consumer.handle(msg)

        @broker.subscriber("trades.raw", group_id=group_id)
        async def handle_trade(msg: str) -> None:
            import json

            from polymarket_pipeline.models import NormalizedTrade

            data = json.loads(msg)
            trade = NormalizedTrade(**data)
            await runner._handle_trade(trade)

        @broker.subscriber("orderbooks.raw", group_id=group_id)
        async def handle_orderbook(msg: str) -> None:
            import json

            data = json.loads(msg)
            runner.handle_orderbook(data)

        # Check if any strategy opts in to pending.signal
        _pending_strategies = [
            (s, c) for s, c in runner.strategies if c.subscribe_pending
        ]

        if _pending_strategies:

            @broker.subscriber("pending.signal", group_id=group_id)
            async def handle_pending(msg: str) -> None:
                import json
                import time as _time

                from polymarket_pipeline.models import NormalizedTrade
                from polymarket_pipeline.strategies.runners.helpers import (
                    apply_fill_to_position,
                    check_risk_gate,
                )
                from polymarket_pipeline.strategies.types import FillStatus

                data = json.loads(msg)
                trade = NormalizedTrade(**data)
                for strategy, _config in _pending_strategies:
                    intents = await strategy.on_trade(trade, runner.ctx)
                    if intents:
                        for intent in intents:
                            # Risk gate (same as _handle_trade)
                            positions = runner.ctx.get_all_positions()
                            allowed, reason = check_risk_gate(
                                intent,
                                _config,
                                positions,
                                runner._last_trade_times,
                                _time.time(),
                            )
                            if not allowed:
                                logger.info(
                                    "pending_intent.rejected",
                                    strategy=strategy.name,
                                    reason=reason,
                                    condition_id=intent.condition_id,
                                )
                                await runner._fire_intent(
                                    intent, "risk_rejected", rejection_reason=reason,
                                )
                                continue

                            fill = await runner.gateway.submit(intent)

                            # Position tracking (filled or partial fills)
                            if fill.status in (
                                FillStatus.FILLED, FillStatus.PARTIAL,
                            ):
                                old_pos = await runner.ctx.get_position(fill.condition_id)
                                new_pos = apply_fill_to_position(old_pos, fill)
                                runner.ctx.set_position(fill.condition_id, new_pos)
                                runner._last_trade_times[intent.strategy] = fill.filled_at

                            await runner._fire_intent(
                                intent,
                                fill.status.value,
                                fill=fill,
                                rejection_reason=fill.error or "",
                            )

        await broker.start()
        logger.warning(
            "strategy_cli.running",
            strategies=len(runner.strategies),
            providers=len(runner.providers),
        )

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await runner.stop()
            await broker.close()
            await pg_pool.close()

    asyncio.run(_run())


@app.command()
def reset(
    log_dir: Path = typer.Option(
        "logs/paper", "--log-dir", help="Root log directory to clear JSONL files from"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Wipe all paper trading state: PG intents/fills/positions, JSONL logs, Kafka topic.

    Strategies should be stopped first (supervisorctl stop strategies:*).
    """
    if not yes:
        typer.confirm(
            "This will DELETE all intents, fills, positions, and JSONL logs. Continue?",
            abort=True,
        )

    from polymarket_pipeline.live.settings import Settings

    settings = Settings()

    # 1. Truncate PG tables via asyncpg
    logger.warning("reset.pg_truncate")
    try:
        import asyncpg

        async def _truncate_pg() -> None:
            conn = await asyncpg.connect(settings.pg_dsn)
            try:
                await conn.execute(
                    "TRUNCATE strategy_intents, fills, positions CASCADE"
                )
            finally:
                await conn.close()

        asyncio.run(_truncate_pg())
        typer.echo("  PG tables truncated")
    except Exception as e:
        typer.echo(f"  PG truncate error: {e}")

    # 2. Delete JSONL files
    logger.warning("reset.jsonl_cleanup", log_dir=str(log_dir))
    count = 0
    if log_dir.exists():
        for jsonl in log_dir.rglob("*.jsonl"):
            jsonl.unlink()
            count += 1
    typer.echo(f"  Deleted {count} JSONL files")

    # 3. Recreate Kafka topic via aiokafka admin client
    logger.warning("reset.kafka_topic")
    try:
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic

        async def _recreate_topic() -> None:
            admin = AIOKafkaAdminClient(bootstrap_servers=settings.redpanda_url)
            await admin.start()
            try:
                try:
                    await admin.delete_topics(["strategy.intents"])
                except Exception:
                    pass  # topic may not exist
                await admin.create_topics(
                    [NewTopic(name="strategy.intents", num_partitions=1, replication_factor=1)]
                )
            finally:
                await admin.close()

        asyncio.run(_recreate_topic())
        typer.echo("  Kafka strategy.intents topic recreated")
    except Exception as e:
        typer.echo(f"  Kafka reset error: {e}")

    typer.echo("Done. Restart strategies: supervisorctl start strategies:*")


# ---------------------------------------------------------------------------
# promote command — check promotion gates
# ---------------------------------------------------------------------------


@app.command()
def promote(
    name: str = typer.Argument(..., help="Strategy name to check"),
    to: str = typer.Option(..., "--to", help="Target mode (paper_dev, paper_prod, live)"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to strategy TOML config"),
    ledger_dir: Path = typer.Option(
        Path("research/output"), "--ledger-dir", help="Directory with ledger parquet files"
    ),
    force: bool = typer.Option(False, "--force", help="Override manual signoff gate"),
) -> None:
    """Check promotion gates for a strategy and print the report.

    Does NOT auto-update the TOML config — prints the required change
    if all gates pass.
    """
    from polymarket_pipeline.strategies.config import load_promotion_thresholds
    from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
    from polymarket_pipeline.strategies.promotion import (
        PromotionChecker,
        PromotionThresholds,
    )
    from polymarket_pipeline.strategies.types import ExecutionMode

    # Load current strategy config
    configs = load_strategy_configs(config)
    if name not in configs:
        typer.echo(f"Strategy '{name}' not found in {config}")
        raise typer.Exit(1)

    strategy_cfg = configs[name]
    from_mode = strategy_cfg.mode

    try:
        to_mode = ExecutionMode(to)
    except ValueError:
        typer.echo(f"Invalid mode: {to}. Options: paper_dev, paper_prod, live")
        raise typer.Exit(1)

    # Load thresholds
    raw_thresholds = load_promotion_thresholds(config)
    thresholds = PromotionThresholds(**raw_thresholds) if raw_thresholds else PromotionThresholds()

    # Load ledger
    ledger_path = ledger_dir / f"ledger_{name}.parquet"
    if not ledger_path.exists():
        typer.echo(f"No ledger found at {ledger_path}")
        typer.echo("Run a backtest first to generate ledger data.")
        raise typer.Exit(1)

    ledger = ParquetLedger(ledger_path)

    # Check gates
    async def _check() -> None:
        try:
            report = await PromotionChecker(ledger, thresholds).check(
                name, from_mode, to_mode, force=force,
            )
        except ValueError as e:
            typer.echo(f"Error: {e}")
            raise typer.Exit(1)

        # Print report
        typer.echo(f"\n{'='*60}")
        typer.echo(f"Promotion: {name}  {from_mode.value} → {to_mode.value}")
        typer.echo(f"{'='*60}")
        for gate in report.gates:
            status = "PASS" if gate.passed else "FAIL"
            typer.echo(f"  [{status}] {gate.gate_name}: {gate.actual} ({gate.required})")

        typer.echo(f"{'─'*60}")
        if report.all_passed:
            typer.echo(f"All gates passed. Update your TOML config:")
            typer.echo(f'  [strategy.{name}]')
            typer.echo(f'  mode = "{to_mode.value}"')
        else:
            failed = [g for g in report.gates if not g.passed]
            typer.echo(f"{len(failed)} gate(s) failed. Strategy not ready for promotion.")

        if report.summary:
            s = report.summary
            typer.echo(f"\nSummary: {s.total_fills} fills, {s.win_count}W/{s.loss_count}L, "
                       f"PnL=${s.total_pnl_net:,.2f}, Sharpe={s.sharpe:.2f}, "
                       f"DD=${s.max_drawdown:,.2f}")

    asyncio.run(_check())
