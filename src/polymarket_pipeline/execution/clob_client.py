"""Polymarket CLOB API client for order execution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# Transient errors that are safe to retry.
_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class OrderResult:
    """Result of an order submission."""

    order_id: str
    success: bool
    filled_price: float | None = None
    filled_size: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class OpenOrder:
    """An open order on the CLOB."""

    order_id: str
    condition_id: str
    asset_id: str
    side: str
    price: float
    size: float
    size_matched: float


@dataclass(frozen=True)
class ClobOrderbook:
    """Orderbook snapshot from CLOB REST API."""

    best_bid: float
    best_ask: float
    spread: float
    fetched_at: float


class ClobClient:
    """Async client for the Polymarket CLOB REST API.

    Parameters
    ----------
    base_url:
        CLOB API base URL.
    api_key:
        API key for authentication.
    api_secret:
        API secret for signing requests.
    api_passphrase:
        API passphrase.
    """

    def __init__(
        self,
        base_url: str = "https://clob.polymarket.com",
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=10.0,
                write=5.0,
                pool=5.0,
            ),
            headers=self._auth_headers(),
        )
        self._ob_cache: dict[str, tuple[float, ClobOrderbook]] = {}
        self._ob_lock = asyncio.Lock()
        self._ob_ttl = 5.0  # seconds

    def _auth_headers(self) -> dict[str, str]:
        """Build authentication headers."""
        if not self._api_key:
            return {}
        return {
            "POLY_API_KEY": self._api_key,
            "POLY_API_SECRET": self._api_secret,
            "POLY_API_PASSPHRASE": self._api_passphrase,
        }

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ClobClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def submit_order(
        self,
        *,
        condition_id: str,
        asset_id: str,
        side: OrderSide,
        size: float,
        price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
        max_retries: int = 3,
    ) -> OrderResult:
        """Submit an order to the CLOB with retry on transient errors.

        Retries up to *max_retries* times on timeouts and connection errors
        with exponential backoff (1s, 2s, 4s).  HTTP 4xx errors (business
        logic failures) are **not** retried.
        """
        payload: dict[str, Any] = {
            "market": condition_id,
            "asset_id": asset_id,
            "side": side.value,
            "size": size,
            "type": order_type.value,
        }
        if price is not None and order_type == OrderType.LIMIT:
            payload["price"] = price

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = await self._client.post("/order", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return OrderResult(
                    order_id=data.get("id", ""),
                    success=True,
                    filled_price=data.get("filled_avg_price"),
                    filled_size=data.get("size_matched"),
                )
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                # 429 = rate limited — back off and retry
                if status_code == 429 and attempt < max_retries:
                    retry_after = float(e.response.headers.get("Retry-After", "2"))
                    log.warning(
                        "clob.rate_limited",
                        attempt=attempt,
                        retry_after_s=retry_after,
                    )
                    await asyncio.sleep(min(retry_after, 10.0))
                    last_error = e
                    continue
                # Other 4xx = business logic error — do NOT retry.
                log.error(
                    "clob.order_failed",
                    status=status_code,
                    body=e.response.text,
                )
                return OrderResult(order_id="", success=False, error=str(e))
            except _RETRYABLE as e:
                last_error = e
                if attempt < max_retries:
                    delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                    log.warning(
                        "clob.order_retry",
                        attempt=attempt,
                        max=max_retries,
                        delay_s=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(
                    "clob.order_exhausted",
                    attempts=max_retries,
                    error=str(e),
                )
            except Exception as e:
                log.exception("clob.order_error")
                return OrderResult(order_id="", success=False, error=str(e))

        return OrderResult(
            order_id="",
            success=False,
            error=f"exhausted {max_retries} retries: {last_error}",
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        try:
            resp = await self._client.delete(f"/order/{order_id}")
            resp.raise_for_status()
            return True
        except Exception:
            log.exception("clob.cancel_error", order_id=order_id)
            return False

    async def get_open_orders(self, condition_id: str | None = None) -> list[OpenOrder]:
        """Get all open orders, optionally filtered by market."""
        params: dict[str, str] = {}
        if condition_id:
            params["market"] = condition_id
        try:
            resp = await self._client.get("/orders", params=params)
            resp.raise_for_status()
            data = resp.json()
            return [
                OpenOrder(
                    order_id=o["id"],
                    condition_id=o.get("market", ""),
                    asset_id=o.get("asset_id", ""),
                    side=o.get("side", ""),
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size", 0)),
                    size_matched=float(o.get("size_matched", 0)),
                )
                for o in data
            ]
        except Exception:
            log.exception("clob.get_orders_error")
            return []

    async def get_balances(self) -> dict[str, float]:
        """Get current token balances."""
        try:
            resp = await self._client.get("/balances")
            resp.raise_for_status()
            data = resp.json()
            return {item["asset_id"]: float(item["balance"]) for item in data}
        except Exception:
            log.exception("clob.get_balances_error")
            return {}

    async def get_orderbook(self, token_id: str) -> ClobOrderbook | None:
        """Fetch orderbook from CLOB REST API with 5s TTL cache.

        ``GET /book?token_id=X`` — public endpoint, no auth required.
        Returns best bid/ask or None on failure.

        Cache reads/writes are serialized via ``_ob_lock`` to prevent
        concurrent callers from issuing redundant network fetches.
        """
        now = time.monotonic()

        # Fast path: check cache without lock (read is safe)
        cached = self._ob_cache.get(token_id)
        if cached is not None:
            ts, ob = cached
            if now - ts < self._ob_ttl:
                return ob

        # Slow path: acquire lock, double-check, then fetch
        async with self._ob_lock:
            # Re-check — another task may have populated cache while we waited
            cached = self._ob_cache.get(token_id)
            if cached is not None:
                ts, ob = cached
                if time.monotonic() - ts < self._ob_ttl:
                    return ob

            try:
                resp = await self._client.get("/book", params={"token_id": token_id})
                resp.raise_for_status()
                data = resp.json()

                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if not bids or not asks:
                    log.debug(
                        "clob.orderbook_empty",
                        token_id=token_id[:16],
                        has_bids=bool(bids),
                        has_asks=bool(asks),
                    )
                    return None

                best_bid = float(bids[0]["price"])
                best_ask = float(asks[0]["price"])
                ob = ClobOrderbook(
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=round(best_ask - best_bid, 6),
                    fetched_at=time.time(),
                )
                self._ob_cache[token_id] = (time.monotonic(), ob)
                return ob
            except Exception:
                log.warning("clob.get_orderbook_error", token_id=token_id[:16])
                return None
