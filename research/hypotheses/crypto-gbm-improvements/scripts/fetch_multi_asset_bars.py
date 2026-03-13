"""Fetch 1-second Binance klines for ETH-USDT, SOL-USDT, XRP-USDT into ClickHouse.

Resume-safe: checks max(ts) per symbol before starting.
Fetches forward from earliest available or from existing max(ts).

Usage:
  PYTHONPATH=. uv run python research/hypotheses/crypto-gbm-improvements/scripts/fetch_multi_asset_bars.py
  PYTHONPATH=. uv run python research/hypotheses/crypto-gbm-improvements/scripts/fetch_multi_asset_bars.py --symbols ETH-USDT
  PYTHONPATH=. uv run python research/hypotheses/crypto-gbm-improvements/scripts/fetch_multi_asset_bars.py --days 30
  PYTHONPATH=. uv run python research/hypotheses/crypto-gbm-improvements/scripts/fetch_multi_asset_bars.py --dry-run
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

COLUMNS = [
    "exchange", "symbol", "ts", "open", "high", "low", "close",
    "volume", "buy_vol", "trades",
]

BINANCE_URL = "https://api.binance.com/api/v3/klines"

# Normalized symbol -> Binance native symbol
SYMBOL_MAP: dict[str, str] = {
    "ETH-USDT": "ETHUSDT",
    "SOL-USDT": "SOLUSDT",
    "XRP-USDT": "XRPUSDT",
}

EXCHANGE = "BINANCE"
RATE_PER_SECOND = 8.0  # conservative (Binance allows ~20/s for klines)
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


# ── HTTP helper ──────────────────────────────────────────────────────────────


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    limiter: RateLimiter,
    max_retries: int = 5,
) -> Any:
    for attempt in range(max_retries):
        await limiter.acquire()
        try:
            resp = await client.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(5 * (2**attempt), 60)
                log.warning("rate_limited", wait=wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 * (attempt + 1)
                log.warning("server_error", status=resp.status_code, wait=wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            wait = 3 * (attempt + 1)
            log.warning("connection_error", err=str(e), wait=wait)
            await asyncio.sleep(wait)
    raise RuntimeError("exhausted retries")


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


def get_time_range(ch: Any, symbol: str) -> tuple[datetime | None, datetime | None]:
    result = ch.query(
        "SELECT min(ts), max(ts) FROM exchange_bars "
        "WHERE exchange = {ex:String} AND symbol = {sym:String}",
        parameters={"ex": EXCHANGE, "sym": symbol},
    )
    row = result.first_row
    if row:
        mn, mx = _parse_ch_ts(row[0]), _parse_ch_ts(row[1])
        # ClickHouse returns epoch 0 for empty aggregation
        if mn and mn.year < 2020:
            return None, None
        return mn, mx
    return None, None


def insert_rows(ch: Any, rows: list[tuple]) -> None:
    if not rows:
        return
    for i in range(0, len(rows), BATCH_INSERT_SIZE):
        batch = rows[i : i + BATCH_INSERT_SIZE]
        ch.insert("exchange_bars", batch, column_names=COLUMNS)


# ── Binance 1s kline fetcher ────────────────────────────────────────────────


async def fetch_binance_1s(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    normalized_symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple]:
    native_symbol = SYMBOL_MAP[normalized_symbol]
    rows: list[tuple] = []
    current_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    page = 0

    while current_ms < end_ms:
        data = await _get_json(
            client,
            BINANCE_URL,
            {
                "symbol": native_symbol,
                "interval": "1s",
                "startTime": current_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            limiter,
        )
        if not data:
            break

        for c in data:
            ts_sec = c[0] // 1000
            rows.append((
                EXCHANGE,
                normalized_symbol,
                datetime.fromtimestamp(ts_sec, tz=timezone.utc),
                float(c[1]),   # open
                float(c[2]),   # high
                float(c[3]),   # low
                float(c[4]),   # close
                float(c[5]),   # volume
                float(c[9]),   # taker_buy_base_vol
                int(c[8]),     # num_trades
            ))

        current_ms = data[-1][6] + 1  # after last candle close time
        page += 1

        if page % 100 == 0:
            latest = datetime.fromtimestamp(data[-1][0] // 1000, tz=timezone.utc)
            log.info(
                "progress",
                symbol=normalized_symbol,
                rows=len(rows),
                latest=latest.strftime("%Y-%m-%d %H:%M"),
            )

    return rows


# ── Orchestration ────────────────────────────────────────────────────────────


async def fetch_symbol(
    symbol: str,
    days: int,
    dry_run: bool = False,
) -> int:
    ch = _get_ch_client()
    limiter = RateLimiter(RATE_PER_SECOND)
    now = datetime.now(timezone.utc)
    desired_start = now - timedelta(days=days)

    min_ts, max_ts = get_time_range(ch, symbol)
    total = 0

    async with httpx.AsyncClient() as client:
        if min_ts is None:
            log.info("starting_fresh", symbol=symbol, from_ts=desired_start.isoformat())
            rows = await fetch_binance_1s(client, limiter, symbol, desired_start, now)
        else:
            rows = []
            # Backfill gap: desired_start -> min_ts
            if desired_start < min_ts - timedelta(seconds=60):
                log.info(
                    "backfill_gap", symbol=symbol,
                    from_ts=desired_start.isoformat(), to_ts=min_ts.isoformat(),
                )
                rows += await fetch_binance_1s(client, limiter, symbol, desired_start, min_ts)

            # Forward fill: max_ts -> now
            forward_start = max_ts + timedelta(seconds=1)
            if forward_start < now - timedelta(minutes=5):
                log.info("fill_forward", symbol=symbol, from_ts=forward_start.isoformat())
                rows += await fetch_binance_1s(client, limiter, symbol, forward_start, now)
            else:
                log.info("up_to_date", symbol=symbol, max_ts=max_ts.isoformat())

        log.info("fetched", symbol=symbol, rows=len(rows))

        if dry_run:
            log.info("dry_run", symbol=symbol, would_insert=len(rows))
            return len(rows)

        if rows:
            t0 = time.monotonic()
            insert_rows(ch, rows)
            log.info(
                "inserted", symbol=symbol, rows=len(rows),
                elapsed=f"{time.monotonic() - t0:.1f}s",
            )
            total = len(rows)

    return total


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Binance 1s klines for ETH/SOL/XRP into exchange_bars",
    )
    parser.add_argument("--days", type=int, default=180, help="Days of history (default: 180)")
    parser.add_argument(
        "--symbols", nargs="+",
        default=list(SYMBOL_MAP.keys()),
        help=f"Symbols to fetch (default: {list(SYMBOL_MAP.keys())})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't insert")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    invalid = [s for s in symbols if s not in SYMBOL_MAP]
    if invalid:
        log.error("unknown_symbols", invalid=invalid, valid=list(SYMBOL_MAP.keys()))
        sys.exit(1)

    log.info("start", symbols=symbols, days=args.days, dry_run=args.dry_run)

    grand_total = 0
    for symbol in symbols:
        try:
            n = await fetch_symbol(symbol, args.days, dry_run=args.dry_run)
            grand_total += n
        except Exception:
            log.exception("symbol_failed", symbol=symbol)

    log.info("done", total_rows=grand_total)


if __name__ == "__main__":
    asyncio.run(main())
