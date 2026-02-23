"""Tests for ConsensusCopyStrategy — event-driven and vectorized paths."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.protocol import Strategy, VectorizedStrategy
from polymarket_pipeline.strategies_impl.consensus_copy.config import ConsensusCopyConfig
from polymarket_pipeline.strategies_impl.consensus_copy.strategy import ConsensusCopyStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CID = "0xcondition_1"
SKILLED = {"0xalice", "0xbob", "0xcharlie", "0xdave"}


def _trade(
    maker: str,
    side: str,
    ts: int,
    cid: str = CID,
    price: float = 0.60,
) -> NormalizedTrade:
    """Build a NormalizedTrade fixture for testing."""
    return NormalizedTrade(
        trade_id=f"test:{maker}:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=Side(side),
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


def _make_strategy(
    direction: str = "NO",
    min_traders: int = 3,
    agreement_pct: float = 0.80,
) -> ConsensusCopyStrategy:
    """Create a ConsensusCopyStrategy with test defaults."""
    cfg = ConsensusCopyConfig(
        skilled_traders=SKILLED,
        min_traders=min_traders,
        agreement_pct=agreement_pct,
        direction=direction,
        base_bet_usd=10.0,
    )
    return ConsensusCopyStrategy(cfg)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_strategy_protocol(self) -> None:
        strat = _make_strategy()
        assert isinstance(strat, Strategy)

    def test_satisfies_vectorized_protocol(self) -> None:
        strat = _make_strategy()
        assert isinstance(strat, VectorizedStrategy)


# ---------------------------------------------------------------------------
# Event-driven path (on_trade)
# ---------------------------------------------------------------------------


class TestOnTrade:
    async def test_no_signal_below_min_traders(self) -> None:
        """2 traders arrive, min_traders=3 -> no signal."""
        strat = _make_strategy(min_traders=3)
        ctx = InMemoryContext()

        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xbob", "SELL", 1001)

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)

        assert r1 is None
        assert r2 is None

    async def test_signal_fires_at_threshold(self) -> None:
        """3 skilled sellers on same market, direction=NO -> fires signal."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.80)
        ctx = InMemoryContext()

        # SELL = selling YES tokens = betting NO
        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xbob", "SELL", 1001)
        t3 = _trade("0xcharlie", "SELL", 1002)

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)

        assert r1 is None
        assert r2 is None
        assert r3 is not None
        assert len(r3) == 1

        intent = r3[0]
        assert intent.strategy == "consensus_copy"
        assert intent.condition_id == CID
        assert intent.side == "BUY"
        assert intent.outcome == "NO"
        assert intent.size_usd == 10.0
        assert intent.signal_time == 1002.0

    async def test_ignores_non_skilled_traders(self) -> None:
        """Non-skilled traders should not count toward consensus."""
        strat = _make_strategy(min_traders=3)
        ctx = InMemoryContext()

        # Two skilled + one unskilled -> should NOT fire
        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xbob", "SELL", 1001)
        t3 = _trade("0xunknown", "SELL", 1002)  # not in skilled_traders

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)

        assert r1 is None
        assert r2 is None
        assert r3 is None

    async def test_no_duplicate_signal_per_market(self) -> None:
        """After signal fires, 4th trader should NOT re-fire."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.80)
        ctx = InMemoryContext()

        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xbob", "SELL", 1001)
        t3 = _trade("0xcharlie", "SELL", 1002)
        t4 = _trade("0xdave", "SELL", 1003)

        await strat.on_trade(t1, ctx)
        await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)
        r4 = await strat.on_trade(t4, ctx)

        assert r3 is not None  # signal fires
        assert r4 is None  # no duplicate

    async def test_direction_filtering(self) -> None:
        """YES traders ignored when direction='NO'."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.50)
        ctx = InMemoryContext()

        # BUY = buying YES tokens = betting YES
        t1 = _trade("0xalice", "BUY", 1000)
        t2 = _trade("0xbob", "BUY", 1001)
        t3 = _trade("0xcharlie", "BUY", 1002)

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)

        # All agree YES, but direction=NO so no signal
        assert r1 is None
        assert r2 is None
        assert r3 is None

    async def test_duplicate_maker_ignored(self) -> None:
        """Same maker trading twice in same market only counts once."""
        strat = _make_strategy(direction="NO", min_traders=2, agreement_pct=0.80)
        ctx = InMemoryContext()

        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xalice", "SELL", 1001)  # duplicate maker

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)

        assert r1 is None
        assert r2 is None  # still only 1 unique trader, need 2

    async def test_on_trade_reads_skilled_from_context(self) -> None:
        """Strategy should prefer skilled_traders from context features over config."""
        # Config has SKILLED set, but context has a DIFFERENT set
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.80)
        ctx = InMemoryContext()

        # Override context with a different skilled set that includes 0xeve
        ctx.update_features({"skilled_traders": frozenset({"0xeve", "0xfrank", "0xgrace"})})

        # These traders are in SKILLED (config) but NOT in context features
        t1 = _trade("0xalice", "SELL", 1000)
        r1 = await strat.on_trade(t1, ctx)
        assert r1 is None  # 0xalice not in context's skilled set

        # These traders ARE in context's skilled set
        t2 = _trade("0xeve", "SELL", 1001)
        t3 = _trade("0xfrank", "SELL", 1002)
        t4 = _trade("0xgrace", "SELL", 1003)

        r2 = await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)
        r4 = await strat.on_trade(t4, ctx)

        assert r2 is None  # only 1 trader
        assert r3 is None  # only 2 traders
        assert r4 is not None  # 3 traders, signal fires
        assert r4[0].outcome == "NO"

    async def test_on_trade_falls_back_to_config(self) -> None:
        """When context has no skilled_traders feature, falls back to config."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.80)
        ctx = InMemoryContext()
        # No features set — should fall back to config's SKILLED set

        t1 = _trade("0xalice", "SELL", 1000)
        t2 = _trade("0xbob", "SELL", 1001)
        t3 = _trade("0xcharlie", "SELL", 1002)

        r1 = await strat.on_trade(t1, ctx)
        r2 = await strat.on_trade(t2, ctx)
        r3 = await strat.on_trade(t3, ctx)

        assert r1 is None
        assert r2 is None
        assert r3 is not None  # uses config's SKILLED set

    async def test_on_market_update_returns_none(self) -> None:
        strat = _make_strategy()
        ctx = InMemoryContext()
        result = await strat.on_market_update({"price": 0.5}, ctx)
        assert result is None

    async def test_on_timer_returns_none(self) -> None:
        strat = _make_strategy()
        ctx = InMemoryContext()
        result = await strat.on_timer(1700000000.0, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Vectorized path (compute_signals)
# ---------------------------------------------------------------------------


class TestComputeSignals:
    def test_vectorized_basic_signal(self) -> None:
        """3 skilled SELL trades -> one NO signal row."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.80)

        trades = pl.LazyFrame(
            {
                "maker": ["0xalice", "0xbob", "0xcharlie", "0xunknown"],
                "condition_id": [CID, CID, CID, CID],
                "side": ["SELL", "SELL", "SELL", "SELL"],
                "published_at": [1000.0, 1001.0, 1002.0, 1003.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": [CID]})

        result = strat.compute_signals(trades, markets)

        assert len(result) == 1
        row = result.row(0, named=True)
        assert row["condition_id"] == CID
        assert row["outcome"] == "NO"
        assert row["side"] == "BUY"
        assert row["signal_time"] == 1002.0
        assert row["size_usd"] == 10.0

    def test_vectorized_no_signal_below_threshold(self) -> None:
        """Only 2 skilled traders, min_traders=3 -> empty result."""
        strat = _make_strategy(direction="NO", min_traders=3)

        trades = pl.LazyFrame(
            {
                "maker": ["0xalice", "0xbob"],
                "condition_id": [CID, CID],
                "side": ["SELL", "SELL"],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": [CID]})

        result = strat.compute_signals(trades, markets)
        assert len(result) == 0

    def test_vectorized_direction_filter(self) -> None:
        """All YES bets, direction=NO -> no signal."""
        strat = _make_strategy(direction="NO", min_traders=3, agreement_pct=0.50)

        trades = pl.LazyFrame(
            {
                "maker": ["0xalice", "0xbob", "0xcharlie"],
                "condition_id": [CID, CID, CID],
                "side": ["BUY", "BUY", "BUY"],
                "published_at": [1000.0, 1001.0, 1002.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": [CID]})

        result = strat.compute_signals(trades, markets)
        assert len(result) == 0
