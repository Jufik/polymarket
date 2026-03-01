"""Tests for spread calibration from trade data."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from polymarket_pipeline.strategies.execution.calibrate import (
    calibrate_spreads,
    calibrate_volumes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(prices: dict[str, list[float]]) -> pl.DataFrame:
    """Build a DataFrame from {condition_id: [price1, price2, ...]}."""
    rows = []
    for cid, pxs in prices.items():
        for px in pxs:
            rows.append({"condition_id": cid, "price": px, "amount_usd": px * 100.0})
    return pl.DataFrame(rows)


def _make_trades(prices: dict[str, list[float]]) -> list:
    """Build a list of NormalizedTrade objects."""
    from polymarket_pipeline.models import NormalizedTrade, Side, Source

    trades = []
    ts = 1000
    for cid, pxs in prices.items():
        for px in pxs:
            trades.append(
                NormalizedTrade(
                    trade_id=f"t:{cid}:{ts}",
                    condition_id=cid,
                    asset_id="asset_1",
                    side=Side.BUY,
                    price=Decimal(str(px)),
                    size=Decimal("100"),
                    amount_usd=Decimal(str(px * 100.0)),
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
            )
            ts += 1
    return trades


# ---------------------------------------------------------------------------
# calibrate_spreads — median_abs_change
# ---------------------------------------------------------------------------


class TestMedianAbsChange:
    def test_known_spread(self) -> None:
        # Alternating prices simulate bid-ask bounce: 0.50, 0.52, 0.50, 0.52, ...
        prices = [0.50 + (0.02 if i % 2 else 0.0) for i in range(40)]
        df = _make_df({"m1": prices})

        spreads = calibrate_spreads(df, min_trades_per_market=10, method="median_abs_change")

        assert "m1" in spreads
        assert spreads["m1"] == pytest.approx(0.02, abs=0.001)

    def test_constant_price_excluded(self) -> None:
        # No price changes → median abs change is 0 → excluded
        df = _make_df({"m1": [0.50] * 30})
        spreads = calibrate_spreads(df, min_trades_per_market=10, method="median_abs_change")
        assert "m1" not in spreads

    def test_min_trades_filter(self) -> None:
        df = _make_df({"m1": [0.50, 0.52, 0.50]})  # only 3 trades
        spreads = calibrate_spreads(df, min_trades_per_market=20, method="median_abs_change")
        assert "m1" not in spreads

    def test_multiple_markets(self) -> None:
        df = _make_df({
            "m1": [0.50 + (0.01 if i % 2 else 0.0) for i in range(30)],
            "m2": [0.30 + (0.03 if i % 2 else 0.0) for i in range(30)],
        })
        spreads = calibrate_spreads(df, min_trades_per_market=10, method="median_abs_change")

        assert "m1" in spreads
        assert "m2" in spreads
        assert spreads["m1"] < spreads["m2"]  # m2 has wider spread


class TestRollEstimator:
    def test_bid_ask_bounce(self) -> None:
        # Perfect bid-ask bounce: 0.50, 0.52, 0.50, 0.52, ...
        # dp = +0.02, -0.02, +0.02, -0.02 → cov(dp, dp_lag) = -0.0004
        # half_spread = sqrt(0.0004) = 0.02
        prices = [0.50 + (0.02 if i % 2 else 0.0) for i in range(40)]
        df = _make_df({"m1": prices})

        spreads = calibrate_spreads(df, min_trades_per_market=10, method="roll")

        assert "m1" in spreads
        assert spreads["m1"] == pytest.approx(0.02, abs=0.005)

    def test_trending_excluded(self) -> None:
        # Monotonically increasing → positive autocorrelation → no spread
        prices = [0.50 + i * 0.001 for i in range(30)]
        df = _make_df({"m1": prices})

        spreads = calibrate_spreads(df, min_trades_per_market=10, method="roll")
        assert "m1" not in spreads


class TestInputFormats:
    def test_accepts_polars_df(self) -> None:
        df = _make_df({"m1": [0.50 + (0.02 if i % 2 else 0.0) for i in range(30)]})
        spreads = calibrate_spreads(df, min_trades_per_market=10)
        assert "m1" in spreads

    def test_accepts_trade_list(self) -> None:
        trades = _make_trades({"m1": [0.50 + (0.02 if i % 2 else 0.0) for i in range(30)]})
        spreads = calibrate_spreads(trades, min_trades_per_market=10)
        assert "m1" in spreads

    def test_empty_input(self) -> None:
        assert calibrate_spreads(pl.DataFrame()) == {}
        assert calibrate_spreads([]) == {}


class TestCalibrateVolumes:
    def test_basic_volumes(self) -> None:
        df = _make_df({"m1": [0.50, 0.60], "m2": [0.30]})
        volumes = calibrate_volumes(df)

        assert "m1" in volumes
        assert "m2" in volumes
        assert volumes["m1"] == pytest.approx(50.0 + 60.0, abs=0.1)
        assert volumes["m2"] == pytest.approx(30.0, abs=0.1)

    def test_accepts_trade_list(self) -> None:
        trades = _make_trades({"m1": [0.50, 0.60]})
        volumes = calibrate_volumes(trades)
        assert "m1" in volumes
