"""Polymarket CLOB API client for order execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


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
            timeout=30.0,
            headers=self._auth_headers(),
        )

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
    ) -> OrderResult:
        """Submit an order to the CLOB.

        For market orders, price is ignored.
        For limit orders, price is required.
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
            log.error("clob.order_failed", status=e.response.status_code, body=e.response.text)
            return OrderResult(order_id="", success=False, error=str(e))
        except Exception as e:
            log.exception("clob.order_error")
            return OrderResult(order_id="", success=False, error=str(e))

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
