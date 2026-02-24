"""Tests for CLOB API client."""

from __future__ import annotations

from polymarket_pipeline.execution.clob_client import (
    ClobClient,
    OpenOrder,
    OrderResult,
    OrderSide,
    OrderType,
)


def test_order_result_dataclass() -> None:
    r = OrderResult(order_id="abc", success=True, filled_price=0.55, filled_size=100.0)
    assert r.order_id == "abc"
    assert r.success is True


def test_open_order_dataclass() -> None:
    o = OpenOrder(
        order_id="x",
        condition_id="c",
        asset_id="a",
        side="BUY",
        price=0.5,
        size=100.0,
        size_matched=50.0,
    )
    assert o.size_matched == 50.0


def test_order_side_enum() -> None:
    assert OrderSide.BUY == "BUY"
    assert OrderSide.SELL == "SELL"


def test_order_type_enum() -> None:
    assert OrderType.MARKET == "MARKET"
    assert OrderType.LIMIT == "LIMIT"


def test_client_auth_headers_empty() -> None:
    client = ClobClient(api_key="")
    assert client._auth_headers() == {}


def test_client_auth_headers_present() -> None:
    client = ClobClient(api_key="k", api_secret="s", api_passphrase="p")
    headers = client._auth_headers()
    assert headers["POLY_API_KEY"] == "k"
    assert headers["POLY_API_SECRET"] == "s"
    assert headers["POLY_API_PASSPHRASE"] == "p"


def test_client_has_context_manager() -> None:
    assert hasattr(ClobClient, "__aenter__")
    assert hasattr(ClobClient, "__aexit__")
