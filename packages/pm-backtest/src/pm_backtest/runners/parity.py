"""Parity gate --- vectorized vs event-driven replay validation.

Runs the *same* strategy through both the vectorized (Polars batch) and
event-driven (trade-by-trade replay) execution paths, then compares outputs.
Any divergence indicates a bug in one of the two implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import polars as pl
import structlog
from pm_strategy.context.memory import InMemoryContext
from pm_strategy.types import TradeIntent

from pm_backtest.runners.vectorized import VectorizedRunner

if TYPE_CHECKING:
    from pm_core.models import NormalizedTrade
    from pm_strategy.protocols import StrategyContext

logger = structlog.get_logger(__name__)


@runtime_checkable
class ParityStrategy(Protocol):
    """A strategy that implements both event-driven and vectorized interfaces."""

    name: str

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None: ...

    def compute_signals(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame: ...


@dataclass
class ParityReport:
    """Result of comparing vectorized and event-driven replay outputs."""

    vectorized_count: int
    replay_count: int
    is_aligned: bool
    vectorized_markets: set[str] = field(default_factory=set)
    replay_markets: set[str] = field(default_factory=set)
    missing_in_replay: set[str] = field(default_factory=set)
    extra_in_replay: set[str] = field(default_factory=set)


async def validate_parity(
    strategy: ParityStrategy,
    trades: list[NormalizedTrade],
    markets: pl.LazyFrame,
) -> ParityReport:
    """Run *strategy* through both paths and compare outputs.

    Parameters
    ----------
    strategy:
        Must satisfy both :class:`Strategy` (``on_trade``) and
        :class:`VectorizedStrategy` (``compute_signals``) protocols.
    trades:
        The trade sequence to replay / convert to a DataFrame.
    markets:
        Market metadata LazyFrame passed to ``compute_signals``.

    Returns
    -------
    ParityReport
        Alignment report including signal counts and market-set diffs.
    """
    # ------------------------------------------------------------------
    # 1. Vectorized path
    # ------------------------------------------------------------------
    trades_lf = _trades_to_lazyframe(trades)
    vec_runner = VectorizedRunner(strategy)
    vec_result = vec_runner.run(trades=trades_lf, markets=markets)

    vec_count = vec_result.total_signals
    vec_markets: set[str] = set()
    if vec_count > 0 and "condition_id" in vec_result.signals.columns:
        vec_markets = set(vec_result.signals["condition_id"].to_list())

    # ------------------------------------------------------------------
    # 2. Event-driven replay path
    # ------------------------------------------------------------------
    ctx = InMemoryContext()
    sorted_trades = sorted(trades, key=lambda t: t.published_at)
    replay_count = 0
    replay_markets: set[str] = set()

    for trade in sorted_trades:
        ctx.set_time(trade.published_at)
        intents = await strategy.on_trade(trade, ctx)
        if intents is not None:
            for intent in intents:
                replay_count += 1
                replay_markets.add(intent.condition_id)

    # ------------------------------------------------------------------
    # 3. Compare
    # ------------------------------------------------------------------
    missing_in_replay = vec_markets - replay_markets
    extra_in_replay = replay_markets - vec_markets
    is_aligned = (vec_count == replay_count) and (vec_markets == replay_markets)

    report = ParityReport(
        vectorized_count=vec_count,
        replay_count=replay_count,
        is_aligned=is_aligned,
        vectorized_markets=vec_markets,
        replay_markets=replay_markets,
        missing_in_replay=missing_in_replay,
        extra_in_replay=extra_in_replay,
    )

    logger.info(
        "parity_check_complete",
        strategy=getattr(strategy, "name", "unknown"),
        vectorized_count=vec_count,
        replay_count=replay_count,
        is_aligned=is_aligned,
        missing_in_replay=len(missing_in_replay),
        extra_in_replay=len(extra_in_replay),
    )

    return report


def _trades_to_lazyframe(trades: list[NormalizedTrade]) -> pl.LazyFrame:
    """Convert a list of NormalizedTrades to a Polars LazyFrame.

    Extracts the columns most commonly needed by vectorized strategies.
    """
    if not trades:
        return pl.LazyFrame(
            {
                "condition_id": pl.Series([], dtype=pl.Utf8),
                "published_at": pl.Series([], dtype=pl.Float64),
                "price": pl.Series([], dtype=pl.Float64),
                "side": pl.Series([], dtype=pl.Utf8),
                "maker": pl.Series([], dtype=pl.Utf8),
                "trade_id": pl.Series([], dtype=pl.Utf8),
            }
        )

    return pl.DataFrame(
        {
            "condition_id": [t.condition_id for t in trades],
            "published_at": [t.published_at for t in trades],
            "price": [float(t.price) for t in trades],
            "side": [t.side.value for t in trades],
            "maker": [t.maker for t in trades],
            "trade_id": [t.trade_id for t in trades],
        }
    ).lazy()
