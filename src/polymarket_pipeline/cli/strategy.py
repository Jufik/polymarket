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
    StrategyConfig,
    load_provider_configs,
    load_strategy_configs,
)
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.runners.live import LiveRunner

logger = structlog.get_logger(__name__)

app = typer.Typer(name="pm-strategy", help="Strategy execution CLI.")


# ---------------------------------------------------------------------------
# Provider registry (manual for now — only SkilledTradersProvider)
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, type[Any]] = {}


def _register_providers() -> None:
    """Register known provider classes."""
    from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
        SkilledTradersProvider,
    )
    from polymarket_pipeline.strategies_impl.crypto_otm_no.providers import (
        CryptoMarketProvider,
    )
    from polymarket_pipeline.strategies_impl.market_size.providers import (
        MarketSizeProvider,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.providers import (
        GradedPoolProvider,
    )
    from polymarket_pipeline.strategies_impl.will_no.providers import (
        WillMarketProvider,
    )

    _PROVIDER_REGISTRY["skilled_traders"] = SkilledTradersProvider
    _PROVIDER_REGISTRY["crypto_markets"] = CryptoMarketProvider
    _PROVIDER_REGISTRY["will_markets"] = WillMarketProvider
    _PROVIDER_REGISTRY["pool_traders"] = GradedPoolProvider
    _PROVIDER_REGISTRY["market_size"] = MarketSizeProvider


# ---------------------------------------------------------------------------
# Strategy factory registry
# ---------------------------------------------------------------------------

_STRATEGY_FACTORIES: dict[str, Any] = {}


def _make_consensus_copy(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.consensus_copy.config import (
        ConsensusCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
        ConsensusCopyStrategy,
    )

    cc_cfg = ConsensusCopyConfig(**config.params)
    return ConsensusCopyStrategy(config=cc_cfg)


def _make_crypto_otm_no(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig
    from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import CryptoOTMNoStrategy

    return CryptoOTMNoStrategy(config=CryptoOTMNoConfig(**config.params))


def _make_will_no(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
    from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy

    return WillNoStrategy(config=WillNoConfig(**config.params))


def _make_proportional_copy(config: StrategyConfig) -> Any:
    from polymarket_pipeline.strategies_impl.proportional_copy.config import (
        ProportionalCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
        ProportionalCopyStrategy,
    )

    return ProportionalCopyStrategy(config=ProportionalCopyConfig(**config.params))


def _register_strategies() -> None:
    """Register known strategy factories."""
    _STRATEGY_FACTORIES["consensus_copy"] = _make_consensus_copy
    _STRATEGY_FACTORIES["crypto_otm_no"] = _make_crypto_otm_no
    _STRATEGY_FACTORIES["will_no"] = _make_will_no
    _STRATEGY_FACTORIES["proportional_copy"] = _make_proportional_copy


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

    import polars as pl

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

            # Special case: skilled_traders with data_dir uses consistency mode
            if pname == "skilled_traders" and "data_dir" in params:
                from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
                    load_skilled_provider,
                )

                provider = load_skilled_provider(**params)
            else:
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

    # Assemble
    ctx = InMemoryContext()
    executor = PaperExecutor(ctx=ctx)
    log_path = (log_dir / "intents.jsonl") if log_dir else None
    # Use delay_s from first strategy's params (if any)
    delay_s = 0.0
    if strategies:
        first_params = strategies[0][1].params
        delay_s = float(first_params.get("delay_s", 0.0))
    gateway = ExecutionGateway(executor=executor, log_path=log_path, delay_s=delay_s)
    backend = PolarsBackend(trades=pl.DataFrame(), markets=pl.DataFrame())

    return LiveRunner(
        strategies=strategies,
        providers=providers,
        gateway=gateway,
        ctx=ctx,
        backend=backend,
    )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="Path to strategies TOML"),
    only: str | None = typer.Option(None, "--only", help="Run only this strategy"),
    log_dir: Path | None = typer.Option(None, "--log-dir", help="Intent log directory"),
) -> None:
    """Start strategies in paper-dev mode against live Kafka."""
    logger.info("strategy_cli.starting", config=str(config), only=only)

    runner = _build_runner(config, only=only, log_dir=log_dir)

    async def _run() -> None:
        from faststream.kafka import KafkaBroker

        from polymarket_pipeline.live.settings import Settings

        settings = Settings()
        broker = KafkaBroker(settings.redpanda_url)

        await runner.initialize()
        await runner.start_background_loops()

        # Subscribe to market events for pool refresh
        from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer

        market_consumer = MarketEventsConsumer(
            pg_pool=None,  # PG updates handled by main pipeline
            runner=runner,
            debounce_s=5.0,
        )

        @broker.subscriber("markets.events", group_id="strategy-market-events")
        async def handle_market_event(msg: str) -> None:
            await market_consumer.handle(msg)

        @broker.subscriber("trades.raw", group_id="strategy-runner")
        async def handle_trade(msg: str) -> None:
            import json

            from polymarket_pipeline.models import NormalizedTrade

            data = json.loads(msg)
            trade = NormalizedTrade(**data)
            await runner._handle_trade(trade)

        @broker.subscriber("orderbooks.raw", group_id="strategy-runner")
        async def handle_orderbook(msg: str) -> None:
            import json

            data = json.loads(msg)
            runner.handle_orderbook(data)

        # Check if any strategy opts in to pending.signal
        _pending_strategies = [
            (s, c) for s, c in runner.strategies if c.subscribe_pending
        ]

        if _pending_strategies:

            @broker.subscriber("pending.signal", group_id="strategy-runner")
            async def handle_pending(msg: str) -> None:
                import json

                from polymarket_pipeline.models import NormalizedTrade

                data = json.loads(msg)
                trade = NormalizedTrade(**data)
                for strategy, _config in _pending_strategies:
                    intents = await strategy.on_trade(trade, runner.ctx)
                    if intents:
                        for intent in intents:
                            await runner.gateway.submit(intent)

        await broker.start()
        logger.info(
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

    asyncio.run(_run())
