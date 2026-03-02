"""Unit tests for ReplayRunner — tick-by-tick settlement + asset_id resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.replay import (
    MarketResolution,
    ReplayRunner,
    load_resolutions_from_rows,
)
from polymarket_pipeline.strategies.types import ExecutionMode, TradeIntent

# ── Helpers ────────────────────────────────────────────────────────────

_CFG = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=100.0,
    max_position_usd=50.0,
    max_open_positions=5,
    cooldown_s=0,
)


class _BuyStrategy:
    """Always buy YES at the trade price."""

    name = "test_buyer"

    async def on_trade(self, trade: NormalizedTrade, ctx: object) -> list[TradeIntent] | None:
        return [
            TradeIntent(
                strategy=self.name,
                condition_id=trade.condition_id,
                side="BUY",
                outcome="YES",
                size_usd=10.0,
                urgency="patient",
                max_price=float(trade.price),
                reason="test",
                signal_time=trade.published_at,
                asset_id=trade.asset_id,
            ),
        ]

    async def on_market_update(self, u: object, ctx: object) -> None:
        return None

    async def on_timer(self, now: float, ctx: object) -> None:
        return None


def _trade(
    cid: str = "cid1",
    asset_id: str = "yes_asset_1",
    price: float = 0.70,
    ts: float = 1000.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"t:{cid}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=Side.BUY,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


# ── Tests ──────────────────────────────────────────────────────────────


async def test_settlement_frees_capital():
    """After settlement, cost_basis=0 and new positions can be opened."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))

    resolutions = {
        "cid1": MarketResolution(
            condition_id="cid1",
            resolved_at=1500.0,
            winning_asset_ids=frozenset({"yes_asset_1"}),
        ),
    }
    token_map = {"cid1": {"YES": "yes_asset_1", "NO": "no_asset_1"}}

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        config=_CFG,
        resolutions=resolutions,
        token_map=token_map,
    )

    trades = [
        _trade(cid="cid1", ts=1000.0, price=0.70),
        # After resolution at 1500, cid1 settles
        _trade(cid="cid2", asset_id="yes_asset_2", ts=2000.0, price=0.80),
    ]

    result = await runner.run(trades)

    # cid1 should be settled: cost_basis=0
    pos1 = await ctx.get_position("cid1")
    assert pos1 is not None
    assert pos1.cost_basis == 0.0
    assert pos1.qty_yes == 0.0
    assert pos1.realized_pnl > 0  # won (YES won, bought at 0.70)

    # cid2 should have an open position
    pos2 = await ctx.get_position("cid2")
    assert pos2 is not None
    assert pos2.qty_yes > 0

    assert runner.n_settled == 1
    assert result.total_fills == 2  # both filled


async def test_settlement_pnl_correct():
    """Verify PnL computation: BUY YES at 0.70, YES wins → profit."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))

    resolutions = {
        "cid1": MarketResolution(
            condition_id="cid1",
            resolved_at=1500.0,
            winning_asset_ids=frozenset({"yes_asset_1"}),
        ),
    }
    token_map = {"cid1": {"YES": "yes_asset_1", "NO": "no_asset_1"}}

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        resolutions=resolutions,
        token_map=token_map,
    )

    # BUY YES at 0.70, size_usd=10 → qty = 10/0.70 = 14.286 tokens
    # YES wins → payout = 14.286 * $1 = $14.286, cost = $10
    # PnL = (1 - 0.70) * 14.286 = $4.286
    trades = [_trade(cid="cid1", ts=1000.0, price=0.70)]
    await runner.run(trades)

    pos = await ctx.get_position("cid1")
    expected_pnl = (1.0 - 0.70) * (10.0 / 0.70)
    assert pos.realized_pnl == pytest.approx(expected_pnl, rel=0.01)


async def test_settlement_loss():
    """BUY YES at 0.70 but NO wins → loss = -$10."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))

    resolutions = {
        "cid1": MarketResolution(
            condition_id="cid1",
            resolved_at=1500.0,
            winning_asset_ids=frozenset({"no_asset_1"}),  # NO wins
        ),
    }
    token_map = {"cid1": {"YES": "yes_asset_1", "NO": "no_asset_1"}}

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        resolutions=resolutions,
        token_map=token_map,
    )

    trades = [_trade(cid="cid1", ts=1000.0, price=0.70)]
    await runner.run(trades)

    pos = await ctx.get_position("cid1")
    # YES tokens worth $0 → loss = avg_entry * qty = 0.70 * (10/0.70) = $10
    assert pos.realized_pnl == pytest.approx(-10.0, rel=0.01)


async def test_ledger_enriched_at_settlement(tmp_path):
    """Ledger records get PnL at settlement time, not post-hoc."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))
    ledger = ParquetLedger(tmp_path / "test.parquet")

    resolutions = {
        "cid1": MarketResolution(
            condition_id="cid1",
            resolved_at=1500.0,
            winning_asset_ids=frozenset({"yes_asset_1"}),
        ),
    }
    token_map = {"cid1": {"YES": "yes_asset_1", "NO": "no_asset_1"}}

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        resolutions=resolutions,
        token_map=token_map,
        ledger=ledger,
    )

    trades = [
        _trade(cid="cid1", ts=1000.0, price=0.70),
        _trade(cid="cid_other", asset_id="other_yes", ts=2000.0),
    ]
    await runner.run(trades)

    records = await ledger.read_all()
    # cid1 should be enriched (resolution happened at 1500 < 2000)
    cid1_records = [r for r in records if r.condition_id == "cid1"]
    assert len(cid1_records) == 1
    assert cid1_records[0].pnl_gross is not None
    assert cid1_records[0].pnl_gross > 0  # won
    assert cid1_records[0].resolution == "WON"


async def test_no_settlement_for_unresolved():
    """Markets without resolution data stay open."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        resolutions={},  # no resolutions
        token_map={},
    )

    trades = [_trade(cid="cid1", ts=1000.0)]
    await runner.run(trades)

    pos = await ctx.get_position("cid1")
    assert pos.qty_yes > 0  # still open
    assert pos.cost_basis > 0
    assert runner.n_settled == 0


async def test_load_resolutions_from_rows():
    """load_resolutions_from_rows builds correct structures."""
    rows = [
        {"condition_id": "cid1", "asset_id": "yes_1", "outcome": "YES",
         "token_won": 0, "resolved_epoch": 1500.0},
        {"condition_id": "cid1", "asset_id": "no_1", "outcome": "NO",
         "token_won": 1, "resolved_epoch": 1500.0},
        {"condition_id": "cid2", "asset_id": "yes_2", "outcome": "YES",
         "token_won": 1, "resolved_epoch": 2000.0},
        {"condition_id": "cid2", "asset_id": "no_2", "outcome": "NO",
         "token_won": 0, "resolved_epoch": 2000.0},
    ]

    resolutions, token_map = load_resolutions_from_rows(rows)

    assert "cid1" in resolutions
    assert "no_1" in resolutions["cid1"].winning_asset_ids
    assert "yes_1" not in resolutions["cid1"].winning_asset_ids
    assert resolutions["cid1"].resolved_at == 1500.0

    assert "cid2" in resolutions
    assert "yes_2" in resolutions["cid2"].winning_asset_ids

    assert token_map["cid1"]["YES"] == "yes_1"
    assert token_map["cid1"]["NO"] == "no_1"
    assert token_map["cid2"]["YES"] == "yes_2"


async def test_capital_recycles_through_settlements():
    """With $30 capital and $10 bets, can fill 3 initially.
    After 2 settle, can fill 2 more (total 5)."""
    ctx = InMemoryContext()
    gw = ExecutionGateway(SimulatedExecutor(fee_pct=0.0))

    cfg = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=30.0,
        max_position_usd=15.0,
        max_open_positions=10,
        cooldown_s=0,
    )

    resolutions = {
        "cid1": MarketResolution("cid1", 1500.0, frozenset({"yes_1"})),
        "cid2": MarketResolution("cid2", 1500.0, frozenset({"yes_2"})),
    }
    token_map = {
        f"cid{i}": {"YES": f"yes_{i}", "NO": f"no_{i}"} for i in range(1, 6)
    }

    runner = ReplayRunner(
        strategy=_BuyStrategy(),
        ctx=ctx,
        gateway=gw,
        config=cfg,
        resolutions=resolutions,
        token_map=token_map,
    )

    trades = [
        # Phase 1: fill 3 positions ($30 capital)
        _trade(cid="cid1", asset_id="yes_1", ts=1000.0),
        _trade(cid="cid2", asset_id="yes_2", ts=1001.0),
        _trade(cid="cid3", asset_id="yes_3", ts=1002.0),
        # Phase 2: after settlement at 1500, capital freed
        _trade(cid="cid4", asset_id="yes_4", ts=2000.0),
        _trade(cid="cid5", asset_id="yes_5", ts=2001.0),
    ]

    result = await runner.run(trades)

    # 3 initial + at least 1-2 after settlement
    assert result.total_fills >= 4
    assert runner.n_settled == 2
