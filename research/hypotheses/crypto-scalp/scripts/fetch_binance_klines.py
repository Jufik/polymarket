"""Fetch historical 1-minute candles from Binance REST API.

No API key required. Paginates automatically (1000 candles per request).

Output schema:
  open_time         i64     Unix ms
  open              f64     Open price
  high              f64     High price (THIS resolves Polymarket "above" markets)
  low               f64     Low price
  close             f64     Close price (THIS resolves Polymarket "up/down" markets)
  volume            f64     Base asset volume
  close_time        i64     Unix ms
  quote_volume      f64     Quote asset volume (USDT)
  num_trades        i64     Number of trades in candle
  taker_buy_vol     f64     Taker buy base volume
  taker_buy_qvol    f64     Taker buy quote volume

Usage:
  # Fetch 30 days of BTC 1m candles
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/fetch_binance_klines.py

  # Fetch specific symbol and period
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/fetch_binance_klines.py \
      --symbol ETHUSDT --days 60

  # Fetch all three assets
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/fetch_binance_klines.py \
      --symbol BTCUSDT ETHUSDT SOLUSDT --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import polars as pl
import structlog

log = structlog.get_logger()

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
OUTPUT_DIR = Path("research/hypotheses/crypto-scalp/data/klines")
BATCH_SIZE = 1000  # Binance max per request
RATE_LIMIT_PAUSE = 0.15  # seconds between requests (stay well under 1200/min)


async def _fetch_batch(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch a single batch of candles from Binance."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": BATCH_SIZE,
    }
    async with session.get(BINANCE_KLINES_URL, params=params) as resp:
        if resp.status == 429:
            log.warning("rate_limited", symbol=symbol)
            await asyncio.sleep(10)
            return await _fetch_batch(session, symbol, interval, start_ms, end_ms)
        resp.raise_for_status()
        return await resp.json()


async def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch all candles for a symbol, paginating as needed."""
    all_candles: list[list] = []
    current_start = start_ms

    async with aiohttp.ClientSession() as session:
        while current_start < end_ms:
            batch = await _fetch_batch(session, symbol, interval, current_start, end_ms)
            if not batch:
                break

            all_candles.extend(batch)
            # Next page starts after last candle's close time
            current_start = batch[-1][6] + 1
            await asyncio.sleep(RATE_LIMIT_PAUSE)

            if len(all_candles) % 10000 == 0:
                log.info("progress", symbol=symbol, candles=len(all_candles))

    return all_candles


def _candles_to_df(candles: list[list]) -> pl.DataFrame:
    """Convert raw Binance kline response to typed DataFrame."""
    return pl.DataFrame(
        {
            "open_time": [c[0] for c in candles],
            "open": [float(c[1]) for c in candles],
            "high": [float(c[2]) for c in candles],
            "low": [float(c[3]) for c in candles],
            "close": [float(c[4]) for c in candles],
            "volume": [float(c[5]) for c in candles],
            "close_time": [c[6] for c in candles],
            "quote_volume": [float(c[7]) for c in candles],
            "num_trades": [int(c[8]) for c in candles],
            "taker_buy_vol": [float(c[9]) for c in candles],
            "taker_buy_qvol": [float(c[10]) for c in candles],
        },
        schema={
            "open_time": pl.Int64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "close_time": pl.Int64,
            "quote_volume": pl.Float64,
            "num_trades": pl.Int64,
            "taker_buy_vol": pl.Float64,
            "taker_buy_qvol": pl.Float64,
        },
    )


async def fetch_and_save(symbol: str, days: int, output_dir: Path) -> Path:
    """Fetch candles and save to Parquet. Returns output path."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    expected_candles = days * 24 * 60  # 1 per minute
    log.info(
        "fetching",
        symbol=symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        expected=f"~{expected_candles:,}",
    )

    candles = await fetch_klines(symbol, "1m", start_ms, end_ms)
    log.info("fetched", symbol=symbol, candles=len(candles))

    if not candles:
        log.error("no_data", symbol=symbol)
        return output_dir / f"{symbol}_1m_empty.parquet"

    df = _candles_to_df(candles)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{symbol}_1m_{days}d.parquet"
    df.write_parquet(path, compression="zstd")
    log.info("saved", path=str(path), rows=len(df))
    return path


async def main(symbols: list[str], days: int) -> None:
    """Fetch candles for all symbols."""
    for symbol in symbols:
        path = await fetch_and_save(symbol, days, OUTPUT_DIR)
        # Quick summary
        df = pl.read_parquet(path)
        log.info(
            "summary",
            symbol=symbol,
            rows=len(df),
            date_range=f"{datetime.fromtimestamp(df['open_time'][0] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} → {datetime.fromtimestamp(df['open_time'][-1] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            avg_trades_per_min=f"{df['num_trades'].mean():.0f}",
            avg_volume_per_min=f"{df['volume'].mean():.2f}",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Binance historical 1m candles")
    parser.add_argument(
        "--symbol",
        nargs="+",
        default=["BTCUSDT"],
        help="Symbol(s) to fetch (default: BTCUSDT)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to fetch (default: 30)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.symbol, args.days))
