"""Tests for the RealisticFillSimulator."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies.execution.realistic import (
    FillModelConfig,
    RealisticFillSimulator,
)
from polymarket_pipeline.strategies.types import FillStatus, TradeIntent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(
    cid: str = "m1",
    side: str = "BUY",
    outcome: str = "YES",
    size: float = 100.0,
    max_price: float = 0.60,
) -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id=cid,
        side=side,
        outcome=outcome,
        size_usd=size,
        urgency="patient",
        max_price=max_price,
        reason="test",
        signal_time=1000.0,
        asset_id="asset_1",
    )


# ---------------------------------------------------------------------------
# Slippage as fee
# ---------------------------------------------------------------------------


class TestSlippage:
    @pytest.mark.asyncio
    async def test_calibrated_spread_in_fee(self) -> None:
        """Per-market spread should be captured as slippage cost in fee_usd."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.01, impact_scale=0.0,
            ),
            market_spreads={"m1": 0.03},
        )
        fill = await sim.execute(_intent(max_price=0.60))

        # Fill price = max_price (same as simulated)
        assert fill.filled_price == pytest.approx(0.60)
        assert fill.status == FillStatus.FILLED
        # Slippage = half_spread * size = 0.03 * 100 = 3.0
        assert fill.fee_usd == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_fallback_spread(self) -> None:
        """Unknown market should use fallback_half_spread."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.02, impact_scale=0.0,
            ),
            market_spreads={},  # no calibration for m1
        )
        fill = await sim.execute(_intent(max_price=0.60))

        assert fill.filled_price == pytest.approx(0.60)
        # Slippage = 0.02 * 100 = 2.0
        assert fill.fee_usd == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_sell_same_slippage_model(self) -> None:
        """SELL uses the same slippage-as-fee model."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.02, impact_scale=0.0,
            ),
        )
        fill = await sim.execute(_intent(side="SELL", max_price=0.60))

        assert fill.filled_price == pytest.approx(0.60)
        assert fill.fee_usd == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_slippage_reduces_pnl(self) -> None:
        """Higher spread → higher fee → lower net PnL."""
        sim_tight = RealisticFillSimulator(
            config=FillModelConfig(fallback_half_spread=0.01, impact_scale=0.0),
        )
        sim_wide = RealisticFillSimulator(
            config=FillModelConfig(fallback_half_spread=0.05, impact_scale=0.0),
        )
        fill_tight = await sim_tight.execute(_intent())
        fill_wide = await sim_wide.execute(_intent())

        # Both fill at same price, but wide spread has higher fee
        assert fill_tight.filled_price == fill_wide.filled_price
        assert fill_wide.fee_usd > fill_tight.fee_usd


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


class TestImpact:
    @pytest.mark.asyncio
    async def test_impact_scales_with_size(self) -> None:
        """Larger orders should have higher slippage fee."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.0,
                impact_scale=1.0,
                default_liquidity_usd=1000.0,
            ),
        )
        fill_small = await sim.execute(_intent(size=10.0))
        fill_large = await sim.execute(_intent(size=500.0))

        # Larger order → higher impact → higher fee
        assert fill_large.fee_usd > fill_small.fee_usd

    @pytest.mark.asyncio
    async def test_impact_formula(self) -> None:
        """Verify impact = (size / liquidity) * scale."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.0,
                impact_scale=1.0,
                default_liquidity_usd=5000.0,
            ),
        )
        fill = await sim.execute(_intent(size=100.0))

        # impact = 100 / 5000 * 1.0 = 0.02
        # slippage_cost = 0.02 * 100 = 2.0
        assert fill.fee_usd == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_volume_based_liquidity(self) -> None:
        """High-volume markets should have lower impact (lower fee)."""
        config = FillModelConfig(
            fallback_half_spread=0.0, impact_scale=1.0, default_liquidity_usd=100.0,
        )
        sim_low = RealisticFillSimulator(config=config, market_volumes={"m1": 1000.0})
        sim_high = RealisticFillSimulator(config=config, market_volumes={"m1": 1_000_000.0})

        fill_low = await sim_low.execute(_intent(size=100.0))
        fill_high = await sim_high.execute(_intent(size=100.0))

        # High volume → more liquidity → less impact → lower fee
        assert fill_high.fee_usd < fill_low.fee_usd


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


class TestRejection:
    @pytest.mark.asyncio
    async def test_liquidity_rejection_deterministic(self) -> None:
        """Rejection should be deterministic with a fixed seed."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.0,
                impact_scale=0.0,
                reject_probability=0.50,
            ),
            rng_seed=42,
        )

        results = []
        for _ in range(20):
            fill = await sim.execute(_intent())
            results.append(fill.status)

        assert FillStatus.FILLED in results
        assert FillStatus.REJECTED in results

        # Same seed → same pattern
        sim2 = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.0,
                impact_scale=0.0,
                reject_probability=0.50,
            ),
            rng_seed=42,
        )
        results2 = []
        for _ in range(20):
            fill = await sim2.execute(_intent())
            results2.append(fill.status)

        assert results == results2

    @pytest.mark.asyncio
    async def test_zero_rejection(self) -> None:
        """reject_probability=0 → always fills."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                reject_probability=0.0, fallback_half_spread=0.0,
                impact_scale=0.0,
            ),
        )
        for _ in range(10):
            fill = await sim.execute(_intent())
            assert fill.status == FillStatus.FILLED


# ---------------------------------------------------------------------------
# Protocol compliance & backward compat
# ---------------------------------------------------------------------------


class TestProtocol:
    @pytest.mark.asyncio
    async def test_zero_config_matches_simulated(self) -> None:
        """With no spread, no impact, no rejection → same as SimulatedExecutor."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(
                fallback_half_spread=0.0,
                impact_scale=0.0,
                reject_probability=0.0,
            ),
            fee_pct=0.0,
        )
        fill = await sim.execute(_intent(max_price=0.60))

        assert fill.status == FillStatus.FILLED
        assert fill.filled_price == pytest.approx(0.60)
        assert fill.filled_size_usd == 100.0
        assert fill.fee_usd == 0.0

    def test_implements_executor_protocol(self) -> None:
        from polymarket_pipeline.strategies.protocol import Executor

        sim = RealisticFillSimulator()
        assert isinstance(sim, Executor)

    @pytest.mark.asyncio
    async def test_combined_fee(self) -> None:
        """Total fee should be exchange fee + slippage cost."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(fallback_half_spread=0.01, impact_scale=0.0),
            fee_pct=0.02,
        )
        fill = await sim.execute(_intent(max_price=0.60, size=100.0))

        # exchange_fee = 0.02 * min(0.60, 0.40) * 100 = 0.80
        # slippage = 0.01 * 100 = 1.0
        # total = 1.80
        assert fill.fee_usd == pytest.approx(1.80, abs=0.01)

    @pytest.mark.asyncio
    async def test_default_price_when_none(self) -> None:
        """max_price=None → fills at 0.50."""
        sim = RealisticFillSimulator(
            config=FillModelConfig(fallback_half_spread=0.0, impact_scale=0.0),
        )
        intent = TradeIntent(
            strategy="test", condition_id="m1", side="BUY", outcome="YES",
            size_usd=100.0, urgency="patient", max_price=None,
            reason="test", signal_time=1000.0, asset_id="a1",
        )
        fill = await sim.execute(intent)
        assert fill.filled_price == pytest.approx(0.50)
