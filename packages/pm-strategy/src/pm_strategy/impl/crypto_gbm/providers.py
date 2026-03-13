"""Feature providers for the crypto GBM repricing strategy."""

from __future__ import annotations

import time
from typing import Any

import structlog
from pm_core.models import NormalizedTrade

from pm_strategy.impl.crypto_gbm.gbm import estimate_rolling_sigma
from pm_strategy.impl.crypto_gbm.window import (
    WindowInfo,
    is_btc_up_down,
    parse_window,
)

log = structlog.get_logger()


class ExchangePriceProvider:
    """Maintains rolling BTC price + sigma from exchange bars.

    Consumes 1-second bars injected via ``handle_bar()`` (called from
    the strategy CLI's Kafka subscriber for ``exchange.bars``).
    """

    name: str = "exchange_prices"

    def __init__(
        self,
        primary_exchange: str = "BINANCE",
        primary_symbol: str = "BTC-USDT",
        sigma_lookback_min: int = 1440,
        **_extra: object,
    ) -> None:
        self._primary_exchange = primary_exchange.upper()
        self._primary_symbol = primary_symbol
        self._sigma_lookback = sigma_lookback_min

        self._latest_price: float = 0.0
        self._latest_ts: float = 0.0

        self._current_minute_ts: int = 0
        self._current_minute_close: float = 0.0
        self._minute_closes: list[float] = []
        self._sigma_1m: float | None = None
        self._bar_count: int = 0

    async def compute(self, backend: Any) -> None:
        """Bootstrap sigma from ClickHouse historical bars."""
        try:
            df = await backend.query_custom(
                "SELECT toStartOfMinute(ts) AS minute, argMax(close, ts) AS close "
                "FROM exchange_bars "
                "WHERE symbol = {symbol:String} AND exchange = {exchange:String} "
                "  AND ts >= now() - INTERVAL 25 HOUR "
                "GROUP BY minute ORDER BY minute",
                symbol=self._primary_symbol,
                exchange=self._primary_exchange,
            )
            if not df.is_empty():
                self._minute_closes = df["close"].to_list()
                self._sigma_1m = estimate_rolling_sigma(self._minute_closes, self._sigma_lookback)
                self._latest_price = self._minute_closes[-1]
                self._latest_ts = time.time()
                log.info(
                    "exchange_prices.bootstrapped",
                    minutes=len(self._minute_closes),
                    sigma=f"{self._sigma_1m:.8f}" if self._sigma_1m else "None",
                    latest_price=f"{self._latest_price:.2f}",
                )
        except Exception:
            log.info("exchange_prices.bootstrap_skipped", reason="no CH data yet")

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op -- exchange prices come via handle_bar()."""

    async def refresh(self, backend: Any) -> None:
        """No-op -- sigma is computed incrementally."""

    def get_features(self) -> dict[str, Any]:
        return {
            "exchange_btc_price": self._latest_price,
            "exchange_btc_sigma": self._sigma_1m,
            "exchange_btc_latest_ts": self._latest_ts,
            "exchange_btc_bar_count": self._bar_count,
        }

    def handle_bar(self, bar: dict[str, Any]) -> None:
        """Process a 1-second bar from the exchange.bars topic."""
        exchange = bar.get("exchange", "")
        symbol = bar.get("symbol", "")
        if self._bar_count == 0:
            log.info(
                "exchange_prices.first_bar",
                exchange=exchange,
                symbol=symbol,
                primary_exchange=self._primary_exchange,
                primary_symbol=self._primary_symbol,
                keys=list(bar.keys())[:5],
            )
        if exchange != self._primary_exchange or symbol != self._primary_symbol:
            return

        close = float(bar.get("close", 0.0))
        ts = int(bar.get("ts", 0))
        if close <= 0 or ts <= 0:
            return

        self._latest_price = close
        self._latest_ts = float(ts)
        self._bar_count += 1

        minute_ts = ts - (ts % 60)
        if minute_ts > self._current_minute_ts:
            if self._current_minute_close > 0:
                self._minute_closes.append(self._current_minute_close)
                max_len = self._sigma_lookback + 100
                if len(self._minute_closes) > max_len:
                    self._minute_closes = self._minute_closes[-max_len:]
                self._sigma_1m = estimate_rolling_sigma(self._minute_closes, self._sigma_lookback)
            self._current_minute_ts = minute_ts

        self._current_minute_close = close


class CryptoWindowProvider:
    """Tracks active BTC Up/Down markets and their window timing."""

    name: str = "crypto_windows"

    def __init__(self, **_extra: object) -> None:
        self._windows: dict[str, WindowInfo] = {}
        self._s0_prices: dict[str, float] = {}

    async def compute(self, backend: Any) -> None:
        df = await backend.query_markets()
        self._rebuild_windows(df)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op -- windows are metadata-driven."""

    async def refresh(self, backend: Any) -> None:
        df = await backend.query_markets()
        self._rebuild_windows(df)

    def get_features(self) -> dict[str, Any]:
        return {
            "crypto_windows": self._windows,
            "crypto_window_s0": self._s0_prices,
        }

    def record_s0(self, condition_id: str, s0: float) -> None:
        """Capture BTC price at window open (called by strategy)."""
        self._s0_prices[condition_id] = s0

    def _rebuild_windows(self, markets_df: Any) -> None:
        """Parse and filter to BTC Up/Down markets near current time."""

        now = time.time()
        new_windows: dict[str, WindowInfo] = {}

        if markets_df is None or markets_df.is_empty():
            return

        for row in markets_df.iter_rows(named=True):
            question = row.get("question", "") or ""
            if not is_btc_up_down(question):
                continue

            cid = row.get("condition_id", "")
            token_yes = row.get("token_yes", "") or ""
            token_no = row.get("token_no", "") or ""

            win = parse_window(question, cid, token_yes, token_no)
            if win is None:
                continue

            start_ts = win.window_start_utc.timestamp()
            end_ts = win.window_end_utc.timestamp()
            if end_ts < now - 60:
                continue
            if start_ts > now + 300:
                continue

            new_windows[cid] = win

        old_count = len(self._windows)
        self._windows = new_windows

        active_cids = set(new_windows.keys())
        expired = [cid for cid in self._s0_prices if cid not in active_cids]
        for cid in expired:
            del self._s0_prices[cid]

        if len(new_windows) != old_count:
            log.info(
                "crypto_windows.refreshed",
                active=len(new_windows),
                s0_tracked=len(self._s0_prices),
            )
