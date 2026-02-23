"""ExecutionGateway — routes TradeIntents through an executor with optional JSONL logging."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.types import Fill, TradeIntent

from polymarket_pipeline.strategies.protocol import Executor

logger = structlog.get_logger(__name__)


class ExecutionGateway:
    """Thin routing layer that logs intents and delegates to an :class:`Executor`.

    Parameters
    ----------
    executor:
        The underlying executor that converts intents to fills.
    log_path:
        Optional path to a JSONL file where every submitted intent is appended.
        If ``None``, no logging is performed.
    """

    def __init__(
        self, executor: Executor, log_path: Path | None = None, *, delay_s: float = 0.0
    ) -> None:
        self.executor = executor
        self.log_path = log_path
        self.delay_s = delay_s

    async def submit(self, intent: TradeIntent) -> Fill:
        """Log *intent* (if configured) and delegate to the executor."""
        if self.log_path is not None:
            self._log_intent(intent)

        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)

        fill: Fill = await self.executor.execute(intent)

        logger.info(
            "gateway_submit",
            intent_strategy=intent.strategy,
            condition_id=intent.condition_id,
            fill_status=fill.status,
        )

        return fill

    def _log_intent(self, intent: TradeIntent) -> None:
        """Append *intent* as a JSON line to :attr:`log_path`."""
        assert self.log_path is not None  # noqa: S101
        with open(self.log_path, "a") as f:
            f.write(json.dumps(dataclasses.asdict(intent)) + "\n")
