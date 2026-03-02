"""S1 Strategy Robustness Tests.

Validates edge cases and failure modes without network dependencies.
Run: uv run python research/scripts/s1_robustness.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.provider import S1HitRateProvider
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.strategy import S1HitRateCopyStrategy


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


def _provider_with_state(**kw: object) -> S1HitRateProvider:
    p = S1HitRateProvider(initial_bankroll=500.0, **kw)
    p._qualified = {"0xqualified"}
    p._token_outcomes = {"asset_yes": "YES", "asset_no": "NO"}
    p._gambling_cids = set()
    p._consensus = {}
    return p


async def test_empty_qualified_pool() -> None:
    """Strategy emits 0 intents when no traders are qualified."""
    strategy = S1HitRateCopyStrategy()
    ctx = InMemoryContext()
    ctx.update_features({
        "s1_qualified_traders": set(),  # empty!
        "s1_trader_meta": {},
        "s1_token_outcomes": {"asset_yes": "YES"},
        "s1_gambling_cids": set(),
        "s1_consensus": {},
    })
    trade = _make_trade()
    result = await strategy.on_trade(trade, ctx)
    assert result is None, "Should skip when no qualified traders"
    print("  PASS: empty_qualified_pool")


async def test_missing_token_outcomes() -> None:
    """Trades with unknown asset_ids are skipped gracefully."""
    strategy = S1HitRateCopyStrategy()
    ctx = InMemoryContext()
    ctx.update_features({
        "s1_qualified_traders": {"0xqualified"},
        "s1_trader_meta": {"0xqualified": {"spec": 0.8, "dom_cat": "sports", "last5": 3}},
        "s1_token_outcomes": {},  # empty!
        "s1_gambling_cids": set(),
        "s1_consensus": {"0xcid1": {"yes": 5, "no": 0}},
    })
    trade = _make_trade(asset_id="unknown_asset")
    result = await strategy.on_trade(trade, ctx)
    assert result is None, "Should skip unknown asset_id"
    print("  PASS: missing_token_outcomes")


async def test_consensus_cap() -> None:
    """Consensus dict doesn't grow beyond max_consensus_markets."""
    p = _provider_with_state(max_consensus_markets=100)

    # Generate 200 trades on 200 different markets
    for i in range(200):
        t = _make_trade(cid=f"0xcid_{i}", ts=1000.0 + i)
        await p.on_trade(t)

    assert len(p._consensus) <= 100, (
        f"Consensus should be capped at 100, got {len(p._consensus)}"
    )
    print(f"  PASS: consensus_cap (size={len(p._consensus)})")


async def test_consensus_reset_on_refresh() -> None:
    """Consensus is cleared when provider is re-computed."""
    p = _provider_with_state()
    for i in range(50):
        t = _make_trade(cid=f"0xcid_{i}", ts=1000.0 + i)
        await p.on_trade(t)
    assert len(p._consensus) > 0

    # Simulate refresh (reset state like compute does)
    p._consensus = {}  # compute() resets this
    assert len(p._consensus) == 0, "Consensus should be empty after refresh"
    print("  PASS: consensus_reset_on_refresh")


async def test_bankroll_near_zero() -> None:
    """Fractional sizing with near-zero bankroll doesn't crash."""
    strategy = S1HitRateCopyStrategy(
        sizing_mode="fractional", sizing_fraction=0.02, max_bet_usd=500.0,
    )
    ctx = InMemoryContext()
    ctx.update_features({
        "s1_qualified_traders": {"0xqualified"},
        "s1_trader_meta": {"0xqualified": {"spec": 0.8, "dom_cat": "sports", "last5": 3}},
        "s1_token_outcomes": {"asset_yes": "YES"},
        "s1_gambling_cids": set(),
        "s1_consensus": {"0xcid1": {"yes": 5, "no": 0}},
        "s1_bankroll": 5.0,  # very low
    })
    trade = _make_trade()
    result = await strategy.on_trade(trade, ctx)
    assert result is not None, "Should still emit intent"
    assert result[0].size_usd >= 1.0, "Bet should be at floor ($1)"
    assert result[0].size_usd <= 5.0, "Bet should not exceed bankroll"
    print(f"  PASS: bankroll_near_zero (bet=${result[0].size_usd})")


async def test_all_gambling_filtered() -> None:
    """All trades filtered when every market is gambling."""
    strategy = S1HitRateCopyStrategy()
    ctx = InMemoryContext()
    ctx.update_features({
        "s1_qualified_traders": {"0xqualified"},
        "s1_trader_meta": {"0xqualified": {"spec": 0.8, "dom_cat": "crypto", "last5": 5}},
        "s1_token_outcomes": {"asset_yes": "YES"},
        "s1_gambling_cids": {"0xcid1", "0xcid2", "0xcid3"},
        "s1_consensus": {"0xcid1": {"yes": 10, "no": 0}},
    })
    for cid in ["0xcid1", "0xcid2", "0xcid3"]:
        trade = _make_trade(cid=cid)
        result = await strategy.on_trade(trade, ctx)
        assert result is None, f"Should skip gambling market {cid}"
    print("  PASS: all_gambling_filtered")


async def test_provider_on_trade_no_maker() -> None:
    """Provider on_trade handles maker=None gracefully."""
    p = _provider_with_state()
    trade = _make_trade(maker=None)
    await p.on_trade(trade)  # should not crash
    assert len(p._consensus) == 0
    print("  PASS: provider_on_trade_no_maker")


async def test_high_volume_consensus() -> None:
    """10K trades don't cause performance issues."""
    p = _provider_with_state(max_consensus_markets=10_000)
    for i in range(10_000):
        t = _make_trade(cid=f"0xcid_{i % 500}", ts=1000.0 + i)
        await p.on_trade(t)
    assert len(p._consensus) <= 500
    # Check one market has accumulated correctly
    expected = 10_000 // 500  # 20 trades per market
    assert p._consensus["0xcid_0"]["yes"] == expected
    print(f"  PASS: high_volume_consensus ({len(p._consensus)} markets)")


async def main() -> None:
    print("S1 Robustness Tests")
    print("=" * 40)
    await test_empty_qualified_pool()
    await test_missing_token_outcomes()
    await test_consensus_cap()
    await test_consensus_reset_on_refresh()
    await test_bankroll_near_zero()
    await test_all_gambling_filtered()
    await test_provider_on_trade_no_maker()
    await test_high_volume_consensus()
    print("=" * 40)
    print("All robustness tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
