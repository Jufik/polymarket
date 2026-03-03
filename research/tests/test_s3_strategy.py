"""Tests for S3 NO Sniper strategy."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from research.strategies.s3_no_sniper import S3Config, S3NoSniper


def _make_trade(
    cid: str,
    price: float,
    ts: float,
    asset_id: str = "asset_yes_1",
    side: Side = Side.BUY,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(round(price * 100, 2))),
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


def _make_strat(
    eligible_tags: frozenset[str] | None = None,
    **kwargs,
) -> S3NoSniper:
    cfg = S3Config(
        eligible_tags=eligible_tags or frozenset({"Tech", "Trump", "Economy"}),
        **kwargs,
    )
    strat = S3NoSniper(cfg)
    strat.set_tag_map({
        "cid_tech": "Tech",
        "cid_trump": "Trump",
        "cid_economy": "Economy",
        "cid_crypto": "Crypto",
        "cid_music": "Music",
    })
    strat.set_token_map({
        "cid_tech": {"YES": "asset_yes_1", "NO": "asset_no_1"},
        "cid_trump": {"YES": "asset_yes_2", "NO": "asset_no_2"},
        "cid_economy": {"YES": "asset_yes_3", "NO": "asset_no_3"},
        "cid_crypto": {"YES": "asset_yes_4", "NO": "asset_no_4"},
    })
    return strat


# ------------------------------------------------------------------
# Tag filter tests
# ------------------------------------------------------------------


def test_eligible_tag_enters():
    """Trade in eligible tag (Tech) with good price triggers entry."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.30, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"
    assert result[0].outcome == "NO"
    assert result[0].asset_id == "asset_no_1"


def test_ineligible_tag_rejected():
    """Trade in non-eligible tag (Crypto) is rejected."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_crypto", price=0.30, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_unknown_market_rejected():
    """Trade in market not in tag_map is rejected."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_unknown", price=0.30, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


# ------------------------------------------------------------------
# Price zone filter tests
# ------------------------------------------------------------------


def test_yes_price_below_min_rejected():
    """YES price < 0.15 is outside the zone."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.10, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_yes_price_above_max_rejected():
    """YES price > 0.50 is outside the zone."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.55, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_yes_price_at_boundary_accepted():
    """YES price exactly at boundaries should work."""
    strat = _make_strat()
    ctx = InMemoryContext()
    # At min boundary
    trade = _make_trade("cid_tech", price=0.15, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None


def test_no_token_trade_price_inversion():
    """When trade is for NO token, YES price = 1 - trade.price."""
    strat = _make_strat()
    ctx = InMemoryContext()
    # Trade is for NO token at price 0.70 → YES price = 0.30 (in zone)
    trade = _make_trade("cid_tech", price=0.70, ts=1000.0, asset_id="asset_no_1")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert result[0].outcome == "NO"
    # max_price should be based on YES=0.30, so NO=0.70 + buffer
    assert result[0].max_price == 0.72


def test_no_token_trade_outside_zone():
    """NO token at price 0.30 → YES price = 0.70, outside zone."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.30, ts=1000.0, asset_id="asset_no_1")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


# ------------------------------------------------------------------
# Market age filter tests
# ------------------------------------------------------------------


def test_first_trade_enters():
    """First trade in a market (age=0s) triggers entry."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.30, ts=1000.0)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None


def test_trade_within_window_enters():
    """Trade within 5-minute window triggers entry (if first didn't match)."""
    strat = _make_strat()
    ctx = InMemoryContext()
    # First trade: too expensive, doesn't trigger
    t1 = _make_trade("cid_trump", price=0.60, ts=1000.0, asset_id="asset_yes_2")
    r1 = asyncio.run(strat.on_trade(t1, ctx))
    assert r1 is None
    # Second trade: 2 min later, good price
    t2 = _make_trade("cid_trump", price=0.35, ts=1120.0, asset_id="asset_yes_2")
    r2 = asyncio.run(strat.on_trade(t2, ctx))
    assert r2 is not None


def test_trade_after_window_rejected():
    """Trade after 5-minute window is rejected."""
    strat = _make_strat()
    ctx = InMemoryContext()
    # First trade establishes market birth
    t1 = _make_trade("cid_tech", price=0.60, ts=1000.0)
    asyncio.run(strat.on_trade(t1, ctx))
    # Second trade: 6 minutes later (360s > 300s)
    t2 = _make_trade("cid_tech", price=0.30, ts=1360.0)
    result = asyncio.run(strat.on_trade(t2, ctx))
    assert result is None


def test_custom_max_age():
    """max_market_age_s=180 (3 min) rejects at 4 min."""
    strat = _make_strat(max_market_age_s=180)
    ctx = InMemoryContext()
    t1 = _make_trade("cid_tech", price=0.60, ts=1000.0)
    asyncio.run(strat.on_trade(t1, ctx))
    # 240s > 180s
    t2 = _make_trade("cid_tech", price=0.30, ts=1240.0)
    result = asyncio.run(strat.on_trade(t2, ctx))
    assert result is None


# ------------------------------------------------------------------
# Dedup (one position per market) tests
# ------------------------------------------------------------------


def test_dedup_blocks_second_entry():
    """Once positioned in a market, second entry is blocked."""
    strat = _make_strat()
    ctx = InMemoryContext()
    t1 = _make_trade("cid_tech", price=0.30, ts=1000.0)
    r1 = asyncio.run(strat.on_trade(t1, ctx))
    assert r1 is not None
    # Second trade in same market (still within window)
    t2 = _make_trade("cid_tech", price=0.25, ts=1060.0)
    r2 = asyncio.run(strat.on_trade(t2, ctx))
    assert r2 is None


def test_different_markets_both_enter():
    """Can enter different markets independently."""
    strat = _make_strat()
    ctx = InMemoryContext()
    t1 = _make_trade("cid_tech", price=0.30, ts=1000.0)
    r1 = asyncio.run(strat.on_trade(t1, ctx))
    assert r1 is not None
    t2 = _make_trade("cid_trump", price=0.35, ts=1010.0, asset_id="asset_yes_2")
    r2 = asyncio.run(strat.on_trade(t2, ctx))
    assert r2 is not None


# ------------------------------------------------------------------
# Trade intent correctness
# ------------------------------------------------------------------


def test_intent_fields():
    """Verify TradeIntent has correct fields."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_economy", price=0.40, ts=1000.0, asset_id="asset_yes_3")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    intent = result[0]
    assert intent.strategy == "s3_no_sniper"
    assert intent.condition_id == "cid_economy"
    assert intent.side == "BUY"
    assert intent.outcome == "NO"
    assert intent.size_usd == 10.0
    assert intent.asset_id == "asset_no_3"
    # max_price = (1 - 0.40) + 0.02 = 0.62
    assert intent.max_price == 0.62
    assert "s3_snipe" in intent.reason
    assert "Economy" in intent.reason


def test_sell_side_ignored():
    """SELL trades do not trigger entry."""
    strat = _make_strat()
    ctx = InMemoryContext()
    trade = _make_trade("cid_tech", price=0.30, ts=1000.0, side=Side.SELL)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None
