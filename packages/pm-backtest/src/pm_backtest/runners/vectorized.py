"""VectorizedRunner --- Polars batch execution wrapper.

Invokes :meth:`VectorizedStrategy.compute_signals` on the provided DataFrames
and wraps the output in a :class:`VectorizedResult`.  The complexity lives in
each strategy's ``compute_signals()``; this runner is intentionally thin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

if TYPE_CHECKING:
    from pm_strategy.protocols import VectorizedStrategy

logger = structlog.get_logger(__name__)


@dataclass
class VectorizedResult:
    """Aggregate output of a vectorized strategy run."""

    total_signals: int
    signals: pl.DataFrame


class VectorizedRunner:
    """Wraps a :class:`VectorizedStrategy` and provides a uniform ``run`` interface.

    Parameters
    ----------
    strategy:
        A strategy implementing :meth:`compute_signals`.
    """

    __slots__ = ("_strategy",)

    def __init__(self, strategy: VectorizedStrategy) -> None:
        self._strategy = strategy

    def run(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> VectorizedResult:
        """Execute the strategy and wrap its output.

        Calls ``strategy.compute_signals(trades, markets)`` and returns a
        :class:`VectorizedResult` containing the signals DataFrame and the
        total signal count.
        """
        signals = self._strategy.compute_signals(trades, markets)

        result = VectorizedResult(
            total_signals=signals.shape[0],
            signals=signals,
        )

        logger.info(
            "vectorized_run_complete",
            strategy=getattr(self._strategy, "name", "unknown"),
            total_signals=result.total_signals,
        )

        return result
