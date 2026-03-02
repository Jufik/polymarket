"""Unit tests for S1 Hit-Rate Provider.

Tests consensus tracking, bankroll management, and feature keys.
No CH or network — provider state manipulated directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.provider import (
    S1HitRateProvider,
)


def _make_trade(
    *,
    maker: str = "0xqualified",
    cid: str = "0xcid1",
    asset_id: str = "asset_yes",
    side: Side = Side.BUY,
    price: float = 0.70,
    ts: float = 1000.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
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


def _provider_with_state() -> S1HitRateProvider:
    """Create a provider with pre-populated state (bypassing compute())."""
    p = S1HitRateProvider(initial_bankroll=500.0)
    p._qualified = {"0xqualified", "0xalice"}
    p._token_outcomes = {"asset_yes": "YES", "asset_no": "NO"}
    p._gambling_cids = set()
    p._consensus = {}
    return p


# ── 2a. Consensus Tracking ────────────────────────────────────────────


async def test_consensus_increments_yes():
    p = _provider_with_state()
    trade = _make_trade(maker="0xqualified", side=Side.BUY, asset_id="asset_yes")
    await p.on_trade(trade)
    assert "0xqualified" in p._consensus["0xcid1"]["yes"]
    assert len(p._consensus["0xcid1"]["no"]) == 0


async def test_consensus_skips_sell():
    """SELL trades don't build consensus — they're exits."""
    p = _provider_with_state()
    trade = _make_trade(maker="0xqualified", side=Side.SELL, asset_id="asset_yes")
    await p.on_trade(trade)
    assert "0xcid1" not in p._consensus


async def test_consensus_buy_no_token():
    p = _provider_with_state()
    # BUY NO token → direction is NO
    trade = _make_trade(maker="0xqualified", side=Side.BUY, asset_id="asset_no")
    await p.on_trade(trade)
    assert "0xqualified" in p._consensus["0xcid1"]["no"]


async def test_consensus_deduplicates_same_trader():
    """Same trader buying twice → set size 1, not counter 2."""
    p = _provider_with_state()
    t1 = _make_trade(maker="0xqualified", side=Side.BUY, asset_id="asset_yes", ts=1000)
    t2 = _make_trade(maker="0xqualified", side=Side.BUY, asset_id="asset_yes", ts=1001)
    await p.on_trade(t1)
    await p.on_trade(t2)
    assert len(p._consensus["0xcid1"]["yes"]) == 1


async def test_consensus_ignores_unqualified():
    p = _provider_with_state()
    trade = _make_trade(maker="0xrandom")
    await p.on_trade(trade)
    assert "0xcid1" not in p._consensus


async def test_consensus_ignores_no_maker():
    p = _provider_with_state()
    trade = _make_trade(maker=None)
    await p.on_trade(trade)
    assert len(p._consensus) == 0


async def test_consensus_ignores_unknown_asset():
    p = _provider_with_state()
    trade = _make_trade(maker="0xqualified", asset_id="asset_unknown")
    await p.on_trade(trade)
    assert len(p._consensus) == 0


async def test_consensus_multiple_markets():
    p = _provider_with_state()
    t1 = _make_trade(maker="0xqualified", cid="0xcid_a", side=Side.BUY, asset_id="asset_yes")
    t2 = _make_trade(maker="0xalice", cid="0xcid_b", side=Side.BUY, asset_id="asset_no")
    await p.on_trade(t1)
    await p.on_trade(t2)
    assert len(p._consensus["0xcid_a"]["yes"]) == 1
    assert len(p._consensus["0xcid_b"]["no"]) == 1


async def test_consensus_accumulates_unique_traders():
    """5 different traders → set size 5."""
    p = _provider_with_state()
    p._qualified = {f"0xtrader{i}" for i in range(5)}
    for i in range(5):
        t = _make_trade(
            maker=f"0xtrader{i}", side=Side.BUY, asset_id="asset_yes", ts=1000.0 + i,
        )
        await p.on_trade(t)
    assert len(p._consensus["0xcid1"]["yes"]) == 5


# ── 2b. Bankroll ──────────────────────────────────────────────────────


def test_update_bankroll_win():
    p = _provider_with_state()
    assert p._bankroll == 500.0
    p.update_bankroll(50.0)
    assert p._bankroll == 550.0
    assert p._realized_pnl == 50.0


def test_update_bankroll_loss():
    p = _provider_with_state()
    p.update_bankroll(-10.0)
    assert p._bankroll == 490.0
    assert p._realized_pnl == -10.0


def test_update_bankroll_cumulative():
    p = _provider_with_state()
    p.update_bankroll(100.0)
    p.update_bankroll(-30.0)
    p.update_bankroll(50.0)
    assert p._bankroll == 620.0
    assert p._realized_pnl == 120.0


# ── 2c. Feature Keys ─────────────────────────────────────────────────


def test_get_features_all_keys():
    p = _provider_with_state()
    features = p.get_features()
    expected_keys = {
        "s1_qualified_traders",
        "s1_trader_meta",
        "s1_token_outcomes",
        "s1_gambling_cids",
        "s1_market_close_epochs",
        "s1_consensus",
        "s1_bankroll",
    }
    assert set(features.keys()) == expected_keys


def test_bankroll_in_features():
    p = _provider_with_state()
    assert p.get_features()["s1_bankroll"] == 500.0
    p.update_bankroll(100.0)
    assert p.get_features()["s1_bankroll"] == 600.0


def test_initial_state_empty():
    p = S1HitRateProvider()
    assert len(p._qualified) == 0
    assert len(p._trader_meta) == 0
    assert len(p._consensus) == 0


def test_qualified_in_features():
    p = _provider_with_state()
    assert "0xqualified" in p.get_features()["s1_qualified_traders"]
    assert "0xalice" in p.get_features()["s1_qualified_traders"]
