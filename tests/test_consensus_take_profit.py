"""Tests for ConsensusV2Strategy take-profit exit logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode, Position
from polymarket_pipeline.strategies_impl.consensus_v2.strategy import ConsensusV2Strategy

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

CONDITION = "0xcondition_abc"
STRATEGY = "politics_no_v3"
YES_ASSET = "0xyes_token"
NO_ASSET = "0xno_token"
TOKEN_MAP = {CONDITION: {"YES": YES_ASSET, "NO": NO_ASSET}}


@dataclass
class FakeTrade:
    condition_id: str = CONDITION
    asset_id: str = NO_ASSET
    side: str = "BUY"
    maker: str = "0xtrader1"
    price: float = 0.35
    amount_usd: float = 100.0
    size: float = 285.0
    published_at: float = 1_700_000_010.0
    trade_id: str = "test_trade_1"


class FakeContext:
    """Minimal StrategyContext stub for take-profit tests."""

    def __init__(
        self, position: Position | None = None, features: dict[str, Any] | None = None,
    ) -> None:
        self._position = position
        self._features = features or {}

    async def get_position(self, condition_id: str) -> Position | None:
        if self._position and self._position.condition_id == condition_id:
            return self._position
        return None

    async def get_features(self, key: str) -> dict[str, Any] | None:
        return self._features.get(key)

    def now(self) -> float:
        return 1_700_000_000.0


def _make_config(take_profit_pct: float | None = None) -> StrategyConfig:
    params: dict[str, Any] = {
        "n_threshold": 2,
        "direction_filter": "NO",
        "size_usd": 100,
    }
    if take_profit_pct is not None:
        params["take_profit_pct"] = take_profit_pct
    return StrategyConfig(
        name=STRATEGY,
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=3000,
        max_position_usd=100,
        max_open_positions=20,
        cooldown_s=60,
        features=["consensus_v3_politics"],
        params=params,
    )


FEATURES = {
    "consensus_v3_politics": {
        "pool": frozenset({"0xtrader1", "0xtrader2", "0xtrader3"}),
        "tag_markets": frozenset({CONDITION}),
        "gambling_markets": frozenset(),
        "token_map": TOKEN_MAP,
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_take_profit_triggers_sell_when_target_reached():
    """NO position at entry 0.30 with TP=50% → target = 0.30 + 0.50×0.70 = 0.65.
    Trade at NO price 0.66 should trigger SELL."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    # Trade on NO token at 0.66 (above target 0.65)
    trade = FakeTrade(asset_id=NO_ASSET, price=0.66)
    intents = await strategy.on_trade(trade, ctx)

    assert intents is not None
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "SELL"
    assert intent.outcome == "NO"
    assert intent.size_usd == 0  # sentinel for full position
    assert intent.strategy == STRATEGY
    assert "take_profit:50%" in intent.reason
    assert intent.asset_id == NO_ASSET


@pytest.mark.asyncio
async def test_take_profit_no_trigger_below_target():
    """NO position at entry 0.30 with TP=50% → target 0.65.
    Trade at NO price 0.60 should NOT trigger."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    trade = FakeTrade(asset_id=NO_ASSET, price=0.60)
    intents = await strategy.on_trade(trade, ctx)
    assert intents is None


@pytest.mark.asyncio
async def test_take_profit_via_complementary_token():
    """NO position — YES trade at 0.30 implies NO price ≈ 0.70. Should trigger."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    # YES trade at 0.30 → NO price ≈ 1 - 0.30 = 0.70 (above target 0.65)
    trade = FakeTrade(asset_id=YES_ASSET, price=0.30)
    intents = await strategy.on_trade(trade, ctx)

    assert intents is not None
    assert intents[0].side == "SELL"
    assert intents[0].outcome == "NO"


@pytest.mark.asyncio
async def test_take_profit_disabled_when_none():
    """No take_profit_pct → no exit logic, even if price is high."""
    config = _make_config(take_profit_pct=None)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    trade = FakeTrade(asset_id=NO_ASSET, price=0.90)
    intents = await strategy.on_trade(trade, ctx)
    # No TP configured → falls through to entry logic (which won't fire either,
    # since the market is already signaled or doesn't meet consensus)
    assert intents is None


@pytest.mark.asyncio
async def test_take_profit_only_fires_once():
    """After triggering TP, subsequent trades in same market should NOT re-trigger."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    trade = FakeTrade(asset_id=NO_ASSET, price=0.66)

    # First call triggers
    intents = await strategy.on_trade(trade, ctx)
    assert intents is not None

    # Second call should NOT trigger (already exited)
    intents = await strategy.on_trade(trade, ctx)
    assert intents is None


@pytest.mark.asyncio
async def test_take_profit_ignores_other_strategy_positions():
    """Position from a different strategy should not trigger our TP."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy="some_other_strategy",  # NOT our strategy
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    trade = FakeTrade(asset_id=NO_ASSET, price=0.90)
    intents = await strategy.on_trade(trade, ctx)
    assert intents is None


@pytest.mark.asyncio
async def test_take_profit_yes_position():
    """YES position at entry 0.40 with TP=50% → target = 0.40 + 0.50×0.60 = 0.70."""
    params = {"n_threshold": 3, "direction_filter": "YES", "size_usd": 100, "take_profit_pct": 0.50}
    config = StrategyConfig(
        name="sports_yes_v3",
        enabled=True,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=3000,
        max_position_usd=100,
        max_open_positions=20,
        cooldown_s=60,
        features=["consensus_v3_sports"],
        params=params,
    )
    strategy = ConsensusV2Strategy(config, **params)

    pos = Position(
        condition_id=CONDITION,
        strategy="sports_yes_v3",
        qty_yes=250.0,
        avg_entry_yes=0.40,
        cost_basis=100.0,
    )
    features = {
        "consensus_v3_sports": {
            "pool": frozenset({"0xtrader1"}),
            "tag_markets": frozenset({CONDITION}),
            "gambling_markets": frozenset(),
            "token_map": TOKEN_MAP,
        },
    }
    ctx = FakeContext(position=pos, features=features)

    # YES trade at 0.72 (above target 0.70)
    trade = FakeTrade(asset_id=YES_ASSET, price=0.72)
    intents = await strategy.on_trade(trade, ctx)

    assert intents is not None
    assert intents[0].side == "SELL"
    assert intents[0].outcome == "YES"
    assert intents[0].asset_id == YES_ASSET


@pytest.mark.asyncio
async def test_on_fill_rejected_allows_retry():
    """If a TP SELL is rejected, the market should be un-exited for retry."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    pos = Position(
        condition_id=CONDITION,
        strategy=STRATEGY,
        qty_no=333.0,
        avg_entry_no=0.30,
        cost_basis=100.0,
    )
    ctx = FakeContext(position=pos, features=FEATURES)

    trade = FakeTrade(asset_id=NO_ASSET, price=0.66)

    # Trigger TP
    intents = await strategy.on_trade(trade, ctx)
    assert intents is not None

    # Simulate rejection
    strategy.on_fill_rejected(CONDITION, "SELL", "NO")

    # Should retry on next qualifying trade
    intents = await strategy.on_trade(trade, ctx)
    assert intents is not None
    assert intents[0].side == "SELL"


@pytest.mark.asyncio
async def test_on_market_update_cleans_state():
    """Resolution event should clean up signaled/exited/market_state."""
    config = _make_config(take_profit_pct=0.50)
    strategy = ConsensusV2Strategy(config, **config.params)

    # Seed internal state
    strategy._signaled.add(CONDITION)
    strategy._exited.add(CONDITION)
    strategy._market_state[CONDITION] = {"0xtrader1": {"YES": 0, "NO": 100}}

    ctx = FakeContext()
    update = {"type": "resolution", "condition_id": CONDITION, "winner": "NO"}
    await strategy.on_market_update(update, ctx)

    assert CONDITION not in strategy._signaled
    assert CONDITION not in strategy._exited
    assert CONDITION not in strategy._market_state
