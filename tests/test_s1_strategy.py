"""Unit tests for S1 Hit-Rate Copy-Trading Strategy.

Tests every filter, direction logic, pricing, and sizing mode.
All tests use mock InMemoryContext — no CH, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.types import OrderbookSnapshot, Position
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.strategy import (
    S1HitRateCopyStrategy,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_trade(
    *,
    maker: str | None = "0xqualified",
    cid: str = "0xcid1",
    asset_id: str = "asset_yes",
    side: Side = Side.BUY,
    price: float = 0.70,
    published_at: float = 1000.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{published_at}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xtaker",
        timestamp=datetime.fromtimestamp(published_at, tz=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=published_at,
    )


def _make_ctx(
    *,
    qualified: set[str] | None = None,
    trader_meta: dict | None = None,
    token_outcomes: dict | None = None,
    gambling_cids: set[str] | None = None,
    consensus: dict | None = None,
    bankroll: float | None = None,
    orderbook: OrderbookSnapshot | None = None,
    position: Position | None = None,
    cid: str = "0xcid1",
) -> InMemoryContext:
    ctx = InMemoryContext()
    ctx.update_features({
        "s1_qualified_traders": qualified if qualified is not None else {"0xqualified"},
        "s1_trader_meta": trader_meta if trader_meta is not None else {
            "0xqualified": {"spec": 0.80, "dom_cat": "sports", "last5": 3},
        },
        "s1_token_outcomes": token_outcomes if token_outcomes is not None else {
            "asset_yes": "YES",
            "asset_no": "NO",
        },
        "s1_gambling_cids": gambling_cids if gambling_cids is not None else set(),
        "s1_consensus": consensus if consensus is not None else {
            cid: {"yes": {f"0xt{i}" for i in range(5)}, "no": {"0xn1", "0xn2"}},
        },
    })
    if bankroll is not None:
        ctx.update_features({"s1_bankroll": bankroll})
    if orderbook is not None:
        ctx.set_orderbook(cid, orderbook)
    if position is not None:
        ctx.set_position(cid, position)
    return ctx


def _strategy(**kw) -> S1HitRateCopyStrategy:
    defaults = dict(
        size_usd=10.0,
        min_dir_price=0.60,
        max_dir_price=0.90,
        min_specialization=0.70,
        min_last5=1,
        min_consensus=4,
    )
    defaults.update(kw)
    return S1HitRateCopyStrategy(**defaults)


# ── 1a. Filter Tests ──────────────────────────────────────────────────


async def test_skip_no_maker():
    trade = _make_trade(maker=None)
    ctx = _make_ctx()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_not_qualified():
    trade = _make_trade(maker="0xunknown")
    ctx = _make_ctx()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_gambling_market():
    trade = _make_trade()
    ctx = _make_ctx(gambling_cids={"0xcid1"})
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_unknown_asset():
    trade = _make_trade(asset_id="asset_mystery")
    ctx = _make_ctx()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_price_too_low():
    trade = _make_trade(price=0.50)  # dir_price = 0.50 < 0.60
    ctx = _make_ctx()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_price_too_high():
    trade = _make_trade(price=0.92)  # dir_price = 0.92 >= 0.90
    ctx = _make_ctx()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_low_specialization():
    ctx = _make_ctx(trader_meta={"0xqualified": {"spec": 0.50, "dom_cat": "sports", "last5": 3}})
    trade = _make_trade()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_cold_streak():
    ctx = _make_ctx(trader_meta={"0xqualified": {"spec": 0.80, "dom_cat": "sports", "last5": 0}})
    trade = _make_trade()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_low_consensus():
    ctx = _make_ctx(consensus={"0xcid1": {"yes": {"0xa", "0xb"}, "no": set()}})  # 2 < 4
    trade = _make_trade()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_no_consensus_entry():
    ctx = _make_ctx(consensus={})  # market not in consensus dict
    trade = _make_trade()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_sell_trade():
    """SELL trades are position exits, not directional signals."""
    ctx = _make_ctx()
    trade = _make_trade(side=Side.SELL)
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_existing_position():
    pos = Position(condition_id="0xcid1", strategy="s1_hitrate_copy", qty_yes=10.0)
    ctx = _make_ctx(position=pos)
    trade = _make_trade()
    assert await _strategy().on_trade(trade, ctx) is None


async def test_skip_book_moved_too_far():
    ob = OrderbookSnapshot(
        condition_id="0xcid1",
        best_bid=0.90, best_ask=0.95, bid_depth=1000, ask_depth=1000, timestamp=1000.0,
    )
    ctx = _make_ctx(orderbook=ob)
    trade = _make_trade(price=0.70)  # trader got 0.70, book now 0.95
    assert await _strategy().on_trade(trade, ctx) is None


async def test_pass_all_filters():
    ctx = _make_ctx()
    trade = _make_trade(price=0.70)
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None
    assert len(result) == 1
    intent = result[0]
    assert intent.strategy == "s1_hitrate_copy"
    assert intent.side == "BUY"
    assert intent.condition_id == "0xcid1"


# ── 1b. Direction Logic ───────────────────────────────────────────────


async def test_buy_yes_copies_yes():
    ctx = _make_ctx()
    trade = _make_trade(side=Side.BUY, asset_id="asset_yes")
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None
    assert result[0].outcome == "YES"


async def test_sell_yes_skipped():
    """SELL YES is an exit, not a NO signal. Skipped."""
    ctx = _make_ctx(consensus={"0xcid1": {"yes": set(), "no": {f"0x{i}" for i in range(5)}}})
    trade = _make_trade(side=Side.SELL, asset_id="asset_yes")
    assert await _strategy().on_trade(trade, ctx) is None


async def test_buy_no_copies_no():
    # BUY a NO token → copy_outcome = NO
    no_cons = {f"0xn{i}" for i in range(5)}
    ctx = _make_ctx(consensus={"0xcid1": {"yes": set(), "no": no_cons}})
    trade = _make_trade(side=Side.BUY, asset_id="asset_no", price=0.30)
    # dir_price for NO: 1 - 0.30 = 0.70 (in range)
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None
    assert result[0].outcome == "NO"


async def test_sell_no_skipped():
    """SELL NO is an exit, not a YES signal. Skipped."""
    ctx = _make_ctx()
    trade = _make_trade(side=Side.SELL, asset_id="asset_no", price=0.30)
    assert await _strategy().on_trade(trade, ctx) is None


# ── 1c. Pricing ───────────────────────────────────────────────────────


async def test_orderbook_price_used():
    ob = OrderbookSnapshot(
        condition_id="0xcid1",
        best_bid=0.72, best_ask=0.74, bid_depth=1000, ask_depth=1000, timestamp=1000.0,
    )
    ctx = _make_ctx(orderbook=ob)
    trade = _make_trade(price=0.70)
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None
    assert result[0].max_price == pytest.approx(0.75, abs=0.001)  # 0.74 + 0.01


async def test_fallback_trader_price():
    ctx = _make_ctx()  # no orderbook set
    trade = _make_trade(price=0.70)
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None
    assert result[0].max_price == pytest.approx(0.72, abs=0.001)  # 0.70 + 0.02


async def test_dir_price_yes():
    """YES outcome: dir_price = trade.price."""
    ctx = _make_ctx()
    # price=0.65, below min_dir_price=0.60? No, 0.65 is fine
    trade = _make_trade(price=0.65, asset_id="asset_yes")
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None  # 0.65 in [0.60, 0.90)


async def test_dir_price_no():
    """NO outcome: dir_price = 1 - trade.price."""
    no_cons = {f"0xn{i}" for i in range(5)}
    ctx = _make_ctx(consensus={"0xcid1": {"yes": set(), "no": no_cons}})
    # price=0.25 for NO token → dir_price = 1 - 0.25 = 0.75
    trade = _make_trade(price=0.25, asset_id="asset_no")
    result = await _strategy().on_trade(trade, ctx)
    assert result is not None  # dir_price 0.75 in [0.60, 0.90)


async def test_no_token_price_too_low():
    """NO token with high price → dir_price = 1 - 0.60 = 0.40 → filtered out."""
    ctx = _make_ctx()
    trade = _make_trade(price=0.60, asset_id="asset_no")
    # dir_price = 1 - 0.60 = 0.40 < 0.60 → skip
    assert await _strategy().on_trade(trade, ctx) is None


# ── 1d. Sizing ────────────────────────────────────────────────────────


async def test_fixed_sizing():
    strat = _strategy(size_usd=25.0)
    ctx = _make_ctx()
    result = await strat.on_trade(_make_trade(), ctx)
    assert result is not None
    assert result[0].size_usd == 25.0


async def test_fractional_sizing():
    strat = S1HitRateCopyStrategy(
        sizing_mode="fractional", sizing_fraction=0.02, max_bet_usd=500.0,
    )
    ctx = _make_ctx(bankroll=1000.0)
    result = await strat.on_trade(_make_trade(), ctx)
    assert result is not None
    assert result[0].size_usd == pytest.approx(20.0)  # 1000 * 0.02


async def test_fractional_bet_cap():
    strat = S1HitRateCopyStrategy(
        sizing_mode="fractional", sizing_fraction=0.02, max_bet_usd=500.0,
    )
    ctx = _make_ctx(bankroll=50000.0)
    result = await strat.on_trade(_make_trade(), ctx)
    assert result is not None
    assert result[0].size_usd == 500.0  # capped


async def test_fractional_bet_floor():
    strat = S1HitRateCopyStrategy(
        sizing_mode="fractional", sizing_fraction=0.02, max_bet_usd=500.0,
    )
    ctx = _make_ctx(bankroll=10.0)
    result = await strat.on_trade(_make_trade(), ctx)
    assert result is not None
    assert result[0].size_usd == 1.0  # floor (10 * 0.02 = 0.2 → clamped to 1.0)


async def test_fractional_no_bankroll_feature():
    strat = S1HitRateCopyStrategy(
        size_usd=10.0, sizing_mode="fractional", sizing_fraction=0.02, max_bet_usd=500.0,
    )
    ctx = _make_ctx()  # no bankroll feature set
    result = await strat.on_trade(_make_trade(), ctx)
    assert result is not None
    # Fallback: size_usd / fraction = 10 / 0.02 = 500, bet = 500 * 0.02 = 10
    assert result[0].size_usd == pytest.approx(10.0)
