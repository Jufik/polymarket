"""ExecutionGateway — routes TradeIntents through an executor with optional JSONL logging."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
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
    """Thin routing layer that logs intents/fills and delegates to an :class:`Executor`.

    Parameters
    ----------
    executor:
        The underlying executor that converts intents to fills.
    log_path:
        Optional path to a JSONL file where every submitted intent is appended.
        If ``None``, no file logging is performed.
    delay_s:
        Optional delay in seconds before executing.
    quality_state:
        Optional pipeline readiness state. When provided, intents are rejected
        if the pipeline is not in CHECKING or READY state.
    strategy_budgets:
        Per-strategy cumulative USD spending caps. Strategies without an entry
        are uncapped.
    """

    def __init__(
        self,
        executor: Executor,
        log_path: Path | None = None,
        *,
        delay_s: float = 0.0,
        quality_state: ReadinessState | None = None,
        strategy_budgets: dict[str, float] | None = None,
    ) -> None:
        self.executor = executor
        self.log_path = log_path
        self.delay_s = delay_s
        self._quality_state = quality_state
        self._strategy_budgets = dict(strategy_budgets) if strategy_budgets else {}
        self._strategy_spent: dict[str, float] = {}
        self._budget_lock = asyncio.Lock()
        self._fill_log_path: Path | None = None
        if log_path is not None:
            self._fill_log_path = log_path.parent / "fills.jsonl"

    async def submit(self, intent: TradeIntent) -> Fill:
        """Log *intent* (if configured) and delegate to the executor.

        If a quality_state is configured and the pipeline is DEGRADED or worse,
        the intent is rejected immediately without reaching the executor.

        Budget checks are serialized via ``_budget_lock`` to prevent concurrent
        intents from overrunning per-strategy spending caps.
        """
        from polymarket_pipeline.strategies.types import Fill as FillType

        # Validate intent basics
        if intent.size_usd <= 0:
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
                filled_at=time.time(),
                error=f"invalid size_usd: {intent.size_usd}",
            )

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

        # Budget gate — lock prevents concurrent intents from overrunning cap
        async with self._budget_lock:
            if self._strategy_budgets:
                budget = self._strategy_budgets.get(intent.strategy)
                if budget is not None:
                    spent = self._strategy_spent.get(intent.strategy, 0.0)
                    if spent + intent.size_usd > budget:
                        logger.warning(
                            "gateway.budget_exceeded",
                            strategy=intent.strategy,
                            spent=spent,
                            requested=intent.size_usd,
                            budget=budget,
                        )
                        return FillType(
                            intent_id=f"budget-{intent.strategy}",
                            strategy=intent.strategy,
                            condition_id=intent.condition_id,
                            side=intent.side,
                            outcome=intent.outcome,
                            filled_price=0.0,
                            filled_size_usd=0.0,
                            fee_usd=0.0,
                            status=FillStatus.REJECTED,
                            filled_at=time.time(),
                            error=f"Budget exhausted: {spent:.2f}/{budget:.2f}",
                        )
                    # Reserve budget before releasing lock to prevent races
                    self._strategy_spent[intent.strategy] = spent + intent.size_usd

        # File I/O — guarded so disk errors never crash the trade loop
        if self.log_path is not None:
            try:
                self._log_intent(intent)
            except Exception:
                logger.warning("gateway.log_intent_error", exc_info=True)

        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)

        try:
            fill: Fill = await self.executor.execute(intent)
        except Exception:
            logger.exception(
                "gateway.executor_error",
                strategy=intent.strategy,
                condition_id=intent.condition_id,
            )
            fill = FillType(
                intent_id="",
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                side=intent.side,
                outcome=intent.outcome,
                filled_price=0.0,
                filled_size_usd=0.0,
                fee_usd=0.0,
                status=FillStatus.REJECTED,
                filled_at=time.time(),
                error="executor raised exception",
            )

        # Adjust budget: we reserved above; refund if not filled.
        async with self._budget_lock:
            if fill.status == FillStatus.FILLED:
                # Correct reservation to actual fill size (may differ from intent)
                reserved = intent.size_usd
                actual = fill.filled_size_usd
                if reserved != actual:
                    self._strategy_spent[intent.strategy] = (
                        self._strategy_spent.get(intent.strategy, 0.0)
                        - reserved + actual
                    )
            else:
                # Refund the reservation — fill was rejected or failed
                self._strategy_spent[intent.strategy] = max(
                    0.0,
                    self._strategy_spent.get(intent.strategy, 0.0) - intent.size_usd,
                )

        # Persist fill to JSONL (guarded)
        if self._fill_log_path is not None:
            try:
                self._log_fill(fill)
            except Exception:
                logger.warning("gateway.log_fill_error", exc_info=True)

        logger.info(
            "gateway_submit",
            intent_strategy=intent.strategy,
            condition_id=intent.condition_id,
            fill_status=fill.status,
            fill_price=fill.filled_price,
            fill_size=fill.filled_size_usd,
            spent=round(self._strategy_spent.get(intent.strategy, 0.0), 2),
        )

        return fill

    def _log_intent(self, intent: TradeIntent) -> None:
        """Append *intent* as a JSON line to :attr:`log_path`."""
        assert self.log_path is not None  # noqa: S101
        with open(self.log_path, "a") as f:
            f.write(json.dumps(dataclasses.asdict(intent)) + "\n")

    def _log_fill(self, fill: Fill) -> None:
        """Append *fill* as a JSON line to :attr:`_fill_log_path`."""
        assert self._fill_log_path is not None  # noqa: S101
        with open(self._fill_log_path, "a") as f:
            f.write(json.dumps(dataclasses.asdict(fill)) + "\n")
