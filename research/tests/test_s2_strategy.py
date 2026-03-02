# research/tests/test_s2_strategy.py
"""Unit tests for S2 hit-rate copy strategy."""
from __future__ import annotations

import pytest

from research.strategies.s2_hitrate_copy import S2HitRateCopy, S2Config


def test_default_config():
    cfg = S2Config()
    assert cfg.min_positions == 30
    assert cfg.min_excess_hr == 0.10
    assert cfg.seed_threshold == 1
    assert cfg.scale_threshold == 4
    assert cfg.seed_pct == 0.25
    assert cfg.seed_timeout_hours is None
    assert cfg.direction == "BOTH"
    assert cfg.recency_months == 6


def test_strategy_name():
    strat = S2HitRateCopy(S2Config())
    assert strat.name == "s2_hitrate_copy"


def test_strategy_has_protocol_methods():
    strat = S2HitRateCopy(S2Config())
    assert hasattr(strat, "on_trade")
    assert hasattr(strat, "on_market_update")
    assert hasattr(strat, "on_timer")


import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext


def _make_trade(
    cid: str,
    maker: str,
    price: float = 0.50,
    ts: float = 1000.0,
    side: Side = Side.BUY,
    asset_id: str = "asset_yes",
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{maker}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(round(price * 100, 2))),
        fee_usd=Decimal("0"),
        maker=maker,
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


def test_sell_trades_are_skipped():
    """SELL trades are exits, not signals — must be filtered."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", side=Side.SELL)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_unqualified_trader_skipped():
    strat = S2HitRateCopy(S2Config(seed_threshold=1))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xrandom_trader")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_seed_entry_on_first_qualified_trader():
    """First qualified trader triggers seed (25% position)."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=4, seed_pct=0.25, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.40)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert len(result) == 1
    intent = result[0]
    assert intent.size_usd == 25.0  # 100 * 0.25
    assert intent.side == "BUY"
    assert intent.condition_id == "cid_1"


def test_scale_entry_on_consensus():
    """4 unique qualified traders triggers scale (remaining 75%)."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=4, seed_pct=0.25, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a", "0xtrader_b", "0xtrader_c", "0xtrader_d"})
    ctx = InMemoryContext()

    # First trader -> seed
    r1 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))
    assert r1 is not None and r1[0].size_usd == 25.0

    # 2nd and 3rd -> no new intent (between seed and scale)
    r2 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_b", ts=2.0), ctx))
    assert r2 is None
    r3 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_c", ts=3.0), ctx))
    assert r3 is None

    # 4th -> scale (top up to full)
    r4 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_d", ts=4.0), ctx))
    assert r4 is not None
    assert r4[0].size_usd == 75.0  # remaining 75%


def test_duplicate_trader_not_counted_twice():
    """Same trader trading twice in same market doesn't inflate consensus."""
    strat = S2HitRateCopy(S2Config(seed_threshold=2, scale_threshold=4))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    r1 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))
    assert r1 is None  # need 2 unique traders
    r2 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=2.0), ctx))
    assert r2 is None  # still only 1 unique trader


def test_no_duplicate_intents_after_scale():
    """Once scaled, no more intents for that market."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, scale_threshold=2, position_size_usd=100.0))
    strat.set_qualified_traders({"0xtrader_a", "0xtrader_b", "0xtrader_c"})
    ctx = InMemoryContext()

    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))  # seed
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_b", ts=2.0), ctx))  # scale

    # 3rd trader -> already scaled, no more intents
    r3 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_c", ts=3.0), ctx))
    assert r3 is None


def test_seed_timeout_exits():
    """Stale seed positions emit SELL after timeout."""
    cfg = S2Config(
        seed_threshold=1,
        scale_threshold=4,
        seed_timeout_hours=24,
        position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    # Enter seed at t=1000
    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=t0), ctx))

    # Timer at t=1000 + 25h -> timeout (> 24h)
    t_timeout = t0 + 25 * 3600
    result = asyncio.run(strat.on_timer(t_timeout, ctx))
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "SELL"
    assert result[0].condition_id == "cid_1"


def test_seed_no_timeout_when_disabled():
    """No timeout when seed_timeout_hours is None."""
    cfg = S2Config(seed_threshold=1, scale_threshold=4, seed_timeout_hours=None)
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1000.0), ctx))

    result = asyncio.run(strat.on_timer(1000.0 + 999 * 3600, ctx))
    assert result is None


def test_qualified_traders_query_has_excess_hr():
    """SQL must filter by excess HR above direction-specific base rate."""
    sql = S2HitRateCopy.qualified_traders_query(
        min_positions=30, min_excess_hr=0.10, recency_months=6
    )
    assert "trader" in sql
    assert "hit_rate" in sql
    assert "excess_hr" in sql
    # Must filter out Up or Down markets
    assert "Up or Down" in sql
    # Must split by direction for base rate adjustment
    assert "position" in sql


# ── Task 1: Pool entry price filter + category exclusion ──


def test_config_entry_price_and_category_defaults():
    cfg = S2Config()
    assert cfg.max_entry_price == 0.95
    assert cfg.exclude_tags == ("Sports", "Weather")


def test_qualified_query_includes_entry_price_filter():
    sql = S2HitRateCopy.qualified_traders_query(max_entry_price=0.80)
    # Must compute direction-adjusted entry price and filter
    assert "dir_entry" in sql or "avg_yes_price" in sql
    assert "0.8" in sql


def test_qualified_query_includes_category_exclusion():
    sql = S2HitRateCopy.qualified_traders_query(
        exclude_tags=("Sports", "Weather")
    )
    # Tag-based exclusion via CTE
    assert "excluded_markets" in sql
    assert "event_tags" in sql
    assert "tags" in sql
    assert "'Sports'" in sql
    assert "'Weather'" in sql


def test_qualified_query_no_entry_price_filter():
    """max_entry_price=1.0 effectively disables the filter."""
    sql = S2HitRateCopy.qualified_traders_query(max_entry_price=1.0)
    # Should not contain a restrictive price filter
    # The dir_entry clause should be absent or trivially true
    assert "dir_entry" not in sql or "1.0" in sql


def test_qualified_query_empty_category_exclusion():
    sql = S2HitRateCopy.qualified_traders_query(exclude_tags=())
    # Should NOT contain tag-based exclusion CTE
    assert "excluded_markets" not in sql
    assert "event_tags" not in sql


# ── Task 2: Signal price filter + adaptive base rates ──


def test_config_signal_price_default():
    cfg = S2Config()
    assert cfg.max_signal_price == 0.85


def test_signal_price_filter_blocks_expensive():
    """Trades above max_signal_price are skipped."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, max_signal_price=0.85))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.90)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is None


def test_signal_price_filter_passes_cheap():
    """Trades below max_signal_price emit intent."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, max_signal_price=0.85))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.50)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert result[0].side == "BUY"


def test_signal_price_at_boundary():
    """Price exactly at threshold is allowed (strict >)."""
    strat = S2HitRateCopy(S2Config(seed_threshold=1, max_signal_price=0.85))
    strat.set_qualified_traders({"0xtrader_a"})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.85)
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None


def test_compute_base_rates_query_returns_sql():
    sql = S2HitRateCopy.compute_base_rates_query(recency_months=6)
    assert "yes_base_rate" in sql
    assert "no_base_rate" in sql
    assert "markets_resolved" in sql


def test_qualified_query_accepts_custom_base_rates():
    sql = S2HitRateCopy.qualified_traders_query(
        yes_base_rate=0.35, no_base_rate=0.65
    )
    assert "0.35" in sql
    assert "0.65" in sql


# ── Task 3: Bayesian shrinkage on HR ──


def test_config_bayesian_hr_default():
    cfg = S2Config()
    assert cfg.use_bayesian_hr is True


def test_qualified_query_bayesian_formula():
    sql = S2HitRateCopy.qualified_traders_query(use_bayesian_hr=True)
    # Must contain Bayesian prior terms (alpha=3.81 for YES, beta=6.19)
    assert "3.81" in sql
    assert "10.0" in sql  # alpha + beta = 10


def test_qualified_query_raw_hr_when_disabled():
    sql = S2HitRateCopy.qualified_traders_query(use_bayesian_hr=False)
    # Must use raw countIf/count formula, no Bayesian terms
    assert "3.81" not in sql


# ── Task 4: Asymmetric YES/NO sizing ──


def test_config_asymmetric_defaults():
    cfg = S2Config()
    assert cfg.yes_weight == 1.0
    assert cfg.no_weight == 1.0


def test_set_token_map():
    strat = S2HitRateCopy(S2Config())
    strat.set_token_map({"asset_yes": {"condition_id": "cid_1", "outcome": "YES"}})
    assert "asset_yes" in strat._token_map


def test_seed_intent_yes_weighted():
    """YES weight multiplies seed size."""
    cfg = S2Config(seed_threshold=1, scale_threshold=4, seed_pct=0.25, position_size_usd=100.0, yes_weight=1.5)
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a"})
    strat.set_token_map({"asset_yes": {"condition_id": "cid_1", "outcome": "YES"}})
    ctx = InMemoryContext()

    trade = _make_trade("cid_1", "0xtrader_a", price=0.40, asset_id="asset_yes")
    result = asyncio.run(strat.on_trade(trade, ctx))
    assert result is not None
    assert result[0].size_usd == 37.5  # 100 * 0.25 * 1.5
    assert result[0].outcome == "YES"


def test_default_weights_unchanged():
    """Default weights (1.0) produce same sizes as before."""
    cfg = S2Config(seed_threshold=1, scale_threshold=2, seed_pct=0.25, position_size_usd=100.0)
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xtrader_a", "0xtrader_b"})
    ctx = InMemoryContext()

    r1 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_a", ts=1.0), ctx))
    assert r1 is not None and r1[0].size_usd == 25.0  # seed

    r2 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xtrader_b", ts=2.0), ctx))
    assert r2 is not None and r2[0].size_usd == 75.0  # scale


# ── Task 5: Consensus velocity + hold time cap ──


def test_config_velocity_and_hold_defaults():
    cfg = S2Config()
    assert cfg.max_consensus_window_hours is None
    assert cfg.max_hold_hours is None


def test_consensus_velocity_blocks_slow():
    """Consensus too slow -> scale not emitted."""
    cfg = S2Config(
        seed_threshold=1, scale_threshold=4,
        max_consensus_window_hours=48, position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xa", "0xb", "0xc", "0xd"})
    ctx = InMemoryContext()

    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xa", ts=t0), ctx))  # seed
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xb", ts=t0 + 3600), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xc", ts=t0 + 2 * 3600), ctx))

    # 4th trade at 72h -> too slow (> 48h window)
    r4 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xd", ts=t0 + 72 * 3600), ctx))
    assert r4 is None


def test_consensus_velocity_passes_fast():
    """Fast consensus -> scale emitted."""
    cfg = S2Config(
        seed_threshold=1, scale_threshold=4,
        max_consensus_window_hours=48, position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xa", "0xb", "0xc", "0xd"})
    ctx = InMemoryContext()

    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xa", ts=t0), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xb", ts=t0 + 3600), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xc", ts=t0 + 2 * 3600), ctx))

    # 4th trade at 24h -> fast enough
    r4 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xd", ts=t0 + 24 * 3600), ctx))
    assert r4 is not None
    assert r4[0].size_usd == 75.0


def test_consensus_velocity_disabled():
    """No velocity filter when None."""
    cfg = S2Config(
        seed_threshold=1, scale_threshold=4,
        max_consensus_window_hours=None, position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xa", "0xb", "0xc", "0xd"})
    ctx = InMemoryContext()

    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xa", ts=t0), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xb", ts=t0 + 3600), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xc", ts=t0 + 2 * 3600), ctx))

    # 4th trade at 720h -> passes because velocity disabled
    r4 = asyncio.run(strat.on_trade(_make_trade("cid_1", "0xd", ts=t0 + 720 * 3600), ctx))
    assert r4 is not None


def test_max_hold_exits_scaled():
    """Scaled position exits after max_hold_hours."""
    cfg = S2Config(
        seed_threshold=1, scale_threshold=2,
        max_hold_hours=168, position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xa", "0xb"})
    ctx = InMemoryContext()

    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xa", ts=t0), ctx))  # seed
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xb", ts=t0 + 3600), ctx))  # scale

    # Timer at scale_time + 169h -> hold timeout
    result = asyncio.run(strat.on_timer(t0 + 3600 + 169 * 3600, ctx))
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "SELL"
    assert result[0].condition_id == "cid_1"
    assert result[0].size_usd == 100.0  # full position


def test_max_hold_disabled():
    """No hold timeout when None."""
    cfg = S2Config(
        seed_threshold=1, scale_threshold=2,
        max_hold_hours=None, position_size_usd=100.0,
    )
    strat = S2HitRateCopy(cfg)
    strat.set_qualified_traders({"0xa", "0xb"})
    ctx = InMemoryContext()

    t0 = 1000.0
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xa", ts=t0), ctx))
    asyncio.run(strat.on_trade(_make_trade("cid_1", "0xb", ts=t0 + 3600), ctx))

    result = asyncio.run(strat.on_timer(t0 + 10000 * 3600, ctx))
    assert result is None
