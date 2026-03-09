"""Tests for ExchangeFeedIngestor bar aggregation."""

from __future__ import annotations

from polymarket_pipeline.live.ingestors.exchange_feed import _KRAKEN_SYMBOL_MAP, Bar


class TestBar:
    def test_empty_bar(self) -> None:
        bar = Bar()
        assert bar.trades == 0
        assert bar.volume == 0.0

    def test_single_trade(self) -> None:
        bar = Bar()
        bar.update(100.0, 0.5, "buy")
        assert bar.open == 100.0
        assert bar.high == 100.0
        assert bar.low == 100.0
        assert bar.close == 100.0
        assert bar.volume == 0.5
        assert bar.buy_vol == 0.5
        assert bar.trades == 1

    def test_multiple_trades(self) -> None:
        bar = Bar()
        bar.update(100.0, 0.5, "buy")
        bar.update(102.0, 0.3, "sell")
        bar.update(99.0, 0.2, "buy")
        bar.update(101.0, 0.1, "sell")

        assert bar.open == 100.0
        assert bar.high == 102.0
        assert bar.low == 99.0
        assert bar.close == 101.0
        assert abs(bar.volume - 1.1) < 1e-9
        assert abs(bar.buy_vol - 0.7) < 1e-9
        assert bar.trades == 4

    def test_to_dict(self) -> None:
        bar = Bar()
        bar.update(100.0, 1.0, "buy")
        d = bar.to_dict("BINANCE", "BTC-USDT", 1700000000)

        assert d["exchange"] == "BINANCE"
        assert d["symbol"] == "BTC-USDT"
        assert d["ts"] == 1700000000
        assert d["open"] == 100.0
        assert d["close"] == 100.0
        assert d["trades"] == 1


class TestKrakenSymbolMap:
    def test_btc_mapping(self) -> None:
        assert _KRAKEN_SYMBOL_MAP["BTC-USD"] == "BTC-USDT"

    def test_eth_mapping(self) -> None:
        assert _KRAKEN_SYMBOL_MAP["ETH-USD"] == "ETH-USDT"

    def test_sol_mapping(self) -> None:
        assert _KRAKEN_SYMBOL_MAP["SOL-USD"] == "SOL-USDT"

    def test_passthrough(self) -> None:
        """Non-Kraken symbols should not be in the map."""
        assert "BTC-USDT" not in _KRAKEN_SYMBOL_MAP
