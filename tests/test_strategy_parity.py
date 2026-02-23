"""Tests for parity gate — vectorized vs replay validation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.runners.parity import ParityReport, validate_parity
from polymarket_pipeline.strategies.types import TradeIntent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade(cid: str, ts: int, price: float = 0.50) -> NormalizedTrade:
    """Build a minimal NormalizedTrade for testing."""
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
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
        published_at=float(ts),
    )


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------


class AlignedStrategy:
    """Emits a signal on the 2nd trade per condition_id (both paths agree).

    Event-driven: tracks trade count per condition_id, emits intent on 2nd trade.
    Vectorized: groups by condition_id, filters to markets with >= 2 trades.
    """

    name: str = "aligned"

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: object,
    ) -> list[TradeIntent] | None:
        cid = trade.condition_id
        self._counts[cid] = self._counts.get(cid, 0) + 1
        if self._counts[cid] == 2:
            return [
                TradeIntent(
                    strategy="aligned",
                    condition_id=cid,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="immediate",
                    max_price=float(trade.price),
                    reason="2nd trade",
                    signal_time=trade.published_at,
                )
            ]
        return None

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        df = trades.group_by("condition_id").agg(pl.len().alias("cnt")).filter(
            pl.col("cnt") >= 2
        ).collect()
        if df.is_empty():
            return pl.DataFrame({"condition_id": pl.Series([], dtype=pl.Utf8)})
        return df.select("condition_id")


class MisalignedStrategy:
    """Event-driven emits on every trade; vectorized emits only on first per market.

    This guarantees a mismatch: event-driven fires N times, vectorized fires once
    per unique condition_id.
    """

    name: str = "misaligned"

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: object,
    ) -> list[TradeIntent] | None:
        return [
            TradeIntent(
                strategy="misaligned",
                condition_id=trade.condition_id,
                side="BUY",
                outcome="YES",
                size_usd=10.0,
                urgency="immediate",
                max_price=float(trade.price),
                reason="every trade",
                signal_time=trade.published_at,
            )
        ]

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        df = trades.select("condition_id").unique().collect()
        return df


class EmptyStrategy:
    """Both paths produce zero signals."""

    name: str = "empty"

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: object,
    ) -> list[TradeIntent] | None:
        return None

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame({"condition_id": pl.Series([], dtype=pl.Utf8)})


# ---------------------------------------------------------------------------
# Markets fixture (empty, just satisfying the interface)
# ---------------------------------------------------------------------------

_EMPTY_MARKETS = pl.LazyFrame({"condition_id": pl.Series([], dtype=pl.Utf8)})

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_aligned_strategy_produces_parity() -> None:
    """AlignedStrategy: both paths agree on same markets -> is_aligned=True."""
    trades = [
        _make_trade("cid_A", 100),
        _make_trade("cid_A", 200),  # 2nd trade for cid_A -> emits signal
        _make_trade("cid_B", 300),
        _make_trade("cid_B", 400),  # 2nd trade for cid_B -> emits signal
        _make_trade("cid_C", 500),  # only 1 trade for cid_C -> no signal
    ]
    strategy = AlignedStrategy()

    report = await validate_parity(strategy, trades, _EMPTY_MARKETS)

    assert isinstance(report, ParityReport)
    assert report.is_aligned is True
    assert report.vectorized_count == 2
    assert report.replay_count == 2
    assert report.vectorized_markets == {"cid_A", "cid_B"}
    assert report.replay_markets == {"cid_A", "cid_B"}
    assert report.missing_in_replay == set()
    assert report.extra_in_replay == set()


async def test_misaligned_strategy_detects_divergence() -> None:
    """MisalignedStrategy: event-driven produces more signals -> is_aligned=False."""
    trades = [
        _make_trade("cid_A", 100),
        _make_trade("cid_A", 200),  # 2nd trade for cid_A -> event fires again
        _make_trade("cid_B", 300),
    ]
    strategy = MisalignedStrategy()

    report = await validate_parity(strategy, trades, _EMPTY_MARKETS)

    assert isinstance(report, ParityReport)
    assert report.is_aligned is False
    # Vectorized: unique condition_ids = 2 (cid_A, cid_B)
    assert report.vectorized_count == 2
    # Event-driven: 3 intents (one per trade)
    assert report.replay_count == 3
    # Markets overlap, but counts differ
    assert report.vectorized_markets == {"cid_A", "cid_B"}
    assert report.replay_markets == {"cid_A", "cid_B"}


async def test_empty_signals_both_paths_zero() -> None:
    """EmptyStrategy: both produce 0 signals -> is_aligned=True."""
    trades = [
        _make_trade("cid_A", 100),
        _make_trade("cid_B", 200),
    ]
    strategy = EmptyStrategy()

    report = await validate_parity(strategy, trades, _EMPTY_MARKETS)

    assert isinstance(report, ParityReport)
    assert report.is_aligned is True
    assert report.vectorized_count == 0
    assert report.replay_count == 0
    assert report.vectorized_markets == set()
    assert report.replay_markets == set()
    assert report.missing_in_replay == set()
    assert report.extra_in_replay == set()


async def test_missing_in_replay_detected() -> None:
    """When vectorized finds a market that replay misses, report it in missing_in_replay."""

    class VecOnlyStrategy:
        """Vectorized signals on cid_A and cid_B; replay only signals on cid_A."""

        name: str = "vec_only"

        def __init__(self) -> None:
            self._counts: dict[str, int] = {}

        async def on_trade(
            self,
            trade: NormalizedTrade,
            ctx: object,
        ) -> list[TradeIntent] | None:
            cid = trade.condition_id
            self._counts[cid] = self._counts.get(cid, 0) + 1
            # Only signal for cid_A (ignore cid_B in event-driven path)
            if cid == "cid_A" and self._counts[cid] == 1:
                return [
                    TradeIntent(
                        strategy="vec_only",
                        condition_id=cid,
                        side="BUY",
                        outcome="YES",
                        size_usd=10.0,
                        urgency="immediate",
                        max_price=0.50,
                        reason="cid_A only",
                        signal_time=trade.published_at,
                    )
                ]
            return None

        def compute_signals(
            self, trades: pl.LazyFrame, markets: pl.LazyFrame
        ) -> pl.DataFrame:
            # Vectorized signals for both cid_A and cid_B
            return pl.DataFrame({"condition_id": ["cid_A", "cid_B"]})

    trades = [
        _make_trade("cid_A", 100),
        _make_trade("cid_B", 200),
    ]

    report = await validate_parity(VecOnlyStrategy(), trades, _EMPTY_MARKETS)

    assert report.is_aligned is False
    assert report.missing_in_replay == {"cid_B"}
    assert report.extra_in_replay == set()


async def test_extra_in_replay_detected() -> None:
    """When replay finds a market that vectorized misses, report it in extra_in_replay."""

    class ReplayOnlyStrategy:
        """Replay signals on cid_A and cid_B; vectorized only signals on cid_A."""

        name: str = "replay_only"

        async def on_trade(
            self,
            trade: NormalizedTrade,
            ctx: object,
        ) -> list[TradeIntent] | None:
            # Signal on every trade
            return [
                TradeIntent(
                    strategy="replay_only",
                    condition_id=trade.condition_id,
                    side="BUY",
                    outcome="YES",
                    size_usd=10.0,
                    urgency="immediate",
                    max_price=0.50,
                    reason="all trades",
                    signal_time=trade.published_at,
                )
            ]

        def compute_signals(
            self, trades: pl.LazyFrame, markets: pl.LazyFrame
        ) -> pl.DataFrame:
            # Only cid_A
            return pl.DataFrame({"condition_id": ["cid_A"]})

    trades = [
        _make_trade("cid_A", 100),
        _make_trade("cid_B", 200),
    ]

    report = await validate_parity(ReplayOnlyStrategy(), trades, _EMPTY_MARKETS)

    assert report.is_aligned is False
    assert report.missing_in_replay == set()
    assert report.extra_in_replay == {"cid_B"}
