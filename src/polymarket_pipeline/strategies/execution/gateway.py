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

from polymarket_pipeline.quality.state import PipelineState, ReadinessState
from polymarket_pipeline.strategies.protocol import Executor
from polymarket_pipeline.strategies.types import FillStatus

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
    delay_s:
        Optional delay in seconds before executing.
    quality_state:
        Optional pipeline readiness state. When provided, intents are rejected
        if the pipeline is not in CHECKING or READY state.
    """

    def __init__(
        self,
        executor: Executor,
        log_path: Path | None = None,
        *,
        delay_s: float = 0.0,
        quality_state: ReadinessState | None = None,
    ) -> None:
        self.executor = executor
        self.log_path = log_path
        self.delay_s = delay_s
        self._quality_state = quality_state

    async def submit(self, intent: TradeIntent) -> Fill:
        """Log *intent* (if configured) and delegate to the executor.

        If a quality_state is configured and the pipeline is DEGRADED or worse,
        the intent is rejected immediately without reaching the executor.
        """
        from polymarket_pipeline.strategies.types import Fill as FillType

        # Check pipeline health before executing
        if self._quality_state is not None:
            state = self._quality_state.current
            if state not in (PipelineState.CHECKING, PipelineState.READY):
                logger.warning(
                    "gateway_rejected",
                    reason=f"pipeline_state={state}",
                    condition_id=intent.condition_id,
                )
                return FillType(
                    intent_id="",
                    strategy=intent.strategy,
                    condition_id=intent.condition_id,
                    side=intent.side,
                    outcome=intent.outcome,
                    filled_price=0.0,
                    filled_size_usd=0.0,
                    fee_usd=0.0,
                    status=FillStatus.REJECTED,
                    filled_at=intent.signal_time,
                    error=f"pipeline state: {state}",
                )

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
