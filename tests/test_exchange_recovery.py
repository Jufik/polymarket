"""Tests for exchange_recovery — Binance REST API gap recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from polymarket_pipeline.live.ingestors.exchange_recovery import (
    BINANCE_NATIVE,
    _fetch_symbol,
    recover_exchange_bars,
)


def _make_kline(ts_ms: int, close: float = 50000.0) -> list[Any]:
    """Build a fake Binance kline array."""
    return [
        ts_ms,           # 0: open time
        str(close),      # 1: open
        str(close + 1),  # 2: high
        str(close - 1),  # 3: low
        str(close),      # 4: close
        "1.5",           # 5: volume
        ts_ms + 999,     # 6: close time
        "75000",         # 7: quote vol
        10,              # 8: trades
        "0.8",           # 9: taker buy base vol
        "40000",         # 10: taker buy quote vol
        "0",             # 11: ignore
    ]


@pytest.fixture
def mock_binance_response():
    """Mock httpx.AsyncClient to return 3 fake klines then empty."""
    base_ts = int(datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    klines = [_make_kline(base_ts + i * 1000) for i in range(3)]

    async def _mock_get(url: str, params: Any = None, timeout: Any = None) -> Any:
        resp = MagicMock()
        # Only return data on the first call
        if not hasattr(_mock_get, "_called"):
            _mock_get._called = True
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json.return_value = klines
        else:
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json.return_value = []
        return resp

    return _mock_get


def test_binance_native_symbols():
    """All expected symbols are mapped."""
    assert "BTC-USDT" in BINANCE_NATIVE
    assert "ETH-USDT" in BINANCE_NATIVE
    assert "SOL-USDT" in BINANCE_NATIVE
    assert BINANCE_NATIVE["BTC-USDT"] == "BTCUSDT"


def test_fetch_symbol_parses_klines(mock_binance_response):
    """_fetch_symbol correctly parses Binance klines into CH-ready rows."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = mock_binance_response

    start = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 10, 12, 0, 10, tzinfo=timezone.utc)

    rows = asyncio.run(_fetch_symbol(client, "BTC-USDT", start, end))

    assert len(rows) == 3
    # Each row: (exchange, symbol, ts, open, high, low, close, volume, buy_vol, trades)
    assert rows[0][0] == "BINANCE"
    assert rows[0][1] == "BTC-USDT"
    assert isinstance(rows[0][2], datetime)
    assert rows[0][3] == 50000.0  # open
    assert rows[0][9] == 10  # trades


def test_fetch_symbol_unknown_symbol():
    """Unknown symbol returns empty list."""
    client = MagicMock(spec=httpx.AsyncClient)
    rows = asyncio.run(_fetch_symbol(client, "DOGE-USDT", datetime.now(timezone.utc), datetime.now(timezone.utc)))
    assert rows == []


def test_recover_skips_fresh_data():
    """recover_exchange_bars skips symbols where gap < min_gap_seconds."""
    now = datetime.now(timezone.utc)

    mock_ch = MagicMock()
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, idx: now - timedelta(seconds=5)  # 5s ago = fresh
    mock_ch.query.return_value = MagicMock(first_row=mock_row)

    with patch(
        "pm_ingest.ingestors.exchange_recovery._get_ch_client",
        return_value=mock_ch,
    ):
        result = asyncio.run(
            recover_exchange_bars("host", 18123, "polymarket", symbols=["BTC-USDT"])
        )

    assert result["BTC-USDT"] == 0
    # No HTTP calls should have been made
