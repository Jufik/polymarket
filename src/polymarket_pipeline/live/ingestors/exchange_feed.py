"""Multi-exchange crypto trade capture via cryptofeed → 1-second bars → Kafka.

Captures BTC (and optionally ETH, SOL) trades from Binance, OKX, Kraken, Bybit.
Aggregates into 1-second OHLCV bars and publishes to `exchange.bars` topic.

Usage:
  Enable via PM_EXCHANGE_FEED_ENABLED=true in environment.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.ingestors.base import BaseIngestor

log = structlog.get_logger()

# ── Symbol normalization (Kraken uses USD, others USDT) ──────────────────────

_KRAKEN_SYMBOL_MAP: dict[str, str] = {
    "BTC-USD": "BTC-USDT",
    "ETH-USD": "ETH-USDT",
    "SOL-USD": "SOL-USDT",
}

# Exchange name → (class import path, symbol → native mapping)
_EXCHANGE_CONFIGS: dict[str, dict[str, str]] = {
    "BINANCE": {"BTC-USDT": "BTC-USDT", "ETH-USDT": "ETH-USDT", "SOL-USDT": "SOL-USDT"},
    "OKX": {"BTC-USDT": "BTC-USDT", "ETH-USDT": "ETH-USDT", "SOL-USDT": "SOL-USDT"},
    "KRAKEN": {"BTC-USDT": "BTC-USD", "ETH-USDT": "ETH-USD", "SOL-USDT": "SOL-USD"},
    "BYBIT": {"BTC-USDT": "BTC-USDT", "ETH-USDT": "ETH-USDT", "SOL-USDT": "SOL-USDT"},
}


def _get_exchange_class(name: str) -> type | None:
    """Lazy-import cryptofeed exchange class by name."""
    try:
        if name == "BINANCE":
            from cryptofeed.exchanges import Binance
            return Binance
        elif name == "OKX":
            from cryptofeed.exchanges import OKX
            return OKX
        elif name == "KRAKEN":
            from cryptofeed.exchanges import Kraken
            return Kraken
        elif name == "BYBIT":
            from cryptofeed.exchanges import Bybit
            return Bybit
    except ImportError:
        log.warning("exchange_feed.import_failed", exchange=name)
    return None


# ── 1-Second Bar Aggregator ──────────────────────────────────────────────────


@dataclass
class Bar:
    """Accumulates trades into a single 1-second OHLCV bar."""

    open: float = 0.0
    high: float = float("-inf")
    low: float = float("inf")
    close: float = 0.0
    volume: float = 0.0
    buy_vol: float = 0.0
    trades: int = 0

    def update(self, price: float, amount: float, side: str) -> None:
        if self.trades == 0:
            self.open = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += amount
        if side == "buy":
            self.buy_vol += amount
        self.trades += 1

    def to_dict(self, exchange: str, symbol: str, ts: int) -> dict[str, Any]:
        return {
            "exchange": exchange,
            "symbol": symbol,
            "ts": ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": round(self.volume, 8),
            "buy_vol": round(self.buy_vol, 8),
            "trades": self.trades,
        }


# ── Ingestor ─────────────────────────────────────────────────────────────────


class ExchangeFeedIngestor(BaseIngestor):
    """Captures crypto trades via cryptofeed, publishes 1s bars to Kafka."""

    source_name = "exchange_feed"

    def __init__(
        self,
        broker: Any,
        topic: str = "exchange.bars",
        status_topic: str = "pipeline.status",
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        bar_interval_s: int = 1,
        flush_delay_s: int = 2,
    ) -> None:
        super().__init__(broker=broker, topic=topic, status_topic=status_topic)
        self._symbols = symbols or ["BTC-USDT"]
        self._exchanges = [e.upper() for e in (exchanges or ["BINANCE", "OKX", "KRAKEN", "BYBIT"])]
        self._bar_interval_s = bar_interval_s
        self._flush_delay_s = flush_delay_s

        # State
        self._bars: dict[tuple[str, str, int], Bar] = {}
        self._total_trades: int = 0
        self._bar_count: int = 0
        self._exchanges_connected: int = 0

    def _heartbeat_fields(self) -> dict[str, Any]:
        return {
            "bar_count": self._bar_count,
            "trade_count": self._total_trades,
            "exchanges_connected": self._exchanges_connected,
            "pending_bars": len(self._bars),
        }

    async def run(self) -> None:
        """Start cryptofeed and flush loop."""
        from cryptofeed import FeedHandler
        from cryptofeed.defines import TRADES

        fh = FeedHandler()

        for exchange_name in self._exchanges:
            exchange_cls = _get_exchange_class(exchange_name)
            if exchange_cls is None:
                continue

            symbol_map = _EXCHANGE_CONFIGS.get(exchange_name, {})
            native_symbols = []
            for sym in self._symbols:
                native = symbol_map.get(sym)
                if native:
                    native_symbols.append(native)

            if not native_symbols:
                continue

            try:
                fh.add_feed(
                    exchange_cls(
                        symbols=native_symbols,
                        channels=[TRADES],
                        callbacks={TRADES: self._make_trade_callback(exchange_name)},
                    )
                )
                self._exchanges_connected += 1
                self._log.info(
                    "exchange_feed.feed_added",
                    exchange=exchange_name,
                    symbols=native_symbols,
                )
            except Exception:
                self._log.exception("exchange_feed.feed_failed", exchange=exchange_name)

        if self._exchanges_connected == 0:
            self._log.error("exchange_feed.no_feeds")
            return

        # Start feeds on the current event loop (non-blocking)
        fh.run(start_loop=False)

        self._log.info(
            "exchange_feed.started",
            exchanges=self._exchanges_connected,
            symbols=self._symbols,
        )

        # Run flush + heartbeat concurrently
        await asyncio.gather(
            self._flush_loop(),
            self._heartbeat_loop(),
        )

    def _make_trade_callback(self, exchange_name: str) -> Any:
        """Create a trade callback bound to this ingestor instance.

        cryptofeed expects ``async def callback(trade, receipt_timestamp)``.
        We need access to ``self`` so we use a closure.
        """
        async def _on_trade(trade: Any, receipt_timestamp: float) -> None:
            # Normalize symbol (Kraken BTC-USD → BTC-USDT)
            symbol = trade.symbol
            normalized = _KRAKEN_SYMBOL_MAP.get(symbol, symbol)

            ts = int(trade.timestamp)
            key = (exchange_name, normalized, ts)

            if key not in self._bars:
                self._bars[key] = Bar()

            self._bars[key].update(
                float(trade.price),
                float(trade.amount),
                trade.side,
            )
            self._total_trades += 1

        return _on_trade

    async def _flush_loop(self) -> None:
        """Periodically flush completed bars to Kafka."""
        while True:
            await asyncio.sleep(self._bar_interval_s)

            cutoff_ts = int(time.time()) - self._flush_delay_s
            to_flush = {k: v for k, v in self._bars.items() if k[2] < cutoff_ts}

            if not to_flush:
                continue

            for (exchange, symbol, ts), bar in to_flush.items():
                if bar.trades == 0:
                    continue
                payload = json.dumps(bar.to_dict(exchange, symbol, ts))
                await safe_publish(
                    self._broker,
                    message=payload,
                    topic=self._topic,
                    key=f"{exchange}:{symbol}".encode(),
                    source=self.source_name,
                    circuit_breaker=self._circuit_breaker,
                )
                self._bar_count += 1

            for k in to_flush:
                del self._bars[k]
