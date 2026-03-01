"""Realistic fill simulator with trade-calibrated spreads.

Drop-in replacement for SimulatedExecutor. Implements the Executor protocol.

In backtest, ``max_price`` is the only price reference (no live orderbook).
The simulator fills at ``max_price`` (same as SimulatedExecutor) but adds
calibrated **slippage cost** to the fee. This correctly penalizes PnL
without the boundary paradox of adding spread to the fill price.

Slippage model:
  slippage_cost = (half_spread + impact) * size_usd
  total_fee = trading_fee + slippage_cost
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

import structlog

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FillModelConfig:
    """Configuration for realistic fill simulation."""

    # Spread: used when no per-market calibration data is available
    fallback_half_spread: float = 0.005

    # Impact: linear price impact = size_usd / liquidity * impact_scale
    impact_scale: float = 1.0
    default_liquidity_usd: float = 5000.0

    # Rejection: flat probability of intent rejection (liquidity miss)
    reject_probability: float = 0.0


class RealisticFillSimulator:
    """Executor that fills at ``max_price`` with calibrated slippage as fee.

    The fill price matches SimulatedExecutor (``max_price`` or default).
    The friction (spread + impact) is captured as slippage cost added to
    ``fee_usd``, so downstream PnL computation correctly accounts for it.

    Parameters
    ----------
    config:
        Fill model configuration. Defaults provide mild friction.
    market_spreads:
        Per-market half-spread estimates from ``calibrate_spreads()``.
        Missing markets fall back to ``config.fallback_half_spread``.
    market_volumes:
        Per-market cumulative USD volume for liquidity scaling.
    fee_pct:
        Exchange fee: ``fee_pct * min(price, 1-price) * size_usd``.
    rng_seed:
        Random seed for deterministic rejection in backtests.
    """

    def __init__(
        self,
        config: FillModelConfig | None = None,
        market_spreads: dict[str, float] | None = None,
        market_volumes: dict[str, float] | None = None,
        fee_pct: float = 0.0,
        rng_seed: int = 42,
    ) -> None:
        self._config = config or FillModelConfig()
        self._market_spreads = market_spreads or {}
        self._market_volumes = market_volumes or {}
        self._fee_pct = fee_pct
        self._rng = random.Random(rng_seed)

    async def execute(self, intent: TradeIntent) -> Fill:
        """Fill at max_price with slippage captured in fee_usd."""
        # 1. Rejection check (liquidity miss)
        if (
            self._config.reject_probability > 0
            and self._rng.random() < self._config.reject_probability
        ):
            return self._reject(intent, "liquidity_reject")

        # 2. Fill price (same as SimulatedExecutor)
        fill_price = (
            intent.max_price if intent.max_price is not None else 0.50
        )

        # 3. Half-spread (per-market calibrated or fallback)
        half_spread = self._market_spreads.get(
            intent.condition_id, self._config.fallback_half_spread
        )

        # 4. Market impact
        impact = self._compute_impact(intent.size_usd, intent.condition_id)

        # 5. Slippage cost: modeled as additional fee
        slippage_cost = (half_spread + impact) * intent.size_usd

        # 6. Exchange fee
        exchange_fee = (
            self._fee_pct
            * min(fill_price, 1.0 - fill_price)
            * intent.size_usd
        )
        total_fee = exchange_fee + slippage_cost

        intent_id = uuid.uuid4().hex[:12]

        fill = Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=fill_price,
            filled_size_usd=intent.size_usd,
            fee_usd=total_fee,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )

        logger.debug(
            "realistic_fill",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            fill_price=round(fill_price, 4),
            half_spread=round(half_spread, 4),
            impact=round(impact, 4),
            slippage_cost=round(slippage_cost, 2),
            exchange_fee=round(exchange_fee, 4),
            total_fee=round(total_fee, 4),
            size_usd=round(intent.size_usd, 2),
        )

        return fill

    def _compute_impact(self, size_usd: float, condition_id: str) -> float:
        """Linear market impact: size / liquidity * scale."""
        vol = self._market_volumes.get(condition_id, 0.0)
        estimated_liquidity = max(
            self._config.default_liquidity_usd,
            vol * 0.01,  # 1% of total volume as rough liquidity proxy
        )
        return (size_usd / estimated_liquidity) * self._config.impact_scale

    def _reject(self, intent: TradeIntent, reason: str) -> Fill:
        """Return a REJECTED fill."""
        intent_id = uuid.uuid4().hex[:12]
        logger.debug(
            "realistic_fill.rejected",
            intent_id=intent_id,
            condition_id=intent.condition_id,
            reason=reason,
        )
        return Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=0.0,
            filled_size_usd=0.0,
            fee_usd=0.0,
            status=FillStatus.REJECTED,
            filled_at=intent.signal_time,
            error=reason,
        )
