"""Backfill historical crypto OHLCV bars into ClickHouse exchange_bars table.

Fetches from Binance (1s), OKX (1s), Bybit (1m), Kraken (1m).
Resume-safe: checks max(ts) per exchange before starting.

Usage:
  PYTHONPATH=. uv run python scripts/backfill_exchange_bars.py
  PYTHONPATH=. uv run python scripts/backfill_exchange_bars.py --days 90
  PYTHONPATH=. uv run python scripts/backfill_exchange_bars.py --exchanges BINANCE OKX
  PYTHONPATH=. uv run python scripts/backfill_exchange_bars.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────────────────

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DATABASE = "polymarket"

COLUMNS = ["exchange", "symbol", "ts", "open", "high", "low", "close", "volume", "buy_vol", "trades"]

# Exchange API endpoints
BINANCE_URL = "https://api.binance.com/api/v3/klines"
OKX_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
OKX_RECENT_URL = "https://www.okx.com/api/v5/market/candles"
BYBIT_URL = "https://api.bybit.com/v5/market/kline"
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"

# Native symbol names per exchange
NATIVE_SYMBOL = {
    "BINANCE": "BTCUSDT",
    "OKX": "BTC-USDT",
    "BYBIT": "BTCUSDT",
    "KRAKEN": "XBTUSDT",
}

NORMALIZED_SYMBOL = "BTC-USDT"

# Conservative rate limits (requests per second)
RATE_LIMITS: dict[str, float] = {
    "BINANCE": 10.0,
    "OKX": 8.0,
    "BYBIT": 1.5,
    "KRAKEN": 0.33,
}

BATCH_INSERT_SIZE = 10_000


# ── Rate Limiter ─────────────────────────────────────────────────────────────


class RateLimiter:
    def __init__(self, rate_per_second: float) -> None:
        self._min_interval = 1.0 / rate_per_second
        self._last = 0.0

    async def acquire(self) -> None:
        now = time.monotonic()
        wait = self._min_interval - (now - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()


# ── HTTP helpers ─────────────────────────────────────────────────────────────


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    limiter: RateLimiter,
    exchange: str,
    max_retries: int = 5,
) -> Any:
    """GET with rate limiting and retry."""
    for attempt in range(max_retries):
        await limiter.acquire()
        try:
            resp = await client.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(5 * (2**attempt), 60)
                log.warning("rate_limited", exchange=exchange, wait=wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 * (attempt + 1)
                log.warning("server_error", exchange=exchange, status=resp.status_code, wait=wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            wait = 3 * (attempt + 1)
            log.warning("connection_error", exchange=exchange, err=str(e), wait=wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{exchange}: exhausted retries")


# ── ClickHouse helpers ───────────────────────────────────────────────────────


def _get_ch_client() -> Any:
    import clickhouse_connect

    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DATABASE)


def _parse_ch_ts(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def get_time_range(ch: Any, exchange: str) -> tuple[datetime | None, datetime | None]:
    """Get (min_ts, max_ts) already in CH for this exchange."""
    result = ch.query(
        "SELECT min(ts), max(ts) FROM exchange_bars "
        "WHERE exchange = {ex:String} AND symbol = {sym:String}",
        parameters={"ex": exchange, "sym": NORMALIZED_SYMBOL},
    )
    row = result.first_row
    if row:
        return _parse_ch_ts(row[0]), _parse_ch_ts(row[1])
    return None, None


def insert_rows(ch: Any, rows: list[tuple], exchange: str) -> None:
    """Batch insert rows into exchange_bars."""
    if not rows:
        return
    for i in range(0, len(rows), BATCH_INSERT_SIZE):
        batch = rows[i : i + BATCH_INSERT_SIZE]
        ch.insert("exchange_bars", batch, column_names=COLUMNS)


# ── Binance (1s klines) ─────────────────────────────────────────────────────


async def fetch_binance(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
    limiter: RateLimiter,
) -> list[tuple]:
    """Fetch Binance 1-second klines. Forward pagination via startTime."""
    rows: list[tuple] = []
    current_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    symbol = NATIVE_SYMBOL["BINANCE"]
    page = 0

    while current_ms < end_ms:
        data = await _get_json(
            client,
            BINANCE_URL,
            {"symbol": symbol, "interval": "1s", "startTime": current_ms, "endTime": end_ms, "limit": 1000},
            limiter,
            "BINANCE",
        )
        if not data:
            break

        for c in data:
            ts_sec = c[0] // 1000
            rows.append((
                "BINANCE",
                NORMALIZED_SYMBOL,
                datetime.fromtimestamp(ts_sec, tz=timezone.utc),
                float(c[1]),  # open
                float(c[2]),  # high
                float(c[3]),  # low
                float(c[4]),  # close
                float(c[5]),  # volume
                float(c[9]),  # taker_buy_base_vol
                int(c[8]),    # num_trades
            ))

        # Next page: after last candle's close time
        current_ms = data[-1][6] + 1
        page += 1

        if page % 50 == 0:
            latest = datetime.fromtimestamp(data[-1][0] // 1000, tz=timezone.utc)
            log.info("binance.progress", rows=len(rows), latest=latest.strftime("%Y-%m-%d %H:%M"))

    return rows


# ── OKX (1s candles) ─────────────────────────────────────────────────────────


async def fetch_okx(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
    limiter: RateLimiter,
) -> list[tuple]:
    """Fetch OKX 1-second candles. Backward pagination via 'after' param."""
    rows: list[tuple] = []
    start_ms = int(start.timestamp() * 1000)
    # OKX paginates backward: start from 'end', walk toward 'start'
    after_ms: int | None = None  # oldest ts in current page; None = start from now
    symbol = NATIVE_SYMBOL["OKX"]
    page = 0
    # OKX history-candles goes back ~3 months; recent candles for last few days
    # We use history-candles with 'before' (upper bound) to paginate backward

    # OKX returns newest-first. 'after' = return data before this ts.
    cursor_ms = int(end.timestamp() * 1000)

    while cursor_ms > start_ms:
        params: dict[str, Any] = {
            "instId": symbol,
            "bar": "1s",
            "limit": "100",
            "after": str(cursor_ms),
        }
        data = await _get_json(client, OKX_HISTORY_URL, params, limiter, "OKX")

        candles = data.get("data", []) if isinstance(data, dict) else []
        if not candles:
            # Try recent endpoint for last few days
            if cursor_ms > int((end - timedelta(days=3)).timestamp() * 1000):
                data = await _get_json(client, OKX_RECENT_URL, params, limiter, "OKX")
                candles = data.get("data", []) if isinstance(data, dict) else []
            if not candles:
                break

        for c in candles:
            ts_ms = int(c[0])
            if ts_ms < start_ms:
                continue
            ts_sec = ts_ms // 1000
            rows.append((
                "OKX",
                NORMALIZED_SYMBOL,
                datetime.fromtimestamp(ts_sec, tz=timezone.utc),
                float(c[1]),  # open
                float(c[2]),  # high
                float(c[3]),  # low
                float(c[4]),  # close
                float(c[5]),  # volume
                0.0,          # buy_vol (not available)
                0,            # trades (not available)
            ))

        # Oldest candle in this page → next cursor
        oldest_ts = min(int(c[0]) for c in candles)
        if oldest_ts >= cursor_ms:
            break  # no progress
        cursor_ms = oldest_ts
        page += 1

        if page % 200 == 0:
            oldest_dt = datetime.fromtimestamp(oldest_ts // 1000, tz=timezone.utc)
            log.info("okx.progress", rows=len(rows), oldest=oldest_dt.strftime("%Y-%m-%d %H:%M"))

    # OKX returns newest-first; sort for orderly insertion
    rows.sort(key=lambda r: r[2])
    return rows


# ── Bybit (1m klines) ───────────────────────────────────────────────────────


async def fetch_bybit(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
    limiter: RateLimiter,
) -> list[tuple]:
    """Fetch Bybit 1-minute klines. Backward pagination."""
    rows: list[tuple] = []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    symbol = NATIVE_SYMBOL["BYBIT"]
    page = 0

    while end_ms > start_ms:
        data = await _get_json(
            client,
            BYBIT_URL,
            {"category": "spot", "symbol": symbol, "interval": "1", "start": start_ms, "end": end_ms, "limit": 200},
            limiter,
            "BYBIT",
        )

        candles = data.get("result", {}).get("list", []) if isinstance(data, dict) else []
        if not candles:
            break

        for c in candles:
            ts_ms = int(c[0])
            ts_sec = ts_ms // 1000
            rows.append((
                "BYBIT",
                NORMALIZED_SYMBOL,
                datetime.fromtimestamp(ts_sec, tz=timezone.utc),
                float(c[1]),  # open
                float(c[2]),  # high
                float(c[3]),  # low
                float(c[4]),  # close
                float(c[5]),  # volume
                0.0,          # buy_vol (not available)
                0,            # trades (not available)
            ))

        # Bybit returns newest first; oldest is last element
        oldest_ts = min(int(c[0]) for c in candles)
        if oldest_ts >= end_ms:
            break  # no progress
        end_ms = oldest_ts - 1
        page += 1

        if page % 50 == 0:
            oldest_dt = datetime.fromtimestamp(oldest_ts // 1000, tz=timezone.utc)
            log.info("bybit.progress", rows=len(rows), oldest=oldest_dt.strftime("%Y-%m-%d %H:%M"))

    rows.sort(key=lambda r: r[2])
    return rows


# ── Kraken (1m OHLC) ────────────────────────────────────────────────────────


async def fetch_kraken(
    client: httpx.AsyncClient,
    start: datetime,
    end: datetime,
    limiter: RateLimiter,
) -> list[tuple]:
    """Fetch Kraken 1-minute OHLC. Forward pagination via 'since'."""
    rows: list[tuple] = []
    since = int(start.timestamp())
    end_ts = int(end.timestamp())
    symbol = NATIVE_SYMBOL["KRAKEN"]
    page = 0

    while since < end_ts:
        data = await _get_json(
            client,
            KRAKEN_URL,
            {"pair": symbol, "interval": 1, "since": since},
            limiter,
            "KRAKEN",
        )

        if isinstance(data, dict) and data.get("error"):
            errors = data["error"]
            if errors:
                log.error("kraken.api_error", errors=errors)
                break

        result = data.get("result", {}) if isinstance(data, dict) else {}
        # Kraken response key can vary
        candles = None
        for key in [symbol, "XBTUSDT", "XXBTZUSD", "XXBTZUSDT"]:
            if key in result:
                candles = result[key]
                break
        if candles is None:
            # Try first non-"last" key
            for key, val in result.items():
                if key != "last" and isinstance(val, list):
                    candles = val
                    break
        if not candles:
            break

        for c in candles:
            ts_sec = int(c[0])
            if ts_sec >= end_ts:
                continue
            rows.append((
                "KRAKEN",
                NORMALIZED_SYMBOL,
                datetime.fromtimestamp(ts_sec, tz=timezone.utc),
                float(c[1]),  # open
                float(c[2]),  # high
                float(c[3]),  # low
                float(c[4]),  # close
                float(c[6]),  # volume (index 6, 5 is vwap)
                0.0,          # buy_vol (not available)
                int(c[7]),    # trades
            ))

        new_since = result.get("last", 0)
        if not new_since or int(new_since) <= since:
            break
        since = int(new_since)
        page += 1

        if page % 20 == 0:
            latest = datetime.fromtimestamp(since, tz=timezone.utc)
            log.info("kraken.progress", rows=len(rows), latest=latest.strftime("%Y-%m-%d %H:%M"))

    return rows


# ── Orchestration ────────────────────────────────────────────────────────────

FETCHERS = {
    "BINANCE": fetch_binance,
    "OKX": fetch_okx,
    "BYBIT": fetch_bybit,
    "KRAKEN": fetch_kraken,
}


async def _fetch_and_insert(
    ch: Any,
    fetcher: Any,
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    exchange: str,
    start: datetime,
    end: datetime,
    dry_run: bool,
) -> int:
    """Fetch one time range and insert. Returns row count."""
    t0 = time.monotonic()
    rows = await fetcher(client, start, end, limiter)
    elapsed = time.monotonic() - t0
    log.info("exchange.fetched", exchange=exchange, rows=len(rows), elapsed=f"{elapsed:.1f}s")

    if dry_run:
        log.info("exchange.dry_run", exchange=exchange, would_insert=len(rows))
        return len(rows)

    if rows:
        t1 = time.monotonic()
        insert_rows(ch, rows, exchange)
        log.info("exchange.inserted", exchange=exchange, rows=len(rows), elapsed=f"{time.monotonic() - t1:.1f}s")

    return len(rows)


async def backfill_exchange(
    exchange: str,
    days: int,
    dry_run: bool = False,
) -> int:
    """Backfill a single exchange. Returns rows inserted."""
    ch = _get_ch_client()
    fetcher = FETCHERS[exchange]
    limiter = RateLimiter(RATE_LIMITS[exchange])

    now = datetime.now(timezone.utc)
    desired_start = now - timedelta(days=days)

    min_ts, max_ts = get_time_range(ch, exchange)
    total = 0

    async with httpx.AsyncClient() as client:
        if min_ts is None:
            # No data at all — fetch full range
            log.info("exchange.starting_fresh", exchange=exchange, from_ts=desired_start.isoformat())
            total += await _fetch_and_insert(ch, fetcher, client, limiter, exchange, desired_start, now, dry_run)
        else:
            # Backfill historical gap: desired_start → min_ts
            if desired_start < min_ts - timedelta(seconds=60):
                log.info(
                    "exchange.backfill_gap",
                    exchange=exchange,
                    from_ts=desired_start.isoformat(),
                    to_ts=min_ts.isoformat(),
                )
                total += await _fetch_and_insert(ch, fetcher, client, limiter, exchange, desired_start, min_ts, dry_run)

            # Fill forward gap: max_ts → now
            forward_start = max_ts + timedelta(seconds=1)
            if forward_start < now - timedelta(minutes=5):
                log.info("exchange.fill_forward", exchange=exchange, from_ts=forward_start.isoformat())
                total += await _fetch_and_insert(ch, fetcher, client, limiter, exchange, forward_start, now, dry_run)
            else:
                log.info("exchange.up_to_date", exchange=exchange, max_ts=max_ts.isoformat())

    return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill exchange_bars from crypto exchanges")
    parser.add_argument("--days", type=int, default=180, help="Days of history (default: 180)")
    parser.add_argument(
        "--exchanges",
        nargs="+",
        default=["BINANCE", "OKX", "BYBIT", "KRAKEN"],
        help="Exchanges to backfill (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't insert")
    args = parser.parse_args()

    exchanges = [e.upper() for e in args.exchanges]
    invalid = [e for e in exchanges if e not in FETCHERS]
    if invalid:
        log.error("unknown_exchanges", invalid=invalid, valid=list(FETCHERS.keys()))
        sys.exit(1)

    log.info("backfill.start", exchanges=exchanges, days=args.days, dry_run=args.dry_run)

    total = 0
    for exchange in exchanges:
        try:
            n = await backfill_exchange(exchange, args.days, dry_run=args.dry_run)
            total += n
        except Exception:
            log.exception("exchange.failed", exchange=exchange)

    log.info("backfill.done", total_rows=total)


if __name__ == "__main__":
    asyncio.run(main())
