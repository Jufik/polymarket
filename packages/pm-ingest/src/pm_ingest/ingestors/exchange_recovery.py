"""Recover missing Binance 1-second bars into ClickHouse at startup.

Called by the strategy CLI before ``runner.initialize()`` so that
``ExchangePriceProvider.compute()`` bootstraps from a gap-free time series.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Normalized symbol -> Binance native symbol
BINANCE_NATIVE: dict[str, str] = {
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
    "SOL-USDT": "SOLUSDT",
    "XRP-USDT": "XRPUSDT",
}

_COLUMNS = [
    "exchange",
    "symbol",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "buy_vol",
    "trades",
]

_MIN_REQUEST_INTERVAL = 0.1


# -- Binance fetch -----------------------------------------------------------


async def _fetch_binance_klines(
    client: httpx.AsyncClient,
    native_symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[list[Any]]:
    """Fetch up to 1000 1s klines from Binance."""
    for attempt in range(5):
        try:
            resp = await client.get(
                BINANCE_KLINES_URL,
                params={
                    "symbol": native_symbol,
                    "interval": "1s",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = min(5 * (2**attempt), 60)
                log.warning("recovery.rate_limited", wait=wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 * (attempt + 1)
                log.warning("recovery.server_error", status=resp.status_code, wait=wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            wait = 3 * (attempt + 1)
            log.warning("recovery.conn_error", err=str(e)[:80], wait=wait)
            await asyncio.sleep(wait)
    return []


async def _fetch_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[Any, ...]]:
    """Fetch all 1s bars for a symbol in [start, end)."""
    native = BINANCE_NATIVE.get(symbol)
    if native is None:
        log.warning("recovery.unknown_symbol", symbol=symbol)
        return []

    rows: list[tuple[Any, ...]] = []
    current_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    page = 0

    while current_ms < end_ms:
        t0 = time.monotonic()

        data = await _fetch_binance_klines(client, native, current_ms, end_ms)
        if not data:
            break

        for c in data:
            ts_sec = c[0] // 1000
            rows.append(
                (
                    "BINANCE",
                    symbol,
                    datetime.fromtimestamp(ts_sec, tz=UTC),
                    float(c[1]),  # open
                    float(c[2]),  # high
                    float(c[3]),  # low
                    float(c[4]),  # close
                    float(c[5]),  # volume
                    float(c[9]),  # taker_buy_base_vol
                    int(c[8]),  # num_trades
                )
            )

        current_ms = data[-1][6] + 1
        page += 1

        elapsed = time.monotonic() - t0
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        if page % 50 == 0:
            latest = datetime.fromtimestamp(data[-1][0] // 1000, tz=UTC)
            log.info(
                "recovery.progress",
                symbol=symbol,
                rows=len(rows),
                latest=latest.strftime("%H:%M:%S"),
            )

    return rows


# -- ClickHouse helpers ------------------------------------------------------


def _get_ch_client(host: str, port: int, database: str) -> Any:
    import clickhouse_connect

    return clickhouse_connect.get_client(host=host, port=port, database=database)


def _get_max_ts(ch: Any, symbol: str) -> datetime | None:
    """Get the latest bar timestamp in CH for a Binance symbol."""
    result = ch.query(
        "SELECT max(ts) FROM exchange_bars WHERE exchange = 'BINANCE' AND symbol = {sym:String}",
        parameters={"sym": symbol},
    )
    row = result.first_row
    if row and row[0]:
        ts = row[0]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=UTC)
            return ts
        return datetime.fromtimestamp(int(ts), tz=UTC)
    return None


def _insert_rows(ch: Any, rows: list[tuple[Any, ...]]) -> None:
    """Batch insert rows into exchange_bars."""
    batch_size = 10_000
    for i in range(0, len(rows), batch_size):
        ch.insert("exchange_bars", rows[i : i + batch_size], column_names=_COLUMNS)


# -- Public API --------------------------------------------------------------


async def recover_exchange_bars(
    ch_host: str,
    ch_port: int,
    ch_database: str,
    symbols: list[str] | None = None,
    max_gap_hours: float = 25.0,
    min_gap_seconds: float = 60.0,
) -> dict[str, int]:
    """Recover missing Binance 1s bars into ClickHouse.

    Returns dict of ``{symbol: rows_inserted}``.
    """
    if symbols is None:
        symbols = list(BINANCE_NATIVE.keys())

    symbols = [s for s in symbols if s in BINANCE_NATIVE]
    if not symbols:
        return {}

    ch = _get_ch_client(ch_host, ch_port, ch_database)
    now = datetime.now(UTC)
    earliest = now - timedelta(hours=max_gap_hours)
    result: dict[str, int] = {}

    async with httpx.AsyncClient() as client:
        for symbol in symbols:
            max_ts = _get_max_ts(ch, symbol)

            if max_ts is None:
                start = earliest
                gap_s = max_gap_hours * 3600
            else:
                gap_s = (now - max_ts).total_seconds()
                if gap_s < min_gap_seconds:
                    log.info(
                        "recovery.fresh",
                        symbol=symbol,
                        gap_s=round(gap_s, 1),
                    )
                    result[symbol] = 0
                    continue
                start = max_ts + timedelta(seconds=1)
                if start < earliest:
                    start = earliest

            log.info(
                "recovery.fetching",
                symbol=symbol,
                gap_s=round(gap_s, 1),
                start=start.strftime("%Y-%m-%d %H:%M:%S"),
            )

            t0 = time.monotonic()
            rows = await _fetch_symbol(client, symbol, start, now)
            fetch_s = time.monotonic() - t0

            if rows:
                t1 = time.monotonic()
                _insert_rows(ch, rows)
                insert_s = time.monotonic() - t1
                log.info(
                    "recovery.done",
                    symbol=symbol,
                    rows=len(rows),
                    fetch_s=round(fetch_s, 1),
                    insert_s=round(insert_s, 1),
                )
            else:
                log.info("recovery.no_rows", symbol=symbol)

            result[symbol] = len(rows)

    return result
