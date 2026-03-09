"""Multi-exchange crypto trade capture → 1-second OHLCV bars.

Captures BTC, ETH, SOL trades from 5 exchanges via cryptofeed,
aggregates into 1-second bars, flushes to rotating Parquet files.

Exchanges (ranked by Chainlink BTC/USD relevance):
  1. Binance  (~40-50% global volume, daily checkpoint resolution source)
  2. Coinbase (~7%, US reference, Chainlink historically relies on)
  3. OKX      (top-3 globally)
  4. Kraken   (reliable, good data quality)
  5. Bybit    (second largest globally)

Output: research/hypotheses/crypto-scalp/data/exchange/bars_YYYYMMDD_HHMMSS.parquet

Schema (1-second bars):
  exchange  str     Exchange name
  symbol    str     Normalized pair (e.g. BTC-USDT)
  ts        i64     Unix timestamp (second)
  open      f64     First trade price in second
  high      f64     Highest price
  low       f64     Lowest price
  close     f64     Last trade price
  volume    f64     Total volume (base currency)
  buy_vol   f64     Taker buy volume (for CVD computation)
  trades    i32     Number of trades

Storage: ~130 MB/day (15 feeds × 86400 seconds × ~100 bytes/bar)

Usage:
  uv sync --extra cryptofeed
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/capture/exchange_feed.py
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/capture/exchange_feed.py --btc-only
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/capture/exchange_feed.py --flush-interval 300
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import structlog
from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES
from cryptofeed.exchanges import Binance, Bybit, Kraken, OKX

log = structlog.get_logger()

# ── Config ────────────────────────────────────────────────────────────────────

_output_dir = Path("research/hypotheses/crypto-scalp/data/exchange")
DEFAULT_FLUSH_S = 60


def _set_output_dir(path: Path) -> None:
    global _output_dir
    _output_dir = path

# Exchange → symbols mapping. Kraken uses USD, others USDT.
# Coinbase removed: requires API key auth for symbol listing.
FEED_CONFIG: dict[type, dict[str, list[str]]] = {
    Binance: {
        "btc": ["BTC-USDT"],
        "eth": ["ETH-USDT"],
        "sol": ["SOL-USDT"],
    },
    OKX: {
        "btc": ["BTC-USDT"],
        "eth": ["ETH-USDT"],
        "sol": ["SOL-USDT"],
    },
    Kraken: {
        "btc": ["BTC-USD"],
        "eth": ["ETH-USD"],
        "sol": ["SOL-USD"],
    },
    Bybit: {
        "btc": ["BTC-USDT"],
        "eth": ["ETH-USDT"],
        "sol": ["SOL-USDT"],
    },
}


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


# Key: (exchange, symbol, second_ts)
_bars: dict[tuple[str, str, int], Bar] = {}
_total_trades = 0


# ── Cryptofeed Callback ──────────────────────────────────────────────────────


async def _on_trade(trade, receipt_timestamp):  # noqa: ANN001
    """Aggregate each trade into the appropriate 1-second bar."""
    global _total_trades
    key = (trade.exchange, trade.symbol, int(trade.timestamp))
    if key not in _bars:
        _bars[key] = Bar()
    _bars[key].update(float(trade.price), float(trade.amount), trade.side)
    _total_trades += 1


# ── Flush Logic ───────────────────────────────────────────────────────────────


def _flush_bars(cutoff_ts: int | None = None) -> int:
    """Write completed bars to Parquet, return count flushed.

    Only flushes bars older than cutoff_ts (default: 2 seconds ago)
    to avoid flushing a partially-filled current-second bar.
    """
    if not _bars:
        return 0

    if cutoff_ts is None:
        cutoff_ts = int(time.time()) - 2

    to_flush = {k: v for k, v in _bars.items() if k[2] < cutoff_ts}
    if not to_flush:
        return 0

    rows = []
    for (exchange, symbol, ts), bar in to_flush.items():
        if bar.trades > 0:
            rows.append(
                {
                    "exchange": exchange,
                    "symbol": symbol,
                    "ts": ts,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "buy_vol": bar.buy_vol,
                    "trades": bar.trades,
                }
            )

    for k in to_flush:
        del _bars[k]

    if not rows:
        return 0

    df = pl.DataFrame(rows).sort("ts", "exchange", "symbol")
    _output_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _output_dir / f"bars_{ts_str}.parquet"
    df.write_parquet(path, compression="zstd")
    return len(rows)


async def _flush_loop(interval_s: int) -> None:
    """Periodic flush of aggregated bars to Parquet."""
    global _total_trades
    while True:
        await asyncio.sleep(interval_s)
        n = _flush_bars()
        log.info(
            "flush",
            bars=n,
            total_trades=_total_trades,
            pending_bars=len(_bars),
        )


# ── Main ──────────────────────────────────────────────────────────────────────


def _build_symbols(assets: list[str]) -> dict[type, list[str]]:
    """Build exchange → symbol list from asset selection."""
    result: dict[type, list[str]] = {}
    for exchange_cls, asset_map in FEED_CONFIG.items():
        symbols = []
        for asset in assets:
            symbols.extend(asset_map.get(asset, []))
        if symbols:
            result[exchange_cls] = symbols
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-exchange crypto trade capture")
    parser.add_argument(
        "--btc-only",
        action="store_true",
        help="Capture BTC only (5 exchanges × 1 pair)",
    )
    parser.add_argument(
        "--assets",
        nargs="+",
        default=["btc", "eth", "sol"],
        choices=["btc", "eth", "sol"],
        help="Assets to capture (default: btc eth sol)",
    )
    parser.add_argument(
        "--flush-interval",
        type=int,
        default=DEFAULT_FLUSH_S,
        help=f"Flush interval in seconds (default: {DEFAULT_FLUSH_S})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_output_dir,
        help=f"Output directory (default: {_output_dir})",
    )
    args = parser.parse_args()

    _set_output_dir(args.output_dir)

    assets = ["btc"] if args.btc_only else args.assets
    exchange_symbols = _build_symbols(assets)

    fh = FeedHandler()
    connected = 0
    for exchange_cls, symbols in exchange_symbols.items():
        try:
            fh.add_feed(
                exchange_cls(
                    symbols=symbols,
                    channels=[TRADES],
                    callbacks={TRADES: _on_trade},
                )
            )
            log.info("feed_added", exchange=exchange_cls.__name__, symbols=symbols)
            connected += 1
        except Exception:
            log.exception("feed_failed", exchange=exchange_cls.__name__, symbols=symbols)

    if connected == 0:
        log.error("no_feeds_connected")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(_flush_loop(args.flush_interval))

    # Graceful shutdown: flush remaining bars
    def _shutdown(sig: int, _frame: object) -> None:
        log.info("shutdown", signal=sig)
        n = _flush_bars(cutoff_ts=int(time.time()) + 10)  # flush everything
        log.info("final_flush", bars=n)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    total_feeds = sum(len(s) for s in exchange_symbols.values())
    log.info(
        "starting",
        exchanges=len(exchange_symbols),
        feeds=total_feeds,
        assets=assets,
        flush_interval=args.flush_interval,
        output=str(_output_dir),
    )
    fh.run()


if __name__ == "__main__":
    main()
