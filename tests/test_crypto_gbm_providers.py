"""Tests for ExchangePriceProvider and CryptoWindowProvider."""

from __future__ import annotations

from polymarket_pipeline.strategies_impl.crypto_gbm.providers import (
    ExchangePriceProvider,
)


class TestExchangePriceProvider:
    def test_initial_state(self) -> None:
        p = ExchangePriceProvider()
        features = p.get_features()
        assert features["exchange_btc_price"] == 0.0
        assert features["exchange_btc_sigma"] is None
        assert features["exchange_btc_bar_count"] == 0

    def test_handle_bar_updates_price(self) -> None:
        p = ExchangePriceProvider(primary_exchange="BINANCE", primary_symbol="BTC-USDT")
        p.handle_bar({
            "exchange": "BINANCE",
            "symbol": "BTC-USDT",
            "close": 67000.0,
            "ts": 1700000000,
        })
        features = p.get_features()
        assert features["exchange_btc_price"] == 67000.0
        assert features["exchange_btc_bar_count"] == 1

    def test_ignores_wrong_exchange(self) -> None:
        p = ExchangePriceProvider(primary_exchange="BINANCE", primary_symbol="BTC-USDT")
        p.handle_bar({
            "exchange": "OKX",
            "symbol": "BTC-USDT",
            "close": 67000.0,
            "ts": 1700000000,
        })
        assert p.get_features()["exchange_btc_price"] == 0.0

    def test_ignores_wrong_symbol(self) -> None:
        p = ExchangePriceProvider(primary_exchange="BINANCE", primary_symbol="BTC-USDT")
        p.handle_bar({
            "exchange": "BINANCE",
            "symbol": "ETH-USDT",
            "close": 3500.0,
            "ts": 1700000000,
        })
        assert p.get_features()["exchange_btc_price"] == 0.0

    def test_minute_aggregation_and_sigma(self) -> None:
        """Feed 120+ bars across multiple minutes → sigma should be computed."""
        p = ExchangePriceProvider(
            primary_exchange="BINANCE",
            primary_symbol="BTC-USDT",
            sigma_lookback_min=100,  # needs 100 closes → 99 returns, > 60 min_samples
        )

        base_ts = 1700000000
        # Feed 120 minutes of data (one bar per minute transition)
        for minute in range(120):
            ts = base_ts + minute * 60
            price = 67000.0 + minute * 10  # slowly rising
            p.handle_bar({
                "exchange": "BINANCE",
                "symbol": "BTC-USDT",
                "close": price,
                "ts": ts,
            })

        features = p.get_features()
        # 120 bars → 119 minute closes appended → sigma from last 100 → 99 log returns
        assert features["exchange_btc_sigma"] is not None
        assert features["exchange_btc_sigma"] > 0

    def test_ignores_zero_close(self) -> None:
        p = ExchangePriceProvider()
        p.handle_bar({
            "exchange": "BINANCE",
            "symbol": "BTC-USDT",
            "close": 0.0,
            "ts": 1700000000,
        })
        assert p.get_features()["exchange_btc_price"] == 0.0
        assert p.get_features()["exchange_btc_bar_count"] == 0
